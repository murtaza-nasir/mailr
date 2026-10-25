# ACT-SURFACES: api
"""Pinned folders in each account's group (core/accountpins.py).

An account's group shows its system folders, its pinned folders in order and one "More folders"
row with the rest. `account_folder_pins` ({account id: [label id, …]}) is validated on save:
unknown accounts and labels are dropped, a malformed value is refused (ACT mail.account_folder_pin,
ACT mail.account_folder_unpin, ACT mail.account_folder_reorder). `account_folders_more_open`
remembers which groups have "More folders" open (ACT mail.account_folders_more), and
`account_folders_show_unread` is the option (ACT mail.account_folders_show_unread). A folder made
with New folder in an account is pinned there.
"""

from helpers.mail_accounts import _two_accounts


async def _label(client, name, **kw):
    r = await client.post("/api/v1/labels", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def _settings(client):
    return (await client.get("/api/v1/me")).json()["settings"]


async def test_defaults(client, user):
    s = await _settings(client)
    assert s["account_folder_pins"] == {}
    assert s["account_folders_more_open"] == []
    assert s["account_folders_show_unread"] is False


async def test_pins_are_validated_and_unknown_ids_dropped(client, user, raw):
    a, b, _ = await _two_accounts(user, raw)
    x = await _label(client, "Receipts")
    y = await _label(client, "Travel")
    inbox = next(lb for lb in (await client.get("/api/v1/labels")).json() if lb["role"] == "inbox")
    body = {"account_folder_pins": {str(a): [y["id"], x["id"], y["id"], 999999, inbox["id"]], str(b): [x["id"]], "424242": [x["id"]]}}
    r = await client.patch("/api/v1/me/settings", json=body)
    assert r.status_code == 200, r.text
    # Order kept, the duplicate, the unknown label, a system folder and the unknown account dropped.
    assert r.json()["account_folder_pins"] == {str(a): [y["id"], x["id"]], str(b): [x["id"]]}
    assert (await _settings(client))["account_folder_pins"] == {str(a): [y["id"], x["id"]], str(b): [x["id"]]}
    # Unpin (ACT mail.account_folder_unpin): an emptied list goes away.
    r = await client.patch("/api/v1/me/settings", json={"account_folder_pins": {str(a): [x["id"]], str(b): []}})
    assert r.json()["account_folder_pins"] == {str(a): [x["id"]]}
    for bad in ([1, 2], {str(a): "x"}, {str(a): [True]}, {str(a): ["3"]}, {"acct": [1]}, {str(a): list(range(1, 300))}):
        r = await client.patch("/api/v1/me/settings", json={"account_folder_pins": bad})
        assert r.status_code == 400, bad


async def test_another_users_label_cannot_be_pinned(client, user, raw):
    import uuid

    from mailr.core.users import create_user
    from mailr.db import session_scope
    from mailr.models import Label

    a, _b, _ = await _two_accounts(user, raw)
    async with session_scope() as db:
        other = await create_user(db, f"t-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
    async with session_scope() as db:
        theirs = Label(user_id=other.id, name="Theirs")
        db.add(theirs)
        await db.flush()
        tid = theirs.id
    r = await client.patch("/api/v1/me/settings", json={"account_folder_pins": {str(a): [tid]}})
    assert r.status_code == 200 and r.json()["account_folder_pins"] == {}


async def test_more_open_and_show_unread(client, user, raw):
    a, b, _ = await _two_accounts(user, raw)
    r = await client.patch("/api/v1/me/settings", json={"account_folders_more_open": [b, a, b, 777777]})
    assert r.status_code == 200 and r.json()["account_folders_more_open"] == [b, a]
    for bad in ({str(a): True}, ["1"], [True]):
        assert (await client.patch("/api/v1/me/settings", json={"account_folders_more_open": bad})).status_code == 400
    r = await client.patch("/api/v1/me/settings", json={"account_folders_show_unread": True})
    assert r.status_code == 200 and r.json()["account_folders_show_unread"] is True
    assert (await client.patch("/api/v1/me/settings", json={"account_folders_show_unread": "on"})).status_code == 400


async def test_new_folder_in_an_account_is_pinned_there(client, user, raw):
    a, b, _ = await _two_accounts(user, raw)
    first = await _label(client, "Receipts", home_account_id=a)
    second = await _label(client, "Projects", home_account_id=a)
    assert (await _settings(client))["account_folder_pins"] == {str(a): [first["id"], second["id"]]}
    # The same name made from the other account is the same label, pinned there too.
    again = await _label(client, "receipts", home_account_id=b)
    assert again["id"] == first["id"]
    assert (await _settings(client))["account_folder_pins"] == {str(a): [first["id"], second["id"]], str(b): [first["id"]]}
    # A plain label is pinned nowhere.
    await _label(client, "Plain")
    assert len((await _settings(client))["account_folder_pins"][str(a)]) == 2


async def test_the_navigation_hint_is_dismissed_once(client, user):
    """`account_folders_hint_dismissed`: the navigation's hint that offers Show each account
    separately (features/layout/accounts.tsx) starts not dismissed and takes only true or false."""
    assert (await _settings(client))["account_folders_hint_dismissed"] is False
    r = await client.patch("/api/v1/me/settings", json={"account_folders_hint_dismissed": True})
    assert r.status_code == 200 and r.json()["account_folders_hint_dismissed"] is True
    assert (await _settings(client))["account_folders_hint_dismissed"] is True
    r = await client.patch("/api/v1/me/settings", json={"account_folders_hint_dismissed": "yes"})
    assert r.status_code == 400
