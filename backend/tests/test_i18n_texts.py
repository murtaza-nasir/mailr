"""Server texts kept as a key and rendered in the reader's language: Activity entries, admin notices, approval cards, diagnostics and the mail the server
writes.

A made-up German catalogue (a few keys; English fills the rest) shows the language reaches each
text; entries written before the keys existed keep their stored English."""

import json
import uuid
from pathlib import Path

import pytest
from conftest import mail_date
from helpers.core_flow import _ingest

from mailr import i18n
from mailr.db import sessionmaker
from mailr.models import AuditLog

API = "/api/v1"
EN = json.loads((Path(i18n.__file__).parent / "en.json").read_text(encoding="utf-8"))

DE = {
    "activity": {
        "what": {"quoted": "„{{title}}“", "conversations_one": "{{count}} Unterhaltung",
                 "conversations_other": "{{count}} Unterhaltungen"},
        "mail": {"archive": "{{what}} archiviert"},
        "tables": {"rows_added_one": "{{count}} Zeile zu {{what}} hinzugefügt",
                   "rows_added_other": "{{count}} Zeilen zu {{what}} hinzugefügt"},
        "admin": {"name": "Ihr Administrator hat Ihren Namen in {{name}} geändert."},
    },
    "health": {
        "check": {"db": "Datenbank"},
        "ready": {"reachable": "erreichbar", "sync_bad_one": "{{count}} Konto synchronisiert nicht",
                  "sync_bad_other": "{{count}} Konten synchronisieren nicht"},
        "model": {"key_refused": "Der Server hat den API-Schlüssel abgelehnt."},
    },
    "mail": {
        "date": {"weekday": {"1": "Di"}, "month": {"10": "Okt."}, "day": "{{weekday}}, {{day}}. {{month}} {{year}}"},
        "itip": {"subject": {"invitation": "Einladung: {{title}}"},
                 "body": {"invitation": "{{organizer}} hat Sie zu diesem Termin eingeladen."},
                 "when": "Wann: {{when}}"},
        "speakr": {"scope": {"no_read": "Dieses Token kann keine Aufnahmen lesen."}},
    },
}


@pytest.fixture
def german(tmp_path, monkeypatch):
    """The real English catalogue and a small German one, in a folder of their own."""
    (tmp_path / "en.json").write_text(json.dumps(EN, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "de.json").write_text(json.dumps(DE, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(i18n, "HERE", tmp_path)
    i18n._cache.clear()
    yield
    i18n._cache.clear()


async def _german(client):
    r = await client.patch(f"{API}/me/settings", json={"language": "de"})
    assert r.status_code == 200, r.text


async def _activity(client, **params):
    r = await client.get(f"{API}/activity", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ---------------------------------------------------------------- the msg helpers


def test_a_msg_renders_nested_params_and_falls_back(german):
    m = i18n.msg("activity.mail.archive", what=i18n.msg("activity.what.quoted", title="Lunch"))
    assert i18n.render(m) == "Archived “Lunch”"
    assert i18n.render(m, "de") == "„Lunch“ archiviert"
    many = i18n.msg("activity.mail.archive", what=i18n.msg("activity.what.conversations", count=3))
    assert i18n.render(many, "de") == "3 Unterhaltungen archiviert"
    assert i18n.render(i18n.msg("no.such.key"), "de", fallback="Stored text") == "Stored text"
    assert i18n.render(None, "de", fallback="Stored text") == "Stored text"
    assert i18n.render_params({"what": i18n.msg("activity.what.a_note")}) == {"what": "a note"}


def test_every_mail_note_and_table_phrase_has_its_key():
    from mailr.core import activity

    for a in [*activity.MAIL_PAST, "other"]:
        assert i18n.has(f"activity.mail.{a}"), a
    for phrase, _c, _u in activity.NOTE_OPS.values():
        assert i18n.has(f"activity.notes.{phrase}"), phrase
    for phrase, _c in activity.TABLE_ACTS.values():
        assert i18n.has(f"activity.tables.{phrase}"), phrase
        if phrase.startswith("rows_"):
            assert i18n.has(f"activity.tables.{phrase}_some"), phrase


# ---------------------------------------------------------------- Activity


async def test_activity_reads_in_the_readers_language(german, client, user, raw):
    q = raw("Lunch on Friday", frm="Dana <dana@example.org>", to=user.email,
            msgid=f"<i18n-{uuid.uuid4().hex[:6]}@x.org>", date=mail_date(7, "09:00"))
    _, cid = await _ingest(user, q, roles=["inbox"], source="connector")
    r = await client.post(f"{API}/conversations/actions", json={"ids": [cid], "action": "archive"})
    assert r.status_code == 200, r.text
    assert (await _activity(client))[0]["text"] == "Archived “Lunch on Friday”"
    await _german(client)
    assert (await _activity(client))[0]["text"] == "„Lunch on Friday“ archiviert"
    # A key German lacks reads in English.
    r = await client.post(f"{API}/conversations/actions", json={"ids": [cid], "action": "star"})
    assert r.status_code == 200, r.text
    assert (await _activity(client))[0]["text"] == "Starred „Lunch on Friday“"


async def test_an_entry_from_before_the_keys_keeps_its_text(german, client, user):
    async with sessionmaker()() as db:
        db.add(AuditLog(user_id=user.id, actor="user", action="mail.archive", target="1", inputs={},
                        result={"activity": {"app": "mail", "text": "Archived “Old row”"}}, ok=True))
        await db.commit()
    await _german(client)
    assert (await _activity(client))[0]["text"] == "Archived “Old row”"


async def test_admin_notices_read_in_the_members_language(german, admin_client, client, user):
    await _german(client)
    r = await admin_client.patch(f"{API}/admin/users/{user.id}", json={"name": "Robin Example"})
    assert r.status_code == 200, r.text
    items = await _activity(client, who="admin")
    assert items[0]["text"] == "Ihr Administrator hat Ihren Namen in Robin Example geändert."
    async with sessionmaker()() as db:
        row = await db.get(AuditLog, items[0]["id"])
        # The English stays in the row, for older readers.
        assert row.inputs["description"] == "Your administrator changed your name to Robin Example."
        assert row.inputs["msg"]["key"] == "activity.admin.name"


# ---------------------------------------------------------------- diagnostics and model health


def test_health_details_keep_their_english_and_their_key(german):
    from mailr.core import health

    ok, detail = health.evaluate_sync([{"status": "error", "age": 3600, "poll_minutes": 0}] * 2)
    assert detail == "2 accounts not syncing"
    assert i18n.render(i18n.msg_of(detail), "de") == "2 Konten synchronisieren nicht"
    assert health.label("db", "de") == "Datenbank" and health.label("db") == "Database"
    assert health.label("not_a_check") == "Not a check"


def test_model_errors_render_in_the_readers_language(german):
    from mailr.core import healthlog
    from mailr.intel import modelhealth

    err = modelhealth.plain(None, 401)
    assert err == "The API key was refused."
    data = {"task:chat": {"error_at": "2026-10-06T10:00:00+00:00", "error": str(err), "error_msg": i18n.msg_of(err)}}
    assert healthlog.entry(data, "task:chat")["error"] == "The API key was refused."
    assert healthlog.entry(data, "task:chat", "de")["error"] == "Der Server hat den API-Schlüssel abgelehnt."
    assert "error_msg" not in healthlog.entry(data, "task:chat", "de")
    assert modelhealth.plain(RuntimeError("some odd answer")) == "some odd answer"


async def test_diagnostics_read_in_the_admins_language(german, admin_client):
    r = await admin_client.patch(f"{API}/me/settings", json={"language": "de"})
    assert r.status_code == 200, r.text
    r = await admin_client.get(f"{API}/admin/diagnostics")
    assert r.status_code == 200, r.text
    db = next(c for c in r.json()["checks"] if c["name"] == "db")
    assert db["label"] == "Datenbank" and db["detail"] == "erreichbar" and "detail_msg" not in db


# ---------------------------------------------------------------- mail the server writes


def test_invitation_mail_is_in_the_organizers_language(german):
    from datetime import date

    from icalendar import Event

    from mailr.calendar import organizer

    ev = Event()
    ev.add("dtstart", date(2026, 10, 6))
    ev.add("summary", "Planning")
    msg = organizer.ItipMsg("REQUEST", "invitation", "u1", "Planning", [], "", comp=ev)
    assert organizer.subject_text("invitation", "Planning", "de") == "Einladung: Planning"
    assert organizer.subject_text("cancel", None) == "Cancelled: (no title)"
    body = organizer.body_text(msg, "Ana", None, "", "de")
    assert body.splitlines()[0] == "Ana hat Sie zu diesem Termin eingeladen."
    assert "Wann: Di, 6. Okt. 2026" in body
    assert "When: Tue 6 Oct 2026" in organizer.body_text(msg, "Ana", None)


def test_speakr_token_warnings_follow_the_reader(german):
    from mailr.integrations.speakr.connect import scope_notes

    notes = scope_notes(["write", "upload"], {}, "de")
    assert notes[0] == "Dieses Token kann keine Aufnahmen lesen."
    assert notes[1].startswith("Without the webhooks scope")  # German lacks it: English
    assert scope_notes(["read", "upload", "delete"], {})[0].endswith("A token without it is safer.")


async def test_an_import_error_has_a_code(client):
    r = await client.post(f"{API}/imports/preview", json={"kind": "nope", "blobs": []})
    assert r.status_code in (400, 422)
    if r.status_code == 400 and "code" in r.json():
        assert r.json()["code"] == "import.bad_kind"
