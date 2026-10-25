"""Shared helpers, from test_tables.py."""

from __future__ import annotations

API = "/api/v1"


async def new_table(client, **kw):
    r = await client.post(f"{API}/tables", json=kw)
    assert r.status_code == 200, r.text
    return r.json()["table"]


def col(table, name):
    return next(c for c in table["columns"] if c["name"] == name)["id"]


def opt(table, colname, name):
    c = next(c for c in table["columns"] if c["name"] == colname)
    return next(o["id"] for o in c["config"]["options"] if o["name"] == name)
