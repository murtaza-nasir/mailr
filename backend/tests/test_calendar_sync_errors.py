"""Calendar sync failures in classes the clients explain (calendar/sync_errors.py): each kind from
the exception a real failure raises (HTTP statuses, transport errors, the network guard), from the
texts stored before kinds existed, and through the APIs that Settings → Calendar & tasks and the
calendar banners read (/connectors, /calendar/sync-status, /calendar/calendars). Also each
calendar's `source`, which the clients group the calendar lists by."""

from __future__ import annotations

import ssl

import httpx
import pytest
from sqlalchemy import select

from mailr.calendar import dav, mirror, phonecal, sync, sync_errors, webcal
from mailr.core import netguard
from mailr.db import sessionmaker
from mailr.models import CalCollection, Connector

BASE = "https://cal.example.org/dav/"


@pytest.fixture
def no_guard(monkeypatch):
    async def no_check(*a, **k):
        return None

    monkeypatch.setattr(dav.netguard, "check_url", no_check)


def _client(handler) -> dav.DavClient:
    return dav.DavClient(BASE, "u", "p", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def _discovery_error(handler) -> dav.DavError:
    """What account discovery (the first step of every sync) raises against this server."""
    async with _client(handler) as c:
        with pytest.raises(dav.DavError) as got:
            await c.home()
    return got.value


@pytest.mark.parametrize(("status", "kind"), [
    (404, "not_found"), (410, "not_found"), (405, "not_found"),
    (401, "auth"), (403, "auth"),
    (500, "server"), (502, "server"), (503, "server"),
    (507, "partial"),
    (418, "unknown"),
])
async def test_http_statuses(no_guard, status, kind):
    e = await _discovery_error(lambda r: httpx.Response(status))
    assert sync_errors.classify(e) == kind
    # The stored text classifies the same way (rows written before kinds existed).
    assert sync_errors.classify_text(str(e)) == kind


def _raises(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc
    return handler


def _expired_certificate() -> httpx.ConnectError:
    cause = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                                            "certificate has expired (_ssl.c:1010)")
    e = httpx.ConnectError(str(cause))
    e.__cause__ = cause
    return e


@pytest.mark.parametrize(("exc", "kind"), [
    (_expired_certificate(), "tls"),
    (httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1010)"), "tls"),
    (httpx.ConnectError("[Errno -2] Name or service not known"), "unreachable"),
    (httpx.ConnectError("[Errno 111] Connection refused"), "unreachable"),
    (httpx.ConnectTimeout("timed out"), "unreachable"),
    (httpx.ReadTimeout("timed out"), "unreachable"),
    (httpx.ReadError("[Errno 104] Connection reset by peer"), "unreachable"),
    (httpx.RemoteProtocolError("Server disconnected without sending a response."), "server"),
])
async def test_transport_failures(no_guard, exc, kind):
    e = await _discovery_error(_raises(exc))
    assert e.kind == kind and sync_errors.classify(e) == kind
    assert type(exc).__name__ in str(e)  # the technical text keeps what happened, for Details
    assert sync_errors.classify_text(str(e)) == kind


@pytest.mark.parametrize(("message", "kind"), [
    ("cannot resolve cal.example.org: [Errno -2] Name or service not known", "unreachable"),
    ("cal.example.org resolves to a private address; this server only connects to public hosts", "blocked"),
    ("cal.example.org resolves to a local or reserved address that Mailr does not connect to", "blocked"),
])
async def test_the_network_guard(monkeypatch, message, kind):
    async def refuse(*a, **k):
        raise netguard.UnsafeTarget(message)

    monkeypatch.setattr(dav.netguard, "check_url", refuse)
    e = await _discovery_error(lambda r: httpx.Response(200))
    assert sync_errors.classify(e) == kind
    assert sync_errors.classify(netguard.UnsafeTarget(message)) == kind


async def test_plain_http_to_an_internet_server_is_blocked(monkeypatch, no_guard):
    monkeypatch.setattr(dav.netguard, "is_private_target", lambda host: False)
    async with dav.DavClient("http://cal.example.org/dav/", "u", "p",
                             http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))) as c:
        with pytest.raises(dav.DavError) as got:
            await c.home()
    assert sync_errors.classify(got.value) == "blocked"


@pytest.mark.parametrize(("text", "kind"), [
    ("PROPFIND https://cal.example.org/dav/: not found", "not_found"),
    ("Sign-in failed. Check the password.", "auth"),
    (sync.TRUNCATED_ERROR, "partial"),
    ("REPORT https://cal.example.org/dav/u/home/: HTTP 503", "server"),
    ("PROPFIND https://cal.example.org/dav/: ConnectError", "unreachable"),
    ("PROPFIND https://cal.example.org/dav/: ConnectTimeout", "unreachable"),
    ("PROPFIND https://cal.example.org/dav/: unreadable XML (syntax error: line 1, column 0)", "server"),
    ("The calendar link answered with an error (HTTP 404).", "not_found"),
    ("The calendar link could not be reached (ConnectError).", "unreachable"),
    ("something nobody planned for", "unknown"),
    ("", None),
    (None, None),
])
def test_stored_texts(text, kind):
    assert sync_errors.classify_text(text) == kind


def test_hosts():
    assert sync_errors.host_of("https://alex@cal.example.org:8443/dav/") == "cal.example.org"
    assert sync_errors.host_of("not a url") is None
    builtin = Connector(kind="caldav", builtin=True, config={})
    assert sync_errors.connector_host(builtin) == sync_errors.host_of(sync_errors_base())


def sync_errors_base() -> str:
    from mailr.config import get_settings

    return get_settings().base_url


def test_a_connector_without_an_error_has_no_problem():
    c = Connector(kind="caldav", status="idle", status_detail="2 calendars", config={"url": BASE}, state={})
    assert sync_errors.connector_problem(c) is None
    c.status, c.status_detail = "error", "PROPFIND https://cal.example.org/dav/: not found"
    assert sync_errors.connector_problem(c) == {"kind": "not_found", "host": "cal.example.org"}
    c.state = {"caldav": {"error_kind": "tls"}}  # the kind the sync stored wins over the text
    assert sync_errors.connector_problem(c)["kind"] == "tls"
    c.state = {"caldav": {"auth_failed": True, "error_kind": "tls"}}
    assert sync_errors.connector_problem(c)["kind"] == "auth"


async def _account(user_id: int, **kw) -> int:
    async with sessionmaker()() as db:
        c = Connector(user_id=user_id, kind="caldav", name="Studio", config={"url": BASE},
                      status="idle", state={"caldav": {"home": BASE + "u/"}}, **kw)
        db.add(c)
        await db.commit()
        return c.id


@pytest.mark.db_user
async def test_a_moved_server_reads_as_not_found_everywhere(client, user, monkeypatch, no_guard):
    """The address no longer serves calendars: the sync stores the kind and the technical text,
    and Settings (/connectors), the banners (/calendar/sync-status) see the kind and the host."""
    cid = await _account(user.id)
    monkeypatch.setattr(sync, "dav_client", lambda conn, user: _client(lambda r: httpx.Response(404)))
    res = await sync.sync_connector(cid)
    assert res.error
    async with sessionmaker()() as db:
        c = await db.get(Connector, cid)
        assert c.status == "error" and c.state["caldav"]["error_kind"] == "not_found"
        assert c.status_detail.endswith(": not found")  # the raw text stays, for Details and Diagnostics
    conns = (await client.get("/api/v1/connectors")).json()
    mine = next(x for x in conns if x["id"] == cid)
    assert mine["error_kind"] == "not_found" and mine["error_host"] == "cal.example.org"
    assert mine["status_detail"].endswith(": not found")
    st = (await client.get("/api/v1/calendar/sync-status")).json()
    got = next(x for x in st["connectors"] if x["id"] == cid)
    assert got["error_kind"] == "not_found" and got["error_host"] == "cal.example.org"

    # The server comes back with a different failure: the kind follows.
    monkeypatch.setattr(sync, "dav_client", lambda conn, user: _client(lambda r: httpx.Response(503)))
    await sync.sync_connector(cid)
    st = (await client.get("/api/v1/calendar/sync-status")).json()
    assert next(x for x in st["connectors"] if x["id"] == cid)["error_kind"] == "server"

    monkeypatch.setattr(sync, "dav_client", lambda conn, user: _client(lambda r: httpx.Response(401)))
    await sync.sync_connector(cid)
    st = (await client.get("/api/v1/calendar/sync-status")).json()
    assert next(x for x in st["connectors"] if x["id"] == cid)["error_kind"] == "auth"


@pytest.mark.db_user
async def test_an_account_with_no_calendars_lists_its_error(client, user, monkeypatch, no_guard):
    """GET /calendar/calendars: an account in error with nothing mirrored shows as one entry with
    the error, its kind and host (it used to fail the response's validation)."""
    cid = await _account(user.id, last_sync_at=None)
    monkeypatch.setattr(sync, "dav_client", lambda conn, user: _client(_raises(_expired_certificate())))
    await sync.sync_connector(cid)
    r = await client.get("/api/v1/calendar/calendars")
    assert r.status_code == 200, r.text
    bad = next(x for x in r.json() if x.get("connector_id") == cid)
    assert bad["error_kind"] == "tls" and bad["error_host"] == "cal.example.org" and bad["name"] == "Studio"
    assert "CERTIFICATE_VERIFY_FAILED" in bad["error"]


@pytest.mark.db_user
async def test_a_calendar_error_carries_its_kind(client, user):
    cid = await _account(user.id)
    async with sessionmaker()() as db:
        db.add(CalCollection(user_id=user.id, connector_id=cid, href=BASE + "u/home/", displayname="Home",
                             components=["VEVENT"], sync_status="backoff", sync_error=sync.TRUNCATED_ERROR))
        await db.commit()
    st = (await client.get("/api/v1/calendar/sync-status")).json()
    col = next(x for x in st["connectors"] if x["id"] == cid)["collections"][0]
    assert col["error_kind"] == "partial"
    async with sessionmaker()() as db:
        coll = (await db.execute(select(CalCollection).where(CalCollection.connector_id == cid))).scalar_one()
        conn = await db.get(Connector, cid)
        assert mirror.collection_out(coll, conn, {})["sync"]["error_kind"] == "partial"


def test_sources():
    """Where each calendar comes from (mirror.source_of), for the grouped lists."""
    account = Connector(id=1, kind="caldav", name="Studio", builtin=False, config={"url": BASE})
    builtin = Connector(id=2, kind="caldav", name="Calendar", builtin=True, config={})
    link = Connector(id=3, kind=webcal.KIND, name="Holidays", builtin=False, config={"url": "https://example.org/h.ics"})
    phone = Connector(id=4, kind=phonecal.KIND, name="Work", builtin=False, config={"device": "Pixel 9"})

    def coll(conn: Connector, href: str) -> CalCollection:
        return CalCollection(connector_id=conn.id, href=href, displayname="X", components=["VEVENT"])

    assert mirror.source_of(coll(account, BASE + "u/a/"), account, {}) == {
        "kind": "account", "name": "Studio", "host": "cal.example.org"}
    assert mirror.source_of(coll(builtin, "https://store.internal/dav/cal/u/b/"), builtin, {}) == {"kind": "builtin"}
    assert mirror.source_of(coll(builtin, "https://store.internal/dav/cal/g/f/"), builtin,
                            {"shared": {"kind": "group", "group": "Family"}}) == {"kind": "shared"}
    assert mirror.source_of(coll(link, webcal.coll_href(3)), link, {}) == {"kind": "link"}
    assert mirror.source_of(coll(phone, phonecal.coll_href("k1")), phone, {}) == {"kind": "phone", "device": "Pixel 9"}
