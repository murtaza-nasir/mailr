"""Shared helpers, from test_recovery_signs_out_apps.py."""

from __future__ import annotations

import pytest


@pytest.fixture
def store(monkeypatch):
    """The store's management calls and app passwords, recorded. `fail` makes the next calls fail."""
    from mailr.calendar import phones
    from mailr.core import users

    state = {"calls": [], "secrets": [], "fail": False, "app_passwords": 2}

    async def mgmt(method, args):
        if state["fail"]:
            raise RuntimeError("store down")
        state["calls"].append(method)
        upd = args.get("update") or {}
        for acct, patch in upd.items():
            if "credentials/0/secret" in patch:
                state["secrets"].append(patch["credentials/0/secret"])
            return {"updated": {acct: None}}
        return {}

    async def revoke_all(account_id):
        if state["fail"]:
            raise RuntimeError("store down")
        state["calls"].append("phones.revoke_all")
        n, state["app_passwords"] = state["app_passwords"], 0
        return n

    monkeypatch.setattr(users, "_mgmt", mgmt)
    monkeypatch.setattr(phones, "revoke_all", revoke_all)
    return state
