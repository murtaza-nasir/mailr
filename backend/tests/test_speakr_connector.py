# ACT-SURFACES: api
"""The Speakr connector (speakr.connect): the token is sealed and never shown or
logged, a bad token or a web page is a clear error, netguard refuses local and metadata addresses,
the data class defaults to local-only, and removal deletes the mirror only. Speakr is the FakeSpeakr
fixture; no test talks to a real Speakr."""

import logging

from speakr_fake import BASE, TOKEN, connect, speakr  # noqa: F401

from mailr.db import sessionmaker
from mailr.models import Connector, SpeakrRecording


async def test_speakr_connect_token_sealed_never_returned_or_logged(client, user, speakr, caplog):
    speakr.add(1, "Weekly sync")
    caplog.set_level(logging.DEBUG)
    t = await client.post("/api/v1/connectors/test", json={"kind": "speakr", "config": {"base_url": BASE + "/api/v1/"},
                                                           "secrets": {"token": TOKEN}})
    assert t.json()["ok"] and "Dana Speakr" in t.json()["detail"] and "1 recording." in t.json()["detail"]
    conn = await connect(client, speakr)
    assert conn["kind"] == "speakr" and conn["config"]["base_url"] == BASE
    assert conn["data_class"] == "local_only"  # the default when the wizard sends none
    listed = await client.get("/api/v1/connectors")
    assert TOKEN not in listed.text and TOKEN not in str(conn)
    async with sessionmaker()() as db:
        row = await db.get(Connector, conn["id"])
        assert row.secrets_enc and TOKEN.encode() not in row.secrets_enc
        assert TOKEN not in str(row.config) and TOKEN not in str(row.state)
        assert len((await db.execute(SpeakrRecording.__table__.select().where(
            SpeakrRecording.connector_id == row.id))).all()) == 1
    assert all(TOKEN not in r.getMessage() for r in caplog.records)
    # Speakr saw the Bearer header only, never ?token=.
    assert all("token" not in c for c in speakr.calls)


async def test_speakr_connect_errors_are_clear(client, user, speakr):
    speakr.token = "another-token"
    r = await client.post("/api/v1/connectors/test", json={"kind": "speakr", "config": {"base_url": BASE},
                                                           "secrets": {"token": TOKEN}})
    assert r.json() == {"ok": False, "detail": "This token is invalid, expired or revoked", "folders": [],
                        "identities": [], "extensions": []}
    r = await client.post("/api/v1/connectors", json={"kind": "speakr", "config": {"base_url": BASE},
                                                      "secrets": {"token": TOKEN}})
    assert r.status_code == 400 and "invalid, expired or revoked" in r.text
    speakr.token = TOKEN
    speakr.html = True
    r = await client.post("/api/v1/connectors/test", json={"kind": "speakr", "config": {"base_url": BASE},
                                                           "secrets": {"token": TOKEN}})
    assert not r.json()["ok"] and "not a Speakr server" in r.json()["detail"]


async def test_speakr_connect_netguard_refuses_local_and_metadata_addresses(client, user, speakr):
    for url, words in (("http://127.0.0.2:8899", "local or reserved"), ("http://169.254.169.254", "local or reserved"),
                       ("http://8.8.8.8", "https://"), ("ftp://speakr.test", "https://")):
        r = await client.post("/api/v1/connectors/test", json={"kind": "speakr", "config": {"base_url": url},
                                                               "secrets": {"token": TOKEN}})
        assert not r.json()["ok"] and words in r.json()["detail"], (url, r.json())
        r = await client.post("/api/v1/connectors", json={"kind": "speakr", "config": {"base_url": url},
                                                          "secrets": {"token": TOKEN}})
        assert r.status_code == 400
    assert not [c for c in speakr.calls]  # nothing reached a server


async def test_speakr_connect_edit_and_remove(client, user, speakr):
    speakr.add(1)
    conn = await connect(client, speakr, data_class="normal")
    assert conn["data_class"] == "normal"
    dup = await client.post("/api/v1/connectors", json={"kind": "speakr", "config": {"base_url": BASE},
                                                        "secrets": {"token": TOKEN}})
    assert dup.status_code == 409
    r = await client.patch(f"/api/v1/connectors/{conn['id']}", json={"data_class": "local_only",
                                                                     "config": {"speakr_models_local": True}})
    assert r.status_code == 200 and r.json()["data_class"] == "local_only"
    assert r.json()["config"]["speakr_models_local"] is True
    # A saved token is never sent to another address.
    r = await client.patch(f"/api/v1/connectors/{conn['id']}", json={"config": {"base_url": "https://other.test"}})
    assert r.status_code == 400 and "token again" in r.text
    assert (await client.post(f"/api/v1/connectors/{conn['id']}/sync")).status_code == 200
    assert (await client.delete(f"/api/v1/connectors/{conn['id']}")).status_code == 200
    async with sessionmaker()() as db:
        left = (await db.execute(SpeakrRecording.__table__.select().where(
            SpeakrRecording.connector_id == conn["id"]))).all()
        assert left == []
    assert 1 in speakr.recordings  # nothing was deleted in Speakr


async def test_speakr_routes_need_the_recordings_scope(client, user, speakr):
    speakr.add(1)
    await connect(client, speakr)
    r = await client.post("/api/v1/tokens", json={"name": "no-rec", "scopes": ["read"], "local_only": True})
    tok = r.json()["token"]
    r = await client.get("/api/v1/speakr/recordings", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
    r = await client.post("/api/v1/tokens", json={"name": "rec", "scopes": ["read", "recordings"], "local_only": True})
    r = await client.get("/api/v1/speakr/recordings", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert r.status_code == 200 and [x["recording_id"] for x in r.json()["items"]] == [1]
