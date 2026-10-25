"""Shared helpers, from test_authres.py."""




ME = "me@hale.example"


def _raw(*headers: str, frm: str = ME) -> bytes:
    return ("".join(f"Authentication-Results: {h}\r\n" for h in headers)
            + f"From: {frm}\r\nTo: {ME}\r\nSubject: x\r\n\r\nbody").encode()


def _auth(*headers: str, frm: str = ME) -> dict:
    from mailr.core.mime import parse

    return parse(_raw(*headers, frm=frm)).auth_results


def _consumers(raw: bytes, frm: str = ME) -> dict[str, bool]:
    """What every consumer of the results decides for this message."""
    from mailr.calendar.inbound import _verified
    from mailr.core.ingest import IngestOptions, is_sent_by_me
    from mailr.core.mime import parse
    from mailr.core.vacation import _sender_verified
    from mailr.integrations.speakr.mail import _aligned

    p = parse(raw)
    dom = frm.rsplit("@", 1)[1]
    return {"sent_by_me": is_sent_by_me(p, {ME}, IngestOptions()),
            "itip": _verified(p.auth_results, frm),
            "vacation": _sender_verified(p.auth_results, frm),
            "speakr": _aligned(p.auth_results, dom)}
