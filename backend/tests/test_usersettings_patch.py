"""Changes to users.settings never drop keys another request or job wrote meanwhile
(core/usersettings.py): `patch` and `patch_in` change only the named keys, in one UPDATE, and the
session's own copy of the user takes the stored result, so a later flush writes nothing older."""

from __future__ import annotations

from mailr.core import usersettings
from mailr.db import session_scope, sessionmaker
from mailr.models import User


async def _settings(uid: int) -> dict:
    async with sessionmaker()() as db:
        return (await db.get(User, uid)).settings or {}


async def _write_elsewhere(uid: int, **kw) -> None:
    async with session_scope() as db:
        await usersettings.patch(db, uid, kw)


async def test_a_patch_keeps_keys_written_after_the_copy_was_loaded(app, user):
    async with session_scope() as db:
        mine = await db.get(User, user.id)
        assert "label_scheme" not in (mine.settings or {})
        await _write_elsewhere(user.id, device_timezone="Europe/Lisbon")  # /me, alongside
        new = await usersettings.patch(db, user.id, {"label_scheme": "pastel"}, remove=["nothing_here"])
        assert new["device_timezone"] == "Europe/Lisbon" and new["label_scheme"] == "pastel"
        # The session's copy is the stored one now: a flush of the user writes nothing older.
        assert mine.settings == new
        mine.name = "Ines Duarte"
    st = await _settings(user.id)
    assert st["device_timezone"] == "Europe/Lisbon" and st["label_scheme"] == "pastel"


async def test_patch_in_changes_one_entry_of_a_nested_setting(app, user):
    await _write_elsewhere(user.id, models={"summary": {"endpoint": "a", "model": "m1"}})
    async with session_scope() as db:
        await usersettings.patch_in(db, user.id, "models", {"triage": {"endpoint": "b", "model": "m2"}})
        await usersettings.patch_in(db, user.id, "models", remove=["summary"])
        await usersettings.patch_in(db, user.id, "task_tags", {"Garden": {"color": "#2e7d32"}})
    st = await _settings(user.id)
    assert st["models"] == {"triage": {"endpoint": "b", "model": "m2"}}
    assert st["task_tags"] == {"Garden": {"color": "#2e7d32"}}


async def test_a_tag_rename_job_keeps_what_changed_while_it_ran(app, user):
    """calendar.retag loads the user at its start and runs a CalDAV round trip per task; its
    write-back of the tag's colour must not undo a setting saved meanwhile."""
    from mailr.calendar import todo
    from mailr.core.auth import ALL_SCOPES, Principal

    await _write_elsewhere(user.id, task_tags={"Garden": {"color": "#2e7d32"}, "Errands": {"pinned": True}})
    async with session_scope() as db:
        job_user = await db.get(User, user.id)
        p = Principal(user=job_user, scopes=set(ALL_SCOPES))
        await _write_elsewhere(user.id, undo_send_seconds=20,
                               task_tags={"Garden": {"color": "#2e7d32"}, "Errands": {"pinned": False}})
        res = await todo.retag_all(db, job_user, p, "garden", "Allotment")
        assert res == {"updated": 0, "failed": []}
    st = await _settings(user.id)
    assert st["undo_send_seconds"] == 20
    assert st["task_tags"] == {"Allotment": {"color": "#2e7d32"}, "Errands": {"pinned": False}}


async def test_spam_learning_keeps_the_settings_page_save(app, user):
    from mailr.intel import spam

    async with session_scope() as db:
        await db.get(User, user.id)  # the copy a bulk job's chunk holds
        await _write_elsewhere(user.id, undo_send_seconds=30)
        await spam._save(db, user.id, add={"spam.example": {"added": "2026-10-01T00:00:00+00:00", "marks": 3}})
    st = await _settings(user.id)
    assert st["undo_send_seconds"] == 30 and "spam.example" in st[spam.DOMAINS_KEY]
