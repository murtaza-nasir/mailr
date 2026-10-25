"""Labels never apply to spam (8 Oct 2026).

A promotion from a bulk sender arrived in Spam. In the same pass the label classifier gave it the
"GitHub" label (p=0.80, auto-applied) and suggested four more, so the spam showed in
the user's GitHub inbox tab. Now:

- the label classifier never runs on mail in Spam or Trash, from a blocked sender, or scored as
  spam (no suggestion, nothing applied), in the first pass or any later one;
- mail that moves to Spam loses the labels the classifier applied, and its open suggestions are
  withdrawn; the user's own labels stay; "Not spam" lets the classifier run again;
- a label applies itself only to mail from a sender (domain, or address at a shared provider) or
  list that its own examples hold; otherwise it is a suggestion;
- a label's list, its count and the inbox tabs built on it leave Spam and Trash out, as Gmail
  does; `in:spam` and `in:anywhere` still find the mail.
"""

from datetime import UTC, datetime

import numpy as np
import pytest
from embedfake import embedding_endpoint
from helpers.core_flow import _ingest
from sklearn.linear_model import LogisticRegression
from sqlalchemy import select

from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.intel import classify, features, learn
from mailr.models import EMBED_DIM, Job, LabelDecision, Message, User


@pytest.fixture(autouse=True)
async def _embeddings(monkeypatch, app):
    async with embedding_endpoint(monkeypatch):
        yield


def _now_hdr():
    return datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _always(p: float) -> LogisticRegression:
    width = EMBED_DIM + len(features.FEATURES)
    m = LogisticRegression().fit(np.vstack([np.zeros(width), np.ones(width)]), [0, 1])
    m.coef_ = np.zeros_like(m.coef_)
    m.intercept_ = np.asarray([np.log(p / (1 - p))])
    return m


GOOD = {"version": classify.LABEL_MODEL_VERSION, "precision": 1.0, "positives": 60, "hand_positives": 60,
        "effective_positives": 60.0, "recency": {"enabled": True, "half_life_months": 12}, "held_out_positives": 15,
        "newest_positive": datetime.now(UTC).isoformat()}


async def _model(user, lid, p=0.95, auto=True):
    async with sessionmaker()() as db:
        await learn.save(db, user.id, f"label:{lid}", learn.Trained(_always(p), dict(GOOD)), auto_apply=auto)
        await db.commit()


async def _process(mid):
    from mailr.intel.tasks import process_message

    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        await process_message(db, m, await db.get(User, m.user_id), allow_llm=False)
        await db.commit()


async def _label(client, name):
    return (await client.post("/api/v1/labels", json={"name": name})).json()["id"]


async def _add(client, cid, lid):
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "add_labels", "label_ids": [lid]})
    assert r.status_code == 200, r.text


async def _act(client, cid, action):
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": action})
    assert r.status_code == 200, r.text


async def _decisions(mid):
    async with sessionmaker()() as db:
        return {(d.target, d.status) for d in (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == mid, LabelDecision.kind == "label",
            LabelDecision.source == "classifier"))).scalars()}


async def _labels_of(mid):
    async with sessionmaker()() as db:
        return set((await db.get(Message, mid)).label_ids or [])


async def _github_label(client, user, raw):
    """A "GitHub" label with one hand-given example from github.com and a model that qualifies."""
    gh = await _label(client, "GitHub")
    _, example = await _ingest(user, raw("[speakr] Issue opened", frm="Gil <notifications@github.com>",
                                         date=_now_hdr(), body="A new issue was opened"))
    await _add(client, example, gh)
    await _model(user, gh)
    return gh


async def test_no_label_on_mail_in_spam_trash_or_scored_as_spam(client, user, raw):
    gh = await _github_label(client, user, raw)
    # The control: the same sender's next notification in the Inbox gets the label by itself.
    inbox, _ = await _ingest(user, raw("[speakr] PR merged", frm="Gil <notifications@github.com>",
                                       date=_now_hdr(), body="Pull request merged"))
    await _process(inbox)
    assert (str(gh), "applied") in await _decisions(inbox) and gh in await _labels_of(inbox)

    for role in ("junk", "trash"):
        mid, _ = await _ingest(user, raw(f"[speakr] Another one {role}", frm="Gil <notifications@github.com>",
                                         date=_now_hdr(), body="Pull request merged"), roles=[role])
        await _process(mid)
        assert await _decisions(mid) == set(), role
        assert gh not in await _labels_of(mid), role

    # In the Inbox, but the spam check scored it as spam (a sender the user writes to, so it stayed).
    scored, _ = await _ingest(user, raw("[speakr] Scored", frm="Gil <notifications@github.com>",
                                        date=_now_hdr(), body="Pull request merged"))
    async with sessionmaker()() as db:
        db.add(LabelDecision(user_id=user.id, message_id=scored, kind="spam", target="spam", value=True,
                             source="classifier", source_ref="stalwart", confidence=0.9, reason="Spam score 18.0",
                             status="suggested"))
        await db.commit()
    await _process(scored)
    assert await _decisions(scored) == set()
    # The user's "Not spam" lifts it: the label classifier runs again (Not spam on mail in the Inbox).
    async with sessionmaker()() as db:
        db.add(LabelDecision(user_id=user.id, message_id=scored, kind="spam", target="spam", value=False,
                             source="user", confidence=1.0, reason="user action", status="accepted"))
        await db.commit()
    async with sessionmaker()() as db:
        m = await db.get(Message, scored)
        assert await classify.labels_blocked(db, m) is None


async def test_a_label_applies_itself_only_to_a_sender_or_list_it_has_seen(client, user, raw):
    """The dive-watch case: a promotion from a bulk sender scored p=0.80 for the GitHub label,
    whose examples are all GitHub notifications. Without a sender or list in common it is a
    suggestion; GitHub's own mail from another address still gets the label by itself."""
    gh = await _github_label(client, user, raw)
    await _model(user, gh, p=0.80)
    promo, _ = await _ingest(user, raw("The Perfect Dive Watch?", frm="Deep Time <news@divewatch.example>",
                                       date=_now_hdr(), body="Our new dive watch is here",
                                       extra="List-Unsubscribe: <https://divewatch.example/u>"))
    await _process(promo)
    assert await _decisions(promo) == {(str(gh), "suggested")}
    assert gh not in await _labels_of(promo)

    other, _ = await _ingest(user, raw("[speakr] Security alert", frm="GitHub <noreply@github.com>",
                                       date=_now_hdr(), body="A security alert"))
    await _process(other)
    assert await _decisions(other) == {(str(gh), "applied")}

    # A mailing list counts as the sender: a list the label's examples came through.
    lists = await _label(client, "Lists")
    _, ex = await _ingest(user, raw("[dev] Release", frm="Ann <ann@one.example>", date=_now_hdr(),
                                    extra="List-Id: Dev list <dev.lists.example.org>"))
    await _add(client, ex, lists)
    via_list, _ = await _ingest(user, raw("[dev] Freeze", frm="Bo <bo@two.example>", date=_now_hdr(),
                                          extra="List-Id: Dev list <dev.lists.example.org>"))
    stranger, _ = await _ingest(user, raw("Hello", frm="Cy <cy@three.example>", date=_now_hdr()))
    # At a shared mail provider the address counts, not the provider.
    fam = await _label(client, "Family")
    _, sis = await _ingest(user, raw("Dinner", frm="Sis <sis.example@gmail.com>", date=_now_hdr()))
    await _add(client, sis, fam)
    sis2, _ = await _ingest(user, raw("Dinner again", frm="Sis <sis.example@gmail.com>", date=_now_hdr()))
    other_gmail, _ = await _ingest(user, raw("Deal", frm="Shop <deals.example@gmail.com>", date=_now_hdr()))
    # An example in Spam does not count.
    await _ingest(user, raw("Sale", frm="Deep Time <news@divewatch.example>", date=_now_hdr()),
                  roles=["junk"], label_names=["Lists"])
    async with sessionmaker()() as db:
        m = lambda i: db.get(Message, i)
        assert await classify.familiar_sender(db, user.id, lists, await m(via_list))
        assert not await classify.familiar_sender(db, user.id, lists, await m(stranger))
        assert await classify.familiar_sender(db, user.id, fam, await m(sis2))
        assert not await classify.familiar_sender(db, user.id, fam, await m(other_gmail))
        assert not await classify.familiar_sender(db, user.id, lists, await m(promo))
        # Labels the classifier applied are not examples: the GitHub label's auto-applied mail
        # from noreply@github.com does not make another domain familiar, and it does not count.
        assert not await classify.familiar_sender(db, user.id, gh, await m(promo))


async def test_moving_to_spam_withdraws_the_classifiers_labels_and_not_spam_runs_it_again(client, user, raw):
    gh = await _github_label(client, user, raw)
    mine = await _label(client, "Keep")
    later = await _label(client, "Later")
    mid, cid = await _ingest(user, raw("[speakr] Weekly digest", frm="GitHub <noreply@github.com>",
                                       date=_now_hdr(), body="Your weekly digest"))
    await _process(mid)
    await _add(client, cid, mine)
    async with sessionmaker()() as db:
        db.add(LabelDecision(user_id=user.id, message_id=mid, kind="label", target=str(later), value=True,
                             source="classifier", source_ref=f"label:{later}", confidence=0.7, reason="similar",
                             status="suggested"))
        await db.commit()
    assert {gh, mine} <= await _labels_of(mid)
    assert any(s["target"] == str(later) for s in (await client.get("/api/v1/suggestions")).json())

    # An upstream move to Spam (a mirror account's Junk folder) keeps the other labels: the one the
    # classifier applied comes off, the user's stays, and the suggestion is withdrawn.
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        m = await db.get(Message, mid)
        before = {m.id: list(m.label_ids)}
        junk = await store.role_label(db, "junk")
        await store.set_labels([m], add=[junk.id], remove=[], db=db)
        assert await classify.withdraw_if_moved_to_spam(db, store, [m], before) == 2
        await db.commit()
    labels = await _labels_of(mid)
    assert mine in labels and gh not in labels
    assert await _decisions(mid) == {(str(gh), "withdrawn"), (str(later), "withdrawn")}
    assert not any(s["target"] == str(later) for s in (await client.get("/api/v1/suggestions")).json())

    # "Not spam" runs the label classifier again on it.
    await _act(client, cid, "not_spam")
    async with sessionmaker()() as db:
        job = (await db.execute(select(Job).where(Job.kind == "intel.retriage", Job.user_id == user.id))).scalars().one()
    assert job.payload["labels"] is True and mid in job.payload["message_ids"]
    assert mid in job.payload["labels_only"]
    from mailr.intel.tasks import retriage_job

    await retriage_job(job.payload, None)
    assert (str(gh), "applied") in await _decisions(mid) and gh in await _labels_of(mid)

    # Report spam: the classifier's labels and suggestions on it are withdrawn again.
    await _act(client, cid, "spam")
    assert {s for _t, s in await _decisions(mid)} == {"withdrawn"}


async def test_label_views_tabs_and_counts_leave_spam_and_trash_out(client, user, raw):
    gh = await _label(client, "github")
    _, real = await _ingest(user, raw("[speakr] Issue", frm="Gil <notifications@github.com>", msgid="<sl1@x.org>"))
    await _add(client, real, gh)
    _, spam = await _ingest(user, raw("The Perfect Dive Watch?", frm="Deep Time <news@divewatch.example>",
                                      msgid="<sl2@x.org>"), roles=["junk"], label_names=["github"])
    _, binned = await _ingest(user, raw("[speakr] Old issue", frm="Gil <notifications@github.com>",
                                        msgid="<sl3@x.org>"), roles=["trash"], label_names=["github"])
    # A conversation in the Inbox whose only "github" message is in Spam: in the Inbox, not in github.
    _, mixed = await _ingest(user, raw("Question", frm="Bob <bob@example.org>", msgid="<sl4@x.org>"))
    _, again = await _ingest(user, raw("Re: Question", frm="Bob <bob@example.org>", msgid="<sl5@x.org>",
                                       irt="<sl4@x.org>"), roles=["junk"], label_names=["github"])
    assert again == mixed

    async def ids(**params):
        r = await client.get("/api/v1/conversations", params=params)
        assert r.status_code == 200, r.text
        return {c["id"] for c in r.json()["items"]}

    assert await ids(view=f"label:{gh}") == {real}
    assert mixed in await ids(view="inbox")
    assert spam in await ids(view="spam") and binned in await ids(view="trash")
    counts = (await client.get("/api/v1/counts")).json()["views"]
    assert counts[f"label:{gh}"]["total"] == 1
    assert next(lb for lb in (await client.get("/api/v1/labels")).json() if lb["id"] == gh)["total"] == 1

    # An inbox tab built on the label, and its count.
    lay = {"name": "GH", "tabs": [
        {"name": "Inbox", "icon": "inbox", "badge": "unread", "sections": [{"title": "All", "base": "inbox"}]},
        {"name": "Github", "icon": "info", "badge": "all", "claims": True, "sections": [{"title": "All", "base": f"label:{gh}"}]},
    ]}
    assert (await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "gh"})).status_code == 200
    assert await ids(layout="gh", tab="github", sec="all") == {real}
    assert (await client.get("/api/v1/layouts/gh/counts")).json()["github"] == 1

    # Searches read the messages: Spam and Trash only when asked.
    assert await ids(view="all", q="label:github") == {real}
    assert await ids(view="all", q="in:spam label:github") == {spam, mixed}
    assert await ids(view="all", q="in:trash label:github") == {binned}
    assert await ids(view="all", q="in:anywhere label:github") == {real, spam, binned, mixed}

    # Back out of Spam, its label counts again.
    await _act(client, spam, "not_spam")
    assert await ids(view=f"label:{gh}") == {real, spam}
    assert (await client.get("/api/v1/counts")).json()["views"][f"label:{gh}"]["total"] == 2
