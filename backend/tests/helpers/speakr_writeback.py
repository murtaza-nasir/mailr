"""Shared helpers, from test_speakr_writeback.py."""



async def _switch(client, cid, **cfg):
    r = await client.patch(f"/api/v1/connectors/{cid}", json={"config": cfg})
    assert r.status_code == 200, r.text
    return r.json()
