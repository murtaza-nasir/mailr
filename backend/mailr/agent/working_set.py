"""The assistant's working set per chat session.

Every item a chat creates or changes (a draft, an event, a task, a note, a filter) is recorded on
the chat session (`context["working_set"]`) with its latest version. Together with the editors open
on screen (agent/screen.py), it tells the assistant what "it" and "that" mean in a follow-up
("make it shorter", "fix that"): the item to update, by its id and version, in place.
"""

from __future__ import annotations

import time
from typing import Any

MAX_ITEMS = 12
KEY = "working_set"
NOUNS = {"draft": "draft", "event": "event", "task": "task", "memory": "note", "rule": "filter"}


def items(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    v = (context or {}).get(KEY)
    return [i for i in v if isinstance(i, dict) and i.get("kind") and i.get("id") is not None] \
        if isinstance(v, list) else []


def add(context: dict[str, Any] | None, refs: list[dict[str, Any]]) -> dict[str, Any]:
    """The session context with these refs ({kind, id, version, title}) recorded, newest last."""
    cur = items(context)
    for r in refs:
        if not r or r.get("kind") is None or r.get("id") is None:
            continue
        kind, iid = str(r["kind"]), str(int(r["id"]) if isinstance(r["id"], int) else r["id"])
        old = next((i for i in cur if i["kind"] == kind and str(i["id"]) == iid), None)
        cur = [i for i in cur if i is not old]
        entry = {"kind": kind, "id": iid, "version": r.get("version"),
                 "title": str(r.get("title") or (old or {}).get("title") or "")[:200], "at": int(time.time())}
        if r.get("deleted"):
            continue
        cur.append(entry)
    return {**(context or {}), KEY: cur[-MAX_ITEMS:]}


def version_of(context: dict[str, Any] | None, kind: str, item: Any) -> int | None:
    for i in reversed(items(context)):
        if i["kind"] == kind and str(i["id"]) == str(item):
            v = i.get("version")
            return int(v) if isinstance(v, int) else None
    return None


def block(context: dict[str, Any] | None, ui: dict[str, Any]) -> str:
    """The prompt lines that say what the chat is working on."""
    ws = items(context)
    open_drafts = [w for w in ui.get("compose") or [] if w.get("draft_id") or w.get("focused")]
    editors = ui.get("editors") or []
    if not ws and not open_drafts and not editors:
        return ""
    lines = ["Working set (what \"it\", \"that\" and \"this\" refer to when the user asks for a change):"]
    for i in ws:
        noun = NOUNS.get(i["kind"], i["kind"])
        idk = "draft_id" if i["kind"] == "draft" else ("uid" if i["kind"] in ("event", "task") else "id")
        lines.append(f"- {noun} you made or changed in this chat: {idk} {i['id']}, version {i.get('version')}"
                     + (f', "{i["title"]}"' if i.get("title") else ""))
    for w in open_drafts:
        if w.get("draft_id"):
            lines.append(f"- draft open in a compose window: draft_id {w['draft_id']}, version "
                         f"{w.get('base_version')}" + (" (focused)" if w.get("focused") else "")
                         + (" (the user has unsaved changes)" if w.get("dirty") else ""))
    for e in editors:
        noun = NOUNS.get(e["kind"], e["kind"])
        idk = "uid" if e["kind"] in ("event", "task") else "id"
        lines.append(f"- {noun} open in an editor: {idk} {e['id']}, version {e.get('base_version')}"
                     + (" (focused)" if e.get("focused") else "")
                     + (" (the user has unsaved changes)" if e.get("dirty") else ""))
    lines.append(
        "Rules: a request to change, fix, shorten or rewrite \"it\" means the item above (the focused or the only "
        "one, else the one you changed last): update that same item in place with its id and version "
        "(update_draft, update_event, update_task, remember with memory_id, manage_filter with filter_id). Never "
        "create a new draft or item to revise one unless the user asks for a new one. If two items could be "
        "meant, ask which one before changing anything.")
    return "\n".join(lines)
