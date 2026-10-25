# ACT mail.show_in_tab @api
"""Show in tab: which tab of the active inbox layout a conversation shows in, and what changes
would make it show in another one.

One place for the rules the lists apply (core/layouts.py): a tab lists the union of its sections,
less what claiming tabs take (`claimed_elsewhere`: a claiming tab yields only to claiming tabs to
its left). A conversation's tab is the leftmost tab that lists it.

Each tab is one of these kinds, from its first section that names no search and no state filter
(its "anchor"):

    first     the layout's first tab (the Inbox): drop the labels of claiming tabs, and set its
              category when that section has one;
    label     the section lists a label: add the label (and the Inbox when the section has
              `inbox_only`);
    category  the section lists a category of the Inbox: set the category;
    inbox     the section lists the plain Inbox (a later tab without a category or label);
    disabled  every section is a search, a state filter (unread, starred, important) or a list
              such as Starred or Sent: labels and categories cannot put mail there, so the menu
              shows it greyed out with `reason`.

Picking a tab (core/actions.py, action "show_in_tab") applies `plan`: the anchor's label (or the
Inbox) is added, the labels of claiming tabs that would take the conversation first are removed,
and the category is set when the anchor has one or when a claiming category tab would otherwise
take the conversation (it then becomes Primary).
"""

from __future__ import annotations

from typing import Any

from mailr.core import layouts

_FILTER_WORDS = {"important": "important", "important_unread": "important and unread", "unread": "unread",
                 "starred": "starred"}
_BASE_WORDS = {"all": "All mail", "starred": "Starred", "important": "Important", "snoozed": "Snoozed",
               "sent": "Sent", "drafts": "Drafts", "archive": "Archive"}


def active_layout(settings: dict[str, Any]) -> dict[str, Any] | None:
    return layouts.find(settings, layouts.active_id(settings)) or layouts.find(settings, "default")


def _anchor(tab: dict[str, Any]) -> dict[str, Any] | None:
    """The first section a label or category can satisfy: no search, no state filter, and a base
    of the Inbox or a label."""
    for s in tab.get("sections") or []:
        base = s.get("base") or "inbox"
        if (s.get("q") or "").strip() or s.get("filter"):
            continue
        if base == "inbox" or base.startswith("label:"):
            return s
    return None


def _reason(tab: dict[str, Any]) -> str:
    secs = tab.get("sections") or []
    if secs and all((s.get("q") or "").strip() for s in secs):
        return "This tab shows the results of a search. Mail shows here when it matches the search."
    flt = next((s.get("filter") for s in secs if s.get("filter")), None)
    if flt:
        return f"This tab shows {_FILTER_WORDS.get(flt, flt)} mail. A label or category cannot put mail here."
    base = next((s.get("base") for s in secs if s.get("base") in _BASE_WORDS), None)
    if base:
        return f"This tab lists {_BASE_WORDS[base]}. A label or category cannot put mail here."
    return "A label or category cannot put mail in this tab."


def targets(layout: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The layout's tabs in tab-bar order, each with its kind (module docstring)."""
    out: list[dict[str, Any]] = []
    for i, t in enumerate((layout or {}).get("tabs") or []):
        a = _anchor(t)
        row: dict[str, Any] = {"id": t["id"], "name": t["name"], "icon": t.get("icon"), "label_id": None,
                               "category": None, "reason": None}
        if a is None:
            row.update(kind="disabled", reason=_reason(t))
        else:
            base = a.get("base") or "inbox"
            if base.startswith("label:"):
                row["label_id"] = int(base.split(":", 1)[1])
            row["category"] = a.get("category") or None
            row["kind"] = ("first" if i == 0 else "label" if row["label_id"] is not None
                           else "category" if row["category"] else "inbox")
        out.append(row)
    return out


def plan(settings: dict[str, Any], layout: dict[str, Any], tab_id: str, category: str | None,
         inbox_id: int | None) -> dict[str, Any]:
    """What makes a conversation show in `tab_id`: {"add": [label ids], "remove": [label ids],
    "category": new category or None to keep it}. `category` is the conversation's own now.
    Raises ValueError for an unknown or disabled tab."""
    tab = next((t for t in layout.get("tabs") or [] if t["id"] == tab_id), None)
    if tab is None:
        raise ValueError("no such tab in the active layout")
    a = _anchor(tab)
    if a is None:
        raise ValueError(_reason(tab))
    add: list[int] = []
    base = a.get("base") or "inbox"
    if base.startswith("label:"):
        add.append(int(base.split(":", 1)[1]))
        # A label section with "Only mail in the Inbox" lists the label's mail in the Inbox.
        if layouts.inbox_only(a) and inbox_id is not None:
            add.append(inbox_id)
    elif inbox_id is not None:
        add.append(inbox_id)
    new_cat = a.get("category") or None
    remove: list[int] = []
    for e in layouts.claimed_elsewhere(settings, layout["id"], tab_id):
        ebase = e.get("base") or "inbox"
        if ebase.startswith("label:"):
            lid = int(ebase.split(":", 1)[1])
            if lid not in add and lid not in remove:
                remove.append(lid)
        elif ebase == "inbox" and e.get("category") and not (e.get("q") or "").strip():
            # A claiming category tab takes the conversation unless its category differs.
            effective = new_cat or category or "primary"
            if effective == e["category"] and e["category"] != "primary" and new_cat is None:
                new_cat = "primary"
        # A claiming search or state tab cannot be satisfied by labels; the result reports the
        # tab the conversation then shows in.
    if new_cat is not None and new_cat == (category or "primary"):
        new_cat = None
    return {"add": add, "remove": remove, "category": new_cat}


async def tab_conditions(db, p, layout: dict[str, Any]) -> list[tuple[str, Any]]:
    """[(tab id, SQL condition)] for the layout's tabs, as the lists build them (a tab that can
    match nothing is left out)."""
    from sqlalchemy import and_

    from mailr.api.layout_routes import _tab_where

    out = []
    for t in layout.get("tabs") or []:
        where = await _tab_where(db, p, layout["id"], t)
        if where is not None:
            out.append((t["id"], and_(*where)))
    return out


async def current_tabs(db, p, layout: dict[str, Any] | None, conv_ids: list[int]) -> dict[int, str | None]:
    """{conversation id: the id of the leftmost tab that lists it, or None}."""
    from sqlalchemy import select

    from mailr.models import Conversation

    out: dict[int, str | None] = {cid: None for cid in conv_ids}
    if not layout or not conv_ids:
        return out
    conds = await tab_conditions(db, p, layout)
    if not conds:
        return out
    cols = [c.label(f"t{i}") for i, (_tid, c) in enumerate(conds)]
    rows = (await db.execute(select(Conversation.id, *cols).where(
        Conversation.user_id == p.user_id, Conversation.id.in_(conv_ids)))).all()
    for r in rows:
        out[r[0]] = next((tid for i, (tid, _c) in enumerate(conds) if r[i + 1]), None)
    return out
