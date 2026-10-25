"""Shared helpers, from test_mail_accounts.py."""

from helpers.audit_fixes import _ingest
from mailr.db import session_scope
from mailr.models import Connector, Identity


async def _account(user_id, address, *, data_class="normal", identity=True):
    async with session_scope() as db:
        c = Connector(user_id=user_id, kind="imap", name=f"Mail {address}", mode="mirror", data_class=data_class,
                      config={"email": address, "smtp": {"host": "smtp.example.org"}}, state={}, enabled=False)
        db.add(c)
        await db.flush()
        if identity:
            db.add(Identity(user_id=user_id, email=address, name="Me", connector_id=c.id, is_default=False))
        return c.id


async def _ids(c, q: str = "", view: str = "all") -> set[int]:
    r = await c.get("/api/v1/conversations", params={"view": view, "limit": 200, **({"q": q} if q else {})})
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


async def _two_accounts(user, raw):
    a = await _account(user.id, "work@example.org")
    b = await _account(user.id, "home@example.net")
    ids = {}
    _, ids["a1"], _ = await _ingest(user, raw("Quarterly numbers", to="work@example.org"), a)
    _, ids["a2"], _ = await _ingest(user, raw("Team lunch", to="work@example.org"), a)
    _, ids["b1"], _ = await _ingest(user, raw("Garden club", to="home@example.net"), b)
    # Sent from the second account's address, with no account of its own (a phone saved it).
    _, ids["b_sent"], _ = await _ingest(user, raw("Weekend trip", frm="Me <home@example.net>", to="friend@example.com"),
                                        roles=["sent"])
    _, ids["none"], _ = await _ingest(user, raw("Loose mail", to="other@example.com"))
    return a, b, ids
