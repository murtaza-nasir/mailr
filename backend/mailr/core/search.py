"""Gmail search syntax → SQL over the message index.

Supported: from: to: cc: bcc: subject: label: in: is: has: filename: after: before:
(is:needs_reply, is:waiting and has:deadline read the triage signals; has:recording and
recording:<text> read the Speakr recording links.)
older_than: newer_than: larger: smaller: category: list: deliveredto: rfc822msgid:
account: (an account's address or id: mail that arrived through that mail account, or that you sent
from one of its addresses; core/mailaccounts.py)
"exact phrase", -negation, OR / |, { a b } groups, ( ) groups, AROUND is treated as AND.
An operator applies to a whole group, as in Gmail: from:(a OR b), from:{a b}. Address operators
also take comma-separated lists: from:a@x.example,b@y.com. "me" in to:/cc:/bcc: means any of the
user's own addresses (account email and identities).
Bare words use Postgres full-text search, with a substring fallback for short tokens.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from dateutil import parser as dateparser
from sqlalchemy import (
    Boolean,
    Text,
    and_,
    bindparam,
    exists,
    false,
    func,
    literal_column,
    not_,
    or_,
    select,
    text,
    true,
    type_coerce,
)
from sqlalchemy.sql.elements import ColumnElement, TextClause

from mailr.models import Label, Message

_TOKEN = re.compile(r'"[^"]*"|\(|\)|\{|\}|[^\s(){}"]+(?:"[^"]*")?')
SIZE_UNITS = {"": 1, "k": 1024, "kb": 1024, "m": 1024**2, "mb": 1024**2}
CATEGORIES = {"primary", "social", "promotions", "updates", "forums", "purchases", "reservations"}
SYSTEM_IN = {"inbox", "sent", "drafts", "spam", "junk", "trash", "archive", "anywhere", "all",
             "snoozed", "starred", "important", "scheduled"}


@dataclass
class Compiled:
    where: ColumnElement[bool]
    # Whether the query itself opted into spam/trash (otherwise they are excluded).
    includes_spam_trash: bool
    words: list[str]


class Ctx:
    def __init__(self, user_id: int, labels: list[Label], zone: tzinfo | None = None):
        self.user_id = user_id
        self.zone = zone  # after: and before: dates without an offset are in it (UTC when None)
        self.by_name = {lb.name.lower(): lb for lb in labels}
        self.by_role = {lb.role: lb for lb in labels if lb.role}
        self.include_spam_trash = False
        self.words: list[str] = []
        self.recording_local_ok = True
        # The accounts account: may name (a token limited to some accounts: those; None: all).
        self.connector_ids: list[int] | None = None

    def label_ids(self, name: str) -> list[int]:
        name = name.lower().replace("-", " ").strip()
        out = []
        for key, lb in self.by_name.items():
            k = key.replace("-", " ")
            if k == name or k.startswith(name + "/"):
                out.append(lb.id)
        if not out and name in self.by_role:
            out.append(self.by_role[name].id)
        return out


def _bool(clause):
    """Raw SQL predicates (TextClause) cannot be negated directly; coerce them to a boolean
    expression so `-term` and `-(group)` work for every operator."""
    if isinstance(clause, TextClause):
        return type_coerce(clause, Boolean).self_group()
    return clause


def _unquote(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v


# The sender as one text, exactly the expression of the trigram index ix_msg_sender_trgm
# (migration f3a8c1d6b2e9): from: and plain words match a substring of it.
SENDER_TEXT = literal_column("(messages.from_name || ' ' || messages.from_email)", Text)

ADDRESS_OPS = {"from", "to", "cc", "bcc", "deliveredto", "list"}

# The user's own addresses, for to:me / cc:me / bcc:me.
_MY_ADDRS = ("(SELECT lower(email) FROM identities WHERE user_id = :uid "
             "UNION SELECT lower(email) FROM users WHERE id = :uid)")


def _addr_is_me(col_json, user_id: int) -> ColumnElement[bool]:
    return text(
        f"EXISTS (SELECT 1 FROM jsonb_array_elements(messages.{col_json}) a "
        f"WHERE lower(a->>'email') IN {_MY_ADDRS})"
    ).bindparams(bindparam("uid", user_id, unique=True))


def _json_like(col_json: str, exact: str, value: str) -> ColumnElement[bool]:
    """`exact` (an EXISTS over the JSON array that binds :p), with a pre-filter the trigram index on
    lower(<column>::text) serves (ix_msg_to_trgm and the others, migration a8d4e2f6c9b3): the
    substring must be somewhere in the column's text before the array is read. A value with a
    quote or a backslash is spelt differently in JSON text, so it goes without the pre-filter."""
    pat = f"%{value.lower()}%"
    pre = "" if '"' in value or "\\" in value else f"lower(messages.{col_json}::text) LIKE :p AND "
    return _bool(text(f"({pre}{exact})").bindparams(bindparam("p", pat, unique=True)))


def _addr_match(col_json, value: str) -> ColumnElement[bool]:
    return _json_like(col_json, f"EXISTS (SELECT 1 FROM jsonb_array_elements(messages.{col_json}) a "
                                "WHERE lower(a->>'email') LIKE :p OR lower(a->>'name') LIKE :p)", value)


# Operator values that cannot be read raise ValueError, and compile_query falls back to plain
# text search; nothing a user types may reach the database as an out-of-range value.
MAX_SIZE = 2**31 - 1  # messages.size is int4
MAX_DIGITS = 12


def _parse_date(v: str, zone: tzinfo | None = None) -> datetime:
    """A date or time a search names; one without an offset is in the user's zone (`zone`)."""
    v = v.replace("/", "-")
    try:
        d = dateparser.parse(v, yearfirst=True)
    except (OverflowError, TypeError) as e:
        raise ValueError(v) from e
    if d.tzinfo is None:
        d = d.replace(tzinfo=zone or UTC)
    return d


_DATE_OPS = re.compile(r"(?i)\b(after|before|newer|older):")


async def query_zone(db: Any, user_id: int, *queries: str | None) -> tzinfo | None:
    """The user's zone when one of these searches names a date (after:, before:), for
    `compile_query`; None otherwise (no lookup)."""
    if not any(q and _DATE_OPS.search(q) for q in queries):
        return None
    from mailr.calendar.tz import user_zone

    return await user_zone(db, user_id)


def _rel(v: str) -> datetime:
    m = re.fullmatch(rf"(\d{{1,{MAX_DIGITS}}})([dmy])", v.lower())
    if not m:
        raise ValueError(v)
    n, u = int(m.group(1)), m.group(2)
    days = {"d": 1, "m": 30, "y": 365}[u] * n
    try:
        return datetime.now(UTC) - timedelta(days=days)
    except OverflowError:
        # Further back than datetime goes: older_than: matches nothing, newer_than: everything.
        return datetime.min.replace(tzinfo=UTC)


def _size(v: str) -> int:
    m = re.fullmatch(rf"(\d{{1,{MAX_DIGITS}}})\s*([kKmM][bB]?)?", v)
    if not m:
        raise ValueError(v)
    return min(int(m.group(1)) * SIZE_UNITS[(m.group(2) or "").lower()], MAX_SIZE)


def _term(ctx: Ctx, tok: str) -> ColumnElement[bool]:
    neg = tok.startswith("-") and len(tok) > 1
    if neg:
        tok = tok[1:]
    expr = _positive(ctx, tok)
    return not_(_bool(expr)) if neg else expr


def _positive(ctx: Ctx, tok: str) -> ColumnElement[bool]:
    if ":" in tok and not tok.startswith('"'):
        op, _, val = tok.partition(":")
        op = op.lower()
        val = _unquote(val)
        if op in ADDRESS_OPS and "," in val:
            parts = [v.strip() for v in val.split(",") if v.strip()]
            if len(parts) > 1:
                return or_(*[_positive(ctx, f"{op}:{v}") for v in parts])
            val = parts[0] if parts else ""
        if op == "from":
            if val.lower() == "me":
                return Message.sent_by_me.is_(True)
            # ILIKE on the sender text that the pg_trgm GIN index ix_msg_sender_trgm holds.
            return SENDER_TEXT.ilike(f"%{val}%")
        if op in ("to", "cc", "bcc"):
            col = op if op != "to" else '"to"'
            if val.lower() == "me":
                return _addr_is_me(col, ctx.user_id)
            return _addr_match(col, val)
        if op == "subject":
            return Message.subject.ilike(f"%{val}%")
        if op == "label":
            ids = ctx.label_ids(val)
            return Message.label_ids.overlap(ids) if ids else false()
        if op == "in":
            return _in(ctx, val.lower())
        if op == "is":
            return _is(ctx, val.lower())
        if op == "has":
            v = val.lower()
            if v in ("attachment", "attachments"):
                return Message.has_attachments.is_(True)
            if v in ("userlabels", "nouserlabels"):
                sys_ids = [lb.id for lb in ctx.by_role.values()]
                has = text(
                    "EXISTS (SELECT 1 FROM unnest(messages.label_ids) l WHERE NOT l = ANY(:s))"
                ).bindparams(bindparam("s", sys_ids, unique=True))
                return has if v == "userlabels" else not_(_bool(has))
            if v in ("drive", "document", "spreadsheet", "presentation", "youtube"):
                return func.lower(Message.body_text).like(f"%{v}%")
            if v == "star" or v.endswith("-star"):
                return Message.flagged.is_(True)
            if v == "deadline":
                return _signal("deadline")
            if v in ("recording", "recordings"):
                return _recording(ctx, None)
            return false()
        if op == "recording":
            return _recording(ctx, val)
        if op == "filename":
            return _json_like("attachments", "EXISTS (SELECT 1 FROM jsonb_array_elements(messages.attachments) a "
                                             "WHERE lower(a->>'filename') LIKE :p OR lower(a->>'type') LIKE :p)", val)
        if op in ("after", "newer"):
            return Message.date >= _parse_date(val, ctx.zone)
        if op in ("before", "older"):
            return Message.date < _parse_date(val, ctx.zone)
        if op == "newer_than":
            return Message.date >= _rel(val)
        if op == "older_than":
            return Message.date < _rel(val)
        if op in ("larger", "size"):
            return Message.size >= _size(val)
        if op == "smaller":
            return Message.size < _size(val)
        if op == "category":
            v = val.lower()
            if v == "primary":
                return or_(Message.category.is_(None), Message.category == "primary")
            return Message.category == v
        if op == "list":
            # The expression of the trigram index ix_msg_list_trgm (a literal '', not a bound one).
            return func.lower(func.coalesce(Message.list_id, literal_column("''"))).like(f"%{val.lower()}%")
        if op == "deliveredto":
            v = val.lower()
            return or_(
                Message.delivered_to.contains([v]),
                _addr_match('"to"', v),
                _addr_match("cc", v),
            )
        if op == "account":
            return _account(ctx, val)
        if op == "rfc822msgid":
            v = val if val.startswith("<") else f"<{val}>"
            return Message.message_id_hdr == v
    # Plain word or phrase.
    word = _unquote(tok)
    if not word:
        return true()
    ctx.words.append(word)
    tsq = func.websearch_to_tsquery("simple", word if not tok.startswith('"') else f'"{word}"')
    # The text of attached emails has a vector of its own (Message.attached_tsv, a partial GIN
    # index): it matches too, and the list marks such a hit (attached_only_matches).
    fts = or_(Message.tsv.op("@@")(tsq), Message.attached_tsv.op("@@")(tsq))
    # Substrings of subject and sender (for words full-text search splits differently), each
    # an ILIKE that a pg_trgm GIN index serves, so the OR is a BitmapOr of three index scans.
    # A word shorter than a trigram cannot use those indexes and would read every message: it
    # matches by full-text search alone.
    if len(word) < 3:
        return fts
    like = f"%{word}%"
    return or_(fts, Message.subject.ilike(like), SENDER_TEXT.ilike(like))


# Whether recording:<text> may match the stored summaries of local-only Speakr accounts. The
# assistant's mail search on a hosted model sets it to False (agent/mail_tools.py), so such a model
# cannot learn what a local-only summary says from which conversations match.
RECORDING_SUMMARIES_LOCAL_OK: ContextVar[bool] = ContextVar("recording_summaries_local_ok", default=True)


# ACT recording.search @web.list (has:recording, recording:<text>)
def _recording(ctx: Ctx, text_: str | None) -> ColumnElement[bool]:
    """has:recording: the conversation is linked to a Speakr recording that is still in the mirror
    (integrations/speakr/mail.py). recording:<text>: such a recording's title or participants
    contain the text, or its stored summary has the words."""
    sql = ("EXISTS (SELECT 1 FROM recording_links rl JOIN speakr_recordings sr "
           "ON sr.connector_id = rl.connector_id AND sr.recording_id = rl.recording_id AND sr.deleted_at IS NULL "
           "WHERE rl.user_id = :uid AND rl.kind = 'conversation' AND rl.state <> 'dismissed' "
           "AND rl.target = messages.conversation_id::text")
    params = [bindparam("uid", ctx.user_id, unique=True)]
    if text_:
        summary = "sr.tsv @@ websearch_to_tsquery('simple', :rqt)"
        if not ctx.recording_local_ok:
            summary = ("(" + summary + " AND EXISTS (SELECT 1 FROM connectors c WHERE c.id = sr.connector_id "
                       "AND c.data_class <> 'local_only'))")
        sql += f" AND (sr.title ILIKE :rq OR array_to_string(sr.participants, ' ') ILIKE :rq OR {summary})"
        params += [bindparam("rq", f"%{text_}%", unique=True), bindparam("rqt", text_, unique=True)]
    return text(sql + ")").bindparams(*params)


# ACT mail.search_account @web.list @api (account:<address or id>)
def _account(ctx: Ctx, value: str) -> ColumnElement[bool]:
    """account:<address or id>: mail of the mail accounts with that id, login address or send-as
    address (core/mailaccounts.account_message_cond). Only accounts the caller may see count: a
    token limited to some accounts, or without local-only access, names no other account."""
    from mailr.core.mailaccounts import MAIL_ACCOUNT_KINDS, account_message_cond
    from mailr.models import Connector, Identity

    v = value.strip().lower()
    if not v:
        return false()
    match = [func.lower(Connector.config["email"].astext) == v,
             func.lower(Connector.config["imap"]["username"].astext) == v,
             exists().where(Identity.connector_id == Connector.id, func.lower(Identity.email) == v)]
    if v.isdigit() and len(v) <= 12:
        match.append(Connector.id == int(v))
    ids = select(Connector.id).where(Connector.user_id == ctx.user_id, Connector.kind.in_(MAIL_ACCOUNT_KINDS),
                                     or_(*match))
    if ctx.connector_ids:
        ids = ids.where(Connector.id.in_(ctx.connector_ids))
    if not ctx.recording_local_ok:
        ids = ids.where(Connector.data_class != "local_only")
    return account_message_cond(ctx.user_id, ids)


def _in(ctx: Ctx, v: str) -> ColumnElement[bool]:
    if v in ("anywhere", "all"):
        ctx.include_spam_trash = v == "anywhere"
        return true()
    if v == "snoozed":
        return text(
            "EXISTS (SELECT 1 FROM conversations c WHERE c.id = messages.conversation_id "
            "AND c.snoozed_until IS NOT NULL)"
        )
    if v == "starred":
        return Message.flagged.is_(True)
    if v == "important":
        return Message.important.is_(True)
    if v == "scheduled":
        return text(
            "EXISTS (SELECT 1 FROM outbox o WHERE o.conversation_id = messages.conversation_id "
            "AND o.status IN ('queued','awaiting_approval'))"
        )
    role = {"spam": "junk", "junk": "junk"}.get(v, v)
    if role in ("junk", "trash"):
        ctx.include_spam_trash = True
    if role in ctx.by_role:
        return Message.label_ids.contains([ctx.by_role[role].id])
    ids = ctx.label_ids(v)
    return Message.label_ids.overlap(ids) if ids else false()


def _is(ctx: Ctx, v: str) -> ColumnElement[bool]:
    if v == "unread":
        return Message.seen.is_(False)
    if v == "read":
        return Message.seen.is_(True)
    if v == "starred":
        return Message.flagged.is_(True)
    if v == "important":
        return Message.important.is_(True)
    if v == "snoozed":
        return _in(ctx, "snoozed")
    if v == "muted":
        return text(
            "EXISTS (SELECT 1 FROM conversations c WHERE c.id = messages.conversation_id AND c.muted)"
        )
    if v == "vip":  # from a VIP sender (Settings → Mail → Inbox)
        return text("lower(messages.from_email) IN (SELECT lower(jsonb_array_elements_text(COALESCE(u.settings->'vip', "
                    "'[]'::jsonb))) FROM users u WHERE u.id = messages.user_id)")
    # mail.follow_up: is:nudged = came back with no reply; is:follow_up = a reminder is set.
    if v in ("nudged", "no_reply", "noreply"):
        return text("EXISTS (SELECT 1 FROM conversations c WHERE c.id = messages.conversation_id "
                    "AND c.nudged_at IS NOT NULL)")
    if v in ("follow_up", "followup", "follow-up"):
        return text("EXISTS (SELECT 1 FROM conversations c WHERE c.id = messages.conversation_id "
                    "AND c.follow_up_at IS NOT NULL)")
    if v in ("draft", "drafts"):
        return Message.draft.is_(True)
    if v in ("answered", "replied"):
        return Message.answered.is_(True)
    # Triage signals (intel/classify.py smart decisions), shared with orders and routines.
    if v in ("needs_reply", "needsreply", "needs-reply"):
        return and_(_signal("needs_reply"), Message.answered.is_(False), Message.sent_by_me.is_(False))
    if v in ("waiting", "waiting_on", "waiting-on"):
        return _signal("waiting_on")
    return false()


def _signal(target: str) -> ColumnElement[bool]:
    return text(
        "EXISTS (SELECT 1 FROM label_decisions d WHERE d.message_id = messages.id AND d.kind = 'smart' "
        "AND d.target = :sig AND d.status = 'applied' AND d.value)"
    ).bindparams(bindparam("sig", target, unique=True))


_GROUP_WORDS = {"OR", "|", "AND", "(", ")", "{", "}", "-"}


def _distribute(toks: list[str]) -> list[str]:
    """Rewrite `op:( a OR b )` / `op:{ a b }` as `( op:a OR op:b )` / `{ op:a op:b }`.
    A bare `op:` with nothing after it is dropped (it would otherwise match everything)."""
    out: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        m = re.fullmatch(r"(-?)([A-Za-z_]+):", t)
        if not m:
            out.append(t)
            i += 1
            continue
        neg, op = m.group(1), m.group(2)
        if i + 1 < len(toks) and toks[i + 1] in ("(", "{"):
            opener = toks[i + 1]
            closer = ")" if opener == "(" else "}"
            depth, j = 0, i + 1
            inner: list[str] = []
            while j < len(toks):
                if toks[j] in ("(", "{"):
                    depth += 1
                elif toks[j] in (")", "}"):
                    depth -= 1
                    if depth == 0:
                        break
                inner.append(toks[j])
                j += 1
            body = []
            for x in _distribute(inner[1:]):
                if x.upper() in _GROUP_WORDS or ":" in x.lstrip("-"):
                    body.append(x)
                elif x.startswith("-") and len(x) > 1:
                    body.append(f"-{op}:{x[1:]}")
                else:
                    body.append(f"{op}:{x}")
            if neg:
                out.append("-")
            out += [opener, *body, closer]
            i = j + 1
            continue
        i += 1  # dangling `op:`
    return out


def _tokens(q: str) -> list[str]:
    return _distribute([t for t in _TOKEN.findall(q) if t.strip()])


def _parse_expr(ctx: Ctx, toks: list[str], i: int, closer: str | None) -> tuple[Any, int]:
    """AND of OR-groups until closer. '{a b}' means OR of a, b."""
    terms: list[Any] = []
    pending_or = False
    while i < len(toks):
        t = toks[i]
        if t == closer:
            return _and(terms), i + 1
        if t in ("(", "{") or (t == "-" and i + 1 < len(toks) and toks[i + 1] in ("(", "{")):
            negate = t == "-"
            if negate:
                i += 1
                t = toks[i]
            node, i = _parse_expr(ctx, toks, i + 1, ")" if t == "(" else "}")
            if negate:
                node = not_(_bool(node))
        elif t.upper() in ("OR", "|"):
            pending_or = True
            i += 1
            continue
        elif t.upper() == "AND" or re.fullmatch(r"AROUND\d*", t.upper()) or t == "-":
            i += 1
            continue
        else:
            node = _term(ctx, t)
            i += 1
        if closer == "}":
            pending_or = True if terms else pending_or
        if pending_or and terms:
            terms[-1] = or_(terms[-1], node)
            pending_or = False
        else:
            terms.append(node)
    return _and(terms), i


def _and(terms: list[Any]) -> ColumnElement[bool]:
    if not terms:
        return true()
    return and_(*terms) if len(terms) > 1 else terms[0]


MAX_QUERY = 2000
MAX_TOKENS = 200
MAX_DEPTH = 20


def compile_query(q: str, user_id: int, labels: list[Label], *, local_ok: bool = True,
                  connector_ids: list[int] | None = None, zone: tzinfo | None = None) -> Compiled:
    """`local_ok` False: recording:<text> skips the stored summaries of local-only Speakr accounts,
    and account: names no local-only account (a token that is not local-only;
    RECORDING_SUMMARIES_LOCAL_OK covers the assistant). `connector_ids`: the accounts a limited
    token may see, the only ones account: can name. `zone`: the user's (`query_zone`)."""
    local_ok = local_ok and RECORDING_SUMMARIES_LOCAL_OK.get()
    ctx = Ctx(user_id, labels, zone)
    ctx.recording_local_ok = local_ok
    ctx.connector_ids = list(connector_ids) if connector_ids else None
    # NUL cannot be sent to Postgres in a text value.
    q = (q or "")[:MAX_QUERY].replace("\x00", "")
    toks = _tokens(q)
    if len(toks) > MAX_TOKENS:
        raise ValueError(f"search has too many terms (at most {MAX_TOKENS})")
    depth = best = 0
    for t in toks:
        if t in ("(", "{"):
            depth += 1
            best = max(best, depth)
        elif t in (")", "}"):
            depth -= 1
    if best > MAX_DEPTH:
        raise ValueError(f"search is nested too deeply (at most {MAX_DEPTH} levels)")
    try:
        expr, _ = _parse_expr(ctx, toks, 0, None)
    except ValueError:
        # Malformed operator value: fall back to plain full-text on the raw query.
        ctx = Ctx(user_id, labels, zone)
        ctx.recording_local_ok = local_ok
        ctx.connector_ids = list(connector_ids) if connector_ids else None
        expr = _positive(ctx, f'"{q}"')
    return Compiled(where=expr, includes_spam_trash=ctx.include_spam_trash, words=ctx.words)


def conversation_ids_query(user_id: int, compiled: Compiled, labels: list[Label]):
    """SELECT conversation_id FROM messages WHERE <query>, excluding spam/trash by default."""
    conds = [Message.user_id == user_id, Message.deleted.is_(False), compiled.where]
    if not compiled.includes_spam_trash:
        hidden = [lb.id for lb in labels if lb.role in ("junk", "trash")]
        if hidden:
            conds.append(not_(Message.label_ids.overlap(hidden)))
    return select(Message.conversation_id).where(*conds).distinct()


def own_text_match(word: str) -> ColumnElement[bool]:
    """A plain word as it matches a message's own text (subject, sender, recipients, file names,
    body), leaving out the emails attached to it."""
    tsq = func.websearch_to_tsquery("simple", word)
    like = f"%{word}%"
    return or_(Message.tsv.op("@@")(tsq), Message.subject.ilike(like), SENDER_TEXT.ilike(like))


async def attached_only_matches(db, user_id: int, conv_ids: list[int], words: list[str]) -> set[int]:
    """The conversations among conv_ids where a searched word matches only inside an attached
    email (never in a message's own text): the list says the match is in an attached email."""
    if not conv_ids or not words:
        return set()
    base = [Message.user_id == user_id, Message.deleted.is_(False), Message.conversation_id.in_(conv_ids)]
    out: set[int] = set()
    for word in words[:8]:
        own = select(Message.conversation_id).where(*base, own_text_match(word))
        rows = await db.execute(
            select(Message.conversation_id).where(
                *base, Message.attached_tsv.is_not(None),
                Message.attached_tsv.op("@@")(func.websearch_to_tsquery("simple", word)),
                not_(Message.conversation_id.in_(own))).distinct())
        out.update(r[0] for r in rows)
    return out


def plain_words(q: str) -> list[str]:
    """The plain words and phrases a search looks for (no operators, no negated words)."""
    out = []
    for t in _tokens((q or "")[:MAX_QUERY].replace("\x00", ""))[:MAX_TOKENS]:
        if t in ("(", ")", "{", "}", "-") or t.upper() in ("OR", "AND", "|") or t.startswith("-"):
            continue
        if ":" in t and not t.startswith('"'):
            continue
        w = _unquote(t)
        if w:
            out.append(w)
    return out
