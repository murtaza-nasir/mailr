"""Worker jobs for People: address-book sync, strength rescoring, group suggestions
and signature details. Importing this module registers the handlers and the contacts.write executor."""

from __future__ import annotations

import logging
from typing import Any

from mailr.contacts import routes as _routes  # noqa: F401  (registers the contacts.write executor)
from mailr.core.jobs import enqueue, handler
from mailr.db import session_scope

log = logging.getLogger("mailr.contacts.tasks")


@handler("contacts.sync")
async def contacts_sync(payload: dict[str, Any], ctx) -> None:
    from mailr.contacts import carddav

    await carddav.sync_connector(int(payload["connector_id"]), full=bool(payload.get("full")))


@handler("people.rescore")
async def people_rescore(payload: dict[str, Any], ctx) -> None:
    from mailr.core import people

    async with session_scope() as db:
        await people.rescore(db, int(payload["user_id"]))


@handler("contacts.suggest")
async def contacts_suggest(payload: dict[str, Any], ctx) -> None:
    from mailr.contacts import suggest

    async with session_scope() as db:
        await suggest.refresh(db, int(payload["user_id"]))


@handler("contacts.signatures")
async def contacts_signatures(payload: dict[str, Any], ctx) -> None:
    from mailr.contacts import signatures

    async with session_scope() as db:
        await signatures.scan(db, int(payload["user_id"]))


async def due_address_books() -> None:
    """Every 30 minutes: read the address books of every calendar account."""
    from mailr.contacts import carddav

    await carddav.due_syncs()


async def queue_people(db, uid: int) -> None:
    """Daily, per user (core/tasks.py SPREAD): rescore strength, then look for group suggestions and
    signature details."""
    for kind in ("people.rescore", "contacts.suggest", "contacts.signatures"):
        await enqueue(db, kind, {"user_id": uid}, user_id=uid, dedupe_key=f"{kind}:{uid}")
