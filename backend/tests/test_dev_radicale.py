"""Test users in the shared dev Radicale (mailr/dev/radicale.py): which names count as test users,
and that the test session and the e2e seeder delete the users they made."""

import io
import json
import uuid

import httpx
import pytest
from dev_services import radicale_up as _radicale_up

from mailr.dev import radicale

RADICALE = radicale.DEV_RADICALE


needs_radicale = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


def test_test_user_names():
    yes = ["u1a2b3c4d", "u00000000", "e2e0badf00d", "e2e12345678", "alex.rivera-mailr-wt-main-test",
           "alex.rivera-mailr-wt-notes-p4-test-gw11"]
    no = ["uidev", "u1a2b3c4", "u1a2b3c4d5", "U1A2B3C4D", "e2e1234567", "qa", "test", "x", "probea5b8c4",
          "alex.rivera", "alex.rivera-mailr-wt-main-demo", "alex.rivera-mailr-test-demo", "alex.rivera-mailr-wt-main",
          "u1a2b3c4d/..", "../u1a2b3c4d"]
    assert [n for n in yes if not radicale.is_test_user(n)] == []
    assert [n for n in no if radicale.is_test_user(n)] == []


def test_delete_users_only_test_users_and_only_locally(monkeypatch):
    asked: list[str] = []

    def request(self, method, url, **kw):
        asked.append(f"{method} {url}")
        return httpx.Response(200)

    monkeypatch.setattr(httpx.Client, "request", request)
    with pytest.raises(ValueError):
        radicale.delete_users(["u1a2b3c4d"], base="https://cal.example.com/")
    assert radicale.delete_users(["u1a2b3c4d", "qa", "alex.rivera", "e2e12345678"]) == 2
    assert asked == ["DELETE http://localhost:15232/e2e12345678/", "DELETE http://localhost:15232/u1a2b3c4d/"]


@needs_radicale
def test_track_and_delete():
    import caldav

    stop = radicale.track()
    try:
        user = f"u{uuid.uuid4().hex[:8]}"
        cal = caldav.DAVClient(url=RADICALE, username=user, password="x").principal().make_calendar(name="T")
        caldav.DAVClient(url="https://cal.example.com/", username=f"u{uuid.uuid4().hex[:8]}", password="x")
        caldav.DAVClient(url=RADICALE, username="alex.rivera", password="x")
    finally:
        made = stop()
    assert made == {user}
    assert httpx.get(str(cal.url), auth=(user, "x"), timeout=5).status_code != 404
    assert radicale.delete_users(made) == 1
    assert httpx.get(str(cal.url), auth=(user, "x"), timeout=5).status_code == 404


@needs_radicale
def test_e2e_seeder_deletes_its_users_when_the_worker_is_done(monkeypatch, capsys):
    """`mailr e2e-seed --serve` (one per Playwright worker) deletes the Radicale users of its seeds
    when its stdin closes; they used to stay for good (about 24,000 by October 2026)."""
    import caldav

    from mailr.dev import e2e

    made: list = []

    async def fake_seed(*a, **kw):
        user = f"e2e{uuid.uuid4().hex[:8]}"
        made.append((user, caldav.DAVClient(url=RADICALE, username=user, password="x").principal().make_calendar(name="P")))
        return {"caldav": {"url": RADICALE, "username": user}}

    monkeypatch.setattr(e2e, "seed", fake_seed)
    monkeypatch.setattr(e2e, "_local", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({}) + "\n" + json.dumps({}) + "\n"))
    e2e.serve()
    out = capsys.readouterr().out
    assert out.count(e2e.SERVE_MARK) == 2
    assert len(made) == 2
    for user, cal in made:
        assert httpx.get(str(cal.url), auth=(user, "x"), timeout=5).status_code == 404
