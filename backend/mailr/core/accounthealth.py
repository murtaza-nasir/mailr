"""Account health in plain words, for the account's owner and the admin.

`of(connector)` says in one short sentence whether a mail account syncs: "Up to date", "Syncing
for the first time", "Paused", "Needs its password again", "Not synced for 3 hours", or "Sync
failed". A store-mode account says that deletion at the provider is paused while the mail export
is off (unless MAILR_STORE_BACKUP_CHECK=false, which takes the export out of that deletion). The owner sees it in Settings → Mail → Mail accounts (and a summary line above the list); the admin
sees it in Admin → Diagnostics.

`on_status(...)` runs when the runner changes an account's status (connectors/runner.py). When an
account newly stops because the provider refused its sign-in, the owner gets one notification
(push, and a toast in open windows) that names the account and says where to fix it. It is sent
once per failure: a retry that fails the same way is quiet, and the next one comes only after the
account worked again. A local-only account's notification never names it.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from mailr import i18n
from mailr.config import get_settings

log = logging.getLogger("mailr.accounthealth")

# What providers answer when the password, app password or OAuth grant no longer works.
_SIGNIN = re.compile(
    r"AUTHENTICATIONFAILED|authentication failed|invalid credentials|LOGIN failed|LoginError|login error|"
    r"username and password not accepted|invalid_grant|invalid_client|token (has been )?(expired|revoked)|"
    r"\b535\b|AUTHENTICATE failed|password (is )?(incorrect|wrong|expired)|app password|"
    r"\bunauthori[sz]ed\b|\b401\b",
    re.IGNORECASE)

STALE_SECONDS = 2 * 3600
SETTINGS_LINK = "/settings/mail/accounts"


def needs_signin(status: str | None, detail: str | None) -> bool:
    return status == "error" and bool(_SIGNIN.search(detail or ""))


def _health(state: str, code: str, lang: str, **params: Any) -> dict[str, Any]:
    """{state, text, code, params}: `text` in `lang`, `code` (under health.account.) and `params`
    for an app that shows it in its own language."""
    return {"state": state, "text": i18n.t(f"health.account.{code}", lang, **params), "code": f"account.{code}",
            "params": params}


def held_deletions(c: Any) -> int:
    """Messages a mirror account did not remove although a listing said they were gone upstream:
    the listing was suspect (connectors/imap.py `sweep_verdict`)."""
    warnings = (getattr(c, "state", None) or {}).get("sync_warnings") or {}
    return sum(int(w.get("count") or 0) for w in warnings.values()
               if isinstance(w, dict) and w.get("code") == "deletions_held")


def of(c: Any, now: datetime | None = None, lang: str = i18n.DEFAULT, unsynced: int = 0,
       unsaved_sent: int = 0) -> dict[str, Any]:
    """{state, text, code, params}: state is ok | syncing | paused | signin | stale | error.
    `unsynced`: local changes the provider has not confirmed yet (`unsynced_changes`); with held
    removals they show as "stale" ("needs a look"), never as "Up to date". `unsaved_sent`: sent
    messages that could not be saved in the provider's Sent folder (connectors/sentcopy.py
    `unsaved`), "stale" too."""
    now = now or datetime.now(UTC)
    status, detail = c.status or "new", c.status_detail or ""
    if not c.enabled or status == "paused":
        return _health("paused", "paused", lang)
    if needs_signin(status, detail):
        return _health("signin", "signin", lang)
    if status == "error":
        return _health("error", "error", lang)
    if c.mode == "store" and not get_settings().mail_export and get_settings().store_backup_check:
        # Retention needs the mail export (connectors/tasks.py retention): nothing is deleted
        # at the provider while MAILR_MAIL_EXPORT is off and MAILR_STORE_BACKUP_CHECK is on.
        return _health("stale", "deletion_paused_export_off", lang)
    if held := held_deletions(c):
        return _health("stale", "deletions_held", lang, count=held)
    if unsynced:
        return _health("stale", "changes_not_synced", lang, count=unsynced)
    if unsaved_sent:
        from mailr.connectors.sentcopy import provider_name

        return _health("stale", "sent_not_saved", lang, count=unsaved_sent, provider=provider_name(c))
    if c.last_sync_at is None:
        if status in ("new", "syncing"):
            return _health("syncing", "first_sync", lang)
        return _health("syncing", "not_synced_yet", lang)
    age = (now - c.last_sync_at).total_seconds()
    poll = int((c.config or {}).get("poll_minutes") or 0) * 60
    if c.mode in ("store", "mirror") and age > max(STALE_SECONDS, 2 * poll):
        h = int(age // 3600)
        if h < 48:
            return _health("stale", "stale_hours", lang, count=h)
        return _health("stale", "stale_days", lang, count=h // 24)
    if status == "syncing":
        return _health("syncing", "syncing", lang)
    return _health("ok", "ok", lang)


# A write-back that failed this many times (about 15 minutes of retries) counts as "not synced yet".
UNSYNCED_AFTER_ATTEMPTS = 3


async def unsynced_changes(db, connector_ids: list[int]) -> dict[int, int]:
    """Per mirror account: messages whose local change (read, star, move, label) the provider has
    not confirmed after several tries: write-backs still retrying, or given up on in the last
    30 days (connectors/tasks.py `write_back`). Local state stays as it is meanwhile
    (connectors/runner.py `_pending_write_backs`)."""
    if not connector_ids:
        return {}
    from sqlalchemy import text

    rows = await db.execute(text(
        """SELECT (payload->>'connector_id')::int AS c, count(DISTINCT x) FROM jobs,
             jsonb_array_elements_text(coalesce(payload->'message_ids', '[]'::jsonb)) x
           WHERE kind = 'connector.write_back' AND (payload->>'connector_id')::int = ANY(:ids)
             AND ((status IN ('queued', 'running') AND attempts >= :n)
                  OR (status = 'failed' AND created_at > now() - interval '30 days'))
           GROUP BY 1"""), {"ids": list(connector_ids), "n": UNSYNCED_AFTER_ATTEMPTS})
    return {int(c): int(n) for c, n in rows}


def summary(rows: list[dict[str, Any]], lang: str = i18n.DEFAULT) -> dict[str, Any]:
    """One line over a member's accounts: the worst state first. rows: {name, health}."""
    if not rows:
        return {"state": "none", "text": ""}
    bad = [r for r in rows if r["health"]["state"] in ("signin", "error", "stale")]
    if not bad:
        n = len(rows)
        return {"state": "ok", "text": i18n.t("health.summary.ok", lang, count=n)}
    first = next((r for r in bad if r["health"]["state"] == "signin"), bad[0])
    text = first["health"]["text"].rstrip(".")
    if len(bad) > 1:
        return {"state": first["health"]["state"],
                "text": i18n.t("health.summary.bad_more", lang, name=first["name"], text=text, count=len(bad) - 1)}
    return {"state": first["health"]["state"], "text": i18n.t("health.summary.bad", lang, name=first["name"], text=text)}


async def on_status(db, connector_id: int, user_id: int, before: tuple[str | None, str | None],
                    status: str, detail: str) -> None:
    """After a status change: notify the owner once when the account starts needing a sign-in."""
    if not needs_signin(status, detail) or needs_signin(*before):
        return
    from mailr.core import push
    from mailr.core.events import notify
    from mailr.models import Connector

    c = await db.get(Connector, connector_id)
    if c is None:
        return
    private = c.data_class == "local_only"
    from mailr.models import User

    lang = i18n.user_language(await db.get(User, user_id))
    title = (i18n.t("push.account.title_private", lang) if private
             else i18n.t("push.account.title", lang, name=c.name))
    body = i18n.t("push.account.body", lang)
    data = {"connector_id": c.id, "click": SETTINGS_LINK}
    try:
        await push.fan_out(db, user_id, "account", title, body, data, private=private)
    except Exception as e:  # noqa: BLE001  the notice is a courtesy; the status is already saved
        log.warning("could not send the sign-in notice for account %s: %s", connector_id, type(e).__name__)
    await notify(db, user_id, {"type": "notification", "kind": "account", "title": title, "body": body, **data})
