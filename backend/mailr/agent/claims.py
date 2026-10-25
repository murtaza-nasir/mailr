"""Truthful replies: what a turn changed, and replies that claim more.

A model can say "Done, the reminder is set" after a write that failed, waited for approval, went
to the wrong item, or never happened (for example, an absolute reminder that was stored but
never fired, reported as set). Three parts keep the reply honest:

1. Task writes are checked. create_task / update_task refuse fields they do not read and
   return an error naming a field that did not take; their result carries the stored state
   (`will_remind`), shown on the step line ("saved · reminds 2026-10-14 06:00").
2. `change_of` records each confirmed write on its step: a step that succeeded and wrote
   something, built from tool results only. The turn's list is kept on the answer row
   (meta.changes) and sent with the final event.
3. `claims_change(text)` matches a reply that says a change happened. When the turn has no
   confirmed write, the model is asked once more, without tools, to answer from the tool results;
   a reply that still claims a change is stored with `unconfirmed`, and the web panel then says
   that nothing was saved in this turn.

The claim test reads English. A reply in another language is not checked; parts 1 and 2 still hold.
"""

from __future__ import annotations

import re
from typing import Any

# Tools that only read. Every other tool that returns without error and without waiting for an
# approval counts as a write.
_READ_PREFIXES = ("get_", "list_", "search_", "find_", "read_", "check_")
_READ_TOOLS = frozenset({
    "recall", "build_context", "meeting_prep", "web_search", "use_skill", "show_in_tab", "note_history",
    "table_stats", "table_history", "ask_recording", "recordings_for_event", "propose_action_items",
})

_CLAIM = re.compile(
    r"(?:^|\n)\s*(?:\*\*)?(?:done|all set|sorted)\b"
    r"|\bi(?:'ve|’ve| have)\s+(?:now\s+|also\s+|just\s+)?"
    r"(?:set|created|added|scheduled|rescheduled|updated|changed|moved|saved|deleted|removed|archived|sent|"
    r"marked|completed|renamed|labell?ed|trashed|booked|turned|put|replaced|cleared|snoozed|made)\b"
    # Present state and present perfect only: "was created" / "were sent on 23 Oct" narrate what
    # happened to things mentioned in mail, and are not claims about this turn.
    r"|\b(?:is|are|has been|have been)\s+(?:now\s+)?"
    r"(?:set|created|added|scheduled|rescheduled|updated|saved|moved|deleted|removed|archived|sent|marked|"
    r"completed|trashed|replaced|cleared)\b(?!\s+(?:once|when|after|if)\b)",
    re.IGNORECASE)


def is_write(name: str) -> bool:
    return not (name.startswith(_READ_PREFIXES) or name in _READ_TOOLS)


def change_of(name: str, data: Any, refs: list[dict[str, Any]], summary: Any) -> dict[str, Any]:
    """One confirmed write, from the tool's result: the tool, the items it changed, its step line."""
    out: dict[str, Any] = {"tool": name,
                           "items": [{"kind": r.get("kind"), "id": r.get("id"), "title": r.get("title") or ""}
                                     for r in refs or []]}
    if isinstance(data, dict):
        if isinstance(data.get("will_remind"), list):
            out["will_remind"] = [w.get("at") for w in data["will_remind"] if isinstance(w, dict)]
        if isinstance(data.get("changed"), list):
            out["changed"] = data["changed"]
    if isinstance(summary, dict) and summary.get("key"):
        out["summary_msg"] = summary
    elif isinstance(summary, str) and summary:
        out["summary"] = summary[:200]
    return out


def claims_change(text: str | None) -> bool:
    return bool(text) and bool(_CLAIM.search(text or ""))


def correction_note(steps: list[dict[str, Any]]) -> str:
    """The note the model gets when its reply claims a change no tool confirmed."""
    failed = [s for s in steps if is_write(str(s.get("name") or "")) and s.get("ok") is False]
    waiting = [s for s in steps if s.get("approval_id") is not None]
    facts = []
    for s in failed[:5]:
        facts.append(f"{s.get('name')} failed: {str(s.get('error') or '')[:300]}")
    if waiting:
        facts.append(f"{len(waiting)} change(s) wait for the user's approval and have not happened yet")
    if not failed and not waiting:
        facts.append("no tool that changes anything ran")
    return ("[mailr: your reply says something was changed, but no change succeeded in this turn ("
            + "; ".join(facts) + "). Write the reply again from the tool results above: say only what they "
            "confirm. If a change failed or waits for approval, say so plainly. If nothing was changed, say "
            "so and what you would do. If your reply does not claim a change after all, repeat it unchanged. "
            "Do not call tools.]")
