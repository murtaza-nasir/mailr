# ACT-SURFACES: api
"""Several mail accounts in one mailbox (core/mailaccounts.py).

The mailbox is unified. `account:<address or id>` narrows a search to the mail of one account:
mail that arrived through it, and mail sent from one of its addresses (ACT mail.search_account).
With "Show each account separately" on (ACT mail.account_folders), each account has folders of its own (`acct:<id>:<view>`),
GET /counts counts them with the conditions of their lists (ACT mail.account_view), and each
account has a colour (ACT mail.account_colour). A token limited to some accounts sees only those.
"""

from helpers.audit_fixes import _ingest, _token
from helpers.mail_accounts import _account, _ids, _two_accounts

from mailr.models import Connector


async def test_account_operator_by_address_and_id(client, user, raw):
    _a, b, ids = await _two_accounts(user, raw)
    assert await _ids(client, "account:work@example.org") == {ids["a1"], ids["a2"]}
    # The id works too, and sent mail from the account's address belongs to it.
    assert await _ids(client, f"account:{b}") == {ids["b1"], ids["b_sent"]}
    assert await _ids(client, "account:HOME@example.net") == {ids["b1"], ids["b_sent"]}
    # Negation, and combined with other words.
    assert ids["a1"] not in await _ids(client, "-account:work@example.org")
    assert await _ids(client, "account:work@example.org lunch") == {ids["a2"]}
    # An address that is no account matches nothing; the unified list keeps everything.
    assert await _ids(client, "account:nobody@example.com") == set()
    assert {ids["a1"], ids["b1"], ids["none"]} <= await _ids(client, "", "all")


async def test_account_operator_in_a_filter_preview(client, user, raw):
    await _two_accounts(user, raw)
    r = await client.post("/api/v1/rules/preview", json={"query": "account:work@example.org"})
    assert r.status_code == 200 and r.json()["count"] == 2, r.text


async def test_restricted_token_names_only_its_accounts(client, user, raw):
    a, b, ids = await _two_accounts(user, raw)
    r = await client.post("/api/v1/tokens", json={"name": "only work", "scopes": ["read"], "connector_ids": [a]})
    assert r.status_code == 200, r.text
    from httpx import AsyncClient

    t = AsyncClient(transport=client._transport, base_url="http://test")
    t.headers["Authorization"] = f"Bearer {r.json()['token']}"
    assert await _ids(t, "account:work@example.org") == {ids["a1"], ids["a2"]}
    assert await _ids(t, f"account:{b}") == set()
    assert await _ids(t, "account:home@example.net") == set()
    # Its counts list only the account it may see; the other's folder lists nothing.
    counts = (await t.get("/api/v1/counts")).json()
    assert [x["id"] for x in counts["accounts"]] == [a]
    assert await _ids(t, "", f"acct:{b}:inbox") == set()


async def test_local_only_account_needs_a_local_only_token(client, user, raw):
    uni = await _account(user.id, "staff@uni.example.edu", data_class="local_only")
    _, cid, _ = await _ingest(user, raw("Student record", to="staff@uni.example.edu"), uni)
    assert await _ids(client, "account:staff@uni.example.edu") == {cid}
    hosted = await _token(client, ["read"])
    assert await _ids(hosted, "account:staff@uni.example.edu") == set()
    assert [x["address"] for x in (await hosted.get("/api/v1/counts")).json()["accounts"]] == []
    local = await _token(client, ["read"], local_only=True)
    assert await _ids(local, "account:staff@uni.example.edu") == {cid}


async def test_account_folders_list_and_count_alike(client, user, raw):
    a, b, ids = await _two_accounts(user, raw)
    await client.post("/api/v1/conversations/actions", json={"ids": [ids["a2"]], "action": "spam"})
    await client.post("/api/v1/conversations/actions", json={"ids": [ids["a1"]], "action": "read"})
    off = (await client.get("/api/v1/counts")).json()
    # Off (the default): the accounts are listed, their folders are not counted.
    assert [x["address"] for x in off["accounts"]] == ["work@example.org", "home@example.net"]
    assert not [v for v in off["views"] if v.startswith("acct:")]
    r = await client.patch("/api/v1/me/settings", json={"account_folders": True})
    assert r.status_code == 200 and r.json()["account_folders"] is True
    assert (await client.patch("/api/v1/me/settings", json={"account_folders": "yes"})).status_code == 400
    counts = (await client.get("/api/v1/counts")).json()
    views = counts["views"]
    for acct in (a, b):
        for v in ("inbox", "drafts", "spam"):
            key = f"acct:{acct}:{v}"
            r = await client.get("/api/v1/conversations", params={"view": key, "limit": 200})
            rows = r.json()["items"]
            assert views[key]["total"] == len(rows), (key, views[key], len(rows))
            assert views[key]["unread"] == sum(1 for x in rows if x["unread_count"] > 0), key
        sched = (await client.get("/api/v1/outbox", params={"scheduled": "true", "account": acct})).json()
        assert views[f"acct:{acct}:scheduled"]["total"] == len(sched)
    assert views[f"acct:{a}:inbox"] == {"unread": 0, "total": 1}
    assert views[f"acct:{a}:spam"]["total"] == 1
    assert views[f"acct:{b}:inbox"] == {"unread": 1, "total": 1}
    # The folders hold only that account's mail; Sent holds what was sent from its address.
    assert await _ids(client, "", f"acct:{b}:inbox") == {ids["b1"]}
    assert ids["none"] not in await _ids(client, "", f"acct:{a}:inbox")
    # A search inside an account's folder stays in it.
    assert await _ids(client, "garden", f"acct:{a}:inbox") == set()
    assert (await client.get("/api/v1/conversations", params={"view": "acct:x:inbox"})).status_code == 400
    assert (await client.get("/api/v1/conversations", params={"view": f"acct:{a}:starred"})).status_code == 400


async def test_select_all_in_an_account_folder_stays_in_it(client, user, raw):
    a, _b, ids = await _two_accounts(user, raw)
    r = await client.post("/api/v1/conversations/actions",
                          json={"action": "archive", "query": {"view": f"acct:{a}:inbox", "q": ""}})
    assert r.status_code == 200, r.text
    inbox = await _ids(client, "", "inbox")
    assert ids["a1"] not in inbox and ids["a2"] not in inbox and ids["b1"] in inbox


async def test_one_account_has_no_folders_of_its_own(client, user, raw):
    a = await _account(user.id, "solo@example.org")
    await _ingest(user, raw("Hello", to="solo@example.org"), a)
    await client.patch("/api/v1/me/settings", json={"account_folders": True})
    counts = (await client.get("/api/v1/counts")).json()
    assert [x["address"] for x in counts["accounts"]] == ["solo@example.org"]
    assert not [v for v in counts["views"] if v.startswith("acct:")]


async def test_account_colours(client, user, raw):
    a, b, _ = await _two_accounts(user, raw)
    accts = {x["id"]: x for x in (await client.get("/api/v1/counts")).json()["accounts"]}
    # Handed out in order, distinct.
    assert (accts[a]["color"], accts[b]["color"]) == ("blue", "orange")
    r = await client.patch(f"/api/v1/connectors/{a}", json={"color": "teal"})
    assert r.status_code == 200, r.text
    accts = {x["id"]: x for x in (await client.get("/api/v1/counts")).json()["accounts"]}
    assert accts[a]["color"] == "teal" and accts[b]["color"] == "blue"
    assert (await client.patch(f"/api/v1/connectors/{a}", json={"color": "#ff0000"})).status_code == 400
    assert (await client.patch(f"/api/v1/connectors/{a}", json={"color": "auto"})).status_code == 200
    accts = {x["id"]: x for x in (await client.get("/api/v1/counts")).json()["accounts"]}
    assert accts[a]["color"] == "orange" or accts[a]["color"] == "blue"
    assert accts[a]["color"] != accts[b]["color"]
    # Compose in an account's folders starts from its own identity.
    assert accts[b]["identity_id"] is not None
    # A token cannot change it.
    t = await _token(client, ["read", "label"])
    assert (await t.patch(f"/api/v1/connectors/{a}", json={"color": "red"})).status_code == 403


def test_colours_are_assigned_in_order():
    from mailr.core.mailaccounts import ACCOUNT_HUES, assign_colors

    rows = [Connector(id=i, config={}) for i in (1, 2, 3)]
    assert assign_colors(rows) == {1: "blue", 2: "orange", 3: "green"}
    rows[0].config = {"color": "orange"}
    assert assign_colors(rows) == {1: "orange", 2: "blue", 3: "green"}
    many = [Connector(id=i, config={}) for i in range(1, len(ACCOUNT_HUES) + 3)]
    got = assign_colors(many)
    assert len(set(list(got.values())[: len(ACCOUNT_HUES)])) == len(ACCOUNT_HUES)


def test_account_views_parse():
    import pytest

    from mailr.core.mailaccounts import parse_view

    assert parse_view("inbox") is None
    assert parse_view("acct:12:sent") == (12, "sent")
    # A label seen through an account (its folders below Trash).
    assert parse_view("acct:1:label:3") == (1, "label:3")
    for bad in ("acct:12", "acct:x:inbox", "acct:1:label:x", "acct:1:label", "acct:1:all", "acct:1234567890123:inbox",
                "acct:1:label:3:4", "acct:1:inbox:3"):
        with pytest.raises(ValueError):
            parse_view(bad)


async def test_rows_carry_their_accounts(client, user, raw):
    a, b, ids = await _two_accounts(user, raw)
    rows = {x["id"]: x for x in (await client.get("/api/v1/conversations", params={"view": "all"})).json()["items"]}
    assert rows[ids["a1"]]["accounts"] == [a]
    assert rows[ids["b_sent"]]["accounts"] == [b]
    assert "accounts" not in rows[ids["none"]]
    # A reply through the other account: the conversation belongs to both, in account order.
    _, c1, _ = await _ingest(user, raw("Board meeting", to="work@example.org", msgid="<board1@example.org>"), b)
    _, c2, _ = await _ingest(user, raw("Re: Board meeting", to="work@example.org", msgid="<board2@example.org>",
                                       irt="<board1@example.org>"), a)
    assert c1 == c2
    rows = {x["id"]: x for x in (await client.get("/api/v1/conversations", params={"view": "all"})).json()["items"]}
    assert rows[c1]["accounts"] == [a, b]
