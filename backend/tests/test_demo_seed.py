"""`mailr demo-seed` (mailr/dev/demo.py): every scenario group the documentation
screenshots need is seeded, a second run resets to the same state, and nothing seeded names a real
person, company or host.

Needs the dev services (Stalwart; Radicale on :15232 for the calendar groups, the fake upstream
for the accounts). Run it on its own database: MAILR_TEST_DB=mailr_test_demo."""

from __future__ import annotations

import json
import re
from email import message_from_bytes
from email.policy import default as email_default
from pathlib import Path

import pytest
import pytest_asyncio
from dev_services import radicale_up
from speakr_fake import BASE, TOKEN, speakr  # noqa: F401  (the fake Speakr fixture)
from sqlalchemy import text

from mailr.db import sessionmaker

# Words that must never appear in seeded data: the deployment, real mail providers and
# real companies a demo might be tempted to use (whole words where they are common substrings).
DENY_SUBSTRINGS = ["gmail.com", "192.168.", ".lan", "googlemail", "yahoo.", "hotmail.", "outlook.com", "icloud.",
                   "protonmail"]
# The deployment's own names and addresses: the release deny-list, where the checkout has it.
_DENY_FILE = Path(__file__).resolve().parents[2] / "tools" / "public-deny.txt"
DENY_PATTERNS = [re.compile(ln.strip(), re.IGNORECASE) for ln in
                 (_DENY_FILE.read_text(encoding="utf-8").splitlines() if _DENY_FILE.exists() else [])
                 if ln.strip() and not ln.lstrip().startswith(("#", "allow "))]
DENY_WORDS = ["google", "github", "gitlab", "linkedin", "facebook", "instagram", "twitter", "slack", "zoom",
              "fedex", "usps", "dhl", "amazon", "paypal", "stripe", "dropbox", "microsoft", "apple", "chase", "chrome",
              "visa", "mastercard", "lighthouse report",
              "wells fargo", "mailchimp", "starbucks", "verizon", "comcast", "spotify", "netflix", "uber"]
_ADDR = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z0-9-]+)")
_RESERVED_TLDS = (".example", ".test", ".invalid", ".localhost")
_RESERVED_DOMAINS = ("example.com", "example.org", "example.net")


def reserved(domain: str) -> bool:
    d = domain.lower().rstrip(".")
    return d.endswith(_RESERVED_TLDS) or any(d == r or d.endswith("." + r) for r in _RESERVED_DOMAINS)


async def q(sql: str, **params) -> list:
    async with sessionmaker()() as db:
        return list((await db.execute(text(sql), params)).all())


async def one(sql: str, **params):
    rows = await q(sql, **params)
    return rows[0][0] if rows else None


@pytest_asyncio.fixture
async def demo_reset():
    """Undo the seed after the test: the demo user, its store account and calendars, and the
    instance-wide "demo" model endpoint (a local endpoint with a chat default, which would answer
    every later test's local-only chat in place of the endpoints that test sets up)."""
    yield
    from mailr.dev import demo

    await demo.reset()


# The whole fictional instance: about 2 minutes here, over 5 on a shared CI runner.
@pytest.mark.timeout(900)
async def test_demo_seed_covers_every_scenario_and_stays_fictional(app, demo_reset):
    from mailr.dev import demo
    from mailr.dev import demo_content as dc

    first = await demo.seed()
    out = await demo.seed()  # a second run resets: the first user is gone, the counts are the same
    u = out["user_id"]
    assert await one("SELECT count(*) FROM users WHERE lower(email) = :e", e=dc.WORK) == 1
    assert await one("SELECT count(*) FROM users WHERE id = :u", u=first["user_id"]) == 0
    assert out["counts"] == first["counts"]
    cal = radicale_up()

    # accounts: three mail accounts, one local-only, a signature on every sending address
    assert await one("SELECT count(*) FROM connectors WHERE user_id=:u AND kind='imap'", u=u) == 3
    assert await one("SELECT count(*) FROM connectors WHERE user_id=:u AND data_class='local_only'", u=u) == 1
    assert await one("SELECT count(*) FROM identities WHERE user_id=:u AND signature_html <> ''", u=u) == 3
    # Alex's account picture: the generated illustration, uploaded; the first run's picture went with its user
    from mailr.core import account_picture

    assert account_picture.info(u)["has"] and account_picture.info(u)["source"] == "upload"
    assert not account_picture.picture_path(first["user_id"]).exists()

    # organize: coloured and nested labels, filters, the active layout with tabs that claim mail,
    # favorites, templates, VIP senders
    assert await one("SELECT count(*) FROM labels WHERE user_id=:u AND role IS NULL AND color LIKE '@%'", u=u) >= 12
    assert await one("SELECT count(*) FROM labels WHERE user_id=:u AND name LIKE 'Clients/%'", u=u) == 3
    assert await one("SELECT count(*) FROM rules WHERE user_id=:u", u=u) >= 5
    settings = await one("SELECT settings FROM users WHERE id=:u", u=u)
    assert settings["inbox_type"] == "layout:studio"
    tabs = settings["inbox_layouts"][0]["tabs"]
    assert len(tabs) >= 4 and any(t.get("claims") for t in tabs)
    assert any(f.startswith("label:") for f in settings["nav_favorites"]) and settings["vip"]
    assert await one("SELECT count(*) FROM templates WHERE user_id=:u", u=u) == 3

    # translation: Lucía's Spanish email is the one message offered a translation; English mail is not
    from mailr.core import langid

    if langid.available():
        langs = await q("SELECT subject, lang FROM messages WHERE user_id=:u AND lang IS NOT NULL AND lang <> 'en'", u=u)
        assert [lang for _, lang in langs] == ["es"], langs

    # mail: categories, spam, attachments, inline images, primary actions, signals, states
    assert await one("SELECT count(*) FROM messages WHERE user_id=:u AND NOT draft", u=u) >= 50
    cats = {r[0] for r in await q("SELECT DISTINCT coalesce(category, 'primary') FROM conversations WHERE user_id=:u", u=u)}
    assert {"primary", "updates", "promotions", "social", "forums", "purchases"} <= cats
    junk = await one("SELECT count(*) FROM messages m JOIN labels l ON l.id = ANY(m.label_ids) "
                     "WHERE m.user_id=:u AND l.role='junk'", u=u)
    assert junk >= 3
    atts = [a for r in await q("SELECT attachments FROM messages WHERE user_id=:u", u=u) for a in r[0] or []]
    assert any(a.get("type") == "application/pdf" for a in atts)
    assert any(a.get("type") == "image/png" and not a.get("inline") for a in atts)
    assert any(a.get("inline") for a in atts)
    assert any(a.get("type", "").startswith("audio/") for a in atts)  # for "Send to Speakr"
    kinds = {r[0] for r in await q("SELECT primary_action->>'kind' FROM messages WHERE user_id=:u "
                                   "AND primary_action IS NOT NULL", u=u)}
    assert {"track", "pay", "code", "checkin", "statement"} <= kinds
    deadlines = await q("SELECT source_ref FROM label_decisions WHERE user_id=:u AND kind='smart' AND target='deadline'", u=u)
    assert len(deadlines) >= 4
    assert await one("SELECT count(*) FROM label_decisions WHERE user_id=:u AND kind='smart' AND target='needs_reply'", u=u) >= 4
    conv = (await q("SELECT bool_or(starred), bool_or(snoozed_until IS NOT NULL), bool_or(muted), "
                    "bool_or(follow_up_at IS NOT NULL), bool_or(nudged_at IS NOT NULL), bool_or(important), "
                    "bool_or(NOT important) FROM conversations WHERE user_id=:u", u=u))[0]
    assert all(conv), conv
    assert await one("SELECT count(*) FROM messages WHERE user_id=:u AND draft AND NOT deleted", u=u) >= 3
    assert await one("SELECT count(*) FROM outbox WHERE user_id=:u AND status='queued' AND send_at > now()", u=u) == 1
    assert await one("SELECT count(*) FROM messages WHERE user_id=:u AND local_only", u=u) == 3
    assert await one("SELECT count(*) FROM messages WHERE user_id=:u AND list_id IS NOT NULL", u=u) >= 5

    # people: groups (one suggested), details from a signature, contacts from the address book
    assert await one("SELECT count(*) FROM person_groups WHERE user_id=:u AND state='active'", u=u) >= 2
    assert await one("SELECT count(*) FROM person_groups WHERE user_id=:u AND state='suggested'", u=u) == 1
    assert await one("SELECT count(*) FROM person_suggestions WHERE user_id=:u AND state='suggested'", u=u) == 2

    # assistant: orders and a paused routine, a brief, approvals waiting and decided, label
    # suggestions, mail in the importance review, a chat, the scripted model
    assert await one("SELECT count(*) FROM assistant_orders WHERE user_id=:u", u=u) >= 5
    assert await one("SELECT count(*) FROM assistant_orders WHERE user_id=:u AND trigger='schedule' AND NOT enabled", u=u) == 1
    assert await one("SELECT count(*) FROM assistant_runs WHERE user_id=:u AND 'brief' = ANY(delivered)", u=u) == 1
    approvals = {(r[0], r[1]) for r in await q("SELECT action, status FROM pending_actions WHERE user_id=:u", u=u)}
    assert {("mail.send", "pending"), ("orders.write", "pending"), ("rules.write", "pending")} <= approvals
    assert {s for _a, s in approvals} >= {"pending", "denied"} and any(s in ("approved", "executed") for _a, s in approvals)
    # a finished bulk review (/sweep) waiting in its chat: the card from agent/review.py card()
    rv = await q("SELECT r.status, p.status, p.payload->>'op', p.preview FROM mail_reviews r "
                 "JOIN pending_actions p ON p.id = r.approval_id WHERE r.user_id=:u AND r.chat_session_id IS NOT NULL", u=u)
    assert len(rv) == 1 and rv[0][:3] == ("done", "pending", "review"), rv
    card = rv[0][3]["review"]
    assert [g["move"] for g in card["groups"]] == ["trash", "read"] and len(card["groups"][0]["senders"]) == 2
    assert card["kept_count"] == 2 and card["unsure_count"] == 1
    assert await one("SELECT count(*) FROM label_decisions WHERE user_id=:u AND kind='label' AND status='suggested'", u=u) >= 3
    assert await one(
        "SELECT count(*) FROM label_decisions d JOIN messages m ON m.id = d.message_id WHERE d.user_id=:u "
        "AND d.kind='importance' AND d.source IN ('heuristic','classifier') AND m.received_at > now() - interval '1 day'",
        u=u) >= 5
    assert await one("SELECT count(*) FROM chat_messages c JOIN chat_sessions s ON s.id=c.session_id WHERE s.user_id=:u", u=u) >= 2
    assert await one("SELECT count(*) FROM model_endpoints WHERE name='demo' AND defaults ? 'chat'") == 1

    if not cal:
        pytest.skip("Radicale is not running on :15232: calendar, tasks and reminders not checked")

    # calendar: several coloured calendars, recurring and all-day events, an invitation waiting for
    # an answer, a guest's reply applied, a counter proposal, an event made from mail, a conflict
    assert await one("SELECT count(*) FROM cal_collections WHERE user_id=:u AND 'VEVENT' = ANY(components) "
                     "AND color IS NOT NULL AND deleted_at IS NULL", u=u) >= 3
    ev = (await q("SELECT bool_or(recurring), bool_or(all_day), bool_or(my_partstat='NEEDS-ACTION'), "
                  "bool_or(is_organizer AND attendee_count > 1) FROM cal_objects WHERE user_id=:u AND kind='event' "
                  "AND deleted_at IS NULL", u=u))[0]
    assert all(ev), ev
    itip = [r[0] for r in await q("SELECT upstream->'itip' FROM messages WHERE user_id=:u AND upstream ? 'itip'", u=u)]
    assert {"REQUEST", "REPLY", "COUNTER", "CANCEL"} <= {i["method"] for i in itip}
    assert any(i.get("counter") for i in itip)
    assert {i["method"] for i in itip if i.get("applied")} >= {"REQUEST", "REPLY", "CANCEL"}
    assert any(i.get("applied") and "Updated" in i.get("detail", "") for i in itip)
    assert await one("SELECT count(*) FROM cal_objects WHERE user_id=:u AND status='CANCELLED'", u=u) == 1
    assert await one("SELECT count(*) FROM event_links WHERE user_id=:u AND kind='event'", u=u) >= 1
    assert await one(
        "SELECT count(*) FROM cal_occurrences a JOIN cal_occurrences b ON a.user_id=b.user_id AND a.object_id < b.object_id "
        "AND a.start_at < b.end_at AND b.start_at < a.end_at AND NOT a.all_day AND NOT b.all_day WHERE a.user_id=:u", u=u) >= 1

    # tasks: two lists, subtasks, repeating, overdue, tags, done, made from mail
    assert await one("SELECT count(*) FROM cal_collections WHERE user_id=:u AND 'VTODO' = ANY(components) AND NOT 'VEVENT' = ANY(components) "
                     "AND deleted_at IS NULL", u=u) == 2
    tk = (await q("SELECT bool_or(parent_uid IS NOT NULL), bool_or(recurring), bool_or(due_date < current_date AND "
                  "status <> 'COMPLETED'), bool_or(cardinality(categories) > 0), bool_or(status = 'COMPLETED') "
                  "FROM cal_objects WHERE user_id=:u AND kind='task' AND deleted_at IS NULL", u=u))[0]
    assert all(tk), tk
    assert await one("SELECT count(*) FROM event_links WHERE user_id=:u AND kind='task'", u=u) >= 2
    assert await one("SELECT count(*) FROM contact_cards WHERE user_id=:u", u=u) >= 5

    # reminders: fired, missed (fired late), snoozed and dismissed
    rs = (await q("SELECT bool_or(fired_at IS NOT NULL AND fired_at - fire_at < interval '1 minute'), "
                  "bool_or(fired_at - fire_at > interval '1 hour') FROM reminder_schedule WHERE user_id=:u", u=u))[0]
    assert all(rs), rs
    states = {r[0] for r in await q("SELECT state FROM reminder_states WHERE user_id=:u", u=u)}
    assert {"snoozed", "handled"} <= states

    # Nothing seeded names a real address, host, person or company.
    await _scan(u)


async def _scan(u: int) -> None:
    from mailr.core.store import user_store
    from mailr.models import Message, User

    texts: list[str] = []
    for sql in (
        ("SELECT from_email, from_name, \"to\"::text, cc::text, subject, body_text, attachments::text, list_id, "
         "coalesce(list_unsubscribe, ''), coalesce(primary_action::text, '') FROM messages WHERE user_id=:u"),
        "SELECT email, name, organization, notes, title, phones::text, addresses::text FROM people WHERE user_id=:u",
        "SELECT name FROM labels WHERE user_id=:u",
        "SELECT name, query, actions::text FROM rules WHERE user_id=:u",
        "SELECT name, subject, body_html FROM templates WHERE user_id=:u",
        "SELECT email, name, signature_html FROM identities WHERE user_id=:u",
        "SELECT name, config::text FROM connectors WHERE user_id=:u",
        "SELECT ics FROM cal_objects WHERE user_id=:u",
        "SELECT displayname FROM cal_collections WHERE user_id=:u",
        "SELECT data::text FROM contact_cards WHERE user_id=:u",
        "SELECT name, query, question, errand, actions::text FROM assistant_orders WHERE user_id=:u",
        "SELECT output FROM assistant_runs WHERE user_id=:u",
        "SELECT preview::text, payload::text FROM pending_actions WHERE user_id=:u",
        "SELECT reason, source_ref FROM label_decisions WHERE user_id=:u",
        "SELECT value FROM person_suggestions WHERE user_id=:u",
        "SELECT name, name_hint, evidence::text FROM person_groups WHERE user_id=:u",
        "SELECT payload::text FROM outbox WHERE user_id=:u",
        "SELECT content FROM chat_messages c JOIN chat_sessions s ON s.id = c.session_id WHERE s.user_id=:u",
        "SELECT settings::text, name, email FROM users WHERE id=:u",
    ):
        for row in await q(sql, u=u):
            texts += [str(v) for v in row if v]
    async with sessionmaker()() as db:
        user = await db.get(User, u)
        store = await user_store(db, user)
        from sqlalchemy import select

        for m in (await db.execute(select(Message).where(Message.user_id == u))).scalars():
            # Headers and decoded parts, not the transfer encoding: base64 of generated media is
            # random text that can spell anything. Images and audio are generated, never text.
            parsed = message_from_bytes(await store.get_raw(m), policy=email_default)
            for part in parsed.walk():
                texts += [f"{k}: {v}" for k, v in part.items()]
                if part.is_multipart() or part.get_content_maintype() in ("image", "audio"):
                    continue
                texts.append((part.get_payload(decode=True) or b"").decode("utf-8", "replace"))
    blob = "\n".join(texts)
    low = blob.lower()
    for s in DENY_SUBSTRINGS:
        assert s not in low, f"seeded data contains {s!r}"
    for rx in DENY_PATTERNS:
        assert not rx.search(blob), f"seeded data matches the deny-list ({rx.pattern})"
    for wd in DENY_WORDS:
        assert not re.search(rf"\b{re.escape(wd)}\b", low), f"seeded data names {wd!r}"
    bad = sorted({d for d in _ADDR.findall(blob) if not reserved(d)})
    assert not bad, f"seeded addresses on non-reserved domains: {bad}"
    urls = sorted({h for h in re.findall(r"https?://([A-Za-z0-9.-]+)", blob)
                   if not reserved(h) and h not in ("localhost", "127.0.0.1", "schema.org")})
    assert not urls, f"seeded links to non-reserved hosts: {urls}"
    json.dumps(blob)  # all of it is text


def test_reserved_domains():
    assert reserved("northwind.example") and reserved("mail.example.com") and reserved("upstream.test")
    assert not reserved("gmail.com") and not reserved("example.co") and not reserved("notexample.com")


def test_seeded_sync_stamps_read_fresh_to_the_server_with_a_pinned_now():
    """A seed pinned days in the past (the shots' --now) still stamps "last synced" a few minutes
    before the real time: the server's health check reads the account as up to date, as the page's
    pinned clock does (setup-accounts-list showed "Not synced for 4 days" beside "synced 3 minutes ago")."""
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from mailr.core import accounthealth
    from mailr.dev.demo import Clock

    clock = Clock(now=datetime.now(UTC) - timedelta(days=4))
    c = SimpleNamespace(status="idle", status_detail="", enabled=True, mode="store", config={},
                        last_sync_at=clock.synced(3))
    assert accounthealth.of(c)["state"] == "ok"
    later = Clock(now=datetime.now(UTC) + timedelta(hours=1))
    assert later.synced(3) == later.now - timedelta(minutes=3)


async def test_demo_speakr_recordings_reach_the_mirror(app, speakr, monkeypatch, demo_reset):
    """tools/demo/speakr_demo.py loads the demo recordings into a Speakr (here the fake), and
    `demo-seed --speakr-url --speakr-token` connects it and syncs them."""
    import importlib.util
    import sys
    from pathlib import Path

    import httpx as _httpx

    from mailr.dev import demo

    spec = importlib.util.spec_from_file_location(
        "speakr_demo", Path(__file__).resolve().parents[2] / "tools" / "demo" / "speakr_demo.py")  # noqa: ASYNC240
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    real_client = _httpx.Client
    deleted: list[str] = []

    summaries: dict[int, str] = {}
    named: dict[int, dict] = {}

    def handler(request):
        if request.method == "DELETE":
            deleted.append(request.url.path)
            speakr.recordings.pop(int(request.url.path.rsplit("/", 1)[-1]), None)
            return _httpx.Response(204)
        m = re.fullmatch(r"/api/v1/recordings/(\d+)(/summary|/speakers/assign)?", request.url.path)
        if m and int(m.group(1)) in speakr.recordings:
            rid = int(m.group(1))
            if request.method == "GET" and speakr.recordings[rid]["status"] == "PENDING":
                # A real Speakr transcribes an upload in the background; the loader waits for it
                # (unless --no-wait), so the fake finishes the job when it is first polled.
                speakr.recordings[rid]["status"] = "COMPLETED"
            if m.group(2) == "/speakers/assign" and request.method == "PUT":
                named[rid] = json.loads(request.content)["speaker_map"]
                return _httpx.Response(200, json={"success": True})
            if m.group(2) == "/summary" and request.method == "PUT":
                summaries[rid] = json.loads(request.content)["summary"]
                speakr.summaries[rid] = summaries[rid]
                return _httpx.Response(200, json={"summary": summaries[rid]})
        return speakr.handler(request)

    monkeypatch.setattr(loader.httpx, "Client", lambda **kw: real_client(transport=_httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(loader.time, "sleep", lambda _s: None)
    # --summary scripted: the fake never summarises, so Speakr's own summary would be waited for.
    monkeypatch.setattr(sys, "argv", ["speakr_demo", "--url", BASE, "--token", TOKEN, "--reset",
                                      "--summary", "scripted"])
    loader.main()
    assert len(speakr.recordings) == 4 and not deleted
    loader.main()  # again: the marked recordings are replaced, not doubled
    assert len(speakr.recordings) == 4 and len(deleted) == 4
    assert all(r["status"] == "COMPLETED" for r in speakr.recordings.values())
    assert set(summaries) >= set(speakr.recordings) and all(summaries.values())

    out = await demo.seed(scenarios=["speakr"], speakr_url=BASE, speakr_token=TOKEN)
    assert "speakr" in out["scenarios"]
    titles = {r[0] for r in await q("SELECT title FROM speakr_recordings WHERE user_id=:u", u=out["user_id"])}
    assert {"Lumen Coffee: kickoff call", "Studio standup"} <= titles
