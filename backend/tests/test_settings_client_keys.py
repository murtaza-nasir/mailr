"""PATCH /me/settings writes only the settings a person chooses (CLIENT_SETTINGS).

users.settings also holds what the server keeps for itself: learned spam senders, the device's
zone and clock, reminders, calendar bookkeeping, task tags, notices. The route used to merge any
key it did not know, so a session could plant such state (once the store sign-in mark, which
recovery trusted). Now every documented setting round-trips, the ones with their own route are sent
there, and every other key is refused and never stored.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mailr.api.auth_routes import CLIENT_SETTINGS, ROUTED_SETTINGS
from mailr.db import sessionmaker
from mailr.models import User

ROOT = Path(__file__).resolve().parents[2]

# A valid value other than the default for every setting, as the route stores it.
SAMPLES = {
    "account_folder_pins": {},
    "account_folders": True,
    "account_folders_hint_dismissed": True,
    "account_folders_more_open": [],
    "account_folders_show_unread": True,
    "advanced_settings": True,
    "archive_also_removes": [],
    "archive_removes_current_label": False,
    "auto_advance": "older",
    "auto_label": False,
    "auto_label_min_examples": 50,
    "auto_label_precision": 0.8,
    "brief_position": "side",
    "calendar_add_new_invites": "needs_action",
    "calendar_conflicts": True,
    "calendar_default_duration": 45,
    "calendar_dim_past": True,
    "calendar_share_free_busy": True,
    "calendar_snap": 15,
    "calendar_video_template": "https://meet.example/{id}",
    "calendar_view": "week",
    "calendar_week_numbers": True,
    "calendar_working_hours": {"start": "08:00", "end": "16:00", "days": [1, 2, 3, 4]},
    "card_bundles": "always",
    "categories": ["primary", "social"],
    "conversation_view": False,
    "currency": "EUR",
    "date_order": "dmy",
    "default_reply": "reply_all",
    "density": "compact",
    "handled_elsewhere": ["desk@elsewhere.example"],
    "images": "ask",
    "importance_markers": False,
    "inbox_count": "all",
    "inbox_type": "unread_first",
    "keyboard_shortcuts": False,
    "label_recency": {"enabled": False, "half_life_months": 6},
    "language": "de",
    "llm_triage": False,
    "mail_notifications": "all",
    "meeting_prep_digest": True,
    "morning_hour": 7,
    "multiple_inboxes": [{"name": "Invoices", "q": "subject:invoice"}],
    "nav_favorites": ["inbox", "sent"],
    "notify_accounts": False,
    "notify_approvals": False,
    "notify_assistant": False,
    "page_size": 25,
    "priority_sections": ["unread", "everything"],
    "reading_pane": "right",
    "reading_pane_by_theme": {"classic": "below"},
    "reply_suggestions": False,
    "send_and_archive": True,
    "sender_pictures": True,
    "side_panel": "calendar",
    "signature_placement": "above",
    "spam_classifier": "score",
    "spam_junk_score": 12.0,
    "spam_suspect_score": 6.0,
    "spam_upstream_unknown_threshold": 3.0,
    "sweep_rules": "Keep receipts.",
    "tasks": {"keep_completed_copy": False},
    "theme": "dark",
    "time_format": "24h",
    "timezone": "Europe/Lisbon",
    "ui_accent": {"preset": "cards.teal", "seed": "#006a6a", "harmony": "single"},
    "ui_theme": "office",
    "undo_send_seconds": 20,
    "vacation": {"enabled": True, "start": "", "end": "", "subject": "Away", "message": "Back on Monday.",
                 "contacts_only": False, "resend_days": 4},
    "vip": ["boss@example.org"],
    "week_start": "monday",
}

# Kept by the server: a client may not set any of them.
SERVER_KEYS = ("store_signin_locked", "signin_epoch", "device_timezone", "device_clock", "reminders",
               "calendar_purged_rev", "calendar_defaults", "calendar_kinds", "collection_prefs", "task_tags",
               "deadline_muted_senders", "managed_declined", "event_change", "model_health", "working_set",
               "activity", "spam_domains", "_private", "anything_new")


def test_every_client_setting_has_a_sample():
    assert set(SAMPLES) == set(CLIENT_SETTINGS)


async def test_every_client_setting_round_trips(client, user):
    for key, value in SAMPLES.items():
        r = await client.patch("/api/v1/me/settings", json={key: value})
        assert r.status_code == 200, (key, r.text)
        assert r.json()[key] == value, (key, r.json()[key])
    me = (await client.get("/api/v1/me")).json()["settings"]
    for key, value in SAMPLES.items():
        assert me[key] == value, key


async def test_server_owned_and_unknown_keys_are_refused(client, user):
    for key in SERVER_KEYS:
        r = await client.patch("/api/v1/me/settings", json={key: True})
        assert r.status_code == 400, (key, r.text)
        # One bad key refuses the whole change.
        r = await client.patch("/api/v1/me/settings", json={"density": "compact", key: 1})
        assert r.status_code == 400, (key, r.text)
    async with sessionmaker()() as db:
        stored = (await db.get(User, user.id)).settings or {}
    assert not set(SERVER_KEYS) & set(stored)
    assert stored.get("density") != "compact"


async def test_routed_settings_name_their_route(client, user):
    for key, why in ROUTED_SETTINGS.items():
        r = await client.patch("/api/v1/me/settings", json={key: {}})
        assert r.status_code == 400 and why in r.text, (key, r.text)


def test_the_assistant_changes_only_client_settings():
    """update_settings goes through its own list; its Undo is a PATCH /me/settings."""
    from mailr.agent.config_tools import SETTINGS

    assert set(SETTINGS) <= CLIENT_SETTINGS


def test_documented_settings_are_client_settings_or_routed():
    """The settings registry (tools/registry/settings): every public per-user key is writable here or
    has its own route; no internal key is."""
    reg = ROOT / "tools" / "registry" / "settings"
    if not reg.is_dir():
        pytest.skip("no settings registry in this tree")
    import yaml

    public: set[str] = set()
    internal: set[str] = set()
    for f in reg.glob("*.yaml"):
        for k, v in (yaml.safe_load(f.read_text()) or {}).items():
            if "." in str(k):  # instance settings and the reminder defaults (their own routes)
                continue
            (internal if isinstance(v, dict) and "internal" in v else public).add(str(k))
    writable = CLIENT_SETTINGS | set(ROUTED_SETTINGS)
    assert public <= writable, sorted(public - writable)
    assert not internal & CLIENT_SETTINGS, sorted(internal & CLIENT_SETTINGS)


def test_the_web_apps_settings_are_client_settings_or_routed():
    """Every key of the web app's Settings type is writable here or has its own route."""
    src = ROOT / "frontend" / "src" / "api" / "types.ts"
    if not src.is_file():
        pytest.skip("no frontend in this tree")
    body = re.search(r"export interface Settings \{(.*?)\n\}", src.read_text(), re.DOTALL).group(1)
    keys = set(re.findall(r"^  ([a-z_]+)\??:", body, re.MULTILINE))
    assert keys <= CLIENT_SETTINGS | set(ROUTED_SETTINGS), sorted(keys - CLIENT_SETTINGS - set(ROUTED_SETTINGS))


async def test_the_older_working_hours_name_is_saved_as_calendar_working_hours(client, user):
    r = await client.patch("/api/v1/me/settings", json={"working_hours": {"start": "08:30", "end": "16:00", "days": [1, 2, 3]}})
    assert r.status_code == 200, r.text
    s = (await client.get("/api/v1/me")).json()["settings"]
    assert s["calendar_working_hours"]["start"] == "08:30" and "working_hours" not in s
