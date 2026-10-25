"""One notification model: what notifies a user, for every device.

Four account settings decide what notifies, the same for Web Push, the toasts and desktop
notifications of open web windows, and the Android app (which reads them from GET /me):

- `mail_notifications`: off | vip | important | primary | all. VIP: mail from VIP senders only.
  Important: important mail (VIP mail is always important). Primary: important mail and mail in
  the Primary category. All: all new mail in the Inbox.
- `notify_approvals`: an agent or the assistant waits for approval, or a message failed to send.
- `notify_assistant`: messages from the assistant and standing orders.
- `notify_accounts`: a mail account needs its password again (core/accounthealth.py).

Reminders keep their own switches (GET /reminders/settings: events, tasks).

A device may only narrow this: a push target whose `events` list names neither "new_mail" nor
"important_mail" gets no mail (the per-device "Mail on this device: Off"), and one that lists
events gets approvals, the assistant and account notices only when it lists "approval",
"assistant" ("account" rides with "approval"). An empty list follows the account settings.

`desktop_notifications` (the old web-only mail setting) is read as `mail_notifications` when
the new key is missing, and PATCH /me/settings still takes it for older clients.
"""

from __future__ import annotations

from typing import Any

MAIL_LEVELS = ("off", "vip", "important", "primary", "all")
DEFAULTS: dict[str, Any] = {"mail_notifications": "important", "notify_approvals": True,
                            "notify_assistant": True, "notify_accounts": True}
LEGACY = "desktop_notifications"
KIND_KEY = {"approval": "notify_approvals", "send_failed": "notify_approvals",
            "assistant": "notify_assistant", "account": "notify_accounts"}


def of(settings: dict[str, Any] | None) -> dict[str, Any]:
    s = settings or {}
    mail = s.get("mail_notifications")
    if mail not in MAIL_LEVELS:
        legacy = s.get(LEGACY)
        mail = legacy if legacy in MAIL_LEVELS else DEFAULTS["mail_notifications"]
    out = {"mail_notifications": mail}
    for k in ("notify_approvals", "notify_assistant", "notify_accounts"):
        v = s.get(k)
        out[k] = v if isinstance(v, bool) else DEFAULTS[k]
    return out


def mail_wanted(prefs: dict[str, Any], *, important: bool, vip: bool, primary: bool) -> bool:
    level = prefs["mail_notifications"]
    if level == "all":
        return True
    if level == "primary":
        return important or vip or primary
    if level == "important":
        return important or vip
    if level == "vip":
        return vip
    return False


def kind_wanted(prefs: dict[str, Any], event: str) -> bool:
    """Does the account want this kind of notification at all? Mail is decided per message
    (mail_wanted); reminders by their own settings."""
    if event in ("new_mail", "important_mail"):
        return prefs["mail_notifications"] != "off"
    key = KIND_KEY.get(event)
    return bool(prefs[key]) if key else True


def device_wants(events: list[str] | None, event: str) -> bool:
    """Does one device take this kind (its own narrowing; see the module text)?"""
    ev = set(events or [])
    if event == "reminder":
        return "reminder" in ev
    if not ev:
        return True
    if event in ("new_mail", "important_mail"):
        return bool(ev & {"new_mail", "important_mail"})
    if event in ("account", "send_failed"):
        return bool(ev & {event, "approval"})
    return event in ev
