# ACT-SURFACES: api
"""Favorites in the mail navigation: nav.favorite_add, nav.favorite_remove and
nav.favorite_reorder save the whole ordered list as the `nav_favorites` setting."""


async def test_favorites_default_and_round_trip(client):
    me = (await client.get("/api/v1/me")).json()
    assert me["settings"]["nav_favorites"] == ["inbox", "sent", "drafts"]

    # nav.favorite_add: a label joins the end of the list.
    r = await client.patch("/api/v1/me/settings", json={"nav_favorites": ["inbox", "sent", "drafts", "label:4503599627370497"]})
    assert r.status_code == 200 and r.json()["nav_favorites"][-1] == "label:4503599627370497"
    # nav.favorite_reorder: the order is kept as sent, duplicates collapse.
    r = await client.patch("/api/v1/me/settings", json={"nav_favorites": ["label:4503599627370497", "inbox", "inbox", "category:social"]})
    assert r.json()["nav_favorites"] == ["label:4503599627370497", "inbox", "category:social"]
    # nav.favorite_remove: an empty list is allowed.
    r = await client.patch("/api/v1/me/settings", json={"nav_favorites": []})
    assert r.json()["nav_favorites"] == []
    assert (await client.get("/api/v1/me")).json()["settings"]["nav_favorites"] == []


async def test_favorites_reject_bad_values(client):
    for bad in ("inbox", ["Inbox!"], ["label:abc"], [1], ["x"] * 3, ["inbox"] * 51):
        r = await client.patch("/api/v1/me/settings", json={"nav_favorites": bad})
        assert r.status_code == 400, bad
