# ACT-SURFACES: api assistant
"""A model-written draft signs off once: with a signature on the sending identity the drafting
prompts ask for no closing, and a closing that repeats the signature is removed before the
signature is added (agent/draft_tools.py strip_repeated_signoff). Without a signature the model's
own sign-off stays."""

import json

from helpers.agent import _drop_endpoint, _endpoint, _setup_identity
from helpers.assistant_knowledge import fake  # noqa: F401
from sqlalchemy import select

from mailr.agent import tools  # noqa: F401  (agent/tools.py loads draft_tools)
from mailr.agent.draft_tools import signature_text, strip_repeated_signoff
from mailr.agent.prompts import draft_system, signature_note
from mailr.db import sessionmaker
from mailr.models import Identity

SIG = "Thanks, Doc"


def test_a_closing_that_repeats_the_signature_goes():
    body = "Hi Marty,\n\nThe flux unit is ready on Friday."
    assert strip_repeated_signoff(body + "\n\nThanks, Doc", SIG) == body
    # Split over two lines, other case and punctuation, trailing blank lines.
    assert strip_repeated_signoff(body + "\n\nthanks\nDoc.\n\n", SIG) == body
    # A "-- " separator the model wrote goes with it.
    assert strip_repeated_signoff(body + "\n\n-- \nThanks, Doc", SIG) == body
    # A signature of several lines: the model repeated its first line (the name).
    assert strip_repeated_signoff(body + "\n\nEmmett Brown", "Emmett Brown\nHill Valley Labs") == body
    assert strip_repeated_signoff(body + "\n\nEmmett Brown\nHill Valley Labs",
                                  "Emmett Brown\nHill Valley Labs") == body


def test_other_endings_stay():
    body = "Hi Marty,\n\nThe flux unit is ready on Friday.\n\nBest,\nDoc"
    # The last line differs from the signature: nothing is removed.
    assert strip_repeated_signoff(body, SIG) == body
    assert strip_repeated_signoff("Hi Marty,\n\nThanks", SIG) == "Hi Marty,\n\nThanks"
    assert strip_repeated_signoff("Hi,\n\nSee you there, Doc", SIG) == "Hi,\n\nSee you there, Doc"
    # The signature's later lines alone are not a closing; a body that is only the closing stays.
    assert strip_repeated_signoff("Hi,\n\nHill Valley Labs", "Emmett Brown\nHill Valley Labs") \
        == "Hi,\n\nHill Valley Labs"
    assert strip_repeated_signoff("Thanks, Doc", SIG) == "Thanks, Doc"
    assert strip_repeated_signoff("Hi\n\nThanks, Doc", "") == "Hi\n\nThanks, Doc"


def test_the_signature_note_is_in_the_prompt_only_with_a_signature():
    from types import SimpleNamespace

    u = SimpleNamespace(name="Emmett Brown", email="doc@example.com", settings={}, language=None)
    assert signature_text('<div>Thanks,<br>Doc</div>') == "Thanks,\nDoc"
    assert signature_note("") == ""
    assert "write no closing and no name" in draft_system(u, {}, signature="Thanks,\nDoc")
    assert '"Thanks, / Doc"' in draft_system(u, {}, signature="Thanks,\nDoc")
    assert "write no closing" not in draft_system(u, {})


async def _sign(user, html):
    async with sessionmaker()() as db:
        for i in (await db.execute(select(Identity).where(Identity.user_id == user.id))).scalars():
            i.signature_html = html
        await db.commit()


async def test_help_me_write_signs_off_once(client, user, fake):
    await _setup_identity(user)
    await _sign(user, "<div>Thanks, Doc</div>")
    ep = await _endpoint(user.id, "local")
    try:
        fake.replies = [json.dumps({"subject": "Flux unit", "body": "Hi Marty,\n\nIt is ready.\n\nThanks,\nDoc"})]
        r = await client.post("/api/v1/agent/draft", json={"mode": "new", "instructions": "tell Marty it is ready",
                                                           "to": ["marty@example.org"]})
        assert r.status_code == 200, r.text
        html = r.json()["payload"]["html"]
        assert html.count("Thanks") == 1 and 'class="mailr_signature">-- <br><div>Thanks, Doc</div>' in html
        system = fake.posts[-1]["body"]["messages"][0]["content"]
        assert "write no closing and no name" in system and '"Thanks, Doc"' in system
        # Without a signature the model's sign-off is the only one, and it stays.
        await _sign(user, "")
        fake.replies = [json.dumps({"subject": "Flux unit", "body": "Hi Marty,\n\nIt is ready.\n\nThanks,\nDoc"})]
        r = await client.post("/api/v1/agent/draft", json={"mode": "new", "instructions": "tell Marty it is ready",
                                                           "to": ["marty@example.org"]})
        html = r.json()["payload"]["html"]
        assert "Thanks,<br>Doc" in html and "mailr_signature" not in html
        assert "write no closing" not in fake.posts[-1]["body"]["messages"][0]["content"]
    finally:
        await _drop_endpoint(ep)


async def test_create_draft_drops_the_repeated_closing(client, user):
    from mailr.agent.tools import ToolCtx, run_tool
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.models import User

    await _setup_identity(user)
    await _sign(user, "<div>Thanks, Doc</div>")
    async with sessionmaker()() as db:
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        d = await run_tool(ToolCtx(db=db, p=p, assistant=True), "create_draft", {
            "to": ["marty@example.org"], "subject": "Flux unit", "body": "Hi Marty,\n\nIt is ready.\n\nThanks, Doc"})
        did = d.data["draft_id"]
    html = (await client.get(f"/api/v1/drafts/{did}")).json()["payload"]["html"]
    assert html.count("Thanks, Doc") == 1 and "It is ready." in html
