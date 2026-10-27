"""A high spam score moves mail to Spam only when nothing vouches for it: mail Gmail delivered outside
Spam, and mail whose sender passes DMARC for the From domain, keep their place (the score is still
recorded). A DigitalOcean sign-in code scored 19.6 on headers alone and went to Spam (27 Oct 2026)."""

from helpers.core_flow import _ingest
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.intel import spam
from mailr.models import Connector, Label, LabelDecision, Message


async def _score(user, raw, monkeypatch, *, gmail=False, auth=None, frm="DigitalOcean <no-reply@referrals.digitalocean.com>"):
    async def high(db, m, raw_bytes):
        return {"score": 19.6, "tags": {"SPOOF_REPLYTO": {"score": 6}, "RCVD_UNAUTH_PBL": {"score": 4}}}

    monkeypatch.setattr(spam, "classify", high)
    body = raw("Verification Code", frm=frm)
    mid, _ = await _ingest(user, body)
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        if gmail:
            c = Connector(user_id=user.id, kind="gmail", name="me@gmail.com", mode="mirror", enabled=False,
                          status="paused", config={})
            db.add(c)
            await db.flush()
            m.connector_id = c.id
        if auth is not None:
            m.auth_results = auth
        await db.commit()
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        await spam.score_and_act(db, m, body if isinstance(body, bytes) else body.encode())
        await db.commit()
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        junk = (await db.execute(select(Label).where(Label.user_id == user.id, Label.role == "junk"))).scalar_one()
        d = (await db.execute(select(LabelDecision).where(LabelDecision.message_id == mid, LabelDecision.kind == "spam",
                                                          LabelDecision.source == "classifier"))).scalars().first()
        return junk.id in (m.label_ids or []), m.spam_score, d


async def test_gmail_delivered_mail_stays_out_of_spam(user, raw, monkeypatch):
    moved, score, d = await _score(user, raw, monkeypatch, gmail=True)
    assert not moved and score == 19.6
    assert d.status == "suggested" and "Gmail delivered it outside Spam" in d.reason


async def test_dmarc_authenticated_mail_stays_out_of_spam(user, raw, monkeypatch):
    moved, _, d = await _score(user, raw, monkeypatch, auth={"dmarc": "pass", "dmarc_from": "referrals.digitalocean.com"})
    assert not moved and d.status == "suggested" and "DMARC passes for referrals.digitalocean.com" in d.reason


async def test_dmarc_for_another_domain_does_not_vouch(user, raw, monkeypatch):
    moved, _, d = await _score(user, raw, monkeypatch, auth={"dmarc": "pass", "dmarc_from": "digitalocean.com.evil.example"})
    assert moved and d.status == "applied"


async def test_unauthenticated_mail_with_the_same_score_still_goes_to_spam(user, raw, monkeypatch):
    moved, _, d = await _score(user, raw, monkeypatch)
    assert moved and d.status == "applied"
