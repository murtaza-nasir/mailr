# ACT-SURFACES: api
"""Contact details from signatures (person.suggest_details, person.accept_detail, person.dismiss_detail):
the rules on real-looking signatures, the model step only for what the rules
missed and only with values found in the signature, the local-only guard, and Accept/Dismiss."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers.people_groups import _msg
from sqlalchemy import select

from mailr.contacts import signatures as sg
from mailr.db import sessionmaker
from mailr.intel import llm
from mailr.models import Person, PersonSuggestion

ACADEMIC = """Hi Rowan,

Thanks for the draft. I will send comments by Friday.

Best regards,
Jane Doe, PhD
Director of Operations
Harbor Logistics Group, Northwind Traders
100 Main St
Anytown, ST 00000-0084
Office: (202) 555-0134 | Cell: 202.555.0199
Fax: 202-555-0100
https://www.springfield.example
"""

CORPORATE = """Sounds good.

--
Tom Baker
Director of Engineering | Northwind Traders Inc.
M: +44 7700 900123
"""

NO_SIG = "ok, thanks!\n\nSent from my iPhone"

QUOTED = """Yes.

Cheers,
Ana
> On Mon, Bob wrote:
> Bob Smith
> CEO, Acme Corp
"""


def test_rules_on_an_academic_signature():
    block = sg.signature_block(ACADEMIC, "Jane Doe")
    assert block[0] == "Jane Doe, PhD"
    f = sg.parse_block(block, "Jane Doe")
    assert [p["value"] for p in f["phone"]] == ["(202) 555-0134", "202.555.0199"]  # the fax is left out
    assert [p["type"] for p in f["phone"]] == ["work", "mobile"]
    assert f["title"] == [{"value": "Director of Operations"}]
    assert f["organization"][0]["value"] == "Harbor Logistics Group"
    assert f["address"] == [{"value": "100 Main St, Anytown, ST 00000-0084"}]


def test_rules_on_a_corporate_signature_and_non_signatures():
    f = sg.parse_block(sg.signature_block(CORPORATE, "Tom Baker"), "Tom Baker")
    assert f["title"] == [{"value": "Director of Engineering"}]
    assert f["organization"] == [{"value": "Northwind Traders Inc."}]
    assert f["phone"][0]["value"] == "+44 7700 900123" and f["phone"][0]["type"] == "mobile"
    assert sg.signature_block(NO_SIG, "Someone") == []
    block = sg.signature_block(QUOTED, "Ana Lopez")
    assert block == ["Ana"]  # the quoted signature of someone else is not read
    assert not any(sg.parse_block(block, "Ana Lopez").values())


class _FakeModel:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[dict] = []

    async def __call__(self, db, user, messages, **kw):
        self.calls.append(kw)
        return {"content": self.reply, "_model": "fake", "_endpoint": "fake"}


async def _person(user_id, email):
    async with sessionmaker()() as db:
        return (await db.execute(select(Person).where(Person.user_id == user_id, Person.email == email))).scalar_one()


async def test_scan_suggests_and_accept_dismiss(client, user, monkeypatch):
    """person.suggest_details: strong correspondents only; Accept writes the detail with provenance;
    Dismiss is remembered (person.accept_detail, person.dismiss_detail)."""
    now = datetime.now(UTC)
    await _msg(user.id, sent=True, to=["jane@springfield.example"], when=now - timedelta(days=3))
    await _msg(user.id, sent=False, frm="jane@springfield.example", when=now - timedelta(days=1), body=ACADEMIC)
    fake = _FakeModel('{"phone": [], "title": "", "organization": "", "address": ""}')
    monkeypatch.setattr(llm, "chat", fake)
    async with sessionmaker()() as db:
        res = await sg.scan(db, user.id)
        await db.commit()
    assert res["people"] == 1 and res["suggestions"] == 5 and fake.calls == []  # the rules found everything
    card = (await client.get("/api/v1/people/jane@springfield.example")).json()
    by = {(s["field"], s["value"]): s for s in card["suggestions"]}
    assert set(by) == {("phone", "(202) 555-0134"), ("phone", "202.555.0199"), ("title", "Director of Operations"),
                       ("organization", "Harbor Logistics Group"),
                       ("address", "100 Main St, Anytown, ST 00000-0084")}
    assert all(s["method"] == "rules" and s["message_id"] for s in by.values())
    t = by[("title", "Director of Operations")]
    r = await client.post(f"/api/v1/people/jane@springfield.example/suggestions/{t['id']}/accept")
    assert r.status_code == 200 and r.json()["state"] == "accepted"
    ph = by[("phone", "(202) 555-0134")]
    await client.post(f"/api/v1/people/jane@springfield.example/suggestions/{ph['id']}/accept")
    d = by[("phone", "202.555.0199")]
    assert (await client.post(f"/api/v1/people/jane@springfield.example/suggestions/{d['id']}/dismiss")).json()["state"] == "dismissed"
    assert (await client.post(f"/api/v1/people/jane@springfield.example/suggestions/{d['id']}/accept")).status_code == 409
    card = (await client.get("/api/v1/people/jane@springfield.example")).json()
    assert card["title"] == "Director of Operations" and card["sources"]["title"] == "signature"
    assert [p["value"] for p in card["phones"]] == ["(202) 555-0134"] and card["phones"][0]["source"] == "signature"
    assert len(card["suggestions"]) == 2

    # The same signature again: nothing new; the dismissed number does not return.
    await _msg(user.id, sent=False, frm="jane@springfield.example", when=now, body=ACADEMIC)
    async with sessionmaker()() as db:
        res = await sg.scan(db, user.id)
        await db.commit()
        rows = (await db.execute(select(PersonSuggestion.field, PersonSuggestion.state).where(
            PersonSuggestion.user_id == user.id))).all()
    assert res["suggestions"] == 0 and sorted(s for _, s in rows) == ["accepted", "accepted", "dismissed", "suggested", "suggested"]
    # Someone I never wrote to is not scanned.
    await _msg(user.id, sent=False, frm="cold@x.example", body=CORPORATE)
    async with sessionmaker()() as db:
        p = await db.get(Person, (await _person(user.id, "cold@x.example")).id)
        p.strength = 0
        await db.commit()
        assert (await sg.scan(db, user.id))["people"] == 0


async def test_model_fills_only_what_rules_missed_and_only_verbatim(user, monkeypatch):
    sig = "Thanks,\nPriya Raman\nHead of People Operations\nContoso\n"
    await _msg(user.id, sent=True, to=["priya@mail.example"])
    await _msg(user.id, sent=False, frm="priya@mail.example", body=sig)
    fake = _FakeModel('{"phone": ["+1 555 000 0000"], "title": "Head of People Operations", '
                      '"organization": "Contoso", "address": "1 Main Street, Springfield"}')
    monkeypatch.setattr(llm, "chat", fake)
    async with sessionmaker()() as db:
        await sg.scan(db, user.id)
        await db.commit()
        rows = {(s.field, s.value, s.method) for s in (await db.execute(select(PersonSuggestion).where(
            PersonSuggestion.user_id == user.id))).scalars()}
    assert len(fake.calls) == 1 and fake.calls[0]["sensitive"] is False
    assert fake.calls[0]["response_format"]["type"] == "json_schema"
    # The phone and the address are not in the signature: dropped. The rules found the title.
    assert ("organization", "Contoso", "model") in rows
    assert not any(f in ("phone", "address") for f, _, _ in rows)
    assert any(f == "title" for f, _, _ in rows)


async def test_local_only_mail_never_reaches_a_hosted_model(user, monkeypatch):
    """A local-only message is read with sensitive=True; with only a hosted endpoint the router
    refuses (LocalOnlyViolation) and the rules' result stands."""
    sig = "Best,\nKim Lee\nVisiting Scholar, Fabrikam Institute\n"
    await _msg(user.id, sent=True, to=["kim@fab.example"])
    async with sessionmaker()() as db:
        mid = await _msg(user.id, sent=False, frm="kim@fab.example", body=sig)
        from mailr.models import Message

        (await db.get(Message, mid)).local_only = True
        await db.commit()
    seen: list[bool] = []

    async def hosted_only(db, user, task, *, sensitive, **_kw):
        seen.append(sensitive)
        if sensitive:
            raise llm.LocalOnlyViolation("no local endpoint")
        raise AssertionError("not reached")

    monkeypatch.setattr(llm, "resolve", hosted_only)
    async with sessionmaker()() as db:
        res = await sg.scan(db, user.id)
        await db.commit()
        rows = (await db.execute(select(PersonSuggestion).where(PersonSuggestion.user_id == user.id))).scalars().all()
    assert seen == [True] and res["model"] == 0
    assert {(s.field, s.value) for s in rows} == {("title", "Visiting Scholar"), ("organization", "Fabrikam Institute")}
    assert all(s.local_only for s in rows)


@pytest.mark.parametrize("body,name,expect", [
    ("Regards,\nLi Wei\nSenior Data Analyst at Contoso\nTel: +86 10 1234 5678\n", "Li Wei",
     {"title": "Senior Data Analyst", "organization": "Contoso", "phone": "+86 10 1234 5678"}),
    ("-- \nMaria Garcia | Program Coordinator | Riverside Community College\n", "Maria Garcia",
     {"title": "Program Coordinator", "organization": "Riverside Community College"}),
    ("Cheers\nSam\n\nSam Patel\nFounder & CEO, Patel Labs LLC\n42 Baker Street\nLondon NW1 6XE\n", "Sam Patel",
     {"organization": "Patel Labs LLC", "address": "42 Baker Street, London NW1 6XE"}),
])
def test_signature_cases(body, name, expect):
    f = sg.parse_block(sg.signature_block(body, name), name)
    for k, v in expect.items():
        assert f[k] and f[k][0]["value"] == v, (k, f)
