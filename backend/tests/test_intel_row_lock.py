"""Triage and the user's own changes: the model calls run with the message row unwritten, so an
archive or move during them neither waits on the job nor is overwritten by its labels."""

import asyncio

from embedfake import embedding_endpoint
from helpers.core_flow import _ingest
from sqlalchemy import select

from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.intel import actions, classify
from mailr.intel import tasks as intel_tasks
from mailr.models import Label, Message, User


async def _label_id(user, role=None, name=None):
    async with sessionmaker()() as db:
        if role:
            return (await db.execute(select(Label.id).where(Label.user_id == user.id, Label.role == role))).scalar_one()
        store = await user_store(db, await db.get(User, user.id))
        lid = (await store.create_label(db, name)).id
        await db.commit()
        return lid


async def test_archive_during_model_calls_is_kept(user, raw, monkeypatch):
    async with embedding_endpoint(monkeypatch):
        mid, _ = await _ingest(user, raw("Quarterly numbers", frm="Lena <lena@example.org>"),
                               roles=["inbox"], source="connector")
        inbox = await _label_id(user, role="inbox")
        archive = await _label_id(user, role="archive")
        receipts = await _label_id(user, name="Receipts")

        errors: list[BaseException] = []

        async def predict(db, u, m, s, *, allow_auto):
            return [{"label_id": receipts, "auto": True, "prob": 0.95}]

        async def extract(db, u, m, raw_, *, sensitive, allow_model):
            # The user archives the message while the model runs: in its own session, it must
            # not wait for the job's transaction.
            async def archive_it():
                async with sessionmaker()() as other:
                    om = await other.get(Message, mid)
                    store = await user_store(other, await other.get(User, user.id))
                    await store.set_labels([om], add=[archive], remove=[inbox], db=other)
                    await other.commit()

            try:
                await asyncio.wait_for(archive_it(), timeout=10)
            except Exception as e:  # noqa: BLE001 - the test reports it
                errors.append(e)
            return None

        monkeypatch.setattr(classify, "predict_labels", predict)
        monkeypatch.setattr(actions, "extract", extract)
        await intel_tasks.intel_message({"message_id": mid}, None)
        assert errors == []
        async with sessionmaker()() as db:
            got = set((await db.get(Message, mid)).label_ids)
        assert inbox not in got
        assert {archive, receipts} <= got


async def test_set_labels_reads_the_stored_labels(user, raw):
    """A copy loaded before another session's change: set_labels works from the stored labels."""
    mid, _ = await _ingest(user, raw("Old copy"), roles=["inbox"], source="connector")
    inbox = await _label_id(user, role="inbox")
    archive = await _label_id(user, role="archive")
    travel = await _label_id(user, name="Travel")
    async with sessionmaker()() as stale:
        sm = await stale.get(Message, mid)
        assert inbox in sm.label_ids
        await stale.commit()  # the copy stays loaded (expire_on_commit is off)
        async with sessionmaker()() as other:
            om = await other.get(Message, mid)
            store = await user_store(other, await other.get(User, user.id))
            await store.set_labels([om], add=[archive], remove=[inbox], db=other)
            await other.commit()
        store = await user_store(stale, await stale.get(User, user.id))
        await store.set_labels([sm], add=[travel], remove=[], db=stale)
        await stale.commit()
    async with sessionmaker()() as db:
        got = set((await db.get(Message, mid)).label_ids)
    assert inbox not in got and {archive, travel} <= got
