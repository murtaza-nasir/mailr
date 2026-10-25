"""The user's mail accounts as the mailbox shows them.

One mail account is one mail connector (IMAP, Gmail, Microsoft 365). A file import is a one-time
copy with no address, so it is not an account here. The mailbox is unified: every list holds the
mail of every account. An account's own folders (the setting "Show each account separately") and
the `account:` search operator narrow a list to the mail of one account:

- mail that arrived through the account (`Message.connector_id`), and
- mail sent from one of its addresses (its send-as identities), drafts included.

A conversation belongs to an account when one of its messages does, so a thread that went through
two accounts is in both. Each account has a colour, for the dot on list rows and in the
navigation: a hue from ACCOUNT_HUES that the apps draw in the light or the dark theme. The user
picks one on the account's card; until then the first hue no other account uses is taken, in the
order the accounts were added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import Connector, Conversation, Identity, Message

MAIL_ACCOUNT_KINDS = ("imap", "gmail", "m365")
# The palette of account colours, in the order they are handed out. The web (lib/accountColours.ts)
# and Android (AccountColours.kt) draw each in a light and a dark tone.
ACCOUNT_HUES = ("blue", "orange", "green", "purple", "pink", "teal", "red", "yellow")
# The folders an account has of its own: the unified views that make sense per account.
ACCOUNT_VIEWS = ("inbox", "sent", "drafts", "scheduled", "spam", "trash")


@dataclass
class MailAccount:
    id: int
    address: str
    name: str
    color: str
    addresses: list[str] = field(default_factory=list)
    # The send-as identity Compose picks in this account's folders: the default one, else the first.
    identity_id: int | None = None
    local_only: bool = False

    def out(self) -> dict[str, Any]:
        return {"id": self.id, "address": self.address, "name": self.name, "color": self.color,
                "addresses": self.addresses, "identity_id": self.identity_id, "local_only": self.local_only}


def address_of(c: Connector, identities: list[Identity]) -> str:
    """The address an account is known by: its login address, else its first send-as address,
    else its name."""
    cfg = c.config or {}
    for v in (cfg.get("email"), (cfg.get("imap") or {}).get("username")):
        if isinstance(v, str) and "@" in v:
            return v.strip().lower()
    mine = [i for i in identities if i.connector_id == c.id]
    if mine:
        return mine[0].email.lower()
    return c.name


def normalize_color(value: str | None) -> str | None:
    """The stored form of an account colour (None goes back to the automatic one). ValueError
    for anything but a hue of ACCOUNT_HUES."""
    if value is None or value == "":
        return None
    v = str(value).strip().lower()
    if v not in ACCOUNT_HUES:
        raise ValueError(f"colour must be one of {', '.join(ACCOUNT_HUES)}")
    return v


def assign_colors(connectors: list[Connector]) -> dict[int, str]:
    """{connector id: hue}: the chosen hue, else the first hue no other account holds (cycling
    once all are taken), in the order the accounts were added."""
    chosen = {c.id: (c.config or {}).get("color") for c in connectors}
    taken = [v for v in chosen.values() if v in ACCOUNT_HUES]
    out: dict[int, str] = {}
    n = 0
    for c in sorted(connectors, key=lambda c: c.id):
        v = chosen[c.id]
        if v in ACCOUNT_HUES:
            out[c.id] = v
            continue
        free = [h for h in ACCOUNT_HUES if h not in taken]
        pick = free[0] if free else ACCOUNT_HUES[n % len(ACCOUNT_HUES)]
        n += 1
        taken.append(pick)
        out[c.id] = pick
    return out


async def mail_accounts(db: AsyncSession, p) -> list[MailAccount]:
    """The mail accounts the caller may see, in the order they were added. Colours are handed out
    over all of the user's accounts, so a token limited to some sees the same colours. Read once
    per request after memo_accounts (the sidebar counts build the conditions of every account's
    folders)."""
    memo = db.info.get(_MEMO)
    key = (p.user_id, p.token.id if p.token is not None else None)
    if memo is not None and key in memo:
        return memo[key]
    out = await _load(db, p)
    if memo is not None:
        memo[key] = out
    return out


_MEMO = "mailr.mail_accounts_memo"


def memo_accounts(db: AsyncSession) -> None:
    """Read the accounts once for the rest of this request. Only for requests that do not change
    accounts."""
    db.info.setdefault(_MEMO, {})


async def _load(db: AsyncSession, p) -> list[MailAccount]:
    uid = p.user_id
    rows = list((await db.execute(select(Connector).where(
        Connector.user_id == uid, Connector.kind.in_(MAIL_ACCOUNT_KINDS)).order_by(Connector.id))).scalars())
    if not rows:
        return []
    idents = list((await db.execute(select(Identity).where(Identity.user_id == uid).order_by(Identity.id))).scalars())
    colors = assign_colors(rows)
    out = []
    for c in rows:
        if not p.can_see_connector(c):
            continue
        mine = [i for i in idents if i.connector_id == c.id]
        address = address_of(c, idents)
        addrs = list(dict.fromkeys([address, *[i.email.lower() for i in mine]] if "@" in address
                                   else [i.email.lower() for i in mine]))
        default = next((i for i in mine if i.is_default), mine[0] if mine else None)
        out.append(MailAccount(id=c.id, address=address, name=c.name, color=colors[c.id], addresses=addrs,
                               identity_id=default.id if default else None, local_only=c.data_class == "local_only"))
    return out


def account_message_cond(uid: int, ids):
    """Messages of these accounts (`ids`: a list or a subquery of connector ids): arrived through
    one of them, or sent from one of their addresses, the send-as identities and the login address
    (drafts are sent_by_me too). MailAccount.addresses holds the same addresses."""
    sent_from = select(func.lower(Identity.email)).where(Identity.user_id == uid, Identity.connector_id.in_(ids)).union(
        select(func.lower(Connector.config["email"].astext)).where(Connector.user_id == uid, Connector.id.in_(ids)),
        select(func.lower(Connector.config["imap"]["username"].astext)).where(Connector.user_id == uid, Connector.id.in_(ids)))
    return or_(Message.connector_id.in_(ids),
               and_(Message.sent_by_me.is_(True), func.lower(Message.from_email).in_(sent_from)))


def conversation_in_accounts(p, ids: list[int], *, indexed: bool = False):
    """A conversation condition: it holds a message of these accounts that the caller may see.
    The list of an account's folder and its count (core/sidebar.py) both use it. `indexed`: the
    caller sees all of the user's mail and the user's account index is current (`index_current`),
    so the stored `Conversation.account_ids` answers without a sub-query per conversation."""
    if indexed:
        return Conversation.account_ids.overlap(list(ids))
    from mailr.core.visibility import visible_message_filter

    return exists().where(Message.conversation_id == Conversation.id, Message.user_id == p.user_id,
                          Message.deleted.is_(False), account_message_cond(p.user_id, ids),
                          visible_message_filter(p))


# ---------------------------------------------------------------- the account index
#
# Conversation.account_ids holds the mail accounts each conversation belongs to, by the rule of
# account_message_cond: a message that arrived through the account, or that the user sent from one
# of its addresses (not deleted). threading.refresh keeps it for the conversations it touches. The
# accounts and their addresses change rarely (an account or a send-as identity added, removed or
# edited); User.account_index holds a hash of them as they were when the column was last rebuilt
# for the whole mailbox. While the hash differs, lists and counts do not use the column: lists fall
# back to the sub-query, and the per-account numbers wait for the rebuild (inline for a small
# mailbox, else the job `mail.account_index`). Restricted tokens never use it: what they may see
# is per message. (8 Oct 2026: the sub-query per conversation and per account, in thirty count
# views at once, took /counts to two minutes on production.)

# Rebuild inline (in the request) up to this many conversations; above it, a job does it.
INDEX_INLINE_MAX = 5000
INDEX_BATCH = 5000
_INDEX_MEMO = "mailr.account_index_memo"

_ACCOUNT_IDS_SQL = """
SELECT m.conversation_id AS cid, array_agg(DISTINCT x.acct ORDER BY x.acct) AS accts
FROM messages m
CROSS JOIN LATERAL (
    SELECT m.connector_id AS acct WHERE m.connector_id = ANY(CAST(:accts AS int[]))
    UNION ALL
    SELECT a.acct FROM unnest(CAST(:emails AS text[]), CAST(:owners AS int[])) AS a(email, acct)
    WHERE m.sent_by_me AND lower(m.from_email) = a.email
) x
WHERE m.conversation_id = ANY(:ids) AND NOT m.deleted
GROUP BY m.conversation_id
"""


async def account_addresses(db: AsyncSession, uid: int) -> dict[int, list[str]]:
    """{account id: the addresses mail sent from it is recognised by}, for every mail account of
    the user: its send-as identities, its login address and its IMAP user name (lowercased), as
    account_message_cond reads them."""
    rows = list((await db.execute(select(Connector.id, Connector.config).where(
        Connector.user_id == uid, Connector.kind.in_(MAIL_ACCOUNT_KINDS)).order_by(Connector.id))).all())
    out: dict[int, set[str]] = {cid: set() for cid, _ in rows}
    for cid, cfg in rows:
        cfg = cfg or {}
        for v in (cfg.get("email"), (cfg.get("imap") or {}).get("username") if isinstance(cfg.get("imap"), dict) else None):
            if isinstance(v, str) and v.strip():
                out[cid].add(v.strip().lower())
    if out:
        for cid, email in (await db.execute(select(Identity.connector_id, Identity.email).where(
                Identity.user_id == uid, Identity.connector_id.in_(list(out))))).all():
            if email:
                out[cid].add(email.lower())
    return {cid: sorted(v) for cid, v in out.items()}


def index_stamp(addrs: dict[int, list[str]]) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(sorted(addrs.items())).encode()).hexdigest()[:32]


async def account_ids_for(db: AsyncSession, uid: int, conv_ids: list[int],
                          addrs: dict[int, list[str]] | None = None) -> dict[int, list[int]]:
    """{conversation id: its account ids, sorted} for these conversations (missing: none)."""
    from sqlalchemy import text

    if not conv_ids:
        return {}
    if addrs is None:
        addrs = await account_addresses(db, uid)
    if not addrs:
        return {}
    pairs = [(e, a) for a, es in addrs.items() for e in es]
    rows = await db.execute(text(_ACCOUNT_IDS_SQL), {
        "ids": list(conv_ids), "accts": list(addrs), "emails": [e for e, _ in pairs], "owners": [a for _, a in pairs]})
    return {r.cid: list(r.accts) for r in rows}


async def index_current(db: AsyncSession, uid: int) -> bool:
    """Whether Conversation.account_ids is current for the whole mailbox (read once per request)."""
    from mailr.models import User

    memo = db.info.setdefault(_INDEX_MEMO, {})
    if uid in memo:
        return memo[uid]
    stored = (await db.execute(select(User.account_index).where(User.id == uid))).scalar()
    memo[uid] = stored is not None and stored == index_stamp(await account_addresses(db, uid))
    return memo[uid]


async def rebuild_index(db: AsyncSession, uid: int, *, commit_each: bool = False) -> int:
    """Recompute Conversation.account_ids for every conversation of the user, in batches, then
    store the hash. Returns the number of conversations changed. `commit_each` (the job): each
    batch is committed, so a batch's rows are not held locked, ingest and refresh waiting on them,
    until the whole mailbox is done; the hash is stored last, so a rebuild that stops is redone."""
    from sqlalchemy import text, update

    from mailr.models import User

    addrs = await account_addresses(db, uid)
    pairs = [(e, a) for a, es in addrs.items() for e in es]
    changed = after = 0
    while True:
        ids = list((await db.execute(select(Conversation.id).where(
            Conversation.user_id == uid, Conversation.id > after).order_by(Conversation.id).limit(INDEX_BATCH))).scalars())
        if not ids:
            break
        after = ids[-1]
        res = await db.execute(text(
            "UPDATE conversations c SET account_ids = coalesce(n.accts, '{}') "
            "FROM (SELECT unnest(CAST(:ids AS bigint[])) AS id) z "
            f"LEFT JOIN ({_ACCOUNT_IDS_SQL}) n ON n.cid = z.id "
            "WHERE c.id = z.id AND c.account_ids IS DISTINCT FROM coalesce(n.accts, '{}')"),
            {"ids": ids, "accts": list(addrs), "emails": [e for e, _ in pairs], "owners": [a for _, a in pairs]})
        changed += res.rowcount or 0
        if commit_each:
            await db.commit()
    await db.execute(update(User).where(User.id == uid).values(account_index=index_stamp(addrs)))
    if changed > INDEX_BATCH:
        # Fresh statistics for the column, so the planner does not take a rebuilt mailbox for an
        # empty one until autovacuum gets to it (it does not block reads or writes).
        await db.execute(text("ANALYZE conversations (account_ids)"))
    db.info.setdefault(_INDEX_MEMO, {})[uid] = True
    return changed


async def ensure_index(db: AsyncSession, uid: int) -> bool:
    """Make the account index current if that is cheap: rebuilt inline for a mailbox of up to
    INDEX_INLINE_MAX conversations, else the job `mail.account_index` is queued. Either way the
    session is committed. True when it is current now."""
    if await index_current(db, uid):
        return True
    n = (await db.execute(select(func.count()).select_from(Conversation).where(
        Conversation.user_id == uid))).scalar_one()
    if n <= INDEX_INLINE_MAX:
        await rebuild_index(db, uid)
        await db.commit()  # a GET does not commit by itself
        return True
    from mailr.core.jobs import enqueue

    await enqueue(db, "mail.account_index", {"user_id": uid}, user_id=uid, dedupe_key=f"mail.account_index:{uid}")
    await db.commit()
    return False


def parse_view(view: str) -> tuple[int, str] | None:
    """`acct:<id>:<view>` (an account's own folder) → (id, view); None for any other view.
    A label seen through an account is `acct:<id>:label:<label id>` → (id, "label:<label id>").
    ValueError for a malformed one."""
    if not view.startswith("acct:"):
        return None
    parts = view.split(":")
    if len(parts) < 3 or not parts[1].isdigit() or len(parts[1]) > 12:
        raise ValueError(view)
    if len(parts) == 4 and parts[2] == "label" and parts[3].isdigit() and len(parts[3]) <= 12:
        return int(parts[1]), f"label:{parts[3]}"
    if len(parts) != 3 or parts[2] not in ACCOUNT_VIEWS:
        raise ValueError(view)
    return int(parts[1]), parts[2]


def label_view_ids(labels) -> list[tuple[int, int]]:
    """(label id, view label id) pairs: the list `label:X` holds the conversations of X and of the
    labels nested under it (api/mail_read.conversation_conditions), so a label counts towards
    itself and each label above it."""
    own = [lb for lb in labels if not lb.role]
    out = []
    for lb in labels:
        for x in own:
            if lb.id == x.id or lb.name.startswith(x.name + "/"):
                out.append((lb.id, x.id))
    return out


async def conversation_accounts(db: AsyncSession, p, conv_ids: list[int]) -> dict[int, list[int]]:
    """{conversation id: the ids of the caller's mail accounts it belongs to}, for the dots on
    list rows. Empty while the caller sees fewer than two accounts (no dots then)."""
    from mailr.core.visibility import visible_message_filter

    if not conv_ids:
        return {}
    accounts = await mail_accounts(db, p)
    if len(accounts) < 2:
        return {}
    ids = [a.id for a in accounts]
    by_addr: dict[str, int] = {}
    for a in accounts:
        for addr in a.addresses:
            by_addr.setdefault(addr, a.id)
    rows = (await db.execute(select(Message.conversation_id, Message.connector_id, Message.sent_by_me,
                                    func.lower(Message.from_email)).where(
        Message.user_id == p.user_id, Message.conversation_id.in_(conv_ids), Message.deleted.is_(False),
        visible_message_filter(p)).distinct())).all()
    out: dict[int, set[int]] = {}
    for cid, conn, sent, frm in rows:
        if conn in ids:
            out.setdefault(cid, set()).add(conn)
        if sent and frm in by_addr:
            out.setdefault(cid, set()).add(by_addr[frm])
    order = {a: i for i, a in enumerate(ids)}
    return {cid: sorted(s, key=order.__getitem__) for cid, s in out.items()}
