"""Recording citations (intel/citations.py): [[r:<id>@<seconds> "quote"]] is
checked against the transcript within ±30 s, a wrong quote stays unverified, an invisible recording
is dropped, and the url is Speakr's deep link at that moment."""

from speakr_fake import connect, speakr  # noqa: F401

from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.intel import citations
from mailr.models import User


async def _p(db, uid):
    return Principal(user=await db.get(User, uid), scopes=set(ALL_SCOPES) - {"admin"})


async def test_recording_citations_verify_quotes_near_the_moment(client, user, speakr):
    speakr.add(12, "Weekly sync")
    speakr.add(13, "Dictation", plain="Remember to call the bank about the loan.")
    await connect(client, speakr)
    async with sessionmaker()() as db:
        p = await _p(db, user.id)
        text, cites = await citations.resolve(db, p, 'We ship Friday [[r:12@754 "agreed to ship the budget on Friday"]]. '
                                                    'Evan drafts it [[r:12@12:50 "I will draft the plan"]].')
        assert text == "We ship Friday [1](mailr-cite:1). Evan drafts it [2](mailr-cite:2)."
        c1, c2 = cites
        assert c1["kind"] == "recording" and c1["recording_id"] == 12 and c1["verified"] and c1["speaker"] == "Dana"
        assert c1["url"] == "https://speakr.test/recordings/12?t=754" and c1["start"] == 754 and c1["title"] == "Weekly sync"
        assert c2["verified"] and c2["t"] == 770 and c2["speaker"] == "Evan"  # 12:50 is 770 s; the turn starts at 790

        # The right words at the wrong moment, and words never said: unverified, still linked.
        _, cites = await citations.resolve(db, p, 'A [[r:12@60 "ship the budget on Friday"]] B [[r:12@754 "cancel the budget"]]')
        assert [c["verified"] for c in cites] == [False, False]
        assert cites[0]["url"] == "https://speakr.test/recordings/12?t=60"

        # A whole recording, a plain-text transcript (no times), and a recording that is not there.
        text, cites = await citations.resolve(db, p, 'See [[r:12]], [[r:13@5 "call the bank"]] and [[r:999@3 "x"]].')
        assert text == "See [1](mailr-cite:1), [2](mailr-cite:2) and."  # a dropped marker takes its space along
        assert cites[0]["url"] == "https://speakr.test/recordings/12" and cites[0]["quote"] is None
        assert cites[1]["verified"] is True


async def test_recording_citations_dropped_for_callers_who_cannot_see_them(client, user, speakr):
    speakr.add(12)
    await connect(client, speakr)  # local-only by default
    tok = (await client.post("/api/v1/tokens", json={"name": "hosted", "scopes": ["read", "recordings"],
                                                     "local_only": False})).json()
    async with sessionmaker()() as db:
        from mailr.models import ApiToken

        t = await db.get(ApiToken, tok["item"]["id"])
        p = Principal(user=await db.get(User, user.id), token=t, scopes={"read", "recordings"})
        text, cites = await citations.resolve(db, p, 'Friday [[r:12@754 "ship the budget"]].')
        assert text == "Friday." and cites == []
