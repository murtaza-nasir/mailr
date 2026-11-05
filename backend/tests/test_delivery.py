"""Which account and address received a message, and Bcc (core/delivery.py).

The receiving addresses come from Delivered-To, X-Original-To, Envelope-To and X-Envelope-To,
then (only when none of those is present) the top-most Received headers' `for` clause. The
reader's details show the user's own ones, the accounts the message came through, and Bcc."""

import email
import email.policy
import uuid
from types import SimpleNamespace

from helpers.agent import _ingest

from mailr.core import delivery
from mailr.core.mime import parse


def _msg(headers: str) -> email.message.Message:
    return email.message_from_bytes((headers + "\r\nSubject: x\r\n\r\nbody\r\n").encode(), policy=email.policy.default)


def _env(headers: str) -> list[str]:
    return delivery.envelope_recipients(_msg(headers))


# --- parsing the headers ------------------------------------------------------------------------


def test_every_delivered_to_in_order_then_the_other_headers():
    got = _env("Delivered-To: Sam@Northwind.example\r\nDelivered-To: lists@northwind.example\r\n"
               "X-Original-To: sam+news@northwind.example\r\nEnvelope-To: ops@northwind.example\r\n"
               "X-Envelope-To: <help@northwind.example>\r\nDelivered-To: sam@northwind.example")
    assert got == ["sam@northwind.example", "lists@northwind.example", "sam+news@northwind.example",
                   "ops@northwind.example", "help@northwind.example"]


def test_x_original_to_alone():
    assert _env("X-Original-To: alias@northwind.example") == ["alias@northwind.example"]


def test_received_for_only_when_no_envelope_header():
    rec = ("Received: from relay.example (relay.example [192.0.2.1]) (using TLSv1.3 with cipher X)\r\n"
           "\tby mx.northwind.example (Postfix) with ESMTPS id 4Xy\r\n\tfor <Sam@Northwind.example>; Tue, 3 Nov 2026 10:00:00 +0000\r\n"
           "Received: from a.example by relay.example for <someone@else.example>; Tue, 3 Nov 2026 09:59:00 +0000")
    assert _env(rec) == ["sam@northwind.example", "someone@else.example"]
    assert _env("Delivered-To: sam@northwind.example\r\n" + rec) == ["sam@northwind.example"]
    # A bare address, and a `for` inside a comment that is no clause.
    assert delivery.received_for("by mx.example id 1 for sam@northwind.example; Tue, 3 Nov 2026") == "sam@northwind.example"
    assert delivery.received_for("from x (looking for <x@evil.test>) by mx.example; Tue, 3 Nov 2026") is None
    assert delivery.received_for("by mx.example for multiple recipients; Tue, 3 Nov 2026") is None


def test_only_the_top_most_received_headers_count():
    hops = "".join(f"Received: by hop{i}.example; Tue, 3 Nov 2026 10:00:0{i} +0000\r\n" for i in range(delivery.RECEIVED_HOPS))
    assert _env(hops + "Received: by low.example for <forged@evil.test>; Tue, 3 Nov 2026 09:00:00 +0000") == []


def test_none_present():
    assert _env("From: a@example.org\r\nTo: b@example.org") == []


def test_hostile_values_are_dropped_or_bounded():
    long_local = "a" * 300
    got = _env(
        "Delivered-To: mailing list team@groups.example\r\n"  # Google Groups' form: no mailbox
        f"Delivered-To: {long_local}@northwind.example\r\n"
        "Delivered-To: <script>alert(1)</script>@evil.test\r\n"
        "Delivered-To: \"quoted, name\" <ok@northwind.example>\r\n"
        "Delivered-To: sam\u202e@northwind.example\r\n"  # a bidi control is removed, not kept
        "Delivered-To: @northwind.example\r\n"
        "X-Original-To: a@b@c.example")
    assert got == ["ok@northwind.example", "sam@northwind.example"]
    many = "".join(f"Delivered-To: u{i}@northwind.example\r\n" for i in range(200))
    got = _env(many.rstrip("\r\n"))
    assert len(got) == delivery.MAX_DELIVERED and got[0] == "u0@northwind.example"
    assert all(len(a) <= delivery.MAX_ADDRESS_LEN for a in got)


def test_parse_stores_them():
    raw = (b"From: Lee <lee@example.org>\r\nTo: a@example.org\r\nEnvelope-To: sam@northwind.example\r\n"
           b"Subject: s\r\nMessage-ID: <d1@example.org>\r\n\r\nhi\r\n")
    assert parse(raw).delivered_to == ["sam@northwind.example"]


# --- own addresses, delivered to, Bcc -------------------------------------------------------------


def _m(**kw):
    base = {"sent_by_me": False, "draft": False, "list_id": None, "to": [], "cc": [], "delivered_to": [],
            "connector_id": None, "upstream": {}}
    return SimpleNamespace(**{**base, **kw})


def _mine(*addrs):
    m = delivery.Mine()
    for a in addrs:
        m.add(a)
    return m


def test_own_addresses_match_subaddresses_and_domains():
    mine = _mine("Sam@northwind.example", "*@lists.northwind.example")
    assert mine.has("sam+invoices@northwind.example") and mine.has("anyone@lists.northwind.example")
    assert not mine.has("sam@other.example") and not mine.has("") and not mine.has(None)


def test_delivered_keeps_the_users_own_else_the_first():
    mine = _mine("sam@northwind.example", "alias@northwind.example")
    stored = ["list@groups.example", "sam@northwind.example", "alias@northwind.example"]
    assert delivery.delivered(stored, mine) == ["sam@northwind.example", "alias@northwind.example"]
    assert delivery.delivered(["other@example.org", "x@example.org"], mine) == ["other@example.org"]
    assert delivery.delivered([], mine) == []
    assert delivery.delivered(["first@example.org"], delivery.Mine()) == ["first@example.org"]


def test_bcc_detection():
    mine = _mine("sam@northwind.example", "alias@northwind.example")
    to_others = [{"email": "lee@example.org"}, {"email": "kim@example.org"}]
    assert delivery.is_bcc(_m(to=to_others, delivered_to=["sam@northwind.example"]), mine)
    # Named in To, by an alias or a subaddress of one of the user's addresses, or in Cc.
    assert not delivery.is_bcc(_m(to=[{"email": "Alias@northwind.example"}], delivered_to=["sam@northwind.example"]), mine)
    assert not delivery.is_bcc(_m(cc=[{"email": "sam+x@northwind.example"}]), mine)
    # The receiving address is in To though it is not one of the user's known addresses (a catch-all).
    assert not delivery.is_bcc(_m(to=[{"email": "docs@northwind.example"}], delivered_to=["docs@northwind.example"]), mine)
    # Undisclosed recipients: To names nobody.
    assert delivery.is_bcc(_m(delivered_to=["sam@northwind.example"]), mine)
    # Never for mail the user sent, drafts or list mail; never without anything to compare.
    assert not delivery.is_bcc(_m(to=to_others, sent_by_me=True), mine)
    assert not delivery.is_bcc(_m(to=to_others, draft=True), mine)
    assert not delivery.is_bcc(_m(to=to_others, list_id="team.groups.example"), mine)
    assert not delivery.is_bcc(_m(to=to_others), delivery.Mine())


def test_accounts_from_the_connector_and_its_copies():
    m = _m(connector_id=3, upstream={"connector": 3, "uid": 1, "copies": [{"connector": "5", "uid": 2},
                                                                             {"connector": 3, "uid": 9}, "junk"]})
    assert delivery.connector_ids(m) == [3, 5]
    rc = delivery.Receipt(mine=delivery.Mine(), accounts={3: {"id": 3}, 5: {"id": 5}})
    assert rc.account(m) == [{"id": 3}, {"id": 5}]
    # A connector the caller may not see (a token limited to other accounts) is left out.
    assert delivery.Receipt(mine=delivery.Mine(), accounts={5: {"id": 5}}).account(m) == [{"id": 5}]
    assert delivery.connector_ids(_m()) == []


# --- the API --------------------------------------------------------------------------------------


async def _connector(user_id, name, email):
    from mailr.db import sessionmaker
    from mailr.models import Connector

    async with sessionmaker()() as db:
        c = Connector(user_id=user_id, kind="imap", name=name, mode="mirror", enabled=False, status="paused",
                      config={"email": email})
        db.add(c)
        await db.commit()
        return c.id


def _bcc_raw(delivered: str, msgid: str) -> bytes:
    return (f"Delivered-To: {delivered}\r\nFrom: Lee Park <lee@example.org>\r\n"
            "To: Kim Ito <kim@example.org>, Ana Ruiz <ana@example.org>\r\nSubject: Plans\r\n"
            f"Message-ID: {msgid}\r\nDate: Tue, 3 Nov 2026 10:00:00 +0000\r\n\r\nSee you there.\r\n").encode()


async def test_api_says_which_account_and_address_received_a_bcc(client, user):
    """A message with the user only in Bcc said nothing about which of their accounts received it
    (before 4 Nov 2026). Two accounts receive the same Bcc; the details name both."""
    work = f"sam-{uuid.uuid4().hex[:6]}@northwind.example"
    home = f"sam-{uuid.uuid4().hex[:6]}@home.example"
    a = await _connector(user.id, "Work", work)
    b = await _connector(user.id, "Home", home)
    msgid = f"<{uuid.uuid4().hex}@example.org>"
    mid, cid = await _ingest(user.id, _bcc_raw(work, msgid), connector_id=a, roles=["inbox"],
                             upstream={"connector": a, "folder": "INBOX", "uidvalidity": 1, "uid": 1})
    mid2, _ = await _ingest(user.id, _bcc_raw(home, msgid), connector_id=b, roles=["inbox"],
                            upstream={"connector": b, "folder": "INBOX", "uidvalidity": 1, "uid": 7})
    assert mid2 == mid  # the second copy merged
    conv = (await client.get(f"/api/v1/conversations/{cid}")).json()
    m = conv["messages"][0]
    assert m["received_bcc"] is True and m["bcc"] == []
    assert m["delivered_to"] == [work, home]
    assert [(x["name"], x["address"], x["kind"]) for x in m["account"]] == [("Work", work, "imap"), ("Home", home, "imap")]
    one = (await client.get(f"/api/v1/messages/{m['id']}")).json()
    assert one["received_bcc"] is True and one["delivered_to"] == [work, home] and len(one["account"]) == 2


async def test_api_not_bcc_when_named_in_to(client, user):
    raw = (f"Delivered-To: {user.email}\r\nFrom: Lee <lee@example.org>\r\nTo: {user.email}\r\nSubject: Hi\r\n"
           f"Message-ID: <{uuid.uuid4().hex}@example.org>\r\nDate: Tue, 3 Nov 2026 10:00:00 +0000\r\n\r\nhi\r\n").encode()
    _, cid = await _ingest(user.id, raw, roles=["inbox"])
    m = (await client.get(f"/api/v1/conversations/{cid}")).json()["messages"][0]
    assert m["received_bcc"] is False and m["delivered_to"] == [user.email.lower()] and m["account"] == []


async def test_backfill_reads_envelope_to_for_stored_mail(user):
    from mailr.core import reparse
    from mailr.db import sessionmaker
    from mailr.models import Message

    raw = (f"Envelope-To: {user.email}\r\nFrom: Lee <lee@example.org>\r\nTo: kim@example.org\r\nSubject: Old\r\n"
           f"Message-ID: <{uuid.uuid4().hex}@example.org>\r\nDate: Tue, 3 Nov 2026 10:00:00 +0000\r\n\r\nhi\r\n").encode()
    mid, _ = await _ingest(user.id, raw, roles=["inbox"])
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        m.delivered_to = ["other@copy.example"]  # stored by the old parser, plus a copy's address
        await db.commit()
    assert (await reparse.delivered_to_backfill(user.id, dry_run=True))["changed"] == 1
    assert (await reparse.delivered_to_backfill(user.id))["changed"] == 1
    async with sessionmaker()() as db:
        assert (await db.get(Message, mid)).delivered_to == [user.email.lower(), "other@copy.example"]
    assert (await reparse.delivered_to_backfill(user.id))["changed"] == 0
