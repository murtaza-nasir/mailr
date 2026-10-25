# ACT-SURFACES: api
"""Folders inside each mail account: a label seen through one account.

With "Show each account separately" on, each account's group lists the labels that hold its mail
and the labels made in it (`labels.home_account_id`, "New folder" in the group:
ACT mail.account_folder_create). Each opens `acct:<id>:label:<label id>`, the label narrowed to
that account, counted in GET /counts with the conditions of its list (ACT mail.account_label_view).
Names are shared: one label, each account showing its own mail. A folder made in a mirror-mode
account is created on that account's server at once.
"""

from helpers.audit_fixes import _token
from helpers.mail_accounts import _ids, _two_accounts
from sqlalchemy import select

from mailr.db import session_scope
from mailr.models import Connector, Job, Label


async def _label(client, name, **kw):
    r = await client.post("/api/v1/labels", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def _file(client, conv_ids, label_id):
    r = await client.post("/api/v1/conversations/actions",
                          json={"ids": list(conv_ids), "action": "add_labels", "label_ids": [label_id]})
    assert r.status_code == 200, r.text


async def _jobs(uid, kind="connector.folder_op"):
    async with session_scope() as db:
        return [j.payload for j in (await db.execute(select(Job).where(
            Job.user_id == uid, Job.kind == kind).order_by(Job.id))).scalars()]


async def _enable(*ids):
    async with session_scope() as db:
        for i in ids:
            (await db.get(Connector, i)).enabled = True


async def test_new_folder_in_an_account_has_that_home(client, user, raw):
    a, b, _ = await _two_accounts(user, raw)
    await _enable(a, b)
    lb = await _label(client, "Receipts", home_account_id=a)
    assert lb["home_account_id"] == a
    listed = {x["name"]: x for x in (await client.get("/api/v1/labels")).json()}
    assert listed["Receipts"]["home_account_id"] == a
    # The account is a mirror: the folder is made on its server now (the job), not on the other's.
    assert await _jobs(user.id) == [{"connector_id": a, "op": "create", "name": "Receipts"}]
    # The same name from the other account is the same label; its home stays.
    again = await _label(client, "receipts", home_account_id=b)
    assert again["id"] == lb["id"] and again["home_account_id"] == a
    # Not one of the caller's accounts.
    r = await client.post("/api/v1/labels", json={"name": "Elsewhere", "home_account_id": 999999})
    assert r.status_code == 404
    # A plain label has no home.
    assert (await _label(client, "Plain"))["home_account_id"] is None
    # The home can be changed and cleared; the new home gets the folder too.
    r = await client.patch(f"/api/v1/labels/{lb['id']}", json={"home_account_id": b})
    assert r.status_code == 200 and r.json()["home_account_id"] == b
    assert (await _jobs(user.id))[-1] == {"connector_id": b, "op": "create", "name": "Receipts"}
    r = await client.patch(f"/api/v1/labels/{lb['id']}", json={"home_account_id": None})
    assert r.status_code == 200 and r.json()["home_account_id"] is None
    # A rename leaves the home alone.
    await client.patch(f"/api/v1/labels/{lb['id']}", json={"home_account_id": a})
    r = await client.patch(f"/api/v1/labels/{lb['id']}", json={"name": "Bills"})
    assert r.json()["home_account_id"] == a and r.json()["name"] == "Bills"


async def test_store_mode_account_gets_no_upstream_folder(client, user, raw):
    a, _b, _ = await _two_accounts(user, raw)
    await _enable(a)
    async with session_scope() as db:
        (await db.get(Connector, a)).mode = "store"
    await _label(client, "Kept here", home_account_id=a)
    assert await _jobs(user.id) == []


async def test_removing_the_account_clears_the_home(client, user, raw):
    a, _b, _ = await _two_accounts(user, raw)
    lb = await _label(client, "Travel", home_account_id=a)
    async with session_scope() as db:
        await db.delete(await db.get(Connector, a))
    async with session_scope() as db:
        assert (await db.get(Label, lb["id"])).home_account_id is None


async def test_labels_through_an_account_list_and_count_alike(client, user, raw):
    a, b, ids = await _two_accounts(user, raw)
    rec = await _label(client, "Receipts")
    nested = await _label(client, "Receipts/2026")
    empty = await _label(client, "Empty folder", home_account_id=b)
    await _file(client, [ids["a1"], ids["b1"]], rec["id"])
    await _file(client, [ids["a2"]], nested["id"])
    await client.post("/api/v1/conversations/actions", json={"ids": [ids["a1"]], "action": "read"})
    off = (await client.get("/api/v1/counts")).json()["views"]
    assert not [v for v in off if v.startswith("acct:")]
    await client.patch("/api/v1/me/settings", json={"account_folders": True})
    views = (await client.get("/api/v1/counts")).json()["views"]
    # Every count equals the length of the list it opens.
    for key in [k for k in views if ":label:" in k]:
        rows = (await client.get("/api/v1/conversations", params={"view": key, "limit": 200})).json()["items"]
        assert views[key]["total"] == len(rows), (key, views[key], len(rows))
        assert views[key]["unread"] == sum(1 for x in rows if x["unread_count"] > 0), key
    # The parent label counts its nested label's mail, per account.
    assert views[f"acct:{a}:label:{rec['id']}"]["total"] == 2
    assert views[f"acct:{a}:label:{nested['id']}"]["total"] == 1
    assert views[f"acct:{b}:label:{rec['id']}"] == {"unread": 1, "total": 1}
    # No mail of that account: no entry (the navigation shows a home folder at 0).
    assert f"acct:{b}:label:{nested['id']}" not in views
    assert f"acct:{b}:label:{empty['id']}" not in views
    # The unified label keeps all its mail; each account's view holds only its own.
    assert await _ids(client, "", f"label:{rec['id']}") == {ids["a1"], ids["a2"], ids["b1"]}
    assert await _ids(client, "", f"acct:{b}:label:{rec['id']}") == {ids["b1"]}
    assert await _ids(client, "", f"acct:{a}:label:{rec['id']}") == {ids["a1"], ids["a2"]}
    # Mail of one account filed in a folder made in another stays where it arrived.
    await _file(client, [ids["a1"]], empty["id"])
    assert await _ids(client, "", f"acct:{b}:label:{empty['id']}") == set()
    assert await _ids(client, "", f"acct:{a}:label:{empty['id']}") == {ids["a1"]}
    # Trash and Spam stay out, as in the unified label.
    await client.post("/api/v1/conversations/actions", json={"ids": [ids["b1"]], "action": "trash"})
    views = (await client.get("/api/v1/counts")).json()["views"]
    assert f"acct:{b}:label:{rec['id']}" not in views
    assert (await client.get("/api/v1/conversations", params={"view": f"acct:{a}:label:x"})).status_code == 400


async def test_a_restricted_token_counts_only_its_account(client, user, raw):
    a, b, ids = await _two_accounts(user, raw)
    rec = await _label(client, "Receipts")
    await _file(client, [ids["a1"], ids["b1"]], rec["id"])
    await client.patch("/api/v1/me/settings", json={"account_folders": True})
    r = await client.post("/api/v1/tokens", json={"name": "both", "scopes": ["read"], "connector_ids": [a]})
    from httpx import AsyncClient

    t = AsyncClient(transport=client._transport, base_url="http://test")
    t.headers["Authorization"] = f"Bearer {r.json()['token']}"
    views = (await t.get("/api/v1/counts")).json()["views"]
    # One account visible: no account folders at all.
    assert not [k for k in views if k.startswith("acct:")]
    assert await _ids(t, "", f"acct:{b}:label:{rec['id']}") == set()
    # A token cannot make a folder in an account it may not see.
    t2 = await _token(client, ["read", "label"])
    assert (await t2.post("/api/v1/labels", json={"name": "X", "home_account_id": 999999})).status_code == 404


async def test_folder_is_created_on_the_imap_server(client, user):
    """End to end against the test IMAP server: "New folder" in a mirror account's group makes the
    folder upstream once the job runs (nested names use the server's delimiter)."""
    from helpers.connectors import create_connector, imap, make_upstream_account, run_jobs, run_sync

    email, pw = make_upstream_account()
    conn = await create_connector(client, email, pw, mode="mirror")
    await run_sync(conn["id"])
    lb = await _label(client, "Projects/Garden", home_account_id=conn["id"])
    assert lb["home_account_id"] == conn["id"]
    assert await run_jobs("connector.folder_op", user.id) == 1
    c = imap(email, pw)
    names = [f[2] for f in c.list_folders()]
    delim = c.list_folders()[0][1]
    c.logout()
    d = delim.decode() if isinstance(delim, bytes) else (delim or "/")
    assert f"Projects{d}Garden" in names, names
    # The next sync keeps it one label, with its home.
    await run_sync(conn["id"])
    same = [x for x in (await client.get("/api/v1/labels")).json() if x["name"] == "Projects/Garden"]
    assert len(same) == 1 and same[0]["home_account_id"] == conn["id"]
