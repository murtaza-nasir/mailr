"""Which of the user's accounts and addresses received a message (the reader's message details).

Two parts:

- At ingest, `envelope_recipients` reads the receiving addresses from the raw headers into
  Message.delivered_to, in this order: every Delivered-To, X-Original-To, Envelope-To and
  X-Envelope-To; only when none of these is present, the `for <address>` clause of the top-most
  Received headers (the hops the user's own receivers stamped; lower ones any sender can write).
  As with Authentication-Results (core/mime._auth_results), the headers are read once and the
  result is stored; `mailr delivered-to-backfill` re-reads stored mail (core/reparse.py).
- On read, `Receipt` (one per request) turns the stored list into what the details show:
  `account`, the mail accounts the message came through (Message.connector_id and the copies in
  Message.upstream["copies"]); `delivered_to`, the stored addresses that are the user's own (their
  accounts' addresses, send-as identities and the addresses those answer for, the sign-in and store
  addresses, confirmed own addresses), else the first one; and `received_bcc`, true when the user received
  a message whose To and Cc name none of those addresses.

The headers are untrusted. Only text that reads as one plain address is kept (no spaces, angle
brackets, quotes or control characters), at most MAX_ADDRESS_LEN characters, at most
MAX_DELIVERED addresses, from at most MAX_HEADERS values per header name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.message import Message as EmailMessage
from email.utils import getaddresses
from typing import Any

from mailr.core import fromline

MAX_DELIVERED = 10
MAX_ADDRESS_LEN = 254
MAX_HEADERS = 20
MAX_HEADER_LEN = 2000
# The top-most Received headers read for a `for` clause: the user's provider stamps one to three.
RECEIVED_HOPS = 3
ENVELOPE_HEADERS = ("Delivered-To", "X-Original-To", "Envelope-To", "X-Envelope-To")

_ADDRESS = re.compile(r"[^\s@<>()\[\],;:\"\\]{1,64}@[^\s@<>()\[\],;:\"\\]{1,253}")
_COMMENT = re.compile(r"\([^()]*\)")
_FOR = re.compile(r"(?i)(?:^|\s)for\s+<?([^\s<>;()]{3,300})>?")


def clean(addr: str | None) -> str | None:
    """One address as stored (lower case, no invisible characters), or None for anything that does
    not read as one plain address."""
    s = str(addr or "").strip().strip("<>")
    if re.search(r"\s", s):
        return None  # "mailing list foo@example.org" (Google Groups) names no mailbox
    a = fromline.address(s)
    if not a or len(a) > MAX_ADDRESS_LEN or not _ADDRESS.fullmatch(a):
        return None
    if a.startswith("@") or a.endswith((".", "@")):
        return None
    return a


def _values(msg: EmailMessage, name: str) -> list[str]:
    """The raw values of one header, top-most first, without the structured parser (which raises on
    some malformed values)."""
    out: list[str] = []
    try:
        items = msg.raw_items()  # type: ignore[attr-defined]
    except AttributeError:
        items = msg.items()
    for k, v in items:
        if str(k).lower() == name.lower():
            v = str(v)
            try:  # raw 8-bit bytes come as surrogate escapes: read them as UTF-8
                v = v.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
            except UnicodeError:
                v = v.encode("utf-8", "replace").decode("utf-8")
            out.append(re.sub(r"[\r\n]+", " ", v)[:MAX_HEADER_LEN])
            if len(out) >= MAX_HEADERS:
                break
    return out


def received_for(value: str) -> str | None:
    """The address in a Received header's `for` clause: before the date (after the last ";"), with
    comments removed."""
    head = value.rsplit(";", 1)[0] if ";" in value else value
    for _ in range(5):  # nested comments, inside out; bounded
        stripped = _COMMENT.sub(" ", head)
        if stripped == head:
            break
        head = stripped
    m = _FOR.search(head)
    return clean(m.group(1)) if m else None


def envelope_recipients(msg: EmailMessage) -> list[str]:
    """The receiving addresses named by the headers, in the order of ENVELOPE_HEADERS, then (only
    when those name none) the top-most Received headers' `for` clauses."""
    out: list[str] = []
    for name in ENVELOPE_HEADERS:
        for v in _values(msg, name):
            try:
                pairs = getaddresses([v])[:MAX_DELIVERED]
            except Exception:  # noqa: BLE001  a malformed value names nobody
                pairs = []
            for _, a in pairs:
                c = clean(a)
                if c and c not in out:
                    out.append(c)
    if not out:
        for v in _values(msg, "Received")[:RECEIVED_HOPS]:
            c = received_for(v)
            if c and c not in out:
                out.append(c)
    return out[:MAX_DELIVERED]


def base(addr: str) -> str:
    """The address without a `+tag` subaddress: user+news@example.org is user@example.org."""
    local, _, domain = addr.lower().rpartition("@")
    return f"{local.split('+', 1)[0] or local}@{domain}" if local else addr.lower()


@dataclass
class Mine:
    """The user's own addresses, compared without `+tag`; `domains` from `*@domain` entries."""

    exact: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)

    def add(self, addr: str | None) -> None:
        a = str(addr or "").strip().lower()
        if a.startswith("*@") and len(a) > 2:
            self.domains.add(a[2:])
        elif "@" in a:
            self.exact.add(base(a))

    def has(self, addr: str | None) -> bool:
        a = str(addr or "").lower()
        if "@" not in a:
            return False
        return base(a) in self.exact or a.rpartition("@")[2] in self.domains

    @property
    def known(self) -> bool:
        return bool(self.exact or self.domains)


def delivered(stored: list[str] | None, mine: Mine) -> list[str]:
    """The stored receiving addresses that are the user's own; when none is, the first one."""
    lst = [a for a in (stored or []) if isinstance(a, str)][:MAX_DELIVERED]
    own = [a for a in lst if mine.has(a)]
    return own or lst[:1]


def is_bcc(m: Any, mine: Mine) -> bool:
    """The user received this message without being named in To or Cc: neither a receiving address
    nor one of the user's own addresses is there. Never for mail the user sent, drafts or list mail
    (a list's To names the list), nor when nothing says which addresses are the user's."""
    if m.sent_by_me or m.draft or m.list_id:
        return False
    stored = [a for a in (m.delivered_to or []) if isinstance(a, str)]
    if not stored and not mine.known:
        return False
    named = {base(str(a.get("email") or "")) for a in [*(m.to or []), *(m.cc or [])] if isinstance(a, dict)}
    named.discard("")
    if any(mine.has(a) for a in named):
        return False
    return not any(base(a) in named for a in stored)


def connector_ids(m: Any) -> list[int]:
    """The accounts a message came through: its own, then those of its upstream copies."""
    out: list[int] = []
    up = m.upstream if isinstance(getattr(m, "upstream", None), dict) else {}
    refs = [up, *[c for c in (up.get("copies") or []) if isinstance(c, dict)]] if up else []
    for v in [m.connector_id, *[r.get("connector") for r in refs]]:
        try:
            i = int(v) if v is not None else None
        except (TypeError, ValueError):
            i = None
        if i is not None and i not in out:
            out.append(i)
    return out


@dataclass
class Receipt:
    """What message_out needs to say how a message reached the user, read once per request."""

    mine: Mine
    # {connector id: {"id", "name", "address", "kind"}} of the mail connectors the caller may see.
    accounts: dict[int, dict[str, Any]] = field(default_factory=dict)

    def account(self, m: Any) -> list[dict[str, Any]]:
        if m.sent_by_me or m.draft:
            ids = [m.connector_id] if m.connector_id in self.accounts else []
        else:
            ids = connector_ids(m)
        return [self.accounts[i] for i in ids if i in self.accounts]

    def fields(self, m: Any) -> dict[str, Any]:
        return {"account": self.account(m), "delivered_to": delivered(m.delivered_to, self.mine),
                "received_bcc": is_bcc(m, self.mine)}


async def own_addresses(db, user_id: int) -> Mine:
    """The user's own addresses: sign-in and store addresses, send-as identities and the addresses
    they answer for, every mail account's addresses, confirmed own addresses."""
    from sqlalchemy import select

    from mailr.core.mailaccounts import account_addresses
    from mailr.models import Identity, TrustedAddress, User

    mine = Mine()
    user = await db.get(User, user_id)
    if user is not None:
        mine.add(user.email)
        mine.add(user.stalwart_login)
    for email, receives in (await db.execute(
            select(Identity.email, Identity.receives).where(Identity.user_id == user_id))).all():
        mine.add(email)
        for r in receives or []:
            mine.add(r)
    for addrs in (await account_addresses(db, user_id)).values():
        for a in addrs:
            mine.add(a)
    for (email,) in (await db.execute(select(TrustedAddress.email).where(
            TrustedAddress.user_id == user_id, TrustedAddress.confirmed_at.is_not(None)))).all():
        mine.add(email)
    return mine


async def receipt(db, p) -> Receipt:
    """The Receipt for this caller: their own addresses and the mail connectors they may see."""
    from sqlalchemy import select

    from mailr.core.mailaccounts import address_of
    from mailr.models import Connector, Identity

    rows = list((await db.execute(select(Connector).where(
        Connector.user_id == p.user_id, Connector.kind != "caldav").order_by(Connector.id))).scalars())
    idents = list((await db.execute(select(Identity).where(Identity.user_id == p.user_id))).scalars()) if rows else []
    accounts: dict[int, dict[str, Any]] = {}
    for c in rows:
        if not p.can_see_connector(c):
            continue
        addr = address_of(c, idents)
        accounts[c.id] = {"id": c.id, "name": c.name, "address": addr if "@" in addr else None, "kind": c.kind}
    return Receipt(mine=await own_addresses(db, p.user_id), accounts=accounts)
