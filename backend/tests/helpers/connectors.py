"""Shared helpers, from test_connectors.py."""

from __future__ import annotations

import uuid

import httpx
import imapclient
from conftest import mail_date
from sqlalchemy import select, text

from mailr.connectors.loader import connector_for
from mailr.connectors.runner import RunnerContext
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Job

UP_JMAP = "http://localhost:18091"


UP_SVC = ("up-svc@upstream.test", "up-svc-pass")


MGMT = ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]


IMAP_HOST, IMAP_PORT = "localhost", 12143


def _jmap(calls):
    r = httpx.post(f"{UP_JMAP}/jmap/", json={"using": MGMT, "methodCalls": calls}, auth=UP_SVC, timeout=30)
    r.raise_for_status()
    return r.json()["methodResponses"]


def make_upstream_account() -> tuple[str, str]:
    """A fresh account on the test upstream server. The server keeps every account from earlier runs,
    so the name has 16 random hex digits (6 collided now and then: primaryKeyViolation), and a
    collision is retried with a new name."""
    dom = _jmap([["x:Domain/query", {"filter": {"name": "upstream.test"}}, "q"]])[0][1]["ids"][0]
    pw = "Upstream-pass-123"
    for _ in range(3):
        local = f"alice{uuid.uuid4().hex[:16]}"
        res = _jmap([["x:Account/set", {"create": {"a": {"@type": "User", "name": local, "domainId": dom,
                                                          "credentials": {"0": {"@type": "Password", "secret": pw}}}}},
                      "c"]])
        if "a" in res[0][1].get("created", {}):
            return f"{local}@upstream.test", pw
        if (res[0][1].get("notCreated") or {}).get("a", {}).get("type") != "primaryKeyViolation":
            break
    raise AssertionError(res)


def imap(user, pw) -> imapclient.IMAPClient:
    c = imapclient.IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=False, timeout=30)
    c.login(user, pw)
    return c


def msg(subject, frm="Bob <bob@example.org>", to="alice@upstream.test", msgid=None, irt=None, body="Hello",
        date=mail_date()):
    msgid = msgid or f"<{uuid.uuid4().hex}@example.org>"
    h = [f"From: {frm}", f"To: {to}", f"Subject: {subject}", f"Message-ID: {msgid}", f"Date: {date}",
         "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8"]
    if irt:
        h += [f"In-Reply-To: {irt}", f"References: {irt}"]
    return ("\r\n".join(h) + "\r\n\r\n" + body + "\r\n").encode()


async def run_sync(cid: int):
    async with sessionmaker()() as db:
        row = await db.get(Connector, cid)
        conn = await connector_for(db, row)
        ctx = RunnerContext(cid, row.user_id, dict(row.state or {}))
    try:
        return await conn.sync_once(ctx)
    finally:
        if hasattr(conn, "_close"):
            await conn._close()


async def run_jobs(kind: str, user_id: int):
    """Run queued jobs of one kind directly (tests do not start the worker)."""
    import mailr.connectors.tasks  # noqa: F401
    from mailr.core.jobs import _HANDLERS

    async with session_scope() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == kind, Job.user_id == user_id,
                                                   Job.status == "queued"))).scalars().all()
        payloads = [(j.id, j.payload) for j in jobs]
        for j in jobs:
            j.status = "done"
    for _, pl in payloads:
        await _HANDLERS[kind](pl, None)
    return len(payloads)


async def create_connector(client, email, pw, mode="store", smtp=None, name=None):
    body = {"kind": "imap", "name": name or email, "mode": mode,
            "config": {"email": email, "imap": {"host": IMAP_HOST, "port": IMAP_PORT, "tls": "none"},
                       "smtp": smtp or {}},
            "secrets": {"password": pw}}
    r = await client.post("/api/v1/connectors", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def msg_count(user_id):
    async with sessionmaker()() as db:
        return (await db.execute(text("SELECT count(*) FROM messages WHERE user_id=:u AND NOT deleted AND NOT draft"),
                                 {"u": user_id})).scalar_one()
