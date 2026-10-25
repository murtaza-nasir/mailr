# ACT-SURFACES: api
"""The Android apps this server hands out (core/apk.py, core/phone_apps.py, api/phone_app_routes.py,
`mailr app-publish`): reading the version from the APK, the publish checks, the previous version
kept as the fallback, the public downloads with their checksum, and who may publish
(settings.phone_apps); the Notes app's own oldest version (settings.notes_min_version)."""

import glob
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from apk_fixture import build, manifest
from sqlalchemy import select

from mailr.config import get_settings
from mailr.core import apk, appversion, phone_apps
from mailr.db import sessionmaker
from mailr.models import AuditLog

API = "/api/v1"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path))
    monkeypatch.setattr(s, "android_apk_url", "")
    monkeypatch.setattr(s, "android_package_mailr", "io.github.murtazanasir.mailr")
    monkeypatch.setattr(s, "android_package_notes", "io.github.murtazanasir.mailr.notes")
    return tmp_path


# ---------------------------------------------------------------- reading an APK


@pytest.mark.parametrize("kw", [{}, {"utf8": True}, {"blank_names": True}])
def test_the_version_comes_from_the_binary_manifest(kw):
    info = apk.read(build("io.github.murtazanasir.mailr.notes", 12, "1.4.0", **kw))
    assert (info.package, info.version_code, info.version_name, info.signed) == ("io.github.murtazanasir.mailr.notes", 12, "1.4.0", True)


def test_what_is_not_an_apk_is_refused():
    with pytest.raises(apk.ApkError, match="not a zip"):
        apk.read(b"hello")
    with pytest.raises(apk.ApkError, match="damaged zip"):
        apk.read(b"PK\x03\x04" + b"\x00" * 40)
    with pytest.raises(apk.ApkError, match="classes.dex"):
        apk.read(build(dex=False))
    with pytest.raises(apk.ApkError, match="not signed"):
        apk.read(build(signed=False))
    with pytest.raises(apk.ApkError, match="binary Android XML"):
        apk.read(build(raw_manifest=b"<manifest package='x'/>"))
    with pytest.raises(apk.ApkError):
        apk.read(build(raw_manifest=manifest("a.b", 1, "1")[:60]))


# A real build, when one is in this checkout (android/build.sh assembleDebug or assembleRelease).
_REAL = sorted(glob.glob(str(Path(__file__).resolve().parents[2] / "android/*/build/outputs/apk/*/*.apk")))


@pytest.mark.skipif(not _REAL, reason="no APK built in this checkout")
@pytest.mark.parametrize("path", _REAL)
def test_a_real_build_reads_as_its_gradle_version(path):
    import json

    meta = json.loads((Path(path).parent / "output-metadata.json").read_text())
    el = meta["elements"][0]
    info = apk.read(Path(path).read_bytes())
    assert (info.package, info.version_code, info.version_name) == (meta["applicationId"], el["versionCode"],
                                                                   el["versionName"])


# ---------------------------------------------------------------- publishing


def test_publish_checks_the_package_and_keeps_the_previous_version(data_dir):
    with pytest.raises(phone_apps.PublishError, match="not the Mailr Notes app"):
        phone_apps.publish("notes", build("io.github.murtazanasir.mailr", 3, "1.0.0"), by="t")
    with pytest.raises(phone_apps.PublishError, match="not the Mailr app"):
        phone_apps.publish("mailr", build("io.github.murtazanasir.mailr.debug", 3, "1.0.0-debug"), by="t")
    with pytest.raises(phone_apps.PublishError, match="empty"):
        phone_apps.publish("notes", b"", by="t")
    with pytest.raises(phone_apps.PublishError, match="unknown app"):
        phone_apps.publish("calendar", build(), by="t")
    assert phone_apps.served("notes") is None
    assert phone_apps.release("notes")["url"] == phone_apps.RELEASES

    one = build("io.github.murtazanasir.mailr.notes", 3, "1.0.2")
    out = phone_apps.publish("notes", one, by="t")
    assert out["replaced"] is None
    r = out["release"]
    assert (r["version_name"], r["version_code"], r["size"]) == ("1.0.2", 3, len(one))
    assert r["sha256"] == hashlib.sha256(one).hexdigest()
    assert r["url"].endswith("/api/v1/app/notes.apk")
    # The same file again changes nothing.
    assert phone_apps.publish("notes", one, by="t")["unchanged"] is True

    two = build("io.github.murtazanasir.mailr.notes", 4, "1.1.0")
    out = phone_apps.publish("notes", two, by="t")
    assert out["replaced"]["version_name"] == "1.0.2"
    assert phone_apps.previous("notes")["version_code"] == 3
    # An older version needs allow_older (going back on purpose).
    with pytest.raises(phone_apps.PublishError, match="older"):
        phone_apps.publish("notes", build("io.github.murtazanasir.mailr.notes", 2, "0.9.0"), by="t")
    # Rollback swaps the two.
    phone_apps.rollback("notes", by="t")
    assert phone_apps.release("notes")["version_name"] == "1.0.2"
    assert phone_apps.previous("notes")["version_name"] == "1.1.0"
    # The current file gone: the previous one is served.
    (data_dir / "app" / "notes" / "current.apk").unlink()
    assert phone_apps.served("notes")[1]["version_name"] == "1.1.0"


def test_the_package_comes_from_settings(data_dir, monkeypatch):
    monkeypatch.setattr(get_settings(), "android_package_notes", "org.example.notes")
    with pytest.raises(phone_apps.PublishError, match="org.example.notes"):
        phone_apps.publish("notes", build("io.github.murtazanasir.mailr.notes", 3, "1.0.2"), by="t")
    assert phone_apps.publish("notes", build("org.example.notes", 3, "1.0.2"), by="t")["release"]["version_code"] == 3


def test_a_hand_copied_apk_is_served_until_the_first_publish_keeps_it(data_dir):
    legacy = data_dir / "app" / "mailr.apk"
    legacy.parent.mkdir(parents=True)
    old = build("io.github.murtazanasir.mailr", 30, "0.5.9")
    legacy.write_bytes(old)
    r = phone_apps.release("mailr")
    assert (r["version_name"], r["sha256"]) == ("0.5.9", hashlib.sha256(old).hexdigest())
    out = phone_apps.publish("mailr", build("io.github.murtazanasir.mailr", 37, "0.6.2"), by="t")
    assert out["replaced"]["version_name"] == "0.5.9"
    assert not legacy.exists()
    assert phone_apps.previous("mailr")["version_code"] == 30


# ---------------------------------------------------------------- the routes


async def test_downloads_are_public_with_version_and_checksum(app, data_dir, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get(f"{API}/app/notes.apk")).status_code == 404
        assert (await anon.get(f"{API}/app/other.apk")).status_code == 404
        apps = {a["app"]: a for a in (await anon.get(f"{API}/app/releases")).json()["apps"]}
        assert apps["notes"]["on_server"] is False and apps["notes"]["url"] == phone_apps.RELEASES

        data = build("io.github.murtazanasir.mailr.notes", 3, "1.0.2")
        phone_apps.publish("notes", data, by="t")
        r = await anon.get(f"{API}/app/notes.apk")
        assert r.status_code == 200 and r.content == data
        assert r.headers["content-type"] == "application/vnd.android.package-archive"
        assert 'filename="mailr-notes-1.0.2.apk"' in r.headers["content-disposition"]
        assert r.headers["x-checksum-sha256"] == hashlib.sha256(data).hexdigest()
        apps = {a["app"]: a for a in (await anon.get(f"{API}/app/releases")).json()["apps"]}
        assert apps["notes"]["sha256"] == hashlib.sha256(r.content).hexdigest()
        assert (apps["notes"]["version_name"], apps["notes"]["size"]) == ("1.0.2", len(data))
        assert apps["notes"]["url"].endswith("/api/v1/app/notes.apk")

        # MAILR_ANDROID_APK_URL still sends the mailr app elsewhere (no version known then).
        phone_apps.publish("mailr", build(), by="t")
        assert (await anon.get(f"{API}/auth/config")).json()["android_apk_url"].endswith("/app/mailr.apk")
        monkeypatch.setattr(get_settings(), "android_apk_url", "https://apps.example.com/mailr.apk")
        apps = {a["app"]: a for a in (await anon.get(f"{API}/app/releases")).json()["apps"]}
        assert apps["mailr"]["url"] == "https://apps.example.com/mailr.apk" and apps["mailr"]["version_name"] is None
        assert (await anon.get(f"{API}/auth/config")).json()["android_apk_url"] == "https://apps.example.com/mailr.apk"


async def test_the_setup_guide_lists_both_apps(client, data_dir):
    phone_apps.publish("notes", build("io.github.murtazanasir.mailr.notes", 3, "1.0.2"), by="t")
    st = (await client.get(f"{API}/onboarding")).json()
    apps = {a["app"]: a for a in st["phone"]["apps"]}
    assert apps["notes"]["version_name"] == "1.0.2" and apps["mailr"]["on_server"] is False


async def test_only_admins_publish_and_it_is_audited(client, admin_client, data_dir):
    data = build("io.github.murtazanasir.mailr.notes", 3, "1.0.2")
    files = {"file": ("notes-release.apk", data, "application/vnd.android.package-archive")}
    # A user, and an admin's API token, cannot publish.
    assert (await client.post(f"{API}/admin/phone-apps/notes", files=files)).status_code == 403
    assert (await client.get(f"{API}/admin/phone-apps")).status_code == 403
    tok = (await admin_client.post(f"{API}/tokens", json={"name": "t", "scopes": ["read", "admin"]})).json()
    if "token" in tok:
        h = {"Authorization": f"Bearer {tok['token']}"}
        assert (await admin_client.post(f"{API}/admin/phone-apps/notes", files=files, headers=h)).status_code == 403

    # The wrong app is refused with the reason, and the refusal is audited.
    r = await admin_client.post(f"{API}/admin/phone-apps/mailr", files=files)
    assert r.status_code == 400 and "not the Mailr app" in r.json()["detail"]
    r = await admin_client.post(f"{API}/admin/phone-apps/notes", files=files)
    assert r.status_code == 200, r.text
    apps = {a["app"]: a for a in r.json()["apps"]}
    assert apps["notes"]["version_name"] == "1.0.2" and apps["notes"]["previous"] is None
    r = await admin_client.post(f"{API}/admin/phone-apps/notes",
                                files={"file": ("n.apk", build("io.github.murtazanasir.mailr.notes", 4, "1.1.0"), "application/octet-stream")})
    assert {a["app"]: a for a in r.json()["apps"]}["notes"]["previous"]["version_name"] == "1.0.2"
    r = await admin_client.post(f"{API}/admin/phone-apps/notes/rollback")
    assert {a["app"]: a for a in r.json()["apps"]}["notes"]["version_name"] == "1.0.2"
    async with sessionmaker()() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.action.in_(("admin.app_publish", "admin.app_rollback")),
                                                        AuditLog.target.in_(("mailr", "notes"))))).scalars().all()
    assert [r.ok for r in rows if r.action == "admin.app_publish"].count(False) >= 1
    assert any(r.action == "admin.app_rollback" for r in rows)


def test_the_cli_reads_the_apk_from_standard_input(data_dir, tmp_path):
    env = {**os.environ, "MAILR_DATA_DIR": str(data_dir)}
    run = [sys.executable, "-m", "mailr.cli", "app-publish", "--app", "notes"]
    bad = subprocess.run(run, input=build("io.github.murtazanasir.mailr", 3, "1.0.0"), capture_output=True, env=env, timeout=120)
    assert bad.returncode != 0 and b"not the Mailr Notes app" in bad.stderr
    good = build("io.github.murtazanasir.mailr.notes", 3, "1.0.2")
    ok = subprocess.run(run, input=good, capture_output=True, env=env, timeout=120)
    assert ok.returncode == 0, ok.stderr.decode()
    assert hashlib.sha256(good).hexdigest().encode() in ok.stdout
    assert (data_dir / "app" / "notes" / "current.apk").read_bytes() == good
    assert b"phones download it at" in ok.stdout


# ---------------------------------------------------------------- the Notes app's oldest version


def test_the_notes_floor_reads_only_the_notes_user_agent():
    assert appversion.too_old("mailr-android-notes/1.0.9", "1.1.0", "notes")
    assert not appversion.too_old("mailr-android-notes/1.1.0-debug", "1.1.0", "notes")
    # Each app is compared with its own floor only.
    assert not appversion.too_old("mailr-android-notes/0.1.0", "0.5.0", "mailr")
    assert not appversion.too_old("mailr-android/0.1.0", "9.0", "notes")
    assert appversion.valid("") and appversion.valid("1.2") and appversion.valid(" 1.2.3 ")
    assert not appversion.valid("1.2.x") and not appversion.valid("latest")


async def test_settings_notes_min_version_answers_426_to_older_notes_apps(client, admin_client, data_dir):
    appversion.clear_cache()
    notes = {"User-Agent": "mailr-android-notes/1.0.4"}
    newer = {"User-Agent": "mailr-android-notes/1.1.0"}
    # No floor yet: every Notes app is answered.
    assert (await client.get(f"{API}/me", headers=notes)).status_code == 200
    out = (await admin_client.get(f"{API}/admin/phone-apps")).json()
    floors = {a["app"]: a["min_version"] for a in out["apps"]}
    assert floors["notes"] == {"min_version": "", "source": "setting"} and floors["mailr"]["source"] == "env"

    # Only an admin's session sets it, and a malformed version is refused.
    assert (await client.put(f"{API}/admin/phone-apps/notes/min-version", json={"min_version": "1.1.0"})).status_code == 403
    r = await admin_client.put(f"{API}/admin/phone-apps/notes/min-version", json={"min_version": "1.1.x"})
    assert r.status_code == 400 and "1.1.0" in r.json()["detail"]
    r = await admin_client.put(f"{API}/admin/phone-apps/notes/min-version", json={"min_version": " 1.1.0 "})
    assert r.status_code == 200, r.text
    assert {a["app"]: a for a in r.json()["apps"]}["notes"]["min_version"]["min_version"] == "1.1.0"

    old = await client.get(f"{API}/me", headers=notes)
    assert old.status_code == 426 and old.json()["detail"] == \
        "This version of the Notes app is no longer supported. Update it to 1.1.0 or later."
    assert old.headers["cache-control"] == "no-store"
    assert (await client.get(f"{API}/notes", headers=notes)).status_code == 426
    assert (await client.get(f"{API}/me", headers=newer)).status_code == 200
    # The mailr app and the browser are not affected by the Notes floor.
    assert (await client.get(f"{API}/me", headers={"User-Agent": "mailr-android/0.6.0"})).status_code == 200
    assert (await client.get(f"{API}/me")).status_code == 200
    # An old Notes app still reaches health, the release list and its APK.
    assert (await client.get(f"{API}/health", headers=notes)).status_code == 200
    assert (await client.get(f"{API}/app/releases", headers=notes)).status_code == 200
    phone_apps.publish("notes", build("io.github.murtazanasir.mailr.notes", 5, "1.1.0"), by="t")
    assert (await client.get(f"{API}/app/notes.apk", headers=notes)).status_code == 200

    # Empty lifts it again, and the change is audited.
    r = await admin_client.put(f"{API}/admin/phone-apps/notes/min-version", json={"min_version": ""})
    assert r.status_code == 200
    assert (await client.get(f"{API}/me", headers=notes)).status_code == 200
    async with sessionmaker()() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.action == "admin.app_min_version").order_by(AuditLog.id))).scalars().all()
    assert [r.inputs.get("min_version") for r in rows][-2:] == ["1.1.0", ""]
    appversion.clear_cache()
