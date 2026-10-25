"""Authentication-Results are parsed per RFC 8601 (core/authres.py).

Method results come only from the token at the start of a result unit, never from inside a
property value (smtp.mailfrom, smtp.helo, policy.*) or a comment, where the receiver copies
text the sender chose."""

import email
import email.policy

import pytest
from helpers.authres import ME, _auth, _consumers, _raw  # noqa: F401

from mailr.core import authres

# Injection through sender-chosen text. The authserv-id is trusted (conftest: mx, mx.example, ...).
ATTACKS = {
    "mailfrom_dkim": "mx.example; dkim=none; spf=pass smtp.mailfrom=dkim=pass.header.d=hale.example@evil.test; "
                     "dmarc=fail header.from=hale.example",
    "mailfrom_dmarc": "mx.example; spf=pass smtp.mailfrom=dmarc=pass.header.from=hale.example@evil.test; "
                      "dmarc=fail header.from=hale.example",
    "mailfrom_dmarc_no_dmarc_unit": "mx.example; spf=pass smtp.mailfrom=dmarc=pass.header.from=hale.example@evil.test",
    "mailfrom_quoted": 'mx.example; spf=pass smtp.mailfrom="x; dkim=pass header.d=hale.example; '
                       'dmarc=pass header.from=hale.example"@evil.test; dmarc=fail header.from=hale.example',
    "mailfrom_quoted_paren": 'mx.example; spf=pass smtp.mailfrom="x) dkim=pass header.d=hale.example (y"@evil.test',
    "helo": "mx.example; spf=none smtp.helo=dkim=pass.header.d=hale.example; dmarc=none header.from=hale.example",
    "policy": "mx.example; iprev=pass policy.iprev=dkim=pass.header.d=hale.example; "
              "auth=pass smtp.auth=dmarc=pass.header.from=hale.example",
    "comment": 'mx.example; spf=pass (mx.example: domain of "x) ; dkim=pass header.d=hale.example ; (y"@evil.test '
               'designates 192.0.2.1 as permitted sender) smtp.mailfrom="x) ; dkim=pass header.d=hale.example ; (y"@evil.test; '
               "dmarc=fail header.from=hale.example",
    "reason": 'mx.example; spf=fail reason="dkim=pass header.d=hale.example; dmarc=pass header.from=hale.example"',
    "header_i_local_part": "mx.example; dkim=pass header.i=hale.example@evil.test",
    "dmarc_for_other_domain": "mx.example; dkim=pass header.d=evil.test; dmarc=pass header.from=evil.test",
    "dmarc_pass_without_from": "mx.example; dmarc=pass",
    # A receiver that copied an unquoted ";" (no parser can tell): a second DMARC unit makes DMARC
    # ambiguous, so it counts as failed.
    "duplicate_dmarc": "mx.example; spf=pass smtp.mailfrom=a@evil.test; dmarc=pass header.from=hale.example; "
                       "dmarc=fail header.from=hale.example",
    # DMARC failed for the From domain: no DKIM pass for that domain can stand beside it.
    "dkim_beside_dmarc_fail": "mx.example; spf=pass smtp.mailfrom=a@evil.test; dkim=pass header.d=hale.example; "
                              "dmarc=fail header.from=hale.example",
}


@pytest.mark.parametrize("case", sorted(ATTACKS))
def test_forged_results_are_refused_by_every_consumer(case):
    got = _consumers(_raw(ATTACKS[case]))
    assert not any(got.values()), (case, got, _auth(ATTACKS[case]))


def test_attack_strings_do_not_become_results():
    a = _auth(ATTACKS["mailfrom_dkim"])
    assert a == {"spf": "pass", "dkim": "none", "dmarc": "fail", "dmarc_from": "hale.example"}
    a = _auth(ATTACKS["mailfrom_quoted"])
    assert a.get("dkim") is None and a["dmarc"] == "fail" and "dkim_domains" not in a
    a = _auth(ATTACKS["comment"])
    assert a.get("dkim") is None and a["spf"] == "pass" and a["dmarc"] == "fail"
    a = _auth(ATTACKS["policy"])
    assert set(a) == set() or "dkim" not in a and "dmarc" not in a
    assert _auth(ATTACKS["header_i_local_part"])["dkim_domains"] == "evil.test"
    assert _auth(ATTACKS["duplicate_dmarc"])["dmarc"] == "fail"  # ambiguity fails


GMAIL = ("mx.example;\r\n"
         "       dkim=pass header.i=@hale.example header.s=s1 header.b=AbC+dE/f;\r\n"
         "       dkim=pass header.i=@esp.example header.s=esp header.b=XyZ0;\r\n"
         "       arc=pass (i=1 spf=pass spfdomain=hale.example dkim=pass dkdomain=hale.example dmarc=pass fromdomain=hale.example);\r\n"
         "       spf=pass (google.com: domain of bounce@hale.example designates 192.0.2.1 as permitted sender) "
         "smtp.mailfrom=bounce@hale.example;\r\n"
         "       dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=hale.example")
HOSTED = ("mx.example; dkim=pass (2048-bit key; unprotected) header.d=hale.example header.i=@hale.example "
              "header.a=rsa-sha256 header.s=mail1 header.b=Ab12;\r\n"
              "\tspf=pass (mailhost.example: domain of hale.example designates 192.0.2.7 as permitted sender) "
              "smtp.mailfrom=hale.example;\r\n\tdmarc=pass (p=quarantine dis=none) header.from=hale.example")
STALWART = ("mx.example;\r\n\tdkim=pass header.d=hale.example header.s=rsa header.b=ZmFrZQ;\r\n"
            "\tdkim=fail (body hash mismatch) header.d=list.example header.s=k1 header.b=Zm9v;\r\n"
            "\tspf=pass (mx.example: domain of me@hale.example designates 192.0.2.9 as permitted sender) "
            "smtp.mailfrom=me@hale.example smtp.helo=out.hale.example;\r\n"
            "\tiprev=pass policy.iprev=192.0.2.9;\r\n\tdmarc=pass header.from=hale.example policy.dmarc=reject")


@pytest.mark.parametrize("hdr", [GMAIL, HOSTED, STALWART], ids=["gmail", "hosted", "stalwart"])
def test_real_header_shapes_still_verify(hdr):
    a = _auth(hdr)
    assert a["dkim"] == "pass" and a["spf"] == "pass" and a["dmarc"] == "pass" and a["dmarc_from"] == "hale.example"
    assert a["dkim_domains"].split(",")[0] == "hale.example"
    got = _consumers(_raw(hdr))
    assert all(got.values()), got


def test_parser_details():
    ar = authres.parse(GMAIL)
    assert ar.authserv_id == "mx.example"
    assert [u.method for u in ar.units] == ["dkim", "dkim", "arc", "spf", "dmarc"]
    assert ar.units[1].prop("header.i") == "@esp.example"
    assert ar.units[3].prop("smtp.mailfrom") == "bounce@hale.example"
    assert authres.summarize(ar)["dkim_domains"] == "hale.example,esp.example"
    # Version, nested comments with escapes, CFWS around "=", "." and method versions, reason.
    ar = authres.parse('mx.example 1 (top (nested \\) still) comment); dkim/1 (c) = (c) pass (x) header (c) . (c) d = '
                       'hale.example; spf=fail reason="no; really" smtp.mailfrom="odd local"@evil.test; none')
    assert ar.version == "1"
    assert [(u.method, u.result) for u in ar.units] == [("dkim", "pass"), ("spf", "fail")]
    assert ar.units[0].version == "1" and ar.units[0].prop("header.d") == "hale.example"
    assert ar.units[1].reason == "no; really" and ar.units[1].prop("smtp.mailfrom") == "odd local@evil.test"
    # "none": no results. Garbage units are dropped, the rest kept.
    assert authres.parse("mx.example; none").units == []
    ar = authres.parse("mx.example; ===; dkim=pass header.d=hale.example; spf")
    assert [(u.method, u.result) for u in ar.units] == [("dkim", "pass")]
    # Unterminated quotes or comments end the header: nothing after them is read.
    ar = authres.parse('mx.example; spf=pass smtp.mailfrom="x; dkim=pass header.d=hale.example')
    assert [u.method for u in ar.units] == []
    ar = authres.parse("mx.example; spf=pass (open; dkim=pass header.d=hale.example")
    assert ar.units == []
    # Microsoft's non-standard "action=none" does not lose the unit.
    ar = authres.parse("mx.example; dmarc=pass action=none header.from=hale.example;compauth=pass reason=100")
    assert authres.summarize(ar)["dmarc_from"] == "hale.example"
    assert authres.parse("") is None


def test_folded_header_through_the_email_parser():
    raw = _raw(GMAIL)
    for pol in (email.policy.default, email.policy.compat32):
        from mailr.core.mime import _auth_results

        m = email.message_from_bytes(raw, policy=pol)
        assert _auth_results(m, {"mx.example"})["dmarc_from"] == "hale.example"
        assert _auth_results(m, {"mx.google.com"}) == {}


def test_only_the_top_header_and_a_trusted_authserv_id():
    """Only the top-most header, from a trusted receiver, counts."""
    from mailr.core.mime import _auth_results

    # A lower header (the sender's) is never read; an untrusted top one gives nothing.
    assert _auth("mx.example; dmarc=fail header.from=hale.example",
                 "mx.example; dmarc=pass header.from=hale.example")["dmarc"] == "fail"
    got = _auth("mx.example; dkim=pass header.d=hale.example; dmarc=pass", "x; dmarc=fail")
    assert got["dmarc"] == "pass" and got["dkim_domains"] == "hale.example"
    assert _auth("attacker.example; dmarc=pass header.from=hale.example") == {}
    assert _auth("attacker.example; dmarc=pass; dkim=pass header.d=hale.example") == {}
    assert _auth('"mx.example"; dkim=pass header.d=hale.example')["dkim_domains"] == "hale.example"
    # The trust list matches whole names; ".google.com" also matches its subdomains.
    m = email.message_from_bytes(b"Authentication-Results: mx3.google.com; dmarc=pass\r\n\r\n", policy=email.policy.default)
    assert _auth_results(m, {".google.com"})["dmarc"] == "pass"
    assert _auth_results(m, {"google.com"}) == {}


def test_verified_for_rules():
    v = authres.verified_for
    assert v({"dmarc": "pass", "dmarc_from": "hale.example"}, "hale.example")
    assert not v({"dmarc": "pass", "dmarc_from": "evil.test"}, "hale.example")
    assert not v({"dmarc": "pass"}, "hale.example")  # a pass without its domain proves nothing
    assert v({"dkim_domains": "hale.example"}, "mail.hale.example")
    assert not v({"dkim_domains": "hale.example"}, "evilhale.example")
    assert not v({"dkim_domains": "hale.example", "dmarc": "fail", "dmarc_from": "hale.example"}, "hale.example")
    assert v({"dkim_domains": "hale.example", "dmarc": "fail", "dmarc_from": "other.example"}, "hale.example")
    assert not v({}, "hale.example") and not v({"dkim_domains": "x"}, "")


async def test_backfill_clears_a_forged_sent_by_me(user):
    """`mailr auth-results-backfill`: mail stored under the old parser as sent by the user only
    because of a forged result loses sent_by_me; genuine results are stored in the new shape."""
    from mailr.core import reparse
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.db import sessionmaker
    from mailr.models import Message, User

    me = user.email
    dom = me.split("@")[1]
    forged = (f"Authentication-Results: mx.example; dkim=none; spf=pass smtp.mailfrom=dkim=pass.header.d={dom}@evil.test; "
              f"dmarc=fail header.from={dom}\r\nFrom: {me}\r\nTo: {me}\r\nSubject: Pay this\r\n"
              f"Message-ID: <{dom}-forged@evil.test>\r\n\r\nbody").encode()
    good = (f"Authentication-Results: mx.example; dkim=pass header.d={dom}; dmarc=pass header.from={dom}\r\n"
            f"From: {me}\r\nTo: x@example.org\r\nSubject: Mine\r\nMessage-ID: <{dom}-good@example.org>\r\n\r\nbody").encode()
    ids = []
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        for raw in (forged, good):
            res = await ingest_raw(db, await user_store(db, u), raw, IngestOptions())
            ids.append(res.message.id)
        await db.commit()
    async with sessionmaker()() as db:
        bad, ok = await db.get(Message, ids[0]), await db.get(Message, ids[1])
        assert not bad.sent_by_me and ok.sent_by_me
        # What the old parser stored.
        bad.sent_by_me, bad.auth_results = True, {"dkim": "pass", "dkim_domains": dom, "spf": "pass", "dmarc": "fail"}
        ok.auth_results = {"dkim": "pass", "dkim_domains": dom, "dmarc": "pass"}
        await db.commit()
    st = await reparse.auth_results_backfill(user.id, dry_run=True)
    assert st["not_mine"] == 1 and st["examples"][0]["id"] == ids[0]
    st = await reparse.auth_results_backfill(user.id)
    assert st["changed"] == 2 and st["not_mine"] == 1
    async with sessionmaker()() as db:
        bad, ok = await db.get(Message, ids[0]), await db.get(Message, ids[1])
        assert not bad.sent_by_me and bad.auth_results.get("dkim") == "none"
        assert ok.sent_by_me and ok.auth_results["dmarc_from"] == dom
    assert (await reparse.auth_results_backfill(user.id))["changed"] == 0
