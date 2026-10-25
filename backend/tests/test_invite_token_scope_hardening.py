# ACT-SURFACES: api assistant
"""Invites, tokens and scopes: regression tests.

- Organizer DESCRIPTION and LOCATION lose images (stored copy and the invite card); an API
  token reaches web_search and read_page only with the `web` scope, which new tokens do not get by
  default.
- `localhost` is not a trusted authserv-id by default; one person and one token use only a
  share of a web provider's daily allowance; reparse-attached-emails drops the triage signals of the old text; the VPN key
  reaches only the egress container. (Calendar links are https only: tests/test_calendar_itip_hardening.py.)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import yaml
from icalendar import Calendar

from mailr.calendar import ical, itip
from mailr.db import sessionmaker

REPO = Path(__file__).resolve().parents[2]

IMG = "![pixel](https://evil.example/p.png?d=secret)"
ICS = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//t//EN\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
       "UID:a39-{u}@example.org\r\nDTSTAMP:20261010T100000Z\r\nDTSTART:20261020T100000Z\r\n"
       "DTEND:20261020T110000Z\r\nSUMMARY:Planning\r\n"
       f"DESCRIPTION:Agenda {IMG} and <img src=\"https://evil.example/q.gif\">\r\n"
       f"LOCATION:Room 4 {IMG}\r\n"
       "ORGANIZER:mailto:org@example.org\r\nATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:me@example.com\r\n"
       "END:VEVENT\r\nEND:VCALENDAR\r\n")


# ---------------------------------------------------------------- Organizer text


def test_organizer_text_loses_images():
    cal = Calendar.from_ical(ICS.format(u=uuid.uuid4().hex))
    ev = next(iter(cal.walk("VEVENT")))
    ical.clean_sender_text(ev)
    for k in ("description", "location"):
        v = str(ev.get(k))
        assert "evil.example" not in v and "[image: pixel]" in v
    assert "Agenda" in str(ev.get("description")) and "Room 4" in str(ev.get("location"))
    card = itip.summarize(ICS.format(u=uuid.uuid4().hex).encode(), {"me@example.com"})
    assert "evil.example" not in card["description"] and "evil.example" not in card["location"]


# ---------------------------------------------------------------- The web scope


async def _tok(client, scopes):
    r = await client.post("/api/v1/tokens", json={"name": f"t-{uuid.uuid4().hex[:4]}", "scopes": scopes})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def test_a_token_needs_the_web_scope_for_the_web_tools(client, user):
    """agent.read_page, agent.web_search"""
    from httpx import AsyncClient

    from mailr.agent.tools import TOOLS
    from mailr.core.auth import ALL_SCOPES

    assert "web" in ALL_SCOPES
    assert TOOLS["read_page"].scope == "web" and TOOLS["web_search"].scope == "web"
    for scopes, ok in ((["read"], False), (["read", "web"], True)):
        a = AsyncClient(transport=client._transport, base_url="http://test")
        a.headers["Authorization"] = f"Bearer {await _tok(client, scopes)}"
        async with a:
            r = await a.post("/api/v1/agent/tools/read_page", json={"url": "https://www.example.org/"})
            if ok:
                assert "lacks the 'web' scope" not in r.text
            else:
                assert r.status_code in (400, 403) and "web" in r.text, r.text
            listed = await a.get("/api/v1/agent/tools")
            if listed.status_code == 200:
                names = {t["name"] for t in listed.json()["tools"]}
                assert ok or not names & {"read_page", "web_search"}


# ---------------------------------------------------------------- Authserv-ids


@pytest.mark.parametrize("host,trusted", [("localhost", False), ("LOCALHOST.", False), ("127.0.0.1", False),
                                          ("mail.example.org", True)])
def test_the_default_store_hostname_is_not_a_trusted_authserv_id(monkeypatch, host, trusted):
    from mailr.config import get_settings
    from mailr.core import mime

    s = get_settings()
    monkeypatch.setattr(s, "imap_hostname", host)
    monkeypatch.setattr(s, "trusted_authserv_ids", "")
    got = mime.trusted_authserv_ids()
    assert (host.lower().rstrip(".") in got) is trusted
    monkeypatch.setattr(s, "trusted_authserv_ids", "localhost")
    assert "localhost" in mime.trusted_authserv_ids()  # listed on purpose: trusted


# ---------------------------------------------------------------- Provider shares


async def test_one_person_and_one_token_use_only_a_share_of_a_provider(user):
    from types import SimpleNamespace

    from mailr.core.visibility import viewer_var
    from mailr.models import WebSearchProvider
    from mailr.websearch import service

    async with sessionmaker()() as db:
        p = WebSearchProvider(name=f"R-{uuid.uuid4().hex[:6]}", purpose="reader", kind="jina",
                              base_url="https://r.example", network="internet", priority=100, max_results=10,
                              timeout_s=5, per_day=8)
        db.add(p)
        await db.commit()
        pid = p.id
    async with sessionmaker()() as db:
        p = await db.get(WebSearchProvider, pid)

    def who(uid, tok=None):
        return SimpleNamespace(user_id=uid, token=SimpleNamespace(id=tok) if tok else None)

    async def take(w) -> str | None:
        t = viewer_var.set(w)
        try:
            await service._take(p)
            return None
        except service.ProviderError as e:
            return str(e)
        finally:
            viewer_var.reset(t)

    # A token: a quarter (2 of 8).
    assert [await take(who(1, 77)) for _ in range(3)][:2] == [None, None]
    assert "share" in (await take(who(1, 77)) or "")
    # The same person in their own chats: up to half (4 of 8), the token's 2 included.
    assert [await take(who(1)) for _ in range(2)] == [None, None]
    assert "share" in (await take(who(1)) or "")
    # Someone else still has theirs; then the day's total ends it for everyone.
    assert [await take(who(2)) for _ in range(4)] == [None] * 4
    assert "limit of 8" in (await take(who(3)) or "")


# ---------------------------------------------------------------- The VPN key


def test_the_vpn_key_reaches_only_the_egress_container():
    compose = yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text())
    for name, svc in compose["services"].items():
        env = svc.get("environment") or {}
        if name == "egress":
            assert "MAILR_VPN_WIREGUARD_KEY" in str(env.get("WIREGUARD_PRIVATE_KEY"))
            continue
        if ".env" in str(svc.get("env_file") or ""):
            assert env.get("MAILR_VPN_WIREGUARD_KEY") == "", name
        else:
            assert "MAILR_VPN_WIREGUARD_KEY" not in json.dumps(env), name


# ---------------------------------------------------------------- Reparse leftovers


async def test_reparse_drops_what_triage_read_in_the_old_text(user, tmp_path, monkeypatch):
    from helpers.agent import _ingest, _setup_identity
    from helpers.attached_emails import ATTACHED, _as_stored_before, _email, _outer

    from mailr.config import get_settings
    from mailr.core import reparse
    from mailr.models import LabelDecision, Message

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    await _setup_identity(user)
    fwd, _ = await _ingest(user.id, _outer([(ATTACHED, _email())]), roles=["inbox"])
    await _as_stored_before(fwd)
    async with sessionmaker()() as db:
        m = await db.get(Message, fwd)
        m.primary_action = {"kind": "pay", "label": "Pay the invoice"}
        db.add(LabelDecision(user_id=user.id, message_id=fwd, kind="smart", target="deadline", value=True,
                             source="llm", source_ref="2026-10-30", confidence=0.8, reason="Deadline", status="applied"))
        db.add(LabelDecision(user_id=user.id, message_id=fwd, kind="smart", target="needs_reply", value=True,
                             source="llm", confidence=0.8, reason="x", status="rejected"))
        await db.commit()
    st = await reparse.attached_emails(user.id, batch=10, dry_run=False, rate=0)
    assert st["changed"] == 1 and st["signals_cleared"] == 2
    async with sessionmaker()() as db:
        from sqlalchemy import select

        m = await db.get(Message, fwd)
        assert m.primary_action is None
        left = (await db.execute(select(LabelDecision.target, LabelDecision.status).where(
            LabelDecision.message_id == fwd, LabelDecision.kind == "smart"))).all()
        assert left == [("needs_reply", "rejected")]  # the user's own choice stays
