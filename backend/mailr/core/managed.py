"""Mail accounts an administrator connects for a member.

Off by default: the instance setting `accounts.admin_connect` (Admin → Users). With it off, the
usual rule holds unchanged: each person connects and manages their own mail
accounts. With it on, an administrator can connect an IMAP/SMTP account for a member, in
Admin → Users (an existing account) or in an email preset (Prepare a new user), which connects it
at that person's first sign-in.

How the password is kept. Every user's secrets are sealed with that user's own data key, and the
data key is wrapped by the instance key (core/crypto.py). The server can therefore seal a value
for any member without the member being present: an admin-entered password is sealed exactly as
the member's own would be (encrypt_for_user, bound to the connector's row), and nothing about
other accounts changes. A preset's password waits under the instance key, bound to that preset
(`preset_context`), and is moved under the member's key and removed from the preset when the
account is made.

What the member sees. The account says "Connected by your administrator"
(`connectors.managed_by_admin`). They can change or disconnect it like their own. A
disconnected one is remembered (`DECLINED` in the member's settings): connecting the same mailbox
again needs the administrator to say so explicitly (`reconnect`), and the member is told again.
Each step writes the member's activity (admin_notice.*) and the administrator's own log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.models import Connector, User

SETTING = "accounts.admin_connect"
# The member's settings key: the mailboxes they disconnected after an administrator connected them.
DECLINED = "managed_declined"
MAX_DECLINED = 50


async def enabled(db: AsyncSession) -> bool:
    from mailr.api.admin_routes import get_instance_setting

    return bool(await get_instance_setting(db, SETTING, False))


def mailbox_key(cfg: dict[str, Any]) -> str:
    """One mailbox: the IMAP server and the account on it, lowercased."""
    imap = cfg.get("imap") or {}
    user = str(imap.get("username") or cfg.get("email") or "").strip().lower()
    return f"{str(imap.get('host') or '').strip().lower()}|{user}"


def address(c: Connector) -> str:
    cfg = c.config or {}
    return str(cfg.get("email") or (cfg.get("imap") or {}).get("username") or c.name)


def declined(user: User) -> list[dict[str, Any]]:
    v = (user.settings or {}).get(DECLINED) or []
    return [d for d in v if isinstance(d, dict) and d.get("key")]


def declined_for(user: User, key: str) -> dict[str, Any] | None:
    return next((d for d in declined(user) if d["key"] == key), None)


async def remember_declined(db: AsyncSession, user: User, c: Connector) -> None:
    from mailr.core import usersettings

    key = mailbox_key(c.config or {})
    await db.refresh(user, ["settings"])
    items = [d for d in declined(user) if d["key"] != key]
    items.append({"key": key, "address": address(c), "at": datetime.now(UTC).isoformat()})
    await usersettings.patch(db, user.id, {DECLINED: items[-MAX_DECLINED:]})


async def forget_declined(db: AsyncSession, user: User, key: str) -> None:
    from mailr.core import usersettings

    await db.refresh(user, ["settings"])
    items = [d for d in declined(user) if d["key"] != key]
    await usersettings.patch(db, user.id, {DECLINED: items})


def preset_context(preset_id: str) -> str:
    return f"onboarding_preset:{preset_id}:mail_account"


def out(c: Connector, owner: User | None = None) -> dict[str, Any]:
    """What an administrator sees of a managed account: never the password, no mail."""
    return {"id": c.id, "user_id": c.user_id, "owner": (owner.name or owner.email) if owner else None,
            "name": c.name, "address": address(c), "mode": c.mode, "data_class": c.data_class,
            "status": c.status, "enabled": c.enabled,
            "created_at": c.created_at.isoformat() if c.created_at else None}


async def on_member_disconnect(db: AsyncSession, member: User, c: Connector) -> None:
    """The member removed an account an administrator connected: remember the mailbox, and write
    both sides' activity. The caller deletes the connector and commits."""
    from mailr.core import activity

    await remember_declined(db, member, c)
    addr = address(c)
    await activity.add(db, member.id, "user", "account.managed_disconnect", str(c.id),
                       activity.entry("account", i18n.msg("activity.account.managed_disconnect", address=addr)))
    if c.managed_by and c.managed_by != member.id:
        who = member.name or member.email
        await activity.add(db, c.managed_by, "system", "account.managed_disconnected", str(c.id),
                           activity.entry("account", i18n.msg("activity.account.managed_disconnected", who=who,
                                                                  address=addr)))


# ---------------------------------------------------------------- presets (Prepare a new user)
#
# An email preset may carry `mail_account`: {name, mode, data_class, config, identities, by,
# secrets_enc} with the secrets sealed under the instance key for that preset only. At the first
# sign-in (core/onboarding.apply_preset) the account is made for the new member, the sealed
# secrets leave the preset, and `connected_at`/`connector_id` (or `skipped`) record what happened.

PRESET_FIELDS = ("name", "mode", "data_class", "config", "identities")


def seal_preset_secrets(preset_id: str, secrets: dict[str, Any]) -> str:
    import base64

    from mailr.core.crypto import encrypt_instance

    return base64.b64encode(encrypt_instance(secrets, context=preset_context(preset_id))).decode()


def open_preset_secrets(preset_id: str, sealed: str | None) -> dict[str, Any]:
    import base64

    from mailr.core.crypto import decrypt_instance

    if not sealed:
        return {}
    return decrypt_instance(base64.b64decode(sealed), context=preset_context(preset_id)) or {}


def preset_account_out(acct: dict[str, Any] | None) -> dict[str, Any] | None:
    """A preset's mail account as the admin sees it: never the password."""
    if not acct:
        return None
    cfg = acct.get("config") or {}
    return {"name": acct.get("name") or "", "mode": acct.get("mode") or "mirror",
            "data_class": acct.get("data_class") or "normal",
            "address": str(cfg.get("email") or (cfg.get("imap") or {}).get("username") or ""),
            "config": cfg, "identities": acct.get("identities"),
            "has_password": bool(acct.get("secrets_enc")), "connected_at": acct.get("connected_at"),
            "connector_id": acct.get("connector_id"), "skipped": acct.get("skipped")}


async def connect_from_preset(db: AsyncSession, user: User, preset: dict[str, Any]) -> Connector | None:
    """At the first sign-in: make the preset's mail account for `user`, as the administrator who
    prepared it. Only for an address the sign-in proved (email_verified) and while the setting is
    on. The sealed password leaves the preset either way. The caller saves the presets and commits."""
    import logging
    from types import SimpleNamespace

    from mailr.api.connector_routes import ConnectorDraft, create_mail_account
    from mailr.core.audit import notice
    from mailr.core.netguard import private_ok_for

    acct = preset.get("mail_account")
    if not acct or acct.get("connected_at") or not acct.get("secrets_enc"):
        return None
    sealed = acct.pop("secrets_enc", None)
    now = datetime.now(UTC).isoformat()
    if not await enabled(db):
        acct["skipped"] = "Not connected: administrators may no longer connect mail accounts on this server."
        return None
    if not user.email_verified:
        acct["skipped"] = "Not connected: nobody has proved that this person owns the address they signed in with."
        return None
    by = await db.get(User, acct.get("by")) if acct.get("by") else None
    if by is None or by.role != "admin" or by.disabled:
        acct["skipped"] = "Not connected: the administrator who prepared it is no longer an administrator."
        return None
    try:
        secrets = open_preset_secrets(preset["id"], sealed)
        draft = ConnectorDraft(kind="imap", name=acct.get("name") or "", mode=acct.get("mode") or "mirror",
                               data_class=acct.get("data_class") or "normal", config=acct.get("config") or {},
                               secrets=secrets, identities=acct.get("identities"), skip_test=True)
        c = await create_mail_account(db, user, draft, private_ok=private_ok_for(user), managed_by=by)
    except Exception as e:  # noqa: BLE001  the sign-in goes on; the admin sees why
        logging.getLogger("mailr.managed").warning("preset %s: mail account not connected: %s", preset.get("id"), e)
        acct["skipped"] = f"Not connected: {getattr(e, 'detail', None) or type(e).__name__}"
        return None
    acct.update(connected_at=now, connector_id=c.id)
    addr = address(c)
    await notice(db, SimpleNamespace(user_id=by.id, user=by), user.id, "account_connected",
                 i18n.msg("activity.admin.account_connected", address=addr), {"connector_id": c.id, "address": addr})
    from mailr.core.audit import audit

    await audit(db, SimpleNamespace(user_id=by.id, user=by, token=None, actor="user"), "admin.accounts.connect",
                str(c.id), {"user_id": user.id, "address": addr, "preset": preset.get("id")}, always=True)
    return c
