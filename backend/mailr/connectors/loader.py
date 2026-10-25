from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from mailr.connectors.base import REGISTRY, MailConnector
from mailr.core.crypto import connector_context, decrypt_for_user, encrypt_for_user
from mailr.models import Connector, User


def _load_all() -> None:
    # Importing the modules registers their connector classes.
    from mailr.connectors import gmail, imap, mbox  # noqa: F401


async def connector_for(db: AsyncSession, row: Connector) -> MailConnector:
    _load_all()
    user = await db.get(User, row.user_id)
    secrets = decrypt_for_user(user.id, user.data_key_wrapped, row.secrets_enc,
                               context=connector_context(row.id)) or {}
    cls = REGISTRY.get(row.kind)
    if cls is None:
        raise ValueError(f"unknown connector kind {row.kind}")
    from mailr.core.netguard import private_ok_for

    conn = cls(row, secrets)
    conn.private_ok = private_ok_for(user)  # may this account's servers be on the LAN?
    return conn


async def set_secrets(db: AsyncSession, row: Connector, secrets: dict) -> None:
    user = await db.get(User, row.user_id)
    if row.id is None:
        await db.flush()  # the id binds the ciphertext to this connector
    row.secrets_enc = encrypt_for_user(user.id, user.data_key_wrapped, secrets, context=connector_context(row.id))
