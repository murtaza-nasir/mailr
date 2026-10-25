"""The person page's open threads (their message is the last one and I have not replied)
look only at conversations that hold a message from them, not at every conversation's last
message. Same answer as before."""

from conftest import mail_date
from helpers.core_flow import _ingest


async def test_open_threads_are_the_ones_waiting_on_me(client, user, raw):
    _, waiting = await _ingest(user, raw("Need the slides", frm="Dana <dana@example.org>", msgid="<ot1@x.org>"),
                               roles=["inbox"])
    _, answered = await _ingest(user, raw("Budget", frm="Dana <dana@example.org>", msgid="<ot2@x.org>",
                                          date=mail_date(0, "09:00")), roles=["inbox"])
    await _ingest(user, raw("Re: Budget", frm=f"Me <{user.email}>", to="dana@example.org", msgid="<ot3@x.org>",
                            irt="<ot2@x.org>", date=mail_date(0, "11:00")), roles=["sent"])
    _, other = await _ingest(user, raw("Hello", frm="Sam <sam@example.org>", msgid="<ot4@x.org>"), roles=["inbox"])
    got = (await client.get("/api/v1/people/dana@example.org")).json()
    assert waiting in got["open_threads"]
    assert answered not in got["open_threads"] and other not in got["open_threads"]
