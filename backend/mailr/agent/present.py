"""The presentation layer for everything the assistant shows the user.

One module owns the words: approval cards (a typed display model, schema v1 in
spec/fixtures/agent-blocks.schema.json), their outcomes, the labels and icons of tool steps, and the
Markdown normaliser. Renderers on the web and on Android draw this model; they never build wording
from raw payloads. Golden fixtures (spec/fixtures/agent-blocks-fixtures.json) are produced from this
module and rendered by both clients' tests.

A display:

    {"v": 1, "icon": "filter", "title": "Create a filter", "summary": "…", "danger": false,
     "reason": null, "fields": [{"label": "Matches", "value": {"t": "query", "text": "…", …}}],
     "text": "plain text, one line per field"}

Every value carries `text`, the plain wording; a renderer that does not know a value type shows it.

The words are catalogue keys (mailr/i18n, `approvals.*`), so each app shows them in its own
language: beside the English `title`, `summary`, `reason`, a field's `label`, a text value's `text`
and an outcome's `text` sits `title_msg`, `summary_msg`, `reason_msg`, `label_msg`, `msg`
(`{"key", "params"}`; a parameter may itself be a msg). A list or joined text value may carry
`msgs`, one per item. The web and Android apps keep a copy of the `approvals` section
(tools/i18n_server_texts.py) and render a msg with their own t(); without one, or for a key they
lack, they show the English text. Titles and lines a caller writes into its preview stay English
unless the caller gives `title_msg`.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n

M = i18n.msg


def en(x: Any) -> str:
    """The English of a msg, or the text itself."""
    return i18n.render(x) if i18n.is_msg(x) else ("" if x is None else str(x))

VERSION = 1
MAX_VALUES_SHOWN = 3   # "From any of: a, b, c, … (6)"
MAX_LIST = 6


# ---------------------------------------------------------------- values

def v_text(text: Any) -> dict[str, Any]:
    """A plain value; a msg (or a list of msgs, shown joined) is kept beside its English."""
    if isinstance(text, list) and text and all(i18n.is_msg(x) for x in text):
        return {"t": "text", "text": ", ".join(en(x) for x in text), "msgs": text}
    if i18n.is_msg(text):
        return {"t": "text", "text": en(text), "msg": text}
    return {"t": "text", "text": str(text)}


def v_labels(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"t": "labels", "text": ", ".join(i["name"] for i in items), "items": items}


def v_people(items: list[dict[str, Any]], limit: int = 1000) -> dict[str, Any]:
    # Every recipient shows on an approval card: a guest or Bcc must never hide as "and 3 more".
    limit = max(limit, 1)
    shown = items[:limit]
    words = [(f"{p['name']} <{p['email']}>" if p.get("name") else p["email"]) for p in shown]
    more = max(0, len(items) - limit)
    return {"t": "people", "text": ", ".join(words) + (f" and {more} more" if more else ""),
            "items": shown, "more": more}


def v_list(items: list[Any], limit: int = MAX_LIST) -> dict[str, Any]:
    """A list; items that are msgs are shown in English in `items`, and kept in `msgs`."""
    shown = [en(x) for x in items[:limit]]
    more = max(0, len(items) - limit)
    out = {"t": "list", "text": "; ".join(shown) + (f"; and {more} more" if more else ""), "items": shown,
           "more": more}
    if any(i18n.is_msg(x) for x in items[:limit]):
        out["msgs"] = [x if i18n.is_msg(x) else None for x in items[:limit]]
    return out


def v_diff(before: Any, after: Any) -> dict[str, Any]:
    """Before and after; an `after` that is a msg ("No place") is kept as `after_msg`."""
    b, a = _plain(before), (en(after) if i18n.is_msg(after) else _plain(after))
    out = {"t": "diff", "text": f"{b} → {a}" if b else a, "before": b, "after": a}
    if i18n.is_msg(after):
        out["after_msg"] = after
    return out


# A quote carries the whole text (clients fold a long one behind "Show more"): an approval card
# must never hide the end of what runs. `limit` is kept for
# callers and ignored; QUOTE_MAX only stops a runaway value.
QUOTE_MAX = 60_000


def v_quote(text: str, limit: int = 1200) -> dict[str, Any]:
    t = str(text or "").strip()
    if len(t) > QUOTE_MAX:
        t = t[:QUOTE_MAX] + f"… ({len(t) - QUOTE_MAX} more characters not shown)"
    return {"t": "quote", "text": t}


def v_when(text: str, start: str | None, end: str | None = None, all_day: bool = False) -> dict[str, Any]:
    return {"t": "when", "text": text, "start": start, "end": end, "all_day": all_day}


def field(label: Any, value: dict[str, Any]) -> dict[str, Any]:
    """A row; `label` is a msg (approvals.field.*) or "" for a row without one."""
    if i18n.is_msg(label):
        return {"label": en(label), "label_msg": label, "value": value}
    return {"label": str(label or ""), "value": value}


def F(name: str) -> dict[str, Any]:
    """The msg of a field label: approvals.field.<name>."""
    return M(f"approvals.field.{name}")


def plural(noun: str, n: int) -> str:
    if n == 1:
        return noun
    if noun.endswith(("s", "x", "ch", "sh")):
        return noun + "es"
    if noun.endswith("y") and noun[-2:-1] not in "aeiou":
        return noun[:-1] + "ies"
    return noun + "s"


def _plain(v: Any) -> str:
    if v is None:
        return ""
    if v is True:
        return "on"
    if v is False:
        return "off"
    if isinstance(v, list):
        return ", ".join(_plain(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {_plain(x)}" for k, x in v.items())
    return str(v)


def display(icon: str, title: Any, fields: list[dict[str, Any]], *, summary: Any = None,
            danger: bool = False, reason: Any = None) -> dict[str, Any]:
    """The display; `title`, `summary` and `reason` are msgs or (from a caller's preview) English."""
    fields = [f for f in fields if f and f["value"].get("text") not in (None, "")]
    title_s, summary_s = en(title), (en(summary) if summary else None)
    text = "\n".join([title_s, *([summary_s] if summary_s else []),
                      *[(f"{f['label']}: " if f["label"] else "") + f["value"]["text"] for f in fields]])
    out = {"v": VERSION, "icon": icon, "title": title_s, "summary": summary_s, "danger": bool(danger),
           "reason": en(reason) if reason else None, "fields": fields, "text": text}
    for k, v in (("title_msg", title), ("summary_msg", summary), ("reason_msg", reason)):
        if i18n.is_msg(v):
            out[k] = v
    return out


def lines_of(d: dict[str, Any]) -> list[str]:
    """The display as the old `lines` (older clients, notification bodies)."""
    out = [d["summary"]] if d.get("summary") else []
    for f in d["fields"]:
        out.append((f"{f['label']}: " if f["label"] else "") + f["value"]["text"])
    return out


# ---------------------------------------------------------------- labels and people

class Labels:
    """The user's labels by id and by name, for label references in payloads and queries."""

    def __init__(self, rows: list[Any]):
        self.by_id = {r.id: r for r in rows}
        self.by_name = {r.name.lower(): r for r in rows}

    @classmethod
    async def load(cls, db: AsyncSession, user_id: int) -> Labels:
        from mailr.models import Label

        return cls(list((await db.execute(select(Label).where(Label.user_id == user_id))).scalars()))

    def ref(self, key: Any) -> dict[str, Any]:
        lb = None
        try:
            lb = self.by_id.get(int(key))
        except (TypeError, ValueError):
            pass
        if lb is None and isinstance(key, str):
            k = key.lower().replace("-", " ")
            lb = self.by_name.get(key.lower()) or next(
                (x for n, x in self.by_name.items() if n.replace("-", " ") == k), None)
        if lb is None:
            return {"name": str(key), "color": None}
        return {"name": ROLE_NAMES.get(lb.role or "", lb.name) if lb.role else lb.name, "color": lb.color}

    def refs(self, keys: Any) -> list[dict[str, Any]]:
        return [self.ref(k) for k in (keys if isinstance(keys, list) else [keys]) if k not in (None, "")]


ROLE_NAMES = {"inbox": "Inbox", "sent": "Sent", "drafts": "Drafts", "junk": "Spam", "trash": "Trash",
              "archive": "Archive"}


def _people(v: Any) -> list[dict[str, Any]]:
    out = []
    for a in v or []:
        if isinstance(a, dict) and a.get("email"):
            out.append({"name": a.get("name") or None, "email": str(a["email"])})
        elif isinstance(a, str) and a:
            m = re.match(r"\s*(.*?)\s*<([^>]+)>\s*$", a)
            out.append({"name": m.group(1) or None, "email": m.group(2)} if m else {"name": None, "email": a})
    return out


# ---------------------------------------------------------------- search queries

FIELD_WORDS = {"from": "From", "to": "To", "cc": "Cc", "bcc": "Bcc", "deliveredto": "Delivered to",
               "list": "From the mailing list"}
IS_WORDS = {"unread": "Is unread", "read": "Is read", "starred": "Is starred", "important": "Is important",
            "snoozed": "Is snoozed", "muted": "Is muted", "needs_reply": "Needs a reply",
            "waiting": "Waiting on a reply", "draft": "Is a draft", "sent": "Was sent by you"}
HAS_WORDS = {"attachment": "Has an attachment", "deadline": "Has a deadline", "recording": "Has a recording",
             "userlabels": "Has one of your labels", "nouserlabels": "Has none of your labels",
             "link": "Has a link", "pdf": "Has a PDF"}
IN_WORDS = {"inbox": "In the inbox", "sent": "In Sent", "drafts": "In Drafts", "spam": "In Spam", "junk": "In Spam",
            "trash": "In Trash", "archive": "Archived", "anywhere": "Anywhere, including Spam and Trash",
            "all": "In all mail", "snoozed": "Snoozed", "starred": "Starred", "important": "Important",
            "scheduled": "Scheduled to send"}
UNITS = {"d": "day", "m": "month", "y": "year"}


def _tree(toks: list[str], i: int, closer: str | None) -> tuple[Any, int]:
    """The query as a tree, with search.py's precedence: AND of OR-groups, `{a b}` is OR."""
    terms: list[Any] = []
    pending_or = False
    while i < len(toks):
        t = toks[i]
        if t == closer:
            return _and(terms), i + 1
        if t in ("(", "{") or (t == "-" and i + 1 < len(toks) and toks[i + 1] in ("(", "{")):
            neg = t == "-"
            if neg:
                i += 1
                t = toks[i]
            node, i = _tree(toks, i + 1, ")" if t == "(" else "}")
            if neg:
                node = ("not", node)
        elif t.upper() in ("OR", "|"):
            pending_or = True
            i += 1
            continue
        elif t.upper() == "AND" or re.fullmatch(r"AROUND\d*", t.upper()) or t == "-":
            i += 1
            continue
        else:
            node = ("term", t)
            i += 1
        if closer == "}":
            pending_or = True if terms else pending_or
        if pending_or and terms:
            prev = terms[-1]
            terms[-1] = ("or", [*(prev[1] if prev[0] == "or" else [prev]), node])
            pending_or = False
        else:
            terms.append(node)
    return _and(terms), i


def _and(terms: list[Any]) -> Any:
    return terms[0] if len(terms) == 1 else ("and", terms)


def _split_term(tok: str) -> tuple[bool, str | None, str]:
    neg = tok.startswith("-") and len(tok) > 1
    t = tok[1:] if neg else tok
    m = re.fullmatch(r"([A-Za-z_]+):(.+)", t)
    if m and m.group(1).lower() in _OPS:
        v = m.group(2)
        v = v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v
        return neg, m.group(1).lower(), v
    return neg, None, t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t


_OPS = {"from", "to", "cc", "bcc", "subject", "label", "in", "is", "has", "filename", "after", "before",
        "older_than", "newer_than", "larger", "smaller", "category", "list", "deliveredto", "rfc822msgid",
        "recording", "due"}


def _fmt_date(v: str) -> str:
    try:
        d = date.fromisoformat("-".join(x.zfill(2) for x in v.replace("/", "-").split("-")))
        return f"{d.day} {d:%b %Y}"
    except ValueError:
        return v


def _rel(v: str) -> str:
    m = re.fullmatch(r"(\d+)([dmy])", v.lower())
    return f"{m.group(1)} {plural(UNITS[m.group(2)], int(m.group(1)))}" if m else v


def _values_text(vals: list[str], quote: bool = False) -> str:
    shown = [f"“{v}”" if quote else v for v in vals[:MAX_VALUES_SHOWN]]
    if len(vals) > MAX_VALUES_SHOWN:
        return ", ".join(shown) + f", … ({len(vals)})"
    return ", ".join(shown)


def _cond(op: str | None, vals: list[str], labels: Labels | None, neg: bool = False) -> dict[str, Any]:
    """One readable condition for a field with one or more values (any of)."""
    many = len(vals) > 1
    c: dict[str, Any] = {"field": op or "text", "neg": neg, "values": vals}
    if op is None:
        txt = ("Contains any of: " if many else "Contains ") + _values_text(vals, quote=True)
    elif op in FIELD_WORDS:
        shown = ["me" if v.lower() == "me" else v for v in vals]
        txt = FIELD_WORDS[op] + (" any of: " if many else " ") + _values_text(shown)
    elif op == "subject":
        txt = "Subject contains " + ("any of: " if many else "") + _values_text(vals, quote=True)
    elif op == "label":
        refs = [labels.ref(v) for v in vals] if labels else [{"name": v, "color": None} for v in vals]
        c["labels"] = refs
        txt = ("Has any of the labels: " if many else "Has the label ") + _values_text([r["name"] for r in refs])
    elif op == "in":
        txt = " or ".join(IN_WORDS.get(v.lower(), f"In {v}") for v in vals)
    elif op == "is":
        txt = " or ".join(IS_WORDS.get(v.lower(), f"Is {v.replace('_', ' ')}") for v in vals)
    elif op == "has":
        txt = " or ".join(HAS_WORDS.get(v.lower(), f"Has {v.replace('_', ' ')}") for v in vals)
    elif op == "filename":
        txt = "Has an attachment named " + _values_text(vals, quote=True)
    elif op in ("after", "before"):
        txt = f"{op.capitalize()} " + " or ".join(_fmt_date(v) for v in vals)
    elif op in ("older_than", "newer_than"):
        txt = ("Older than " if op == "older_than" else "Newer than ") + " or ".join(_rel(v) for v in vals)
    elif op in ("larger", "smaller"):
        txt = ("Larger than " if op == "larger" else "Smaller than ") + " or ".join(
            re.sub(r"(?i)(\d+)\s*([km])b?$", lambda m: f"{m.group(1)} {m.group(2).upper()}B", v) for v in vals)
    elif op == "category":
        txt = "In the " + " or ".join(v.capitalize() for v in vals) + (" categories" if many else " category")
    elif op == "recording":
        txt = "Linked to a recording about " + _values_text(vals, quote=True)
    elif op == "due":
        txt = "Due " + " or ".join(vals)
    else:
        txt = f"{op}: " + _values_text(vals)
    if neg:
        txt = _negate(txt)
    c["text"] = txt
    return c


def _negate(txt: str) -> str:
    """"Has the label X" → "Does not have the label X", "From x" → "Not from x"."""
    for a, b in (("Has ", "Does not have "), ("Is ", "Is not "), ("Contains ", "Does not contain "),
                 ("Subject contains ", "Subject does not contain "), ("In ", "Not in "),
                 ("Needs a reply", "Does not need a reply")):
        if txt.startswith(a):
            return b + txt[len(a):]
    return "Not " + txt[0].lower() + txt[1:]


def _describe(node: Any, labels: Labels | None) -> dict[str, Any]:
    kind = node[0]
    if kind == "term":
        neg, op, v = _split_term(node[1])
        if op in FIELD_WORDS and "," in v:  # from:a@x.example,b@y.com
            return _cond(op, [x for x in v.split(",") if x], labels, neg)
        return _cond(op, [v], labels, neg)
    if kind == "or":
        parts = [_split_term(x[1]) if x[0] == "term" else None for x in node[1]]
        if all(p is not None and not p[0] for p in parts) and len({p[1] for p in parts}) == 1:
            return _cond(parts[0][1], [p[2] for p in parts], labels)
        subs = [_describe(x, labels) for x in node[1]]
        return {"field": "any", "neg": False, "values": [s["text"] for s in subs],
                "text": "Any of: " + "; ".join(s["text"] for s in subs)}
    if kind == "not":
        inner = _describe(node[1], labels)
        return {**inner, "neg": True, "text": _negate(inner["text"])}
    subs = [_describe(x, labels) for x in node[1]]
    return {"field": "all", "neg": False, "values": [s["text"] for s in subs],
            "text": "All of: " + "; ".join(s["text"] for s in subs)}


def describe_query(q: str, labels: Labels | None = None) -> dict[str, Any]:
    """A search query as readable conditions: "from:a OR from:b" → "From any of: a, b"."""
    from mailr.core.search import _tokens

    raw = " ".join(str(q or "").split())
    try:
        toks = _tokens(raw)
        tree, _ = _tree(toks, 0, None)
    except Exception:  # noqa: BLE001 - a query we cannot read is shown as written
        return {"t": "query", "text": raw, "raw": raw, "conditions": []}
    nodes = tree[1] if tree and tree[0] == "and" else ([tree] if tree else [])
    conds: list[dict[str, Any]] = []
    words: list[str] = []
    for n in nodes:
        if n[0] == "term":
            neg, op, v = _split_term(n[1])
            if op is None and not neg:
                words.append(v)
                continue
        conds.append(_describe(n, labels))
    if words:
        conds.insert(0, {"field": "text", "neg": False, "values": words,
                         "text": ("Contains " if len(words) == 1 else "Contains all of: ") + _values_text(words, True)})
    return {"t": "query", "text": "; ".join(c["text"] for c in conds) or raw, "raw": raw, "conditions": conds}


# ---------------------------------------------------------------- dates

def _parse_when(v: Any) -> datetime | date | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return date.fromisoformat(v)
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _day(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def when_value(start: Any, end: Any = None, tz: ZoneInfo | None = None) -> dict[str, Any] | None:
    """"Tue 30 Sep, 14:00–15:00", "Tue 30 Sep (all day)", "Tue 30 Sep – Thu 2 Oct"."""
    s, e = _parse_when(start), _parse_when(end)
    if s is None:
        return v_text(start) if start else None
    if isinstance(s, datetime):
        if tz is not None and s.tzinfo is not None:
            s = s.astimezone(tz)
            e = e.astimezone(tz) if isinstance(e, datetime) and e.tzinfo is not None else e
        txt = f"{_day(s.date())}, {s:%H:%M}"
        if isinstance(e, datetime):
            txt += f"–{e:%H:%M}" if e.date() == s.date() else f" – {_day(e.date())}, {e:%H:%M}"
        return v_when(txt, str(start), str(end) if end else None)
    txt = _day(s)
    if isinstance(e, date) and not isinstance(e, datetime) and (e - s).days > 1:
        from datetime import timedelta

        txt += f" – {_day(e - timedelta(days=1))}"
    return v_when(txt + " (all day)", str(start), str(end) if end else None, all_day=True)


# ---------------------------------------------------------------- builders

# Mail actions with a phrase of their own: approvals.mail.<action>.ask ("Archive {{what}}"), .done
# ("archived {{what}}") and .ing ("Archiving {{what}}"); any other action reads approvals.mail.other.
MAIL_VERB = frozenset({
    "archive", "inbox", "trash", "untrash", "spam", "not_spam", "delete_forever", "read", "unread", "star",
    "unstar", "important", "not_important", "add_labels", "remove_labels", "move", "snooze", "unsnooze", "mute",
    "unmute", "category", "show_in_tab", "follow_up", "cancel_follow_up",
})


def mail_verb(action: str, n: int, noun: str = "message", done: bool = False, ing: bool = False) -> dict[str, Any]:
    """"Move 6 messages to Trash", done: "moved 6 messages to Trash", ing: "Moving 6 messages to Trash"
    (as a msg)."""
    what = M(f"approvals.what.{plural(noun, 2)}", count=n) if n else \
        M("approvals.what.the_conversations" if noun == "conversation" else "approvals.what.the_messages")
    form = "done" if done else "ing" if ing else "ask"
    if action in MAIL_VERB:
        return M(f"approvals.mail.{action}.{form}", what=what)
    return M(f"approvals.mail.other.{form}", what=what, action=action.replace("_", " "))


MAIL_ICON = {"trash": "trash", "spam": "spam", "delete_forever": "delete", "archive": "archive",
             "add_labels": "label", "remove_labels": "label", "move": "label", "snooze": "snooze"}

# What a filter or an option does besides labels: approvals.flag.<name> ("Skip the inbox") and, inside
# a summary sentence, approvals.flag_inline.<name> ("skip the inbox").
FILTER_FLAGS = ("archive", "mark_read", "star", "spam", "never_spam", "trash", "notify", "add_to_calendar")


def _flag_words(acts: dict[str, Any], inline: bool = False) -> list[dict[str, Any]]:
    sec = "approvals.flag_inline" if inline else "approvals.flag"
    out = [M(f"{sec}.{k}") for k in FILTER_FLAGS if acts.get(k) is True]
    if acts.get("important") is True:
        out.append(M(f"{sec}.important_always"))
    elif acts.get("important") is False:
        out.append(M(f"{sec}.important_never"))
    if acts.get("category"):
        out.append(M(f"{sec}.category", category=str(acts["category"]).capitalize()))
    if acts.get("forward_to"):
        out.append(M(f"{sec}.forward", to=str(acts["forward_to"])))
    return out


def _action_fields(acts: dict[str, Any], labels: Labels) -> list[dict[str, Any]]:
    out = []
    if acts.get("add_labels"):
        out.append(field(F("add_label" if len(acts["add_labels"]) == 1 else "add_labels"),
                         v_labels(labels.refs(acts["add_labels"]))))
    if acts.get("remove_labels"):
        out.append(field(F("remove_labels"), v_labels(labels.refs(acts["remove_labels"]))))
    flags = _flag_words(acts)
    if flags:
        out.append(field(F("also" if out else "then"), v_list(flags) if len(flags) > 1 else v_text(flags[0])))
    return out


def _filter_summary(q: dict[str, Any], acts: dict[str, Any], labels: Labels) -> dict[str, Any] | None:
    conds = q.get("conditions") or []
    if len(conds) == 1 and conds[0]["field"] == "from" and not conds[0]["neg"]:
        n = len(conds[0]["values"])
        who = M("approvals.filter.who.sender", sender=conds[0]["values"][0]) if n == 1 else \
            M("approvals.filter.who.senders", count=n)
    else:
        who = M("approvals.filter.who.matching")
    names = [r["name"] for r in labels.refs(acts.get("add_labels") or [])]
    if names:
        return M("approvals.filter.summary.labels", who=who, names=", ".join(names), count=len(names))
    flags = _flag_words(acts, inline=True)
    return M("approvals.filter.summary.flag", who=who, flag=flags[0]) if flags else None


def _match_field(preview: dict[str, Any]) -> dict[str, Any] | None:
    """The match check on a filter or standing order card (agent/config_tools.py match_check): how many
    emails of the last 6 months the search matches, with the newest; a warning when none."""
    m = preview.get("match")
    if not isinstance(m, dict) or not isinstance(m.get("count"), int):
        return None
    if not m["count"]:
        return field(F("matches_now"), v_text(M("approvals.match.none")))
    when = ""
    if m.get("newest_date"):
        try:
            d = date.fromisoformat(str(m["newest_date"]))
            when = f"{d.day} {d:%b}" if d.year == date.today().year else f"{d.day} {d:%b %Y}"
        except ValueError:
            when = str(m["newest_date"])
    subject = _q(m.get("newest_subject") or "(no subject)", 80)
    key = "approvals.match.many" if m.get("capped") else "approvals.match.some"
    return field(F("matches_now"), v_text(M(key, count=m["count"], subject=subject, date=when)))


async def _filter(db, uid, payload, preview, labels) -> dict[str, Any]:
    op = payload.get("op") or "create"
    rule = payload.get("rule") or {}
    if not rule and payload.get("rule_id") and db is not None:
        from mailr.models import Rule

        r = await db.get(Rule, int(payload["rule_id"]))
        if r is not None and r.user_id == uid:
            rule = {"name": r.name, "query": r.query, "actions": r.actions or {}}
    title = M(f"approvals.filter.title.{op}") if op in ("create", "update", "delete", "apply") else \
        (preview.get("title") or M("approvals.filter.title.update"))
    if not rule:
        return _generic(preview, icon="filter")
    q = describe_query(rule.get("query") or "", labels)
    acts = rule.get("actions") or {}
    fields = [field(F("name"), v_text(rule["name"])) if rule.get("name") else None,
              field(F("matches"), q), *(_action_fields(acts, labels) if op != "apply" else [])]
    fields.append(_match_field(preview))
    if rule.get("apply_existing"):
        fields.append(field(F("existing_mail"), v_text(M("approvals.value.apply_existing"))))
    return display("filter", title, [f for f in fields if f],
                   summary=_filter_summary(q, acts, labels) if op in ("create", "update") else None,
                   danger=op == "delete")


async def _order(db, uid, payload, preview, labels) -> dict[str, Any]:
    op = payload.get("op") or "create"
    o = payload.get("order") or {}
    name = o.get("name") or ""
    vop = op if op in ("create", "update", "pause", "resume", "delete") else "update"
    verb = M(f"approvals.order.title.{vop}")
    if not o:
        return _generic(preview, icon="order", title=preview.get("title") or verb)
    trig = o.get("trigger") or "message"

    def when_of(inline: bool) -> dict[str, Any]:
        sec = "approvals.order.when_inline" if inline else "approvals.order.when"
        if trig in ("message", "recording"):
            return M(f"{sec}.{trig}")
        if o.get("timezone"):
            return M(f"{sec}.schedule", schedule=str(o.get("schedule")), zone=str(o["timezone"]))
        return M(f"{sec}.schedule_local", schedule=str(o.get("schedule")))

    fields = [field(F("when"), v_text(when_of(False)))]
    if o.get("recordings"):
        fields.append(field(F("recordings"), v_text(_plain(o["recordings"]))))
    if o.get("from_trusted"):
        fields.append(field(F("mail"), v_text(M("approvals.order.from_trusted"))))
    if o.get("from_me"):
        fields.append(field(F("mail"), v_text(M("approvals.order.from_me"))))
    if o.get("query"):
        fields.append(field(F("mail"), describe_query(o["query"], labels)))
        if (mf := _match_field(preview)) is not None:
            fields.append(mf)
    choices = o.get("choices") or []
    if o.get("question"):
        fields.append(field(F("pick_single" if choices else "only_if"), v_text(o["question"])))
    if choices:
        items = []
        for c in choices:
            then = _action_fields(c.get("actions") or {}, labels)
            items.append({"name": c.get("name") or "", "description": c.get("description") or "",
                          "then": then,
                          "text": f"{c.get('name')}: " + ("; ".join(f"{f['label']} {f['value']['text']}" for f in then)
                                                          or "nothing")})
        fields.append(field(F("options"), {"t": "choices", "text": "; ".join(i["text"] for i in items),
                                        "items": items}))
    fields += _action_fields(o.get("actions") or {}, labels)
    if o.get("errand"):
        fields.append(field(F("errand"), v_quote(o["errand"], 600)))
        if o.get("deliver"):
            known = {"brief", "notify", "digest"}
            fields.append(field(F("result_goes_to"), v_text(
                [M(f"approvals.order.deliver.{d}") for d in o["deliver"]] if set(o["deliver"]) <= known
                else ", ".join(str(d) for d in o["deliver"]))))
    summary = None
    if op == "create" and choices:
        summary = M("approvals.order.summary", when=when_of(True), count=len(choices))
    title = M(f"approvals.order.title_named.{vop}", name=name) if name else verb
    return display("order", title, [f for f in fields if f],
                   summary=summary, danger=op == "delete")


async def _mail_action(db, uid, payload, preview, labels, tz) -> dict[str, Any]:
    action = payload.get("action") or ""
    args = payload.get("args") or {}
    ids = payload.get("message_ids") or preview.get("message_ids") or []
    n = len(ids)
    title = mail_verb(action, n)
    fields = []
    lab_ids = args.get("label_ids") or ([args["label_id"]] if args.get("label_id") else [])
    if lab_ids:
        fields.append(field(F("labels"), v_labels(labels.refs(lab_ids))))
    if args.get("until"):
        fields.append(field(F("until"), when_value(args["until"], tz=tz) or v_text(args["until"])))
    items = [x for x in (preview.get("lines") or []) if not str(x).startswith(("Labels: ", "Until ", "…and "))]
    if items:
        rows = [re.sub(r"^(.*?): ", r"\1 — ", str(x), count=1) for x in items]
        fields.append(field(F("messages"), {**v_list(rows), "more": max(0, n - len(rows))} if n > len(rows)
                            else v_list(rows)))
        if n > len(rows):
            fields[-1]["value"]["text"] += f"; and {n - len(rows)} more"
    icon = MAIL_ICON.get(action, "mail")
    return display(icon, title, fields, danger=action in ("delete_forever",) or bool(preview.get("danger")))


def who_text(people: list[dict[str, Any]], limit: int = 2) -> Any:
    """The people an email goes to, as a title says them: "Marco Silva", "Marco Silva and Dana",
    "Marco Silva and 2 others" (a name, or a msg)."""
    names = [str(p.get("name") or p.get("email") or "") for p in people if p.get("name") or p.get("email")]
    if not names:
        return M("approvals.who.nobody")
    if len(names) <= limit:
        return M("approvals.who.two", first=names[0], second=names[1]) if len(names) == 2 else names[0]
    return M("approvals.who.more", first=names[0], count=len(names) - 1)


def _send(preview: dict[str, Any]) -> dict[str, Any]:
    e = preview.get("email") or {}
    subj = e.get("subject") or M("approvals.value.no_subject")
    to, cc, bcc = _people(e.get("to")), _people(e.get("cc")), _people(e.get("bcc"))
    # Every recipient and the whole body: a send card never cuts at an "On … wrote:" line (an
    # agent can write a fake one to hide what follows) or after the first few recipients.
    fields = [field(F("from"), v_text(e.get("from") or "")),
              field(F("to"), v_people(to, limit=max(len(to), 1))),
              field(F("cc"), v_people(cc, limit=len(cc))) if e.get("cc") else None,
              field(F("bcc"), v_people(bcc, limit=len(bcc))) if e.get("bcc") else None,
              field(F("subject"), v_text(subj))]
    body = str(e.get("text") or "").strip()
    if body:
        fields.append(field("", v_quote(body)))
    att = [a for a in e.get("attachments") or [] if a]
    if att:
        fields.append(field(F("attachments"), v_list([a if isinstance(a, str) else str(a.get("filename") or a)
                                                   for a in att], limit=max(len(att), 1))))
    # The text says "[image]": each remote address an image loads from, in full, and where
    # every link goes.
    images = [str(x) for x in e.get("images") or [] if x]
    if images:
        fields.append(field(F("remote_images"), v_list(images, limit=len(images))))
    links = [str(x) for x in e.get("links") or [] if x]
    if links:
        fields.append(field(F("links_to"), v_list(links, limit=len(links))))
    # The title says what happens and to whom: "Send to Marco Silva". The subject is a field.
    return display("send", M("approvals.send.title", who=who_text(to + cc + bcc)), [f for f in fields if f], danger=bool(preview.get("danger")))


# Fields of an event change shown with their own words; the rest go through _change_text.
_EVENT_KNOWN = {"summary", "start", "end", "location", "attendees", "calendar", "calendar_name", "recurrence", "uid",
                "description", "reminders", "connector_id", "conversation_id", "all_day", "timezone", "tz", "op",
                "etag", "base_version"}
# How much of a series a change touches: approvals.scope.<word>.
_SCOPE_WORDS = {"this": "this", "following": "following", "future": "following", "all": "all"}


def _cal_text(ev: dict[str, Any]) -> str | None:
    """The calendar's label (calendar/describe.py); never a URL, id or address."""
    from mailr.calendar.describe import looks_internal

    if ev.get("calendar_name"):
        return str(ev["calendar_name"])
    v = ev.get("calendar")
    return None if v is None or looks_internal(v) else str(v)


def _change_text(k: str, v: Any, all_day: bool = False) -> tuple[Any, Any] | None:
    """(label, words) of a changed field that has no field of its own (msgs where the words are ours)."""
    none = M("approvals.value.none")
    if k == "reminders" and isinstance(v, list):
        from mailr.calendar.planner import offset_label

        return F("reminders"), ", ".join(offset_label(int(m), all_day) for m in v if isinstance(m, int)) or none
    if k == "transparent":
        return F("show_as"), M("approvals.value.free" if v else "approvals.value.busy")
    if k == "url":
        return F("video_link"), str(v) or none
    if k == "status":
        st = str(v).upper()
        return F("status"), M(f"approvals.value.status_{st.lower()}") if st in ("CANCELLED", "CONFIRMED", "TENTATIVE") \
            else str(v)
    if k == "class":
        c = str(v or "").lower()
        return F("visibility"), M(f"approvals.value.class_{c or 'default'}") \
            if c in ("", "public", "private", "confidential") else c.capitalize()
    if k == "send_updates":
        return F("mail_the_guests"), M("approvals.value.yes" if v else "approvals.value.no")
    if k == "message":
        return F("note_to_guests"), str(v)
    if v in (None, "", [], {}):
        return None
    return k.replace("_", " ").capitalize(), _plain(v)


def _applies(before: dict[str, Any], scope: str | None, tz, clock: str) -> tuple[Any, Any] | None:
    """Which event or occurrence a change or delete touches, and how much of a series:
    ("Applies to", "Tue 6 Oct, 10:00–11:00 AM (this occurrence only)"), ("Applies to", "This and
    following, from Tue 6 Oct"), ("Applies to", "The whole series (weekly on Monday)"), or for a
    single event ("When", "Tue 6 Oct, 10:00–11:00 AM")."""
    from mailr.agent import when

    span = when.span_text(before.get("start"), before.get("end"), bool(before.get("all_day")), tz, fmt=clock,
                          event_zone=before.get("tz"))
    if not before.get("recurring"):
        return (F("when"), span) if span else None
    scope = scope or "all"
    if scope == "this" and span:
        return F("applies_to"), M("approvals.event.applies_this", span=span)
    if scope in ("following", "future") and before.get("start"):
        day = when.span_text(before["start"], None, True, tz)
        return F("applies_to"), M("approvals.event.applies_following", day=day.removesuffix(", all day")) if day else None
    rule = when.repeat_text(before.get("rrule"), before.get("first") or before.get("start"))
    return F("applies_to"), M("approvals.event.applies_series_rule", rule=rule[0].lower() + rule[1:]) if rule \
        else M("approvals.event.applies_series")


def _after_span(before: dict[str, Any], ev: dict[str, Any], tz) -> tuple[Any, Any, bool]:
    """The new start and end of a change: a start without an end keeps the length (calendar/ical.py
    apply_changes); a time without an offset is in the user's zone."""
    from datetime import timedelta

    from mailr.agent import when

    def at(v: Any) -> Any:
        d = when._parse(v)
        if isinstance(d, datetime) and d.tzinfo is None and tz is not None:
            d = d.replace(tzinfo=tz)
        return d

    all_day = bool(ev["all_day"]) if ev.get("all_day") is not None else bool(before.get("all_day"))
    b0, b1 = at(before.get("start")), at(before.get("end"))
    s = at(ev.get("start")) if ev.get("start") else b0
    e = at(ev.get("end")) if ev.get("end") else None
    if e is None and s is not None:
        if b0 is not None and b1 is not None and type(b0) is type(b1) is type(s):
            e = s + (b1 - b0)
        else:
            e = s + (timedelta(days=1) if not isinstance(s, datetime) else timedelta(hours=1))
    return s, e, all_day


def _event(action: str, preview: dict[str, Any], tz, clock: str = "24h") -> dict[str, Any]:
    from mailr.agent import when

    ev = preview.get("event") or {}
    before = preview.get("before") if isinstance(preview.get("before"), dict) else None
    title = preview.get("title_msg") or preview.get("title") or M("approvals.event.title")
    title_s = en(title)
    scope = preview.get("scope")
    if scope is None:
        for ln in preview.get("lines") or []:
            k, _, v = str(ln).partition(":")
            if k == "scope":
                scope = v.strip()
    fields = []
    if before is not None:
        name = ev.get("summary") if action == "calendar.create" else before.get("summary")
        if name and "“" not in title_s:
            fields.append(field(F("event"), v_text(name)))
        applies = _applies(before, scope, tz, clock)
        timed = action == "calendar.update" and any(k in ev for k in ("start", "end", "all_day"))
        if applies and not (timed and en(applies[0]) == "When"):
            fields.append(field(applies[0], v_text(applies[1])))
        if action == "calendar.update":
            if timed:
                s, e, all_day = _after_span(before, ev, tz)
                was = when.span_text(before.get("start"), before.get("end"), bool(before.get("all_day")), tz,
                                     fmt=clock, event_zone=before.get("tz"))
                now_ = when.span_text(s, e, all_day, tz, fmt=clock, event_zone=ev.get("tz") or before.get("tz"))
                if was and now_ and was.split(", ")[0] == now_.split(", ")[0] and ", " in now_ \
                        and not now_.endswith("all day") and " – " not in now_:
                    now_ = now_.split(", ", 1)[1]  # the same day: "Tue 6 Oct, 10:00–11:00 → 14:00–15:00"
                fields.append(field(F("time"), v_diff(was, now_)))
            if "summary" in ev and ev["summary"] != before.get("summary"):
                fields.append(field(F("title"), v_diff(before.get("summary") or "",
                                                       ev["summary"] or M("approvals.value.no_title"))))
            if "location" in ev and ev["location"] != before.get("location"):
                fields.append(field(F("where"), v_diff(before.get("location") or "",
                                                       ev["location"] or M("approvals.value.no_place"))))
            if "recurrence" in ev:
                start = ev.get("start") or before.get("first") or before.get("start")
                fields.append(field(F("repeats"), v_diff(when.repeat_text(before.get("rrule"), before.get("first")),
                                                      when.repeat_text(ev["recurrence"], start))))
            if ev.get("calendar") or ev.get("calendar_name"):
                fields.append(field(F("calendar"), v_diff(before.get("calendar") or "",
                                                          _cal_text(ev) or M("approvals.value.another_calendar"))))
        elif before.get("calendar"):
            fields.append(field(F("calendar"), v_text(before["calendar"])))
    else:
        if ev.get("summary") and "“" not in title_s:
            fields.append(field(F("event"), v_text(ev["summary"])))
        if ev.get("start"):
            span = when.span_text(ev.get("start"), ev.get("end"), bool(ev.get("all_day")), tz, fmt=clock,
                                  event_zone=ev.get("tz") or ev.get("timezone"))
            wv = v_when(span, str(ev["start"]), str(ev["end"]) if ev.get("end") else None, bool(ev.get("all_day"))) \
                if span else when_value(ev.get("start"), ev.get("end"), tz)
            if wv:
                fields.append(field(F("when"), wv))
        if ev.get("location"):
            fields.append(field(F("where"), v_text(ev["location"])))
        if ev.get("recurrence"):
            fields.append(field(F("repeats"), v_text(when.repeat_text(ev["recurrence"], ev.get("start")))))
        if _cal_text(ev):
            fields.append(field(F("calendar"), v_text(_cal_text(ev))))
        if scope and action != "calendar.create":
            fields.append(field(F("applies_to"), v_text(M(f"approvals.scope.{_SCOPE_WORDS[scope]}") if scope in _SCOPE_WORDS
                                                        else scope.capitalize())))
    if ev.get("attendees"):
        fields.append(field(F("guests"), v_people(_people(ev["attendees"]))))
    if action == "calendar.update":
        all_day = bool(ev.get("all_day") if ev.get("all_day") is not None else (before or {}).get("all_day"))
        for k, v in ev.items():
            if k in _EVENT_KNOWN or (k == "status" and "Cancel" in title_s):
                continue
            got = _change_text(k, v, all_day)
            if got:
                fields.append(field(got[0], v_text(got[1])))
        if "reminders" in ev:
            got = _change_text("reminders", ev["reminders"], all_day)
            if got:
                fields.append(field(got[0], v_text(got[1])))
        if ev.get("description"):
            fields.append(field(F("notes"), v_quote(str(ev["description"]))))
    # Mail the event write sends (calendar.invite / calendar.cancel, agent/calendar_common.py _run_gated).
    rc = _people(preview.get("recipients") or [])
    for ln in preview.get("lines") or []:
        ln = str(ln)
        if ln.startswith("Mail to "):
            fields.append(field(F("invitations_to"), v_people(rc or _people(ln[8:].split(", ")))))
        elif ln.startswith("Cancel for "):
            fields.append(field(F("cancellation_to"), v_people(rc or _people(ln[11:].split(", ")))))
        elif ln.startswith("From ") and "@" in ln:
            fields.append(field(F("from"), v_text(ln[5:])))
        elif ln.startswith(("Guests ", "Invitations", "Cancellation")):
            fields.append(field("", v_text(ln)))
    if not fields:
        return _generic(preview, icon="event")
    return display("event", title, fields, danger=bool(preview.get("danger")) or action == "calendar.delete")


def _list_text(t: dict[str, Any]) -> str | None:
    """A task list's name on a card: `list_name`, else `list` only when it reads as a name (never a
    URL or id; an older preview had only the URL)."""
    from mailr.calendar.describe import looks_internal

    if t.get("list_name"):
        return str(t["list_name"])
    v = t.get("list")
    return None if v is None or looks_internal(v) else str(v)


def _task(preview: dict[str, Any], tz) -> dict[str, Any]:
    t = preview.get("task") or {}
    fields = []
    if t.get("summary") and "“" not in (preview.get("title") or ""):
        fields.append(field(F("task"), v_text(t["summary"])))
    if t.get("due"):
        fields.append(field(F("due"), when_value(t["due"], tz=tz) or v_text(t["due"])))
    lst = _list_text(t)
    if lst:
        fields.append(field(F("list"), v_text(lst)))
    if t.get("priority"):
        fields.append(field(F("priority"), v_text(t["priority"])))
    if t.get("description"):
        fields.append(field(F("notes"), v_quote(t["description"], 400)))
    if not fields:
        return _generic(preview, icon="task")
    return display("task", preview.get("title_msg") or preview.get("title") or M("approvals.task.title"), fields,
                   danger=bool(preview.get("danger")))


def _settings(preview: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else None
    fields = []
    if changes:
        for k, v in changes.items():
            fields.append(field(k.replace("_", " ").capitalize(), v_text(_plain(v))))
    else:
        return _generic(preview, icon="settings")
    return display("settings", preview.get("title_msg") or preview.get("title") or M("approvals.settings.title"), fields)


_SKILL_FIELDS = {"Applies automatically": "applies_automatically", "Enabled": "enabled", "Used for": "used_for"}


def _skill(preview: dict[str, Any]) -> dict[str, Any]:
    title = preview.get("title_msg") or (re.sub(r"/{2,}", "/", str(preview["title"])) if preview.get("title")
                                         else M("approvals.skill.title"))
    lines = [str(x) for x in preview.get("lines") or []]
    fields = []
    body = []
    for ln in lines:
        k, sep, v = ln.partition(": ")
        if sep and k in _SKILL_FIELDS:
            fields.append(field(F(_SKILL_FIELDS[k]), v_text(v)))
        else:
            body.append(ln)
    if body:
        head = body[0] if len(body) > 1 else None
        rest = body[1:] if head else body
        if head:
            fields.insert(0, field(F("about"), v_text(head)))
        if rest:
            fields.insert(1 if head else 0, field(F("instructions"), v_quote("\n".join(rest), 900)))
    return display("skill", title, fields)


def _memory(preview: dict[str, Any]) -> dict[str, Any]:
    lines = [str(x) for x in preview.get("lines") or []]
    now = next((ln[5:] for ln in lines if ln.startswith("Now: ")), None)
    new = next((ln[5:] for ln in lines if ln.startswith("New: ")), None)
    fields = []
    if now is not None and new is not None:
        fields.append(field(F("note"), v_diff(now, new)))
        lines = [ln for ln in lines if not ln.startswith(("Now: ", "New: "))]
    for ln in lines:
        fields.append(field("", v_quote(ln, 400) if len(ln) > 120 else v_text(ln)))
    return display("memory", preview.get("title_msg") or preview.get("title") or M("approvals.memory.title"), fields, danger=bool(preview.get("danger")))


def _generic(preview: dict[str, Any], icon: str = "action", title: str | None = None) -> dict[str, Any]:
    """Title and lines as written by the caller: "Key: value" lines become fields, others plain rows."""
    fields = []
    for ln in preview.get("lines") or []:
        s = str(ln)
        k, sep, v = s.partition(": ")
        if sep and 0 < len(k) <= 24 and len(k.split()) <= 3 and v:
            fields.append(field(k[0].upper() + k[1:], v_text(v)))
        else:
            fields.append(field("", v_text(s)))
    return display(icon, title or preview.get("title_msg") or preview.get("title") or M("approvals.generic.title"), fields,
                   danger=bool(preview.get("danger")))


def _forward(pv: dict[str, Any], name: str) -> dict[str, Any]:
    """A note the user forwarded from a trusted address (assistant/forwards.py): act on it? `name`:
    the user's name for the assistant, which carries out the note."""
    fields = [field(F("note"), v_quote(pv.get("note") or "", 1200)),
              field(F("subject"), v_text(pv.get("subject") or "")),
              field(F("from"), v_text(pv.get("address") or ""))]
    return display("action", M("approvals.forward.title"), fields, summary=M("approvals.forward.summary", name=name))


ICON_BY_KIND = {"label": "label", "people": "people", "memory": "memory", "skill": "skill", "order": "order",
                "filter": "filter", "settings": "settings", "rsvp": "event", "event": "event", "task": "task",
                "note": "note"}
ICON_BY_ACTION = {"mail.unsubscribe": "unsubscribe", "contacts.write": "people", "labels.write": "label",
                  "labels.delete": "label", "speakr.upload": "recording", "speakr.write": "recording",
                  "speakr.share": "recording", "speakr.link": "recording", "mail.draft": "draft",
                  "mail.screener": "people"}


async def approval_display(db: AsyncSession, user_id: int, action: str, payload: dict[str, Any] | None,
                           preview: dict[str, Any] | None) -> dict[str, Any]:
    """The display model of a pending action. Never raises: a builder that fails falls back to
    the caller's title and lines."""
    from mailr.calendar.tz import user_clock, user_zone
    from mailr.models import User

    clock = "24h"
    try:
        labels = await Labels.load(db, user_id)
        tz = await user_zone(db, user_id)
        user = await db.get(User, user_id)
        clock = user_clock(user, default="12h")
    except Exception:  # noqa: BLE001
        labels, tz = Labels([]), None
    return await build_display(action, payload, preview, labels, tz, db=db, user_id=user_id, clock=clock)


async def build_display(action: str, payload: dict[str, Any] | None, preview: dict[str, Any] | None,
                        labels: Labels, tz: ZoneInfo | None, *, db: AsyncSession | None = None,
                        user_id: int = 0, clock: str = "24h") -> dict[str, Any]:
    """approval_display with the labels, time zone and clock ("24h" or "12h", the user's
    time_format) given (the fixtures use this directly)."""
    pv = dict(preview or {})
    pl = dict(payload or {})
    try:
        kind = pv.get("kind")
        if action == "rules.write" and (kind == "filter" or pl.get("rule") is not None or pl.get("rule_id")):
            d = await _filter(db, user_id, pl, pv, labels)
        elif action == "orders.write":
            d = await _order(db, user_id, pl, pv, labels)
        elif action == "mail.send" and pv.get("email"):
            d = _send(pv)
        elif action == "mail.draft" and pv.get("email"):
            d = _send(pv)
            fields = d["fields"] + ([field(F("recipients_now"), v_text(pv["before"]))] if pv.get("before") else [])
            # What happens is a saved draft, never a send: the title and summary say so.
            e = pv.get("email") or {}
            to = who_text(_people(e.get("to")) + _people(e.get("cc")) + _people(e.get("bcc")))
            title = M("approvals.draft.title_recipients" if pv.get("before")
                      else "approvals.draft.title_change" if pl.get("draft_id") else "approvals.draft.title_save", who=to)
            d = display("draft", title, fields, summary=M("approvals.draft.summary"), reason=pv.get("reason"))
        elif action.startswith("mail.") and pl.get("action") and ("message_ids" in pl or "message_ids" in pv):
            d = await _mail_action(db, user_id, pl, pv, labels, tz)
        elif pv.get("event") is not None and action.startswith("calendar."):
            d = _event(action, pv, tz, clock)
        elif pv.get("task") is not None:
            d = _task(pv, tz)
        elif kind == "settings":
            d = _settings(pv, pl)
        elif kind == "skill":
            d = _skill(pv)
        elif kind == "memory":
            d = _memory(pv)
        elif kind == "forward" and action == "agent.forwarded_instructions":
            from mailr.knowledge import profile

            d = _forward(pv, await profile.name_of(db, user_id))
        else:
            d = _generic(pv, icon=ICON_BY_KIND.get(kind or "") or ICON_BY_ACTION.get(action, "action"))
    except Exception:
        import logging

        logging.getLogger("mailr.agent").warning("approval display failed for %s", action, exc_info=True)
        d = _generic(pv)
    if pv.get("reason"):
        r = _reason(pv["reason"], pv)
        d["reason"] = en(r)
        d.pop("reason_msg", None)
        if i18n.is_msg(r):
            d["reason_msg"] = r
    if pv.get("danger"):
        d["danger"] = True
    return d


def _reason(r: str, pv: dict[str, Any] | None = None) -> Any:
    """Why the card asks, in the user's terms: "Ambrose wrote this after reading mail. Marco
    Silva was not in that conversation.", not the rule's internal wording. A msg where the words
    are ours; another rule's reason as written (English)."""
    r = str(r)
    pv = pv or {}
    if r.startswith("hard rule: an agent that read email") and pv.get("outsiders") is not None:
        by = str(pv.get("by_name") or "")
        out = [str(x) for x in pv.get("outsiders") or [] if x]
        bcc = bool((pv.get("email") or {}).get("bcc"))
        sec = "approvals.reason.read_mail" if by else "approvals.reason.read_mail_agent"
        who = f"{by[:1].upper()}{by[1:]}"
        if out:
            if len(out) == 1:
                return M(f"{sec}.outsider", who=who or None, names=out[0])
            return M(f"{sec}.outsiders", who=who or None, names=", ".join(out[:-1]), last=out[-1])
        if bcc:
            return M(f"{sec}.bcc", who=who or None)
        return M(f"{sec}.plain", who=who or None)
    if r.startswith("hard rule: an agent changes who a saved draft goes to"):
        return M("approvals.reason.draft_recipients")
    if r.startswith("hard rule: a forwarded organizer change is not verified"):
        return M("approvals.reason.forwarded_unverified")
    r = r.removeprefix("hard rule: ")
    return r[0].upper() + r[1:] if r else r


async def enrich_preview(db: AsyncSession, user_id: int, action: str, payload: dict[str, Any] | None,
                         preview: dict[str, Any] | None) -> dict[str, Any]:
    """The stored preview: the caller's preview plus `display`, with `lines` rewritten from the
    display (readable text for older clients and notifications)."""
    pv = dict(preview or {"title": action})
    d = await approval_display(db, user_id, action, payload, pv)
    pv["display"] = d
    pv["title"] = d["title"]
    pv["lines"] = lines_of(d)
    return pv


# ---------------------------------------------------------------- outcomes

def _done_text(action: str, payload: dict[str, Any], result: dict[str, Any], d: dict[str, Any] | None) -> dict[str, Any]:
    """What an executed action did, as a msg (approvals.done.*): "moved 6 messages to Trash"."""
    result = result if isinstance(result, dict) else {}
    D = lambda k: M(f"approvals.done.{k}")  # noqa: E731
    if action.startswith("mail.") and payload.get("action"):
        n = result.get("affected")
        if not isinstance(n, int):
            n = len(payload.get("message_ids") or [])
        return mail_verb(payload["action"], n, done=True)
    op = payload.get("op")
    if action == "rules.write":
        return D(f"filter_{op}") if op in ("create", "update", "delete", "apply") else D("done")
    if action == "orders.write":
        return D(f"order_{op}") if op in ("create", "update", "pause", "resume", "delete") else D("done")
    if action == "mail.send":
        return D("sent")
    if action == "labels.write":
        return {"label.create": D("label_create"), "label.update": D("label_update")}.get(op, D("done"))
    if action == "labels.delete":
        return D("label_delete")
    if action == "calendar.create":
        return D("event_added")
    if action in ("calendar.update", "calendar.invite"):
        return D("event_changed")
    if action in ("calendar.delete", "calendar.cancel"):
        return D("event_removed")
    if action == "calendar.rsvp":
        return D("rsvp")
    if action.startswith("tasks."):
        return {"tasks.create": D("task_added"), "tasks.complete": D("task_completed"),
                "tasks.delete": D("task_deleted")}.get(action, D("task_changed"))
    if action == "skills.write":
        return D("skill_saved")
    if action == "memory.write":
        return D("memory_saved") if op != "forget" else D("memory_forgot")
    if action == "prefs.write":
        return D("settings_changed")
    if action == "mail.unsubscribe":
        return D("unsubscribed")
    if action.startswith("notes."):
        if action == "notes.create":
            return D("note_made")
        if action == "notes.delete":
            return D("note_trashed") if any(o.get("op") == "note.trash" for o in payload.get("ops") or []) \
                else D("checked_cleared")
        if action == "notes.labels":
            return D("note_labels")
        return D("note_changed")
    return D("done")


def outcome(action: str, status: str, payload: dict[str, Any] | None = None, result: Any = None,
            d: dict[str, Any] | None = None) -> dict[str, Any]:
    """{"state", "text", "msg"} for a card that changes in place: "Approved · moved 6 messages to Trash"."""
    if status == "denied" and (payload or {}).get("op") == "review":
        m = M("approvals.outcome.discarded")  # a review card's Discard (agent/review.py)
    elif status in ("pending", "denied", "expired"):
        m = M(f"approvals.outcome.{status}")
    elif status in ("approved", "executing"):
        m = M("approvals.outcome.working")
    elif status == "executed":
        m = M("approvals.outcome.executed", done=_done_text(action, payload or {}, result or {}, d))
    elif status == "failed":
        err = (result or {}).get("error") if isinstance(result, dict) else None
        m = M("approvals.outcome.failed_error", error=str(err)[:160]) if err else M("approvals.outcome.failed")
    else:
        return {"state": status, "text": status.capitalize()}
    return {"state": status, "text": en(m), "msg": m}


async def approval_card(db: AsyncSession, pa, *, covers: bool = False) -> dict[str, Any]:
    """A pending action as the clients show it: pending_out, with `display` ensured on the
    preview (rows stored before the display model get one computed now) and `outcome`.
    With `covers` (the signed-in user's own views), a waiting card also carries `also_covers`:
    how many other waiting requests each grant on it would decide too (permissions.would_cover).
    `grants`: whether it offers "Allow for this chat" and "Always allow" (permissions.takes_grant)."""
    from mailr.core.permissions import pending_out, takes_grant, would_cover

    out = pending_out(pa)
    out["grants"] = takes_grant(pa)
    pv = dict(out.get("preview") or {})
    if not isinstance(pv.get("display"), dict):
        pv = await enrich_preview(db, pa.user_id, pa.action, pa.payload, pv)
        out["preview"] = pv
    out["display"] = pv["display"]
    out["outcome"] = outcome(pa.action, pa.status, pa.payload, pa.result, pv["display"])
    if covers and pa.status == "pending":
        out["also_covers"] = await would_cover(db, pa)
    return out


# ---------------------------------------------------------------- tool steps

def _q(v: Any, n: int = 60) -> str:
    s = " ".join(str(v or "").split())
    return (s[: n - 1] + "…") if len(s) > n else s


async def _subject(db: AsyncSession, uid: int, conv_id: Any = None, msg_id: Any = None) -> str | None:
    from mailr.models import Conversation, Message

    try:
        if conv_id is not None:
            c = await db.get(Conversation, int(conv_id))
            return c.subject if c and c.user_id == uid else None
        if msg_id is not None:
            m = await db.get(Message, int(msg_id))
            return m.subject if m and m.user_id == uid else None
    except (TypeError, ValueError):
        return None
    return None


async def step_label(db: AsyncSession, uid: int, name: str, args: dict[str, Any] | None) -> str:
    """A short, human description of a tool call for the activity list (English; step_msg is the msg)."""
    return en(await step_msg(db, uid, name, args))


# Tools whose step reads the same every time: approvals.step.fixed.<tool>.
STEP_FIXED = frozenset({
    "list_labels", "list_calendar", "list_tasks", "create_event", "create_task", "create_draft", "update_draft",
    "send_draft", "get_outbox_status", "apply_label", "archive", "show_in_tab", "list_drafts", "get_draft",
    "list_skills", "get_skill", "create_skill", "update_skill", "forget", "upsert_entity", "get_configuration",
    "update_settings", "update_signature", "update_profile", "get_brief", "list_attachments", "update_brief",
    "read_screen", "list_view", "review_mail",
    "get_signals", "search_events", "get_event", "get_calendar_defaults", "get_free_busy", "update_event",
    "delete_event", "cancel_event", "search_tasks", "update_task", "complete_task", "move_task", "delete_task",
    "bulk_tasks", "list_reminders", "list_recordings", "get_recording", "get_transcript", "search_recordings",
    "get_summary", "recordings_for_event", "link_recording", "propose_action_items", "draft_follow_up",
    "send_to_speakr", "update_recording", "share_recording", "list_notes", "get_note", "create_note",
    "update_note", "checklist_items", "delete_note", "note_history", "manage_note_labels",
})
_VIEWS = ("inbox", "all", "sent", "starred", "important", "drafts", "snoozed")


async def step_msg(db: AsyncSession, uid: int, name: str, args: dict[str, Any] | None) -> Any:
    """The step's description as a msg (approvals.step.*), or plain text for a tool without one."""
    a = args or {}
    S = lambda k, **p: M(f"approvals.step.{k}", **p)  # noqa: E731
    if name == "search_conversations":
        if a.get("q"):
            return S("search_mail", q=_q(a["q"]))
        view = str(a.get("view") or "all")
        return S(f"view.{view}") if view in _VIEWS else S("view.label")
    if name == "search_semantic":
        return S("search_meaning", q=_q(a.get("q")))
    if name == "web_search":
        # The exact query the provider receives (whitespace folded, as web_tools sends it).
        return S("search_web", q=" ".join(str(a.get("query") or "").split())[:400])
    if name == "read_page":
        from mailr.websearch.adapters import site_of

        site = site_of(str(a.get("url") or ""))
        return S("read_site", site=site) if site else S("read_page")
    if name == "find_passages":
        subj = None
        if a.get("conversation_id") is not None:
            subj = await _subject(db, uid, conv_id=a.get("conversation_id"))
        return S("passages_in", q=_q(a.get("q"), 48), subject=_q(subj, 40)) if subj else S("passages", q=_q(a.get("q"), 48))
    if name == "get_conversation":
        subj = await _subject(db, uid, conv_id=a.get("conversation_id"))
        return S("read_conversation_named", subject=_q(subj, 56)) if subj else S("read_conversation")
    if name == "get_message_body":
        subj = await _subject(db, uid, msg_id=a.get("message_id"))
        return S("read_message_named", subject=_q(subj, 48)) if subj else S("read_message")
    if name == "get_attachment":
        ref = a.get("attachment")
        return S("attachment_named", name=_q(ref, 48)) if isinstance(ref, str) and ref and not ref.isdigit() \
            else S("attachment")
    if name == "get_person":
        return S("person", email=_q(a.get("email"), 48))
    if name == "build_context":
        subj = await _subject(db, uid, conv_id=a.get("conversation_id")) if a.get("conversation_id") else None
        return S("context_named", subject=_q(subj, 48)) if subj else S("context")
    if name == "recall":
        return S("recall", q=_q(a.get("q") or a.get("about"), 48))
    if name == "remember":
        return S("remember", text=_q(a.get("text"), 56))
    if name in ("get_note", "update_note", "checklist_items", "delete_note", "note_history") and a.get("note") \
            and not str(a.get("note")).startswith("n"):
        verb = "restore_note" if name == "delete_note" and a.get("restore") else name
        return S(f"note_named.{verb}", note=_q(a["note"], 48))
    if name == "search_notes":
        return S("search_notes", q=_q(a.get("q"), 48))
    if name == "use_skill":
        return S("use_skill", name=_q(str(a.get("name") or "").lstrip("/"), 30))
    if name == "get_entity":
        return S("entity", ref=_q(a.get("ref"), 40))
    if name == "search_calendar":
        return S("search_calendar_q", q=_q(a.get("q"), 48)) if a.get("q") else S("search_calendar")
    if name == "find_time":
        return S("find_time")
    if name == "rsvp":
        r = str(a.get("response"))
        return S(f"rsvp.{r if r in ('accepted', 'declined', 'tentative') else 'other'}")
    if name in ("manage_lists", "manage_calendars"):
        what = "list" if name == "manage_lists" else "calendar"
        verb = str(a.get("action"))
        verb = verb if verb in ("create", "rename", "color", "delete") else "other"
        return S(f"{what}_named.{verb}", name=_q(a.get("name"), 30)) if a.get("name") else S(f"{what}.{verb}")
    if name in ("manage_filter", "manage_label", "manage_layout"):
        what = {"manage_filter": "filter", "manage_label": "label", "manage_layout": "layout"}[name]
        verb = str(a.get("action"))
        verb = verb if verb in ("create", "update", "delete", "apply", "activate", "save") else "other"
        named = a.get("label") or (name == "manage_filter" and a.get("name"))
        return S(f"{what}_named.{verb}", name=_q(a.get("label") or a.get("name"), 30)) if named else S(f"{what}.{verb}")
    if name == "manage_order":
        verb = str(a.get("action"))
        if verb == "list":
            return S("order_list")
        verb = verb if verb in ("create", "update", "delete", "pause", "resume") else "other"
        return S(f"order_named.{verb}", name=_q(a.get("name"), 30)) if a.get("name") else S(f"order.{verb}")
    if name == "modify_conversations":
        return mail_verb(str(a.get("action")), len(a.get("conversation_ids") or []), "conversation", ing=True)
    if name in STEP_FIXED:
        return S(f"fixed.{name}")
    return name.replace("_", " ").capitalize()


STEP_ICONS = (
    ("note", ("note", "checklist")),
    ("search", ("search", "find_passages", "recall", "find_time")),
    ("read", ("get_conversation", "read_screen", "list_view", "get_message_body", "build_context", "get_brief", "get_draft", "list_drafts",
              "attachment", "read_page")),
    ("person", ("get_person", "get_entity", "upsert_entity")),
    ("calendar", ("calendar", "event", "free_busy", "rsvp")),
    ("task", ("task", "reminder")),
    ("draft", ("draft", "brief", "signature")),
    ("send", ("send", "outbox")),
    ("label", ("label", "filter", "modify_conversations", "archive", "review_mail", "order", "layout", "show_in_tab")),
    ("recording", ("recording", "transcript", "speakr", "summary", "action_items")),
    ("memory", ("remember", "forget", "skill", "memory")),
    ("settings", ("settings", "configuration", "profile")),
)


def step_icon(name: str) -> str:
    """The icon of a tool step: note, search, read, person, calendar, task, draft, send, label,
    recording, memory, settings or other."""
    for icon, keys in STEP_ICONS:
        if any(k in name for k in keys):
            return icon
    return "other"


def result_label(name: str, res) -> tuple[str, int | None]:
    """(summary text, count) for a finished step (English; result_msg has the msg)."""
    m, n = result_msg(name, res)
    return en(m), n


_RESULT_NOUN = {"find_passages": "passage", "list_calendar": "event", "list_tasks": "task", "list_notes": "note",
                "search_notes": "note", "note_history": "version", "manage_note_labels": "label",
                "list_labels": "label", "list_skills": "skill", "list_recordings": "recording",
                "recordings_for_event": "recording", "search_events": "event", "search_tasks": "task",
                "list_drafts": "draft", "list_attachments": "attachment", "list_reminders": "item"}


def result_msg(name: str, res) -> tuple[Any, int | None]:
    """(summary as a msg, or the tool's own text; count) for a finished step (approvals.result.*)."""
    d = res.data
    C = lambda noun, n: M(f"approvals.count.{noun}", count=n)  # noqa: E731
    if name == "web_search" and isinstance(d, dict) and isinstance(d.get("items"), list):
        n = len(d["items"])
        return (M("approvals.result.results_from", count=n, provider=str(d["provider"])) if d.get("provider")
                else C("result", n)), n
    if name == "review_mail" and isinstance(d, dict):
        if d.get("status") == "nothing_to_review":
            return M("approvals.result.nothing_to_review"), None
        key = "approvals.result.review_running" if d.get("status") == "already_running" else "approvals.result.reviewing"
        return M(key, count=int(d.get("total") or 0)), None
    if name == "read_page" and isinstance(d, dict) and "text" in d:
        return M("approvals.result.page_read_cut" if d.get("truncated") else "approvals.result.page_read"), None
    if isinstance(d, dict) and isinstance(d.get("items"), list):
        n = len(d["items"])
        return C(_RESULT_NOUN.get(name, "conversation"), n), n
    if isinstance(d, dict) and isinstance(d.get("messages"), list):
        n = len(d["messages"])
        return C("message", n), n
    if isinstance(d, dict) and isinstance(d.get("memories"), list):
        n = len(d["memories"]) + len(d.get("entities") or [])
        return (C("memory", n) if n else M("approvals.result.nothing_saved")), n
    if isinstance(d, dict) and d.get("summary") and d.get("status") == "done":
        return str(d["summary"])[:120], None
    if name in ("checklist_items", "update_note", "create_note", "delete_note") and isinstance(d, dict) \
            and isinstance(d.get("results"), list):
        rs = d["results"]
        added = sum(len(r.get("added") or []) + len(r.get("merged") or []) for r in rs)
        done = sum(1 for r in rs if r.get("ok"))
        waiting = sum(1 for r in rs if r.get("ok") is None)
        failed = sum(1 for r in rs if r.get("ok") is False)
        parts = []
        if added:
            parts.append(M("approvals.result.items_added", count=added))
        elif done:
            parts.append(M("approvals.result.changes_made", count=done))
        if waiting:
            parts.append(M("approvals.result.waiting", count=waiting))
        if failed:
            parts.append(M("approvals.result.failed", count=failed))
        if not parts:
            return M("approvals.result.nothing_changed"), None
        out = parts[-1]
        for p in reversed(parts[:-1]):
            out = M("approvals.result.join", first=p, rest=out)
        return out, None
    if name == "get_note" and isinstance(d, dict) and isinstance(d.get("items"), list) and d.get("kind") == "checklist":
        n = len(d["items"])
        return C("item", n), n
    if name == "update_draft" and isinstance(d, dict) and d.get("window"):
        return M("approvals.result.open_draft"), None
    if name in ("create_task", "update_task") and isinstance(d, dict) and isinstance(d.get("will_remind"), list):
        # The step says what was stored, from the tool's result (it showed the task's status,
        # "needs-action", before: 10 Oct 2026). Times are local, written 2026-10-14 06:00.
        first = d["will_remind"][0] if d["will_remind"] else None
        if first and isinstance(first.get("at"), str):
            return M("approvals.result.saved_reminds", when=first["at"][:16].replace("T", " ")), None
        return M("approvals.result.saved_no_reminder"), None
    if isinstance(d, list):
        return C("item", len(d)), len(d)
    return res.summary(), None


# ---------------------------------------------------------------- Markdown

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$|^[^|\n]+\|[^\n]*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


def normalize_markdown(text: str | None) -> str:
    """Agent Markdown made compact and predictable:
    the shallowest heading becomes ###, the next ####, deeper ones bold lines; setext headings
    become ATX; a table gets a blank line before it and <br> in its cells becomes "; ";
    blank-line runs collapse; a trailing rule is dropped. Code blocks are left alone."""
    if not text:
        return text or ""
    lines = str(text).replace("\r\n", "\n").split("\n")
    out: list[str] = []
    fenced = False
    for ln in lines:
        if _FENCE.match(ln):
            fenced = not fenced
            out.append(ln)
            continue
        if fenced:
            out.append(ln)
            continue
        # Setext underline (=== or ---) under a text line.
        if out and re.fullmatch(r"\s*(=+|-{2,})\s*", ln) and out[-1].strip() and not _HEADING.match(out[-1]) \
                and not _TABLE_ROW.match(out[-1]) and not re.match(r"\s*([-*+>]|\d+\.)\s", out[-1]):
            single = len(out) == 1 or not out[-2].strip()
            if single:
                out[-1] = ("# " if ln.strip().startswith("=") else "## ") + out[-1].strip()
                continue
            # Under a paragraph of several lines the writer meant a rule, not a heading.
            out.append("")
        out.append(ln)
    # Heading levels.
    fenced = False
    levels = set()
    for ln in out:
        if _FENCE.match(ln):
            fenced = not fenced
        elif not fenced and (m := _HEADING.match(ln)):
            levels.add(len(m.group(1)))
    order = sorted(levels)
    res: list[str] = []
    fenced = False
    for ln in out:
        if _FENCE.match(ln):
            fenced = not fenced
            res.append(ln)
            continue
        if fenced:
            res.append(ln)
            continue
        m = _HEADING.match(ln)
        if m:
            rank = order.index(len(m.group(1)))
            body = m.group(2).strip()
            if rank == 0:
                res.append("### " + body)
            elif rank == 1:
                res.append("#### " + body)
            else:
                res.append("**" + body.strip("*") + "**")
            continue
        res.append(ln)
    # Tables: a blank line before the header row; <br> in cells.
    final: list[str] = []
    fenced = False
    for i, ln in enumerate(res):
        if _FENCE.match(ln):
            fenced = not fenced
        if not fenced and "|" in ln and _TABLE_ROW.match(ln):
            ln = _BR.sub("; ", ln)
            starts = i + 1 < len(res) and _TABLE_SEP.match(res[i + 1]) and not _TABLE_SEP.match(ln)
            if starts and final and final[-1].strip():
                final.append("")
        elif not fenced:
            ln = _BR.sub("\n", ln)
        final.append(ln.rstrip())
    s = "\n".join(final)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    s = re.sub(r"\n+\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "", s).strip()
    return s
