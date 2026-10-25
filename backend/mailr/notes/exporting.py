"""Markdown for notes (note.export): text notes as they are, checklists as `- [ ]` lines that the
item grammar parses back to the same items (`milk ×2`, `500 g flour`), sections as `## Name`."""

from __future__ import annotations

import re
from typing import Any

from mailr.notes.itemadd import line_for
from mailr.notes.models import Note


def markdown(note: dict[str, Any]) -> str:
    out: list[str] = []
    if note.get("title"):
        out += [f"# {note['title']}", ""]
    if note.get("kind") == "checklist":
        names = {s["id"]: s["name"] for s in note.get("sections") or []}
        by: dict[str | None, list[dict[str, Any]]] = {}
        for i in note.get("items") or []:
            by.setdefault(i.get("section_id"), []).append(i)
        for sid in [None] + [s["id"] for s in note.get("sections") or []]:
            rows = by.get(sid) or []
            if sid is not None:
                if out and out[-1] != "":
                    out.append("")
                out += [f"## {names[sid]}", ""]
            for i in rows:
                q = i.get("qty")
                line = ("- [x] " if i.get("checked") else "- [ ] ") + line_for(i["text"], q and {
                    "amount": q["amount"], "unit": q["unit"]})
                out.append(line)  # the extra line is in notes.json only: a line here would become an item
    else:
        out.append(note.get("body") or "")
    return "\n".join(out).rstrip() + "\n"


def filename(note: Note, used: set[str]) -> str:
    base = re.sub(r"[^\w\- ]+", "", note.title or "").strip()[:60] or ("List" if note.kind == "checklist" else "Note")
    name = f"{base}.md"
    n = 2
    while name.lower() in used:
        name = f"{base} ({n}).md"
        n += 1
    used.add(name.lower())
    return name
