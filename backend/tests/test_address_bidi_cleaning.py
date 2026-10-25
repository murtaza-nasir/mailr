"""Bidi and invisible characters in the ADDRESS itself. Regression tests: each one is an attack
that must fail.

`From: Service <"‮moc.lapyap@ecivres"@evil.test>` once kept the RIGHT-TO-LEFT OVERRIDE in the
stored address, so the reader showed `"tset.live@"service@paypal.com` and Copy address copied it.
Addresses now lose bidi, invisible, joiner and control characters at parse time
(core/textsafe.clean_address), and a From address that needed it is flagged (from_problem), so it
verifies nobody.
"""  # noqa: PLE2502

from helpers.agent import _ingest

from mailr.core.mime import parse

RLO = "‮"  # noqa: PLE2502


def _raw(frm: str, to: str = "me@example.com") -> bytes:
    return (f"From: {frm}\r\nTo: {to}\r\nSubject: Account notice\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\nPlease confirm.\r\n").encode()


def test_parse_drops_bidi_and_invisible_characters_from_addresses():
    cases = {
        f'Service <"{RLO}moc.lapyap@ecivres"@evil.test>': '"moc.lapyap@ecivres"@evil.test',
        f"Service <{RLO}tset.live@evil.test>": "tset.live@evil.test",
        "Bob <ceo\u200b@evil.test>": "ceo@evil.test",
        "Bob <service⁦@evil.test>": "service@evil.test",  # noqa: PLE2502
    }
    for header, addr in cases.items():
        p = parse(_raw(header))
        assert p.from_email == addr, (header, p.from_email)
        assert not any(c in p.from_email for c in (RLO, "\u200b", "⁦"))  # noqa: PLE2502
        assert p.auth_results.get("from_problem") == "malformed"  # flagged: it verifies nobody
    p = parse(_raw("Bob <bob@example.com>", to=f"Ann <ann{RLO}@example.com>, x\u200b@example.com"))
    assert [a["email"] for a in p.to] == ["ann@example.com", "x@example.com"]


async def test_the_reader_api_never_serves_the_override(client, user):
    mid, cid = await _ingest(user.id, _raw(f'Service <"{RLO}moc.lapyap@ecivres"@evil.test>'), roles=["inbox"])
    conv = (await client.get(f"/api/v1/conversations/{cid}")).json()
    frm = conv["messages"][0]["from"]
    assert RLO not in frm["email"]
    assert frm["email"] == '"moc.lapyap@ecivres"@evil.test'
