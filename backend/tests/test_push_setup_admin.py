# ACT-SURFACES: api
"""Admin → Phone apps → Instant notifications on phones (GET /admin/push-setup): what a new
member does for push, and how many people's phones push through which server. Counts only."""



async def test_admin_push_setup_counts_phones_by_push_server(app, admin_client, monkeypatch):
    # ACT admin.push_setup
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "ntfy_url", "https://ntfy.example.com")
    r = await admin_client.get("/api/v1/admin/push-setup")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["server"]["host"] == "ntfy.example.com" and d["steps"] == ["install_ntfy", "set_default_server"]
    assert set(d["people"]) == {"phones", "push", "no_push"}
    assert all(set(s) == {"host", "people", "own"} for s in d["servers"])
    monkeypatch.setattr(get_settings(), "ntfy_url", "")
    d = (await admin_client.get("/api/v1/admin/push-setup")).json()
    assert d["server"] is None and d["steps"] == ["install_ntfy"]


async def test_members_cannot_read_push_setup(client):
    assert (await client.get("/api/v1/admin/push-setup")).status_code == 403
