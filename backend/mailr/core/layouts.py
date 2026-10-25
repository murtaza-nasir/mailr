# ACT layout.tab_detail @api, ACT layout.inbox_only @api, ACT layout.hide_empty @api
"""Inbox layouts: optional tabs, each holding one or more sections.

Every inbox type is a layout. The built-in types (Default with category tabs, Important/Unread/
Starred first, Custom sections, Multiple Inboxes) are presets computed from the user's settings;
custom layouts live in settings["inbox_layouts"]. Clients render any layout the same way, so a
new preset or a user's own layout needs no client changes.

    layout  = {id, name, preset?, tab_detail?, hide_empty?, tabs: [tab]}
    tab     = {id, name, icon?, indicators: ["unread"|"all"|"due"], badge, claims?, sections: [section]}
    section = {id, title, base, category?, filter?, q?, limit?, exclude_above, inbox_only?}

A section lists the conversations of `base` (a list view: inbox, all, starred, important,
label:<id>, …), narrowed by `category`, a conversation-state `filter` (important,
important_unread, unread, starred) and a search `q` in the search syntax. `inbox_only` narrows a
base other than the inbox to conversations the Inbox lists ("Only mail in the Inbox"), so a
Financial label section beside Inbox sections drops a conversation once it is archived. It is always false on an inbox base. With
`exclude_above`, conversations that belong to an earlier section of the same tab are left out
(Gmail's "Everything else"). `limit` is how many conversations a section shows before
"View all"; null means the section is the tab's main list, paged from the toolbar.

A tab with `claims` keeps its conversations to itself: the other tabs of the layout leave out
whatever it lists (as Gmail's category tabs keep promotions out of Primary). A "GitHub" tab on
label:github with claims takes GitHub mail out of the Inbox tab without archiving it. Mail that
two claiming tabs list goes to the leftmost of them (`claimed_elsewhere`).

A tab's `indicators` pick what its label shows: "unread" ("3 new"), "all" (every conversation
the tab lists, read or unread) and "due" (conversations with a deadline, with the earliest
date). `badge` is the older single choice ("unread", "all" or "none"), still written for
clients that read only it: the first of unread and all that the tab shows. Layouts saved before
indicators existed hold only `badge`, sometimes as a boolean; true reads as ["unread"] and false
as [].

A layout's `tab_detail` (ACT layout.tab_detail; off when absent) asks clients to show all of a
tab's indicators while the tab strip has room, each tab falling back to its usual form when it
does not. The server only stores it.

A layout's `hide_empty` (ACT layout.hide_empty; off when absent) asks clients to leave out a
section that has no conversations, instead of showing "No … conversations" under its header. A
section shown alone (the section focus) still shows, empty or not. The server only stores it.
"""

from __future__ import annotations

import re
from typing import Any

MAX_LAYOUTS = 12
MAX_TABS = 8
MAX_SECTIONS = 8
FILTERS = ("important", "important_unread", "unread", "starred")
BASES = ("inbox", "all", "starred", "important", "snoozed", "sent", "drafts", "archive")
BADGES = ("unread", "all", "none")
INDICATORS = ("unread", "all", "due")
ICONS = ("inbox", "tag", "users", "info", "message", "bag", "star", "clock", "bookmark", "briefcase",
         "home", "flag", "zap", "laptop", "book", "heart", "bell", "folder", "mail")

_SECTION_TITLES = {
    "important": "Important",
    "important_unread": "Important and unread",
    "unread": "Unread",
    "starred": "Starred",
    "everything": "Everything else",
}
_CAT = {
    "primary": ("Primary", "inbox"),
    "promotions": ("Promotions", "tag"),
    "social": ("Social", "users"),
    "updates": ("Updates", "info"),
    "forums": ("Forums", "message"),
    "purchases": ("Purchases", "bag"),
}
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
PRESET_IDS = ("default", "important_first", "unread_first", "starred_first", "priority", "multiple", "simple")


def _sec(id: str, title: str, base: str = "inbox", *, filter: str | None = None, q: str = "",
         category: str | None = None, limit: int | None = None, exclude_above: bool = True) -> dict[str, Any]:
    return {"id": id, "title": title, "base": base, "category": category, "filter": filter, "q": q,
            "limit": limit, "exclude_above": exclude_above, "inbox_only": False}


def inbox_only(sec: dict[str, Any]) -> bool:
    """The section lists only conversations in the Inbox, on top of its own base. An inbox base
    already does, so the flag means nothing there."""
    return bool(sec.get("inbox_only")) and (sec.get("base") or "inbox") != "inbox"


def _priority_sections(names: list[str], preview: int = 10) -> list[dict[str, Any]]:
    names = [n for n in names if n in _SECTION_TITLES] or ["important", "unread", "everything"]
    if names[-1] != "everything" and "everything" not in names:
        names = [*names, "everything"]
    out = []
    for i, n in enumerate(names):
        main = i == len(names) - 1
        out.append(_sec(n, _SECTION_TITLES[n], filter=None if n == "everything" else n,
                        limit=None if main else preview))
    return out


def presets(settings: dict[str, Any]) -> list[dict[str, Any]]:
    cats = [c for c in (settings.get("categories") or ["primary"]) if c in _CAT]
    if "primary" not in cats:
        cats.insert(0, "primary")
    default_tabs = [
        {"id": c, "name": _CAT[c][0], "icon": _CAT[c][1], "badge": "none" if c == "primary" else "unread",
         "sections": [_sec(c, _CAT[c][0], category=c)]}
        for c in cats
    ]

    def one(id: str, name: str, sections: list[dict[str, Any]]) -> dict[str, Any]:
        return {"id": id, "name": name, "preset": True,
                "tabs": [{"id": "inbox", "name": "Inbox", "icon": "inbox", "badge": "none", "sections": sections}]}

    multi = [
        _sec(f"m{i}", m.get("name") or f"Section {i + 1}", "all", q=m.get("q") or "", limit=10, exclude_above=False)
        for i, m in enumerate(settings.get("multiple_inboxes") or []) if (m.get("q") or "").strip()
    ]
    return [
        {"id": "default", "name": "Categories", "preset": True, "tabs": default_tabs},
        one("important_first", "Important first", _priority_sections(["important", "everything"])),
        one("unread_first", "Unread first", _priority_sections(["unread", "everything"])),
        one("starred_first", "Starred first", _priority_sections(["starred", "everything"])),
        one("priority", "Custom sections", _priority_sections(list(settings.get("priority_sections") or []))),
        one("multiple", "Multiple inboxes", [*multi, _sec("inbox", "Inbox", exclude_above=False)]),
        # One list, newest first, no tabs (the setup guide's "Simple").
        one("simple", "Simple", [_sec("inbox", "Inbox", exclude_above=False)]),
    ]


def badge_of(v: Any) -> str:
    """A tab's count choice, with the old boolean mapped (true: unread, false: none). A missing
    or unknown value counts unread, as it always has."""
    if isinstance(v, bool):
        return "unread" if v else "none"
    return v if v in BADGES else "unread"


def indicators_of(tab: dict[str, Any]) -> list[str]:
    """What a tab shows by its label, in INDICATORS order. A tab saved before indicators existed
    has only `badge`."""
    v = tab.get("indicators")
    if isinstance(v, list):
        return [x for x in INDICATORS if x in v]
    b = badge_of(tab.get("badge"))
    return [] if b == "none" else [b]


def badge_for(indicators: list[str]) -> str:
    """The single `badge` older clients read: unread first, then all."""
    return next((x for x in ("unread", "all") if x in indicators), "none")


def _norm_tab(t: dict[str, Any]) -> dict[str, Any]:
    inds = indicators_of(t)
    return dict(t, indicators=inds, badge=badge_for(inds))


def all_layouts(settings: dict[str, Any]) -> list[dict[str, Any]]:
    lays = presets(settings) + [dict(x, preset=False) for x in (settings.get("inbox_layouts") or [])]
    return [dict(x, tabs=[_norm_tab(t) for t in x.get("tabs") or []]) for x in lays]


def active_id(settings: dict[str, Any]) -> str:
    t = settings.get("inbox_type") or "default"
    return t.split(":", 1)[1] if t.startswith("layout:") else t


def find(settings: dict[str, Any], layout_id: str) -> dict[str, Any] | None:
    return next((x for x in all_layouts(settings) if x["id"] == layout_id), None)


def find_section(settings: dict[str, Any], layout_id: str, tab_id: str, sec_id: str
                 ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """The section and the sections above it in its tab."""
    lay = find(settings, layout_id)
    tab = next((t for t in (lay or {}).get("tabs", []) if t["id"] == tab_id), None)
    if tab is None:
        return None
    secs = tab["sections"]
    idx = next((i for i, s in enumerate(secs) if s["id"] == sec_id), None)
    if idx is None:
        return None
    return secs[idx], secs[:idx]


def claimed_elsewhere(settings: dict[str, Any], layout_id: str, tab_id: str) -> list[dict[str, Any]]:
    """What other tabs with `claims` list (their sections without the state filter): the
    conversations this tab leaves out. A tab without `claims` leaves out every claiming tab's
    mail. A claiming tab leaves out only what claiming tabs to its left claim, so mail two of them
    match belongs to the leftmost one; otherwise each would hide it from the other and it would
    show in no tab while still counting as unread."""
    tabs = (find(settings, layout_id) or {}).get("tabs", [])
    me = next((i for i, t in enumerate(tabs) if t["id"] == tab_id), None)
    claims_own = me is not None and bool(tabs[me].get("claims"))
    return [{**s, "filter": None} for i, t in enumerate(tabs) if t.get("claims") and t["id"] != tab_id
            and not (claims_own and i > me) for s in t["sections"]]


def view_all_query(sec: dict[str, Any], label_names: dict[int, str]) -> str:
    """The search that lists the whole section (what "View all" opens)."""
    parts: list[str] = []
    base = sec.get("base") or "inbox"
    if base.startswith("label:"):
        name = label_names.get(int(base.split(":", 1)[1]), "")
        if name:
            parts.append(f'label:"{name}"' if " " in name else f"label:{name}")
    elif base in ("inbox", "starred", "important", "snoozed", "sent", "drafts"):
        parts.append({"drafts": "is:draft"}.get(base, f"in:{base}"))
    if inbox_only(sec):
        parts.append("in:inbox")
    if sec.get("category"):
        parts.append(f"category:{sec['category']}")
    f = sec.get("filter")
    parts += {"important": ["is:important"], "important_unread": ["is:important", "is:unread"],
              "unread": ["is:unread"], "starred": ["is:starred"]}.get(f or "", [])
    if (sec.get("q") or "").strip():
        parts.append(f"({sec['q'].strip()})" if " OR " in sec["q"] or "|" in sec["q"] else sec["q"].strip())
    return " ".join(parts)


class LayoutError(ValueError):
    pass


def _clean_id(v: Any, used: set[str], fallback: str) -> str:
    s = str(v or "").strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s).strip("-")[:40] or fallback
    if not _ID.match(s):
        s = fallback
    base, n = s, 2
    while s in used:
        s, n = f"{base[:36]}-{n}", n + 1
    used.add(s)
    return s


def validate(layouts: Any, label_ids: set[int]) -> list[dict[str, Any]]:
    """Normalise user-edited layouts; raise LayoutError with a readable message."""
    if not isinstance(layouts, list):
        raise LayoutError("inbox_layouts must be a list")
    if len(layouts) > MAX_LAYOUTS:
        raise LayoutError(f"at most {MAX_LAYOUTS} custom layouts")
    out: list[dict[str, Any]] = []
    used_layouts: set[str] = set(PRESET_IDS)
    for li, lay in enumerate(layouts):
        if not isinstance(lay, dict):
            raise LayoutError("each layout must be an object")
        name = str(lay.get("name") or "").strip()[:60] or f"Layout {li + 1}"
        tabs_in = lay.get("tabs") or []
        if not isinstance(tabs_in, list) or not tabs_in:
            raise LayoutError(f"“{name}” needs at least one tab")
        if len(tabs_in) > MAX_TABS:
            raise LayoutError(f"“{name}” has more than {MAX_TABS} tabs")
        used_tabs: set[str] = set()
        tabs = []
        for ti, tab in enumerate(tabs_in):
            tname = str(tab.get("name") or "").strip()[:40] or f"Tab {ti + 1}"
            secs_in = tab.get("sections") or []
            if not isinstance(secs_in, list) or not secs_in:
                raise LayoutError(f"Tab “{tname}” needs at least one section")
            if len(secs_in) > MAX_SECTIONS:
                raise LayoutError(f"Tab “{tname}” has more than {MAX_SECTIONS} sections")
            used_secs: set[str] = set()
            secs = []
            for si, sec in enumerate(secs_in):
                base = str(sec.get("base") or "inbox")
                if base.startswith("label:"):
                    try:
                        lid = int(base.split(":", 1)[1])
                    except ValueError:
                        raise LayoutError(f"Section {si + 1} in “{tname}”: unknown label") from None
                    if lid not in label_ids:
                        raise LayoutError(f"Section {si + 1} in “{tname}”: that label no longer exists")
                elif base not in BASES:
                    raise LayoutError(f"Section {si + 1} in “{tname}”: unknown list {base!r}")
                cat = sec.get("category") or None
                if cat is not None and cat not in _CAT:
                    raise LayoutError(f"Section {si + 1} in “{tname}”: unknown category {cat!r}")
                flt = sec.get("filter") or None
                if flt is not None and flt not in FILTERS:
                    raise LayoutError(f"Section {si + 1} in “{tname}”: unknown filter {flt!r}")
                limit = sec.get("limit")
                if limit is not None:
                    try:
                        limit = max(1, min(50, int(limit)))
                    except (TypeError, ValueError):
                        raise LayoutError(f"Section {si + 1} in “{tname}”: limit must be a number") from None
                secs.append({
                    "id": _clean_id(sec.get("id") or sec.get("title"), used_secs, f"s{si + 1}"),
                    "title": str(sec.get("title") or "").strip()[:60] or f"Section {si + 1}",
                    "base": base, "category": cat, "filter": flt,
                    "q": str(sec.get("q") or "").strip()[:500],
                    "limit": limit, "exclude_above": bool(sec.get("exclude_above", True)),
                    "inbox_only": base != "inbox" and bool(sec.get("inbox_only")),
                })
            icon = tab.get("icon") if tab.get("icon") in ICONS else None
            inds = indicators_of(tab)
            tabs.append({"id": _clean_id(tab.get("id") or tname, used_tabs, f"t{ti + 1}"), "name": tname,
                         "icon": icon, "indicators": inds, "badge": badge_for(inds),
                         "claims": bool(tab.get("claims")), "sections": secs})
        out.append({"id": _clean_id(lay.get("id") or name, used_layouts, f"layout-{li + 1}"), "name": name,
                    "tab_detail": bool(lay.get("tab_detail")), "hide_empty": bool(lay.get("hide_empty")),
                    "tabs": tabs})
    return out
