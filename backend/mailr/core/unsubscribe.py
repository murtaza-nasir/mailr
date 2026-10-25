"""List-Unsubscribe (RFC 2369) with one-click POST (RFC 8058).

Order of preference:
1. one-click: an https URL plus `List-Unsubscribe-Post: List-Unsubscribe=One-Click` → mailr
   POSTs that exact body (no cookies, no redirects, through the outbound guard);
2. mailto: a message to the list's unsubscribe address, sent through the send gate from the
   identity that received the mail. The subject and body are always "unsubscribe" (never the
   header's ?subject=&body=), and the address must be in the sender's or the List-Id's
   registrable domain; otherwise the mailto is not used;
3. a plain web link: returned for the user to open; mailr never "visits" arbitrary pages.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import re
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.store import user_store
from mailr.models import Message, User

_ITEM = re.compile(r"<\s*([^>]+?)\s*>")
# Second-level labels under which registrations happen (co.uk, com.au, ac.jp, …). Without a
# public-suffix list this covers the common cases; anything else uses the last two labels.
_SLD = {"co", "com", "net", "org", "ac", "edu", "gov", "or", "ne", "go", "gob", "nic", "ltd", "plc", "sch", "mil"}


def registrable_domain(domain: str) -> str:
    labels = [x for x in (domain or "").lower().strip(". ").split(".") if x]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SLD:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _list_domain(list_id: str | None) -> str:
    m = _ITEM.search(list_id or "")
    return (m.group(1) if m else (list_id or "")).strip().lower()


def mailto_allowed(m: Message, address: str) -> bool:
    """The unsubscribe address must belong to the sender's or the list's registrable domain."""
    dom = registrable_domain(address.rsplit("@", 1)[-1]) if "@" in address else ""
    if not dom:
        return False
    own = {registrable_domain((m.from_email or "").rsplit("@", 1)[-1])}
    if m.list_id:
        own.add(registrable_domain(_list_domain(m.list_id)))
    return dom in {d for d in own if d}


def mailto_address(url: str) -> str:
    return unquote(urlsplit(url).path).strip().lower()


def _parse(header: str) -> tuple[list[str], list[str]]:
    https, mailtos = [], []
    for item in _ITEM.findall(header or ""):
        low = item.lower()
        if low.startswith("https://"):
            https.append(item)
        elif low.startswith("mailto:"):
            mailtos.append(item)
        elif low.startswith("http://"):
            https.append(item)
    return https, mailtos


def _choose(m: Message, header: str, post: str) -> dict[str, Any]:
    https, mailtos = _parse(header)
    one_click = "list-unsubscribe=one-click" in (post or "").lower().replace(" ", "")
    if one_click and any(u.lower().startswith("https://") for u in https):
        return {"method": "one_click", "url": next(u for u in https if u.lower().startswith("https://"))}
    ok = [u for u in mailtos if mailto_allowed(m, mailto_address(u))]
    if ok:
        return {"method": "mailto", "mailto": ok[0], "to": mailto_address(ok[0])}
    if https:
        return {"method": "link", "url": https[0]}
    if mailtos:
        return {"method": "refused", "to": mailto_address(mailtos[0]),
                "reason": "the unsubscribe address is not in the sender's or the list's domain"}
    return {"method": "none"}


def method_of(m: Message, header: str | None, post: str | None) -> str | None:
    """The stored summary of a message's List-Unsubscribe (Message.unsub_method): one_click,
    mailto, link or none (a header nothing can use); None without the header. Set at ingest from
    the parsed headers, and again whenever plan() reads the message from the store."""
    if not (header or "").strip():
        return None
    method = _choose(m, header or "", post or "")["method"]
    return {"refused": "none"}.get(method, method)


async def plan(db: AsyncSession, user: User, m: Message) -> dict[str, Any]:
    header, post = m.list_unsubscribe or "", ""
    try:
        store = await user_store(db, user)
        raw = await store.get_raw(m)
        head = email.message_from_bytes(raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n", policy=email.policy.default)
        header = str(head.get("List-Unsubscribe", header) or header)
        post = str(head.get("List-Unsubscribe-Post", "") or "")
        # The stored method follows what the message really offers (the subscriptions page shows
        # it; mail stored before the column only knew the indexed header).
        known = method_of(m, header, post)
        if known != m.unsub_method:
            m.unsub_method = known
    except Exception:  # noqa: BLE001, S110
        pass
    return _choose(m, header, post)


async def execute(db: AsyncSession, p, m: Message, pl: dict[str, Any]) -> dict[str, Any]:
    if pl["method"] == "one_click":
        from mailr.core import egress as content
        from mailr.core.imageproxy import instance_config
        from mailr.core.netguard import UnsafeTarget

        # A content fetch (core/egress.py): through the egress proxy when one is set (the sender
        # does not learn mailr's address, and mailr does not resolve the name), otherwise
        # pinned to the checked public address, and not at all while a required egress is not
        # usable. The answer is a status.
        _, egress = await instance_config(db)
        try:
            egress = await content.gate(egress)
            await content.check_url(pl["url"], egress)
            async with asyncio.timeout(20), content.client(egress, timeout=15) as c:
                # Only the status matters: the body is never read.
                async with c.stream("POST", pl["url"], content=b"List-Unsubscribe=One-Click",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"}) as r:
                    status = r.status_code
            return {"method": "one_click", "ok": status < 400, "status": status}
        except UnsafeTarget as e:
            return {"method": "one_click", "ok": False, "error": str(e)[:300]}
        except (httpx.HTTPError, TimeoutError) as e:
            return {"method": "one_click", "ok": False, "error": f"the unsubscribe server could not be reached "
                                                                 f"({type(e).__name__})"}
    if pl["method"] == "mailto":
        from mailr.core.drafts import pick_identity
        from mailr.core.sendgate import SendGate

        addr = mailto_address(pl["mailto"])
        if not mailto_allowed(m, addr):
            return {"method": "mailto", "ok": False, "error": "the unsubscribe address is not in the sender's domain"}
        ident = await pick_identity(db, p.user_id, m)
        # Never the header's ?subject=&body=: the text is ours, not the sender's.
        payload = {"identity_id": ident.id if ident else None, "to": [{"email": addr, "name": ""}],
                   "subject": "unsubscribe", "text": "unsubscribe"}
        # A server-made message: the list's address never becomes one the user has written to.
        ob = await SendGate(db, p).submit(payload, system={"unsubscribe": True})
        return {"method": "mailto", "ok": True, "outbox_id": ob.id, "to": addr}
    return pl
