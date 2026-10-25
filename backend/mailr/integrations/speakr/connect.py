"""Adding, testing and changing a Speakr connector (speakr.connect; Settings → Accounts → Speakr).

Requests for kind "speakr" go from api/connector_routes.py to these functions, so a Speakr account
has the same routes as the others: POST /connectors/test, POST /connectors, PATCH and DELETE /connectors/{id}.
The token is sealed with the user's data key and never returned, logged or put in a URL. One
mailr user connects a given Speakr server once.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.connectors.loader import set_secrets
from mailr.core.crypto import connector_context, decrypt_for_user
from mailr.core.netguard import private_ok_for
from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import store
from mailr.models import Connector, User

# Switches in the connector card. write_back (edit title, participants, notes, tags) and
# share_links (public links) stay off until the user turns them on, and so does
# include_shared (recordings other users shared with the account).
CONFIG_KEYS = ("speakr_models_local", "write_back", "share_links", "include_shared")

# The scopes a token for mailr needs: read always; write, upload and share for the switches that use
# them. Scopes beyond what mailr can use are named as a warning.
RISKY_SCOPES = ("full", "delete", "webhooks", "account", "process")


def scope_notes(scopes: list[str] | None, cfg: dict[str, Any] | None = None, lang: str | None = None) -> list[str]:
    """Warnings about a token's scopes for the wizard and the card (empty for an older Speakr,
    whose tokens have no scopes), in `lang` (mail.speakr.scope.*). `cfg` names the switches that are on."""
    if scopes is None:
        return []
    cfg = cfg or {}
    S = lambda k, **kw: i18n.t(f"mail.speakr.scope.{k}", lang, **kw)  # noqa: E731
    out = []
    if "full" in scopes:
        out.append(S("full"))
        return out
    extra = [s for s in scopes if s in RISKY_SCOPES[1:]]
    if extra:
        out.append(S("extra", scopes=", ".join(extra), count=len(extra)))
    if "read" not in scopes:
        out.append(S("no_read"))
    need = {"write_back": "write", "share_links": "share"}
    for key, scope in need.items():
        if cfg.get(key) and scope not in scopes:
            out.append(S(f"no_{scope}"))
    if "upload" not in scopes:
        out.append(S("no_upload"))
    if "webhooks" not in scopes:
        out.append(S("no_webhooks"))
    return out


def _err(e: sc.SpeakrError) -> str:
    if isinstance(e, sc.SpeakrAuthError):
        return sc.AUTH_MESSAGE
    if isinstance(e, sc.SpeakrNotFound):
        return "This address answered, but not with Speakr's API (/api/v1)"
    return str(e)


async def probe(base_url: str, token: str, private_ok: bool | None = None) -> dict[str, Any]:
    """GET /users/me and /stats: who the token belongs to and how many recordings there are."""
    url = sc.normalize_base(base_url)
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "The Speakr address must start with https:// (or http:// on a private network)")
    if not token.strip():
        raise HTTPException(400, "Paste the API token from Speakr")
    cl = sc.SpeakrClient(url, token.strip(), private_ok=private_ok)
    try:
        me = await cl.me()
        stats = await cl.stats()
        caps = await cl.capabilities()
        tok = await cl.token_info() if sc.has_feature(caps, "token_scopes") else None
    except sc.SpeakrError as e:
        raise HTTPException(400, _err(e)) from None
    total = ((stats.get("recordings") or {}).get("total")) if isinstance(stats.get("recordings"), dict) else None
    return {"base_url": url, "speakr_user_id": me.get("id"), "speakr_username": str(me.get("username") or ""),
            "speakr_name": str(me.get("name") or me.get("username") or ""), "recordings": total,
            "capabilities": caps, "token": tok}


# ACT speakr.connect @api
async def test(body: Any, private_ok: bool | None = None, lang: str | None = None) -> dict[str, Any]:
    cfg = body.config or {}
    try:
        info = await probe(str(cfg.get("base_url") or cfg.get("url") or ""), str((body.secrets or {}).get("token") or ""),
                           private_ok)
    except HTTPException as e:
        return {"ok": False, "detail": e.detail, "folders": [], "identities": [], "extensions": []}
    who = info["speakr_name"] or info["speakr_username"]
    tok = info.get("token")
    scopes = tok["scopes"] if tok else None
    detail = i18n.t("mail.speakr.signed_in_count", lang, who=who, count=info["recordings"]) \
        if isinstance(info["recordings"], int) else i18n.t("mail.speakr.signed_in", lang, who=who)
    if scopes is not None:
        detail += " " + i18n.t("mail.speakr.token_access", lang, scopes=i18n.t("mail.speakr.token_full", lang)
                               if "full" in scopes else ", ".join(scopes))
    return {"ok": True, "detail": detail, "account": info, "scopes": scopes,
            "warnings": scope_notes(scopes, cfg, lang), "folders": [], "identities": [], "extensions": []}


# ACT speakr.connect
async def create(db: AsyncSession, p: Any, body: Any) -> Connector:
    cfg = body.config or {}
    token = str((body.secrets or {}).get("token") or "").strip()
    info = await probe(str(cfg.get("base_url") or cfg.get("url") or ""), token, private_ok_for(p.user))
    dup = (await db.execute(select(Connector).where(Connector.user_id == p.user_id, Connector.kind == store.KIND))).scalars()
    if any(store.base_url(c) == info["base_url"] for c in dup):
        raise HTTPException(409, "This Speakr server is already connected")
    # Recordings hold other people's voices: local-only unless the user chose otherwise.
    data_class = body.data_class if "data_class" in body.model_fields_set else "local_only"
    row = Connector(user_id=p.user_id, kind=store.KIND, name=(body.name or "").strip()[:200] or f"Speakr ({info['speakr_username']})",
                    mode="mirror", data_class=data_class,
                    config={"base_url": info["base_url"], "speakr_user_id": info["speakr_user_id"],
                            "speakr_username": info["speakr_username"], "speakr_name": info["speakr_name"],
                            "speakr_models_local": bool(cfg.get("speakr_models_local", False)),
                            "speakr_capabilities": info["capabilities"],
                            **({"speakr_token": info["token"]} if info.get("token") else {})},
                    state={}, retention_days=0, delete_upstream=False, enabled=True, status="new",
                    status_detail="Starting…")
    db.add(row)
    await db.flush()
    await set_secrets(db, row, {"token": token})
    from mailr.integrations.speakr.sync import queue_sync

    await queue_sync(db, row)
    await db.commit()
    return row


# ACT speakr.connect
async def patch(db: AsyncSession, p: Any, c: Connector, body: Any) -> Connector:
    token = str((body.secrets or {}).get("token") or "").strip()
    cfg = dict(c.config or {})
    new_url = (body.config or {}).get("base_url")
    if new_url is not None and sc.normalize_base(str(new_url)) != store.base_url(c) and not token:
        # A saved token is only ever sent to the server it was saved for.
        raise HTTPException(400, "enter the token again when changing the Speakr address")
    if token:
        info = await probe(str(new_url or cfg.get("base_url") or ""), token, private_ok_for(p.user))
        cfg.update({k: info[k] for k in ("base_url", "speakr_user_id", "speakr_username", "speakr_name")})
        cfg["speakr_capabilities"] = info["capabilities"]
        if info.get("token"):
            cfg["speakr_token"] = info["token"]
        else:
            cfg.pop("speakr_token", None)
        user = await db.get(User, c.user_id)
        kept = (decrypt_for_user(user.id, user.data_key_wrapped, c.secrets_enc, context=connector_context(c.id)) or {}
                if c.secrets_enc else {})
        await set_secrets(db, c, {**{k: v for k, v in kept.items() if k == "webhook_secret"}, "token": token})
        store.forget(c.id)
    shared_before = bool(cfg.get("include_shared"))
    for k in CONFIG_KEYS:
        if body.config and k in body.config:
            cfg[k] = bool(body.config[k])
    if bool(cfg.get("include_shared")) != shared_before and cfg.get("speakr_webhook_id") \
            and sc.has_feature(cfg.get("speakr_capabilities"), "internal_sharing"):
        # mailr's webhook follows the switch: events of shared recordings too, or not.
        try:
            await (await store.client_for(db, c)).update_webhook(int(cfg["speakr_webhook_id"]),
                                                                 {"include_shared": bool(cfg.get("include_shared"))})
        except sc.SpeakrError:
            pass  # the ten-minute sync still covers shared recordings
    if body.secrets and "webhook_secret" in body.secrets:
        # Webhooks (webhook.py): a secret turns them on with a new random address; an empty one
        # turns them off (and deletes the webhook in Speakr when mailr made it). The token stays.
        from mailr.integrations.speakr.webhook import new_webhook_id, unregister

        secret = str(body.secrets.get("webhook_secret") or "").strip()
        if not secret and cfg.get("speakr_webhook_id"):
            c.config = cfg
            await unregister(db, c)
            cfg = {**dict(c.config or {}), **{k: cfg[k] for k in CONFIG_KEYS if k in cfg}}
        current = {"token": token or await store.token_of(db, c)}
        if secret:
            current["webhook_secret"] = secret[:500]
            cfg["webhook_id"] = cfg.get("webhook_id") or new_webhook_id()
        else:
            cfg.pop("webhook_id", None)
        await set_secrets(db, c, current)
    c.config = cfg
    if body.name is not None:
        c.name = body.name.strip()[:200] or c.name
    if body.data_class:
        c.data_class = body.data_class
    if body.enabled is not None:
        c.enabled = body.enabled
        if body.enabled and c.status == "paused":
            c.status, c.status_detail = "new", "Resuming…"
    if token or bool(cfg.get("include_shared")) != shared_before:
        from mailr.integrations.speakr.sync import queue_sync

        await queue_sync(db, c, reset=bool(token))
    await db.commit()
    return c
