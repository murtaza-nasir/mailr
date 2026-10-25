"""Migration b7d1e4a9c3f5 narrowed. Existing unrestricted tokens keep page reading (`web`); restricted and local-only tokens lose it,
and the owner ticks it back where needed. Run against real rows, twice (idempotent)."""

from __future__ import annotations

import importlib.util
import pathlib
import uuid

from sqlalchemy import update

from mailr.db import sessionmaker
from mailr.models import ApiToken

PATH = pathlib.Path(__file__).parents[1] / "migrations/versions/b7d1e4a9c3f5_token_web_scope_reader_shares.py"


def _migration():
    spec = importlib.util.spec_from_file_location("token_web_scope", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(m, conn):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with Operations.context(MigrationContext.configure(conn)):
        m.upgrade()


async def test_web_scope_goes_only_to_existing_unrestricted_read_tokens(user):
    kinds = {
        "unrestricted": dict(scopes=["read", "notes"]),
        "unrestricted_send_policy": dict(scopes=["read", "send"], send_policy={"mode": "policy", "domains": ["x.example"]}),
        "local_only": dict(scopes=["read"], local_only=True),
        "connector_limited": dict(scopes=["read"], connector_ids=[1]),
        "identity_limited": dict(scopes=["read", "send"], send_policy={"mode": "policy", "identities": [3]}),
        "no_read": dict(scopes=["notes"]),
        "revoked": dict(scopes=["read"], revoked=True),
        "already": dict(scopes=["read", "web"]),
    }
    ids = {}
    async with sessionmaker()() as db:
        for k, kw in kinds.items():
            t = ApiToken(user_id=user.id, name=k, prefix="mlr_x", token_hash=uuid.uuid4().hex,
                         **{"send_policy": {"mode": "approve_all"}, **kw})
            db.add(t)
            await db.flush()
            ids[k] = t.id
        await db.commit()
    m = _migration()
    async with sessionmaker()() as db:
        for _ in (0, 1):  # safe to run again
            await db.run_sync(lambda s: _run(m, s.connection()))
        await db.commit()
    async with sessionmaker()() as db:
        got = {k: (await db.get(ApiToken, i)).scopes for k, i in ids.items()}
        # Other users' rows in the test database are left as they were by this check.
        await db.execute(update(ApiToken).where(ApiToken.id.in_(list(ids.values()))).values(revoked=True))
        await db.commit()
    assert got["unrestricted"] == ["read", "notes", "web"]
    assert got["unrestricted_send_policy"] == ["read", "send", "web"]
    assert got["already"] == ["read", "web"]
    for k in ("local_only", "connector_limited", "identity_limited", "no_read", "revoked"):
        assert "web" not in got[k], k
