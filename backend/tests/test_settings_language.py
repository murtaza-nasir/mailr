"""The web app's language (Settings → You → Region): a language tag, saved with
the other settings; null clears it, and the app then follows the browser."""


async def test_language_saves_and_clears(client):
    r = await client.patch("/api/v1/me/settings", json={"language": "pt-BR"})
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/me")).json()["settings"]["language"] == "pt-BR"
    r = await client.patch("/api/v1/me/settings", json={"language": None})
    assert r.status_code == 200, r.text
    assert "language" not in (await client.get("/api/v1/me")).json()["settings"]


async def test_language_must_be_a_tag(client):
    for bad in ("english", "en_US", 7, "e"):
        r = await client.patch("/api/v1/me/settings", json={"language": bad})
        assert r.status_code == 400, (bad, r.text)
