# ACT-SURFACES: api
"""The Subscriptions page and the Clean up page (core/mailgroups.py, api/mailgroup_routes.py):
grouping, the protected conversations, exact previews, Undo, filters,
the egress requirement of one-click unsubscribe, and the access policy of model suggestions.

Actions tested here: subscription.list, subscription.unsubscribe, subscription.archive_all,
subscription.auto_archive, subscription.label, subscription.keep, cleanup.groups, cleanup.archive,
cleanup.label, cleanup.trash, cleanup.read, cleanup.category, cleanup.keep, cleanup.rule,
cleanup.suggest."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers.mailgroups import LIST, _ago, _cleanup_box, _ingest, _labels, _role, _set
from sqlalchemy import select, update

from mailr.db import sessionmaker
from mailr.models import Job, MailGroupState, Message, Rule, User

SHOP = "List-Unsubscribe: <mailto:unsub@shop.example>"


async def _mailbox(user, raw):
    """A digest list (3 issues, 1 read), a shop sending with an unsubscribe address (2), and two
    personal messages (no list headers)."""
    ids = {}
    ids["d1"] = await _ingest(user, raw("Digest 1", frm="Weekly <digest@news.example>", to=user.email,
                                        date=_ago(5), extra=LIST))
    ids["d2"] = await _ingest(user, raw("Digest 2", frm="Weekly <digest@news.example>", to=user.email,
                                        date=_ago(40), extra=LIST))
    ids["d3"] = await _ingest(user, raw("Digest 3", frm="Weekly <digest@news.example>", to=user.email,
                                        date=_ago(200), extra=LIST))
    ids["s1"] = await _ingest(user, raw("Sale", frm="Shop <deals@shop.example>", to=user.email,
                                        date=_ago(2), extra=SHOP))
    ids["s2"] = await _ingest(user, raw("More sale", frm="Shop <deals@shop.example>", to=user.email,
                                        date=_ago(60), extra=SHOP))
    ids["p1"] = await _ingest(user, raw("Lunch?", frm="Ann <ann@friends.example>", to=user.email, date=_ago(1)))
    await _set(Message, ids["d1"][0], seen=True)
    return ids


async def test_subscriptions_group_by_list_and_sender(client, user, raw):
    """subscription.list: one row per list or bulk sender, with volume, reading and the
    unsubscribe method; personal mail is not a subscription."""
    await _mailbox(user, raw)
    r = await client.get("/api/v1/subscriptions", params={"sort": "name"})
    assert r.status_code == 200, r.text
    rows = {x["key"]: x for x in r.json()["items"]}
    assert set(rows) == {"list:weekly.news.example", "from:deals@shop.example"}
    d = rows["list:weekly.news.example"]
    assert (d["total"], d["last_30"], d["last_90"], d["read"]) == (3, 1, 2, 1)
    assert d["kind"] == "list" and d["unsubscribe"] == "one_click" and d["last_read"] is not None
    assert d["open_rate"] == pytest.approx(1 / 3, abs=0.01)
    s = rows["from:deals@shop.example"]
    assert (s["total"], s["read"], s["unsubscribe"], s["kind"]) == (2, 0, "mailto", "sender")
    assert s["last_read"] is None and not s["replied"]
    # The default order puts the busy, never-read sender first.
    r = await client.get("/api/v1/subscriptions")
    assert r.json()["items"][0]["key"] == "from:deals@shop.example"
    r = await client.get("/api/v1/subscriptions", params={"filter": "never_read"})
    assert [x["key"] for x in r.json()["items"]] == ["from:deals@shop.example"]
    r = await client.get("/api/v1/subscriptions", params={"q": "weekly"})
    assert [x["key"] for x in r.json()["items"]] == ["list:weekly.news.example"]


async def test_subscription_replied_flag(client, user, raw):
    _mid, _cid = await _ingest(user, raw("Club news", frm="Club <club@club.example>", to=user.email,
                                         msgid="<club1@club.example>", extra=SHOP.replace("shop", "club")))
    await _ingest(user, raw("Re: Club news", frm=f"Me <{user.email}>", to="club@club.example",
                            irt="<club1@club.example>"), source="send", roles=["sent"])
    rows = {x["key"]: x for x in (await client.get("/api/v1/subscriptions")).json()["items"]}
    assert rows["from:club@club.example"]["replied"] is True


async def test_subscription_archive_all_and_undo(client, user, raw):
    """subscription.archive_all: the subscription's Inbox conversations, with the usual Undo."""
    ids = await _mailbox(user, raw)
    inbox = await _role(user, "inbox")
    key = "list:weekly.news.example"
    pv = (await client.get("/api/v1/subscriptions/preview", params={"key": key})).json()
    assert pv["conversations"] == 3 and len(pv["samples"]) == 3 and pv["undo"] is True
    r = await client.post("/api/v1/subscriptions/actions", json={"key": key, "action": "archive_all"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["conversations"] == 3
    for k in ("d1", "d2", "d3"):
        assert inbox not in await _labels(ids[k][1])
    assert inbox in await _labels(ids["s1"][1]), "other subscriptions are untouched"
    r = await client.post("/api/v1/conversations/actions", json=res["undo"])
    assert r.status_code == 200, r.text
    for k in ("d1", "d2", "d3"):
        assert inbox in await _labels(ids[k][1])


async def test_subscription_auto_archive_keep_and_label(client, user, raw):
    """subscription.auto_archive (a filter, undone by removing it), subscription.keep and
    subscription.label."""
    ids = await _mailbox(user, raw)
    key = "from:deals@shop.example"
    r = await client.post("/api/v1/subscriptions/actions", json={"key": key, "action": "auto_archive"})
    assert r.status_code == 200, r.text
    rid = r.json()["rule_id"]
    async with sessionmaker()() as db:
        rule = await db.get(Rule, rid)
        assert rule.query == "from:deals@shop.example" and rule.actions == {"archive": True}
    rows = {x["key"]: x for x in (await client.get("/api/v1/subscriptions")).json()["items"]}
    assert rows[key]["auto_archive_rule_id"] == rid
    # New mail from the sender skips the Inbox.
    inbox = await _role(user, "inbox")
    _m, cid = await _ingest(user, raw("Flash sale", frm="Shop <deals@shop.example>", to=user.email, extra=SHOP))
    assert inbox not in await _labels(cid)
    r = await client.post("/api/v1/subscriptions/actions", json={"key": key, "action": "remove_auto_archive"})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        assert await db.get(Rule, rid) is None
    r = await client.post("/api/v1/subscriptions/actions", json={"key": key, "action": "keep"})
    assert r.json()["undo"] == {"action": "unkeep"}
    rows = {x["key"]: x for x in (await client.get("/api/v1/subscriptions", params={"filter": "kept"})).json()["items"]}
    assert list(rows) == [key]
    assert key not in [x["key"] for x in (await client.get("/api/v1/subscriptions", params={"filter": "review"})).json()["items"]]
    lab = (await client.post("/api/v1/labels", json={"name": "Shopping"})).json()["id"]
    r = await client.post("/api/v1/subscriptions/actions", json={"key": key, "action": "label", "label_id": lab})
    assert r.status_code == 200, r.text
    assert lab in await _labels(ids["s1"][1]) and lab in await _labels(ids["s2"][1])
    r = await client.post("/api/v1/subscriptions/actions", json={"key": key, "action": "label", "label_id": inbox})
    assert r.status_code == 400, "only the user's own labels"


async def test_unsubscribe_respects_required_egress(client, user, raw, monkeypatch):
    """subscription.unsubscribe goes through core/egress.py: with MAILR_REQUIRE_EGRESS on and no
    usable egress proxy nothing is sent, and the subscription is not marked unsubscribed."""
    from mailr.config import get_settings
    from mailr.core import egress, imageproxy

    await _mailbox(user, raw)
    monkeypatch.setattr(get_settings(), "require_egress", True)

    async def state(use_cache=True):
        return None

    async def config(db):
        return True, ""

    monkeypatch.setattr(egress, "state", state)
    monkeypatch.setattr(imageproxy, "instance_config", config)
    key = "list:weekly.news.example"
    plan = (await client.get("/api/v1/subscriptions/unsubscribe", params={"key": key})).json()
    assert plan["method"] == "one_click" and plan["url"] == "https://news.example/u/1"
    r = await client.post("/api/v1/subscriptions/unsubscribe", json={"key": key})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False and "paused" in r.json()["error"]
    async with sessionmaker()() as db:
        st = (await db.execute(select(MailGroupState).where(MailGroupState.user_id == user.id))).scalars().all()
        assert not [s for s in st if s.unsubscribed_at]


async def test_unsubscribe_one_click_through_egress_marks_the_subscription(client, user, raw, monkeypatch):
    from mailr.core import unsubscribe

    await _mailbox(user, raw)
    sent = []

    async def execute(db, p, m, pl):
        sent.append(pl)
        return {"method": "one_click", "ok": True, "status": 200}

    monkeypatch.setattr(unsubscribe, "execute", execute)
    key = "list:weekly.news.example"
    r = await client.post("/api/v1/subscriptions/unsubscribe", json={"key": key})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert sent[0]["method"] == "one_click"
    rows = {x["key"]: x for x in (await client.get("/api/v1/subscriptions", params={"filter": "unsubscribed"})).json()["items"]}
    assert list(rows) == [key] and rows[key]["unsubscribed_by"] == "one_click"


async def test_cleanup_groups_leave_protected_conversations_alone(client, user, raw):
    """cleanup.groups: starred, important and replied-to conversations are counted as left alone,
    and included only when asked."""
    await _cleanup_box(user, raw)
    r = await client.get("/api/v1/cleanup/groups", params={"by": "sender"})
    assert r.status_code == 200, r.text
    g = {x["key"]: x for x in r.json()["items"]}["news@bulk.example"]
    assert g["conversations"] == 1 and g["protected"] == 3
    r = await client.get("/api/v1/cleanup/groups", params={"by": "domain"})
    assert {x["key"]: x for x in r.json()["items"]}["bulk.example"]["conversations"] == 1
    r = await client.get("/api/v1/cleanup/groups", params={"by": "sender", "include_starred": True,
                                                          "include_important": True, "include_replied": True})
    assert {x["key"]: x for x in r.json()["items"]}["news@bulk.example"]["conversations"] == 4
    r = await client.get("/api/v1/cleanup/groups", params={"by": "sender", "where": "inbox"})
    assert {x["key"]: x for x in r.json()["items"]}["news@bulk.example"]["conversations"] == 1
    r = await client.get("/api/v1/cleanup/groups", params={"by": "sender", "older_than_days": 1000})
    assert "news@bulk.example" not in {x["key"] for x in r.json()["items"]}
    r = await client.get("/api/v1/cleanup/groups", params={"by": "category"})
    assert {x["key"] for x in r.json()["items"]} >= {""}
    assert {x["key"]: x for x in r.json()["items"]}[""]["label"] == "Uncategorised"


async def test_cleanup_preview_is_exact_and_trash_has_undo(client, user, raw):
    """cleanup.trash: the preview lists exactly what changes (and what is left alone, by reason);
    the action moves those conversations to Trash, never deletes them, and Undo restores them."""
    box = await _cleanup_box(user, raw)
    pv = (await client.get("/api/v1/cleanup/preview", params={"by": "sender", "key": "news@bulk.example"})).json()
    assert pv["conversations"] == 1 and pv["messages"] == 1
    assert pv["protected"] == {"starred": 1, "important": 1, "replied": 1}
    assert [s["subject"] for s in pv["samples"]] == ["Bulk plain"]
    body = {"by": "sender", "key": "news@bulk.example", "action": "trash", "expect": 1}
    r = await client.post("/api/v1/cleanup/apply", json=body)
    assert r.status_code == 200, r.text
    trash = await _role(user, "trash")
    assert await _labels(box["plain"][1]) == [trash]
    for name in ("starred", "important", "replied", "inbox"):
        assert trash not in await _labels(box[name][1]), name
    async with sessionmaker()() as db:
        assert (await db.get(Message, box["plain"][0])).deleted is False, "Trash, never deleted forever"
    r = await client.post("/api/v1/conversations/actions", json=r.json()["undo"])
    assert r.status_code == 200, r.text
    assert trash not in await _labels(box["plain"][1])
    # The group changed since the preview: the action refuses to run.
    r = await client.post("/api/v1/cleanup/apply", json={**body, "expect": 5})
    assert r.status_code == 409


async def test_cleanup_label_read_category_keep(client, user, raw):
    """cleanup.label, cleanup.read, cleanup.category and cleanup.keep on one group."""
    box = await _cleanup_box(user, raw)
    lab = (await client.post("/api/v1/labels", json={"name": "Old news"})).json()["id"]
    base = {"by": "sender", "key": "news@bulk.example"}
    r = await client.post("/api/v1/cleanup/apply", json={**base, "action": "label", "label_id": lab})
    assert r.status_code == 200, r.text
    assert lab in await _labels(box["plain"][1]) and lab not in await _labels(box["starred"][1])
    r = await client.post("/api/v1/cleanup/apply", json={**base, "action": "read"})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        assert (await db.get(Message, box["plain"][0])).seen is True
        assert (await db.get(Message, box["starred"][0])).seen is False
    r = await client.post("/api/v1/cleanup/apply", json={**base, "action": "category", "category": "promotions"})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        assert (await db.get(Message, box["plain"][0])).category == "promotions"
    r = await client.post("/api/v1/conversations/actions", json=r.json()["undo"])
    async with sessionmaker()() as db:
        assert (await db.get(Message, box["plain"][0])).category is None
    r = await client.post("/api/v1/cleanup/apply", json={**base, "action": "keep"})
    assert r.status_code == 200
    g = {x["key"]: x for x in (await client.get("/api/v1/cleanup/groups")).json()["items"]}["news@bulk.example"]
    assert g["reviewed"] is True


async def test_cleanup_rule_creation(client, user, raw):
    """cleanup.rule: a filter for the group's future mail through the rules engine."""
    await _cleanup_box(user, raw)
    r = await client.post("/api/v1/cleanup/rule", json={"by": "domain", "key": "bulk.example",
                                                        "actions": {"archive": True, "mark_read": True}})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        rule = await db.get(Rule, r.json()["rule"]["id"])
        assert rule.query == "from:@bulk.example" and rule.actions == {"archive": True, "mark_read": True}
    r = await client.post("/api/v1/cleanup/rule", json={"by": "sender", "key": "x@y.example",
                                                        "actions": {"forward_to": "a@b.example"}})
    assert r.status_code == 400
    r = await client.post("/api/v1/cleanup/rule", json={"by": "category", "key": "", "actions": {"archive": True}})
    assert r.status_code == 400


async def test_large_cleanup_runs_as_a_job_with_undo(client, user, raw, monkeypatch):
    """Above the inline limit the action is a bulk job with progress; its Undo is a job too
    (here with the limit lowered to 1)."""
    import types

    from mailr.api import mail_write
    from mailr.core import tasks

    monkeypatch.setattr(mail_write, "BULK_INLINE_MAX", 1)
    box = await _cleanup_box(user, raw)
    body = {"by": "sender", "key": "news@bulk.example", "action": "trash",
            "scope": {"include_starred": True, "include_important": True}}
    r = await client.post("/api/v1/cleanup/apply", json=body)
    assert r.status_code == 202, r.text
    job = r.json()["job"]

    async def run(jid):
        async with sessionmaker()() as db:
            j = await db.get(Job, jid)
            payload = dict(j.payload)
            j.status = "running"
            await db.commit()
        await tasks.bulk_conversations(payload, types.SimpleNamespace(job_id=jid, user_id=user.id))
        await _set(Job, jid, status="done")

    await run(job)
    trash = await _role(user, "trash")
    for name in ("plain", "starred", "important"):
        assert await _labels(box[name][1]) == [trash]
    assert trash not in await _labels(box["replied"][1])
    st = (await client.get(f"/api/v1/bulk/{job}")).json()
    assert st["status"] == "done" and st["undo"]["action"] == "restore_labels"
    r = await client.post("/api/v1/conversations/actions", json=st["undo"])
    assert r.status_code == 202
    await run(r.json()["job"])
    for name in ("plain", "starred", "important"):
        assert trash not in await _labels(box[name][1])


async def test_large_category_change_has_undo(client, user, raw, monkeypatch):
    """A category change that ran as a job keeps its undo record in parts (restore_category)."""
    import types

    from mailr.api import mail_write
    from mailr.core import tasks

    monkeypatch.setattr(mail_write, "BULK_INLINE_MAX", 1)
    box = await _cleanup_box(user, raw)
    r = await client.post("/api/v1/cleanup/apply", json={"by": "sender", "key": "news@bulk.example",
                                                         "action": "category", "category": "updates",
                                                         "scope": {"include_starred": True}})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    async with sessionmaker()() as db:
        payload = dict((await db.get(Job, job)).payload)
    await tasks.bulk_conversations(payload, types.SimpleNamespace(job_id=job, user_id=user.id))
    await _set(Job, job, status="done")
    async with sessionmaker()() as db:
        assert (await db.get(Message, box["starred"][0])).category == "updates"
    st = (await client.get(f"/api/v1/bulk/{job}")).json()
    assert st["undo"] == {"action": "restore_category", "job": job, "ids": []}
    r = await client.post("/api/v1/conversations/actions", json=st["undo"])
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        assert (await db.get(Message, box["starred"][0])).category is None
        assert (await db.get(Message, box["plain"][0])).category is None
    r = await client.post("/api/v1/conversations/actions", json=st["undo"])
    assert r.status_code == 404, "an undo runs once"


async def test_tokens_cannot_run_page_actions(client, user, raw):
    from helpers.permissions import _token

    await _cleanup_box(user, raw)
    agent, _ = await _token(client, ["read", "label", "rules", "send"])
    assert (await agent.get("/api/v1/cleanup/groups")).status_code == 200
    r = await agent.post("/api/v1/cleanup/apply", json={"by": "sender", "key": "news@bulk.example", "action": "trash"})
    assert r.status_code == 403
    r = await agent.post("/api/v1/subscriptions/actions", json={"key": "from:news@bulk.example", "action": "keep"})
    assert r.status_code == 403


# ------------------------------------------------------------------ model suggestions


async def test_suggestions_follow_the_access_policy(client, user, raw, monkeypatch):
    """cleanup.suggest: only with the user's classify model under their access policy. A user in no
    group with a policy gets a refusal and no job."""
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.assigned_groups = []
        await db.commit()
    await _cleanup_box(user, raw)
    r = await client.post("/api/v1/cleanup/suggest", json={"by": "sender", "now": True})
    assert r.status_code == 403
    async with sessionmaker()() as db:
        assert not (await db.execute(select(Job).where(Job.user_id == user.id, Job.kind == "cleanup.suggest"))).first()


async def test_suggestions_are_stored_not_applied(client, user, raw, monkeypatch):
    """The job reads a few messages of each uncategorised group with the user's model (local-only
    mail as sensitive) and stores a suggestion; nothing changes until the user applies it. Outside
    01:00 to 06:00 it waits for the night."""
    import types

    from mailr.core import jobs, mailgroup_tasks
    from mailr.intel import categories, llm
    from mailr.intel.categories import CategoryGuess

    box = await _cleanup_box(user, raw)
    await _set(Message, box["plain"][0], local_only=True)
    calls = []

    async def resolve(db, u, task, *, sensitive, probe=False):
        calls.append(("resolve", u.id if u else None, task))
        return types.SimpleNamespace(local=True)

    async def guess(db, u, m, *, sensitive):
        calls.append(("guess", u.id, m.id, sensitive))
        return CategoryGuess("promotions", 0.8, "test model", "llm")

    monkeypatch.setattr(llm, "resolve", resolve)
    monkeypatch.setattr(categories, "llm_guess", guess)
    r = await client.post("/api/v1/cleanup/suggest", json={"by": "sender"})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        j = (await db.execute(select(Job).where(Job.user_id == user.id, Job.kind == "cleanup.suggest"))).scalar_one()
        payload, jid = dict(j.payload), j.id
    ctx = types.SimpleNamespace(job_id=jid, user_id=user.id)

    async def quiet(db, u):
        return False, datetime.now(UTC) + timedelta(hours=3)

    monkeypatch.setattr(mailgroup_tasks, "in_quiet_hours", quiet)
    with pytest.raises(jobs.Retry):
        await mailgroup_tasks.suggest(payload, ctx)
    assert not [c for c in calls if c[0] == "guess"], "nothing is read outside the night"

    async def night(db, u):
        return True, datetime.now(UTC)

    monkeypatch.setattr(mailgroup_tasks, "in_quiet_hours", night)
    await mailgroup_tasks.suggest(payload, ctx)
    assert ("guess", user.id, box["plain"][0], True) in calls, "local-only mail is sensitive"
    assert all(c[1] == user.id for c in calls), "the user's own model"
    g = {x["key"]: x for x in (await client.get("/api/v1/cleanup/groups",
                                                params={"uncategorised": True})).json()["items"]}["news@bulk.example"]
    assert g["suggestion"]["category"] == "promotions"
    async with sessionmaker()() as db:
        assert (await db.get(Message, box["plain"][0])).category is None, "a suggestion only"
    st = (await client.get("/api/v1/cleanup/suggest")).json()
    assert st["status"] in ("queued", "running", "done")


async def test_off_hours_start(client, user):
    from mailr.api.mailgroup_routes import QUIET_END, QUIET_START, next_quiet_start
    from mailr.calendar import tz as tzmod

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        at = await next_quiet_start(db, u)
        zone = await tzmod.user_zone(db, u)
    local = at.astimezone(zone)
    assert QUIET_START <= local.hour < QUIET_END
    assert at >= datetime.now(UTC) - timedelta(seconds=5)
    assert at <= datetime.now(UTC) + timedelta(days=1)


async def test_probe_learns_one_click_for_older_mail(client, user, raw):
    """Mail stored before the one-click marker says "unknown"; the page queues a probe that reads
    the newest message's headers from the store."""
    import types

    from mailr.core import mailgroup_tasks

    ids = await _mailbox(user, raw)
    async with sessionmaker()() as db:
        await db.execute(update(Message).where(Message.user_id == user.id, Message.list_id.isnot(None))
                         .values(unsub_method="unknown"))
        await db.commit()
    r = await client.get("/api/v1/subscriptions", params={"q": "weekly"})
    assert r.json()["items"][0]["unsubscribe"] == "unknown" and r.json()["unknown"] == 1
    async with sessionmaker()() as db:
        j = (await db.execute(select(Job).where(Job.user_id == user.id, Job.kind == "subscriptions.probe"))).scalar_one()
    await mailgroup_tasks.probe({}, types.SimpleNamespace(job_id=j.id, user_id=user.id))
    r = await client.get("/api/v1/subscriptions", params={"q": "weekly"})
    assert r.json()["items"][0]["unsubscribe"] == "one_click"
    assert r.json()["items"][0]["message_id"] is not None
    assert ids["d1"][0]
