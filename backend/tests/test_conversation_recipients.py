"""A conversation's `to_self` and `recipients` (core/threading.py; on Android
a sent email to an outside address with no reply yet read "Note to self" in the Sent list).

`to_self` is true only when the user wrote every message and each went to the user's own addresses
(the account address or an identity, an alias included). When the user is the only sender,
`recipients` names the people the messages went to, so a list row can read "To: Dana Whitfield".
The migration's fill (a6d2f9c4e1b8) computes the same values in SQL for existing rows."""

import importlib.util
from pathlib import Path

from conftest import mail_date
from helpers.core_flow import _ingest
from sqlalchemy import text, update

from mailr.db import sessionmaker
from mailr.models import Conversation, Identity


async def _sent(user, data, **kw):
    """The user's own mail, as from a Sent folder."""
    return await _ingest(user, data, roles=["sent"], **kw)


async def _alias(user, email: str) -> None:
    async with sessionmaker()() as db:
        db.add(Identity(user_id=user.id, email=email, name="Me"))
        await db.commit()


async def _summary(client, cid: int) -> dict:
    lst = (await client.get("/api/v1/conversations", params={"view": "all"})).json()
    by_id = {c["id"]: c for c in lst["items"]}
    if cid in by_id:
        return by_id[cid]
    return (await client.get(f"/api/v1/conversations/{cid}")).json()["conversation"]


async def _stored(cid: int) -> tuple[bool, list]:
    async with sessionmaker()() as db:
        c = await db.get(Conversation, cid)
        return c.to_self, list(c.recipients or [])


async def test_note_to_self_and_alias(client, user, raw):
    me = f"Test User <{user.email}>"
    _, c1 = await _sent(user, raw("Groceries", frm=me, to=user.email, body="milk, eggs"))
    s = await _summary(client, c1)
    assert s["to_self"] is True and s["recipients"] == []

    # Mail to an alias of the user (one of their identities) is a note to self too, in any case.
    await _alias(user, "mr.alias@example.net")
    _, c2 = await _sent(user, raw("Parking spot", frm=me, to="MR.Alias@example.net", body="level 3"))
    s = await _summary(client, c2)
    assert s["to_self"] is True and s["recipients"] == []


async def test_sent_to_someone_else_names_the_recipients(client, user, raw):
    me = f"Test User <{user.email}>"
    _, cid = await _sent(user, raw("Visit on Friday", frm=me, to="Dana Whitfield <dana@outside.example>",
                                     extra=f"Cc: {user.email}, Lee <lee@outside.example>"))
    s = await _summary(client, cid)
    assert s["to_self"] is False
    assert [p["email"] for p in s["participants"]] == [user.email] and s["participants"][0]["me"]
    # The user's own address (the Cc to self) is left out; first appearance order.
    assert s["recipients"] == [{"email": "dana@outside.example", "name": "Dana Whitfield"},
                               {"email": "lee@outside.example", "name": "Lee"}]
    assert await _stored(cid) == (False, s["recipients"])


async def test_a_reply_from_someone_else_ends_both(client, user, raw):
    me = f"Test User <{user.email}>"
    _, cid = await _sent(user, raw("Quote", frm=me, to="Dana <dana@outside.example>", msgid="<q1@x.example>"))
    assert (await _summary(client, cid))["recipients"]
    _, c2 = await _ingest(user, raw("Re: Quote", frm="Dana <dana@outside.example>", to=user.email,
                                    irt="<q1@x.example>", date=mail_date(1)))
    assert c2 == cid
    s = await _summary(client, cid)
    assert s["to_self"] is False and s["recipients"] == []

    # A note to self that someone else then writes into is no longer one.
    _, n = await _sent(user, raw("Ideas", frm=me, to=user.email, msgid="<n1@x.example>"))
    assert (await _summary(client, n))["to_self"] is True
    await _ingest(user, raw("Re: Ideas", frm="Bob <bob@outside.example>", to=user.email, irt="<n1@x.example>",
                            date=mail_date(2)))
    assert (await _summary(client, n))["to_self"] is False


async def test_the_migration_fill_matches_refresh(user, raw):
    """Existing rows: the migration's SQL gives what refresh() stores."""
    me = f"Test User <{user.email}>"
    await _alias(user, "alias2@example.net")
    convs = [
        (await _sent(user, raw("Self", frm=me, to=user.email)))[1],
        (await _sent(user, raw("Alias", frm=me, to="alias2@example.net")))[1],
        (await _sent(user, raw("Out", frm=me, to="Dana <dana@outside.example>",
                                 extra="Bcc: Zed <zed@outside.example>")))[1],
        (await _ingest(user, raw("In", frm="Carol <carol@outside.example>", to=user.email)))[1],
    ]
    expected = {c: await _stored(c) for c in convs}
    assert [expected[c][0] for c in convs] == [True, True, False, False]
    assert [p["email"] for p in expected[convs[2]][1]] == ["dana@outside.example", "zed@outside.example"]
    path = next(Path(__file__).parents[1].glob("migrations/versions/a6d2f9c4e1b8_*.py"))
    spec = importlib.util.spec_from_file_location("mig_recipients", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    async with sessionmaker()() as db:
        await db.execute(update(Conversation).where(Conversation.id.in_(convs)).values(to_self=False, recipients=None))
        await db.execute(text(mig.FILL))
        await db.commit()
    for c in convs:
        got = await _stored(c)
        assert got == expected[c], (c, got, expected[c])
