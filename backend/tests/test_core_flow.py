
from conftest import mail_date
from helpers.core_flow import _ingest


async def test_ingest_thread_list_actions(client, user, raw):
    m1 = raw("Budget 2027", msgid="<b1@x.org>", body="Numbers for the budget are attached.")
    m2 = raw("Re: Budget 2027", frm="Bob <bob@example.org>", msgid="<b2@x.org>", irt="<b1@x.org>",
             date=mail_date(0, "12:00"), body="Updated numbers.")
    other = raw("Lunch?", frm="Carol <carol@example.org>", date=mail_date(1, "09:00"))
    id1, c1 = await _ingest(user, m1)
    id2, c2 = await _ingest(user, m2)
    _, c3 = await _ingest(user, other)
    assert c1 == c2 != c3

    labels = (await client.get("/api/v1/labels")).json()
    roles = {lb["role"]: lb for lb in labels if lb["role"]}
    assert {"inbox", "sent", "drafts", "junk", "trash", "archive"} <= set(roles)
    assert roles["inbox"]["unread"] == 2

    lst = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert [c["id"] for c in lst["items"]] == [c3, c1]
    conv = next(c for c in lst["items"] if c["id"] == c1)
    assert conv["message_count"] == 2 and conv["unread_count"] == 2
    assert [p["email"] for p in conv["participants"]] == ["alice@example.org", "bob@example.org"]

    full = (await client.get(f"/api/v1/conversations/{c1}")).json()
    assert [m["id"] for m in full["messages"]] == [id1, id2]
    body = (await client.get(f"/api/v1/messages/{id1}/body")).json()
    assert "budget" in body["html"].lower()

    # Duplicate delivery is merged, not stored twice.
    again, _ = await _ingest(user, m1)
    assert again == id1

    # Search (Gmail operators + full text).
    s = (await client.get("/api/v1/conversations", params={"view": "all", "q": "from:bob numbers"})).json()
    assert [c["id"] for c in s["items"]] == [c1]
    s = (await client.get("/api/v1/conversations", params={"view": "all", "q": "lunch -from:bob"})).json()
    assert [c["id"] for c in s["items"]] == [c3]

    # Actions: read, star, archive + undo, label, trash, spam.
    r = await client.post("/api/v1/conversations/actions", json={"ids": [c1], "action": "read"})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/conversations/actions", json={"ids": [c1], "action": "star"})
    assert r.json()["affected"] == 2
    r = await client.post("/api/v1/conversations/actions", json={"ids": [c1], "action": "archive"})
    undo = r.json()["undo"]
    lst = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert [c["id"] for c in lst["items"]] == [c3]
    r = await client.post("/api/v1/conversations/actions",
                          json={"ids": undo["ids"], "action": undo["action"], "labels": undo["labels"]})
    assert r.status_code == 200, r.text
    lst = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert {c["id"] for c in lst["items"]} == {c1, c3}
    st = (await client.get("/api/v1/conversations", params={"view": "starred"})).json()
    assert [c["id"] for c in st["items"]] == [c1]

    lb = (await client.post("/api/v1/labels", json={"name": "Projects/Budget"})).json()
    r = await client.post("/api/v1/conversations/actions", json={"ids": [c1], "action": "add_labels", "label_ids": [lb["id"]]})
    assert r.status_code == 200
    by_label = (await client.get("/api/v1/conversations", params={"view": f"label:{lb['id']}"})).json()
    assert [c["id"] for c in by_label["items"]] == [c1]
    parent = next(x for x in (await client.get("/api/v1/labels")).json() if x["name"] == "Projects")
    by_parent = (await client.get("/api/v1/conversations", params={"view": f"label:{parent['id']}"})).json()
    assert [c["id"] for c in by_parent["items"]] == [c1]

    await client.post("/api/v1/conversations/actions", json={"ids": [c3], "action": "spam"})
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert [c["id"] for c in inbox["items"]] == [c1]
    spam = (await client.get("/api/v1/conversations", params={"view": "spam"})).json()
    assert [c["id"] for c in spam["items"]] == [c3]


async def test_rules_and_snooze(client, user, raw):
    lb = (await client.post("/api/v1/labels", json={"name": "Paperless"})).json()
    r = await client.post("/api/v1/rules", json={"query": "to:docs@example.com",
                                                 "actions": {"add_labels": [lb["id"]], "archive": True}})
    assert r.status_code == 200, r.text
    _, c = await _ingest(user, raw("Scan 001", to="docs@example.com"))
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert c not in [x["id"] for x in inbox["items"]]
    pl = (await client.get("/api/v1/conversations", params={"view": f"label:{lb['id']}"})).json()
    assert [x["id"] for x in pl["items"]] == [c]

    _, c2 = await _ingest(user, raw("Later please"))
    r = await client.post("/api/v1/conversations/actions",
                          json={"ids": [c2], "action": "snooze", "until": "2099-01-01T08:00:00Z"})
    assert r.status_code == 200, r.text
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert c2 not in [x["id"] for x in inbox["items"]]
    sn = (await client.get("/api/v1/conversations", params={"view": "snoozed"})).json()
    assert [x["id"] for x in sn["items"]] == [c2]


async def test_tokens_and_scopes(client, user, raw):
    _, c = await _ingest(user, raw("Token test"))
    r = await client.post("/api/v1/tokens", json={"name": "agent", "scopes": ["read"]})
    tok = r.json()["token"]
    from httpx import AsyncClient
    async with AsyncClient(transport=client._transport, base_url="http://test") as a:
        a.headers["Authorization"] = f"Bearer {tok}"
        ok = await a.get("/api/v1/conversations", params={"view": "inbox"})
        assert ok.status_code == 200 and ok.json()["total"] >= 1
        deny = await a.post("/api/v1/conversations/actions", json={"ids": [c], "action": "archive"})
        assert deny.status_code == 403
        g = await a.get(f"/api/v1/conversations/{c}")
        assert g.headers.get("x-mailr-content") == "untrusted"
    audit = (await client.get("/api/v1/audit")).json()
    assert any(x["action"] == "conversations.get" for x in audit["items"])


async def test_history_import_state_wins(client, user, raw):
    """The first Gmail history import decides inbox/archive/read state for mail mailr already has
    and never changed."""
    m = raw("Old booking", msgid="<old-booking@x.org>")
    mid, cid = await _ingest(user, m)  # arrived via a provider mailbox, lands in Inbox unread
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert cid in [c["id"] for c in inbox["items"]]
    again, _ = await _ingest(user, m, roles=["archive"], label_names=["Hotels"], source="import",
                             keywords={"seen": True}, state_authoritative=True,
                             merge_provider_state=True)
    assert again == mid
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert cid not in [c["id"] for c in inbox["items"]]
    conv = (await client.get(f"/api/v1/conversations/{cid}")).json()["conversation"]
    assert conv["unread_count"] == 0
    names = {lb["id"]: lb["name"] for lb in (await client.get("/api/v1/labels")).json()}
    assert "Hotels" in {names[i] for i in conv["label_ids"]}
    # And the reverse: kept in the Gmail inbox stays in the inbox, unread.
    m2 = raw("Still open", msgid="<still-open@x.org>")
    _, c2 = await _ingest(user, m2, roles=["archive"])
    await _ingest(user, m2, roles=["inbox"], source="import", keywords={}, state_authoritative=True,
                  merge_provider_state=True)
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert c2 in [c["id"] for c in inbox["items"]]


async def test_oidc_start_restarts_on_canonical_host(app, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "base_url", "https://mail.example.com")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://mail.home.arpa") as c:
        r = await c.get("/api/v1/auth/oidc/start", params={"next": "/mail/inbox"})
    assert r.status_code == 307
    assert r.headers["location"].startswith("https://mail.example.com/api/v1/auth/oidc/start?next=")


async def test_oidc_callback_errors_return_to_login(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8025") as c:
        r = await c.get("/api/v1/auth/oidc/callback", params={"code": "x", "state": "y"})
    assert r.status_code == 307 and r.headers["location"].startswith("/login?error=")


async def test_mark_unread_marks_latest_message(client, user, raw):
    _, c = await _ingest(user, raw("Thread", msgid="<u1@x.org>", date=mail_date()))
    await _ingest(user, raw("Re: Thread", msgid="<u2@x.org>", irt="<u1@x.org>", date=mail_date(0, "11:00")))
    await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "read"})
    r = await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "unread"})
    assert r.status_code == 200, r.text
    msgs = (await client.get(f"/api/v1/conversations/{c}")).json()["messages"]
    assert [m["seen"] for m in msgs] == [True, False]


def test_claimed_brand():
    from mailr.core.spamrules import claimed_brand

    assert claimed_brand("Facebook", "support@vibrazzi.site") == "facebook"
    assert claimed_brand("Facebook", "security@facebookmail.com") is None
    assert claimed_brand("PayPal Service", "service@paypal.com") is None
    assert claimed_brand("Microsoft 365 Security", "alert@m365-secure.xyz") == "microsoft"
    assert claimed_brand("Alice Upsdell", "alice@example.com") is None  # 'ups' only as a word


async def test_impersonation_goes_to_spam_and_strangers_do_not_thread(client, user, raw):
    labels = {lb["role"]: lb["id"] for lb in (await client.get("/api/v1/labels")).json() if lb["role"]}
    a_id, a_conv = await _ingest(user, raw("새로운 기기에서 로그인", frm="Facebook <support@scam-one.sbs>",
                                           to="me@example.com"))
    b_id, b_conv = await _ingest(user, raw("새로운 기기에서 로그인", frm="Facebook <support@scam-two.site>",
                                           to="me@example.com", date=mail_date(1)))
    assert a_conv != b_conv  # only the user in common: not one thread
    for mid in (a_id, b_id):
        m = (await client.get(f"/api/v1/messages/{mid}")).json()
        assert m["label_ids"] == [labels["junk"]]
    dec = (await client.get(f"/api/v1/messages/{b_id}/decisions")).json()
    assert any("claims to be Facebook" in d.get("reason", "") for d in dec["decisions"])


async def test_important_section_keeps_read_mail(client, user, raw):
    _, c = await _ingest(user, raw("Big news", msgid="<imp1@x.org>"))
    await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "important"})
    await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "read"})
    imp = (await client.get("/api/v1/conversations", params={"view": "inbox", "section": "important",
                                                              "sections": "important,unread,everything"})).json()
    assert c in [x["id"] for x in imp["items"]]
    iu = (await client.get("/api/v1/conversations", params={"view": "inbox", "section": "important_unread",
                                                             "sections": "important_unread,unread,everything"})).json()
    assert c not in [x["id"] for x in iu["items"]]


async def test_provider_score_threshold_for_new_senders(client, user, raw):
    labels = {lb["role"]: lb["id"] for lb in (await client.get("/api/v1/labels")).json() if lb["role"]}
    near = raw("Account notice", frm="Notice <n@odd-domain.top>",
               extra="X-Spam-Status: No, score=4.28 required=5 tests=FROM_SUSPICIOUS_NTLD=1")
    ok = raw("Hello there", frm="Friend <friend@example.net>",
             extra="X-Spam-Status: No, score=1.2 required=5 tests=NONE")
    near_id, _ = await _ingest(user, near)
    ok_id, _ = await _ingest(user, ok)
    assert (await client.get(f"/api/v1/messages/{near_id}")).json()["label_ids"] == [labels["junk"]]
    assert labels["inbox"] in (await client.get(f"/api/v1/messages/{ok_id}")).json()["label_ids"]
    # Turning the rule off (0) keeps near-misses in the inbox.
    await client.patch("/api/v1/me/settings", json={"spam_upstream_unknown_threshold": 0})
    again_id, _ = await _ingest(user, raw("Account notice 2", frm="Notice <n2@odd-domain.top>",
                                          extra="X-Spam-Status: No, score=4.5 required=5"))
    assert labels["inbox"] in (await client.get(f"/api/v1/messages/{again_id}")).json()["label_ids"]
