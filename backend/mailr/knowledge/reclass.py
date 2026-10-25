"""What an account carries along when it becomes local-only.

Switching a connector to `local_only` (Settings → Mail accounts, or the admin's local-only domains)
restamps its messages (admin_routes.set_connector_data_class). What mailr learned from those
messages before the switch is marked too, so it stays off hosted models from then on:

- memories whose references name one of the account's messages or conversations become sensitive;
- entities linked to one of its conversations become sensitive;
- contact-detail suggestions taken from its messages, and the details of a contact whose source is
  one of its messages, become local-only;
- cached conversation summaries of its conversations are deleted (the next view builds them again,
  on a local model).

The mark is sticky: switching the account back to normal leaves these marks in place.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import Connector, Entity, Message, Person, PersonSuggestion


async def mark_learned_local_only(db: AsyncSession, c: Connector) -> dict[str, int]:
    """Mark what was learned from connector `c`'s messages as local-only. Returns counts."""
    if c.id is None:
        return {}
    uid = c.user_id
    msg_ids = list((await db.execute(select(Message.id).where(Message.connector_id == c.id))).scalars())
    conv_ids = sorted({int(x) for x in (await db.execute(select(Message.conversation_id).where(
        Message.connector_id == c.id, Message.conversation_id.is_not(None)))).scalars() if x is not None})
    out = {"memories": 0, "entities": 0, "suggestions": 0, "people": 0, "summaries": 0}
    if not msg_ids:
        return out
    res = await db.execute(text(
        "UPDATE kb_memories SET sensitive = true WHERE user_id = :uid AND sensitive = false AND EXISTS ("
        " SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(refs) = 'array' THEN refs ELSE '[]'::jsonb END) r"
        " WHERE ((r->>'message_id') ~ '^[0-9]+$' AND (r->>'message_id')::bigint = ANY(:mids))"
        " OR ((r->>'conversation_id') ~ '^[0-9]+$' AND (r->>'conversation_id')::bigint = ANY(:cids)))"),
        {"uid": uid, "mids": msg_ids, "cids": conv_ids or [0]})
    out["memories"] = int(getattr(res, "rowcount", 0) or 0)
    if conv_ids:
        res = await db.execute(update(Entity).where(Entity.user_id == uid, Entity.sensitive.is_(False),
                                                    Entity.conversation_ids.overlap(conv_ids))
                               .values(sensitive=True))
        out["entities"] = int(getattr(res, "rowcount", 0) or 0)
    res = await db.execute(update(PersonSuggestion).where(PersonSuggestion.user_id == uid,
                                                          PersonSuggestion.local_only.is_(False),
                                                          PersonSuggestion.message_id.in_(msg_ids))
                           .values(local_only=True))
    out["suggestions"] = int(getattr(res, "rowcount", 0) or 0)
    wanted = set(msg_ids)
    people = (await db.execute(select(Person).where(Person.user_id == uid,
                                                    text("people.provenance::text LIKE '%message_id%'")))).scalars()
    for person in people:
        prov: dict[str, Any] = dict(person.provenance or {})
        changed = False
        for field, src in prov.items():
            if isinstance(src, dict) and not src.get("local_only") and src.get("message_id") in wanted:
                prov[field] = {**src, "local_only": True}
                changed = True
        if changed:
            person.provenance = prov
            out["people"] += 1
    if conv_ids:
        from mailr.intel.summarize import drop_cached

        out["summaries"] = drop_cached(uid, conv_ids)
    return out
