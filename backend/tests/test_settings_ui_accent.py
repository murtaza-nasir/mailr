# ACT-SURFACES: api
"""The colours (`ui_accent`): {preset, seed, harmony}, checked on save
(ACT settings.colours_preset, ACT settings.colours_custom).
A bare Cards accent from before 8 Oct 2026 is saved in the new shape; null is the theme's own."""


async def _saved(client):
    return (await client.get("/api/v1/me")).json()["settings"].get("ui_accent")


async def test_preset_and_custom_save(client):
    r = await client.patch("/api/v1/me/settings", json={"ui_accent": {"preset": "office.navy", "seed": "#1F4E8C", "harmony": "single"}})
    assert r.status_code == 200, r.text
    assert await _saved(client) == {"preset": "office.navy", "seed": "#1f4e8c", "harmony": "single"}
    r = await client.patch("/api/v1/me/settings", json={"ui_accent": {"seed": "#aa3355", "harmony": "complementary"}})
    assert r.status_code == 200, r.text
    assert await _saved(client) == {"seed": "#aa3355", "harmony": "complementary"}
    r = await client.patch("/api/v1/me/settings", json={"ui_accent": None})
    assert r.status_code == 200, r.text
    assert await _saved(client) is None


async def test_old_cards_string_is_migrated(client):
    r = await client.patch("/api/v1/me/settings", json={"ui_accent": "teal"})
    assert r.status_code == 200, r.text
    assert await _saved(client) == {"preset": "cards.teal", "seed": "#006a6a", "harmony": "single"}


async def test_bad_values_are_refused(client):
    for bad in ("mauve", 7, {"seed": "red"}, {"harmony": "single"}, {"seed": "#123456", "harmony": "wild"},
                {"preset": "Cards Teal"}, {"seed": "#123456", "extra": 1}):
        r = await client.patch("/api/v1/me/settings", json={"ui_accent": bad})
        assert r.status_code == 400, (bad, r.text)
