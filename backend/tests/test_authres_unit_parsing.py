"""Injected Authentication-Results units. Regression tests: each one is an attack that must
fail.

core/authres.py reads results only from the token that starts a unit. When the receiving MTA
copies sender text with an unquoted ";" (the HELO name in smtp.helo, or the envelope local part in
smtp.mailfrom), a new unit starts there. Before the fix two parser rules worked for the attacker: an
injected `dkim=pass header.d=<victim>` added the victim domain, and a second `dmarc` unit made DMARC
"ambiguous" and dropped the receiver's real `dmarc=fail`. Now a repeated dmarc/spf/arc unit FAILS
the method, and with several dkim units a pass for the DMARC domain counts only when DMARC passed.
"""

from helpers.authres import ME, _auth, _consumers, _raw

HELO = ("mx.example; spf=none smtp.mailfrom=a@evil.test "
        "smtp.helo=x;dmarc=none;dkim=pass(x)header.d=hale.example; "
        "dmarc=fail header.from=hale.example")
MAILFROM = ("mx.example; spf=pass smtp.mailfrom=x;dmarc=none;dkim=pass(x)header.d=hale.example;y@evil.test; "
            "dmarc=fail header.from=hale.example")
# Only a DKIM unit injected; the receiver's own units say the message is unsigned and the domain has
# no DMARC policy.
DKIM_ONLY = ("mx.example; spf=none smtp.helo=x;dkim=pass(x)header.d=hale.example; dkim=none; "
             "dmarc=none header.from=hale.example")


def test_unquoted_semicolon_in_helo_cannot_erase_the_dmarc_fail():
    a = _auth(HELO)
    assert a["dmarc"] == "fail"  # two dmarc units: ambiguity is a fail, never "absent"
    assert not any(_consumers(_raw(HELO), frm=ME).values())


def test_unquoted_semicolon_in_mailfrom_cannot_erase_the_dmarc_fail():
    a = _auth(MAILFROM)
    assert a["dmarc"] == "fail"
    assert not any(_consumers(_raw(MAILFROM), frm=ME).values())


def test_an_injected_dkim_pass_beside_a_dmarc_none_does_not_verify():
    a = _auth(DKIM_ONLY)
    assert "hale.example" not in a.get("dkim_domains", "")
    assert not any(_consumers(_raw(DKIM_ONLY), frm=ME).values())


def test_one_real_dkim_pass_still_verifies():
    # Control: an honest receiver's single DKIM pass (no DMARC policy) still verifies the sender,
    # and two signatures with a DMARC pass do too.
    one = "mx.example; dkim=pass header.d=hale.example header.s=s1; dmarc=none header.from=hale.example"
    two = ("mx.example; dkim=pass header.d=hale.example; dkim=pass header.d=esp.example; "
           "dmarc=pass header.from=hale.example")
    assert all(_consumers(_raw(one), frm=ME).values())
    assert all(_consumers(_raw(two), frm=ME).values())


def test_the_same_header_without_the_injection_is_refused():
    clean = "mx.example; spf=none smtp.mailfrom=a@evil.test smtp.helo=x.evil.test; dmarc=fail header.from=hale.example"
    assert not any(_consumers(_raw(clean), frm=ME).values())


def test_the_trust_rule_refuses_the_forged_sender():
    from mailr.core.mime import parse
    from mailr.core.trusted import check_message
    from mailr.models import Message

    p = parse(_raw(HELO, frm="me@hale.example"))
    m = Message(from_email=p.from_email, auth_results=p.auth_results, to=p.to, cc=p.cc,
                draft=False, deleted=False, list_id=None, list_unsubscribe=None)
    v = check_message(m, trusted={"me@hale.example"}, mine={"me@hale.example"})
    assert not v.ok and v.reason == "not_verified"


def test_a_decoy_dmarc_unit_never_unlocks_an_injected_dkim_pass():
    """A second, injected DMARC
    unit once made DMARC absent, and with it the "DMARC fail cancels DKIM" rule. Any ambiguity in DMARC
    now fails closed: no domain verifies."""
    from mailr.core import authres

    real_tail = "; dkim=none; dmarc=fail header.from=hale.example"
    only_dkim = "mx.example; spf=pass smtp.mailfrom=a;dkim=pass header.d=hale.example" + real_tail
    with_decoy = "mx.example; spf=pass smtp.mailfrom=a;dkim=pass header.d=hale.example;dmarc=pass header.from=evil.test" + real_tail
    assert not authres.verified_for(authres.summarize(authres.parse(only_dkim)), "hale.example")
    s = authres.summarize(authres.parse(with_decoy))
    assert s["dmarc"] == "fail" and "dmarc_from" not in s
    for dom in ("hale.example", "evil.test"):
        assert not authres.verified_for(s, dom)
    assert not any(_consumers(_raw(with_decoy), frm=ME).values())
