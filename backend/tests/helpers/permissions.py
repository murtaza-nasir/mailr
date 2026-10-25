"""Shared helpers, from test_permissions.py."""

from httpx import AsyncClient


async def _token(client, scopes, name="agent"):
    r = await client.post("/api/v1/tokens", json={"name": name, "scopes": scopes})
    assert r.status_code == 200, r.text
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return a, r.json()["item"]["id"]
