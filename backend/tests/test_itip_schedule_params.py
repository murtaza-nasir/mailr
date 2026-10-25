"""iMIP mail never carries the RFC 6638 SCHEDULE-* parameters. The copy mailr stores on a server
that schedules by itself has SCHEDULE-AGENT=CLIENT (organizer.stamp), and a server adds
SCHEDULE-STATUS; both are for the CalDAV server, not for the people who get the mail."""

import email
import email.policy
import re

from icalendar import Calendar

from mailr.calendar import itip, organizer
from mailr.core.compose import build_message

ME = "dana@northwind.example"
EVENT = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\nUID:sched@northwind.example\r\n"
         "DTSTAMP:20261004T120000Z\r\nDTSTART:20261012T090000Z\r\nDTEND:20261012T100000Z\r\nSUMMARY:Planning\r\n"
         "ATTENDEE;CN=Lee Park;SCHEDULE-STATUS=3.7:mailto:lee@contoso.example\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def _unfold(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text)


def _sent_parts(blob: str, method: str) -> list[str]:
    m = build_message({"subject": "Invitation: Planning", "text": "hi", "to": [{"email": "lee@contoso.example", "name": ""}],
                       "_system": {"itip": {"method": method, "blob": blob}}},
                      from_addr={"email": ME, "name": "Dana"})
    parsed = email.message_from_bytes(m.as_bytes(), policy=email.policy.default)
    out = []
    for part in parsed.walk():
        if part.get_content_type() in ("text/calendar", "application/ics"):
            data = part.get_payload(decode=True).decode()
            out.append(data)
    return out


def test_organizer_request_mail_has_no_schedule_params():
    cal = Calendar.from_ical(EVENT)
    organizer.stamp(cal, None, ME, "Dana", {ME}, auto_schedule=True)
    stored = _unfold(cal.to_ical().decode())
    assert "SCHEDULE-AGENT=CLIENT" in stored  # the stored copy keeps it, for the CalDAV server
    [msg] = organizer.plan(None, cal, {ME})
    assert "SCHEDULE-" in _unfold(msg.ics)
    parts = _sent_parts(msg.ics, msg.method)
    assert len(parts) == 2  # the text/calendar alternative and invite.ics
    for data in parts:
        flat = _unfold(data)
        assert "SCHEDULE-" not in flat.upper()
        assert "METHOD:REQUEST" in flat and "mailto:lee@contoso.example" in flat and "Lee Park" in flat
        assert "RSVP=TRUE" in flat


def test_rsvp_reply_mail_has_no_schedule_params():
    stored = EVENT.replace("ATTENDEE;", "ORGANIZER;SCHEDULE-AGENT=CLIENT:mailto:lee@contoso.example\r\nATTENDEE;")
    reply = itip.build_reply(stored.encode(), ME, "accepted", "Dana").decode()
    assert "SCHEDULE-AGENT" in _unfold(reply)  # copied from the stored organizer line
    for data in _sent_parts(reply, "REPLY"):
        flat = _unfold(data)
        assert "SCHEDULE-" not in flat.upper() and "PARTSTAT=ACCEPTED" in flat


def test_strip_leaves_other_text_alone():
    plain = EVENT.replace(";SCHEDULE-STATUS=3.7", "")
    assert itip.strip_schedule_params(plain) is plain
    out = _unfold(itip.strip_schedule_params(EVENT))
    assert "SCHEDULE" not in out and "ATTENDEE;CN=\"Lee Park\":mailto:lee@contoso.example" in out
    assert itip.strip_schedule_params("not a calendar SCHEDULE-AGENT") == "not a calendar SCHEDULE-AGENT"


def test_summarize_gives_the_organizer_zone_as_an_iana_name():
    """Outlook writes Windows zone names; the browser's formatter refuses them, and an invite
    from Outlook could not be opened (9 Oct 2026)."""
    from mailr.calendar import itip

    ics = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:ops-1\r\n"
           b"DTSTAMP:20261009T150000Z\r\nDTSTART;TZID=Central Standard Time:20261013T140000\r\n"
           b"DTEND;TZID=Central Standard Time:20261013T150000\r\nSUMMARY:Ops Review\r\n"
           b"ORGANIZER;CN=Perry:mailto:perry@uni.example\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    s = itip.summarize(ics, set())
    assert s["tz"] == "America/Chicago" and s["summary"] == "Ops Review"
    weird = ics.replace(b"Central Standard Time", b"Nowhere Standard Time")
    assert itip.summarize(weird, set())["tz"] is None
