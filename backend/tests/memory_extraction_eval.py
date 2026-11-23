"""The memory extraction eval: what one learning pass leaves behind for a batch of sent mail.

The fixture (tests/fixtures/memory_extraction.yaml) holds synthetic batches, each with the notes
known before the pass and labels for what should and should not come out of it. `run_batch` seeds a
batch for a user, runs `tasks.extract_for_user` once and scores it; `report` adds the batches up:

- proposals per batch: new suggestions stored;
- duplicates: new notes that repeat a known note (`same`) instead of updating it;
- skipped wrongly kept: new notes that match an `avoid` pattern (events over, project details);
- wanted kept: of the `want` labels, the share some new or updated note says.

Targets (docs/design/memory-quality.md, E): no duplicate, nothing to skip kept, at least 80% of the
wanted notes. tests/test_memory_extraction_eval.py runs it against a real chat model when
MAILR_MEMORY_EVAL_CHAT_URL is set (and MAILR_MEMORY_EVAL_EMBED_URL, MAILR_MEMORY_EVAL_MICA_URL for
the merge by meaning).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, select

FIXTURE = Path(__file__).parent / "fixtures" / "memory_extraction.yaml"


def load(path: Path = FIXTURE) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _date() -> str:
    return datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")


async def run_batch(user, fx: dict[str, Any], batch: dict[str, Any], *, ingest, raw, me: str) -> dict[str, Any]:
    """Seed one batch (after clearing the user's notes), run a learning pass, score what is left."""
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.db import sessionmaker
    from mailr.knowledge import memory, tasks
    from mailr.models import AssistantProfile, Entity, Memory, Message, User

    async with sessionmaker()() as db:
        await db.execute(delete(Memory).where(Memory.user_id == user.id))
        await db.execute(delete(Entity).where(Entity.user_id == user.id))
        await db.execute(delete(Message).where(Message.user_id == user.id))
        prof = await db.get(AssistantProfile, user.id)
        if prof is not None:
            prof.state = {}
        await db.commit()
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        ents: dict[str, int] = {}
        for key, spec in (fx.get("entities") or {}).items():
            e, _ = await memory.upsert_entity(db, p, dict(spec))
            ents[key] = e.id
        known: dict[str, int] = {}
        for key, n in (batch.get("known") or {}).items():
            about = n.get("about")
            data: dict[str, Any] = {"text": n["text"], "kind": "fact"}
            refs: list[dict[str, Any]] = []
            if about == "self":
                data["entity_id"] = (await memory.self_entity(db, p, create=True)).id
            elif about in ents and n.get("status") == "active":
                data["entity_id"] = ents[about]
            elif about in (fx.get("entities") or {}):
                spec = fx["entities"][about]
                refs = [{"about": {"name": spec["name"], "kind": spec["kind"], "emails": spec.get("emails") or []}}]
            m, _ = await memory.remember(db, p, data, source="extracted", status=n.get("status", "active"), refs=refs)
            known[key] = m.id
        await memory.embed_pending(db, user.id)
        await db.commit()
    for e in batch["emails"]:
        irt = None
        if e.get("answering"):
            irt = f"<{uuid.uuid4().hex}@example.org>"
            await ingest(user.id, raw("Re: " + e["subject"].removeprefix("Re: "), frm=e["answering"]["from"], to=me,
                                      msgid=irt, body=e["answering"]["body"], date=_date()))
        await ingest(user.id, raw(e["subject"], frm=f"Me <{me}>", to=e["to"], irt=irt, body=e["body"], date=_date()),
                     source="send", roles=["sent"])
    res = await tasks.extract_for_user(user.id, force=True)
    async with sessionmaker()() as db:
        rows = list((await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars())
    known_ids = set(known.values())
    before = {known[k]: n["text"] for k, n in (batch.get("known") or {}).items()}
    new = [m.text for m in rows if m.id not in known_ids]
    changed = [m.text for m in rows if m.id in known_ids and m.text != before[m.id]]
    said = new + changed
    ok = batch.get("ok") or []
    dupes = [t for k in batch.get("same") or [] for t in new
             if re.search(batch["known"][k]["match"], t) and not _is_edit(rows, t, known[k])
             and not any(re.search(pat, t) for pat in ok)]
    avoided = [t for t in new for pat in batch.get("avoid") or [] if re.search(pat, t)]
    want = batch.get("want") or {}
    kept = [k for k, pat in want.items() if any(re.search(pat, t) for t in said)]
    return {"id": batch["id"], "result": res, "new": new, "changed": changed, "duplicates": dupes,
            "avoided_kept": avoided, "wanted": len(want), "wanted_kept": len(kept),
            "missed": [k for k in want if k not in kept]}


def _is_edit(rows, text: str, known_id: int) -> bool:
    """A suggestion that edits the known note (refs `replaces`) is an update, not a copy."""
    return any(m.text == text and any(isinstance(r, dict) and r.get("replaces") == known_id for r in m.refs or [])
               for m in rows)


def report(results: list[dict[str, Any]]) -> dict[str, Any]:
    wanted = sum(r["wanted"] for r in results)
    return {"batches": len(results),
            "proposals_per_batch": round(sum(len(r["new"]) for r in results) / max(1, len(results)), 2),
            "duplicates": sum(len(r["duplicates"]) for r in results),
            "avoided_kept": sum(len(r["avoided_kept"]) for r in results),
            "wanted_kept": round(sum(r["wanted_kept"] for r in results) / wanted, 2) if wanted else 1.0}
