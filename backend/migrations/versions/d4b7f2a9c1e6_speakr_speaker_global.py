"""Speakr: speaker mappings by name on the whole connector (integrations/speakr/people.py)

Revision ID: d4b7f2a9c1e6
Revises: c8e2f4a6b1d3
Create Date: 2026-10-01 18:00:00

A named speaker is now keyed "name:<lower-case name>" on every recording of a connector. Rows
stored under a voice-profile key ("id:<n>") with a speaker name, and rows stored under a
recording's label ("label:<recording>:<label>") whose label is a name, move to the name key. When
the name key already has a different confirmed person, a label row becomes the override of its
own recording ("rec:<recording>:name:<name>", no speaker name) and an id row is dropped (the newer
choice stays). A row whose name key and address already exist is dropped. Unnamed labels
(SPEAKER_02) stay as they are.
"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4b7f2a9c1e6'
down_revision: Union[str, Sequence[str], None] = 'c8e2f4a6b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LABEL_RX = re.compile(r"^speaker[ _-]?\d+$")


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def plan(rows: list[dict]) -> tuple[list[tuple[int, str, str]], list[int]]:
    """(updates [(id, new key, new speaker name)], deletes [id]) for rows
    {id, connector_id, speaker_key, speaker_name, email, state}, newest first."""
    taken = {(r["connector_id"], r["speaker_key"], r["email"]) for r in rows}
    confirmed = {(r["connector_id"], r["speaker_key"]): r["email"] for r in rows if r["state"] == "confirmed"}
    updates, deletes = [], []
    for r in rows:
        key, cid = r["speaker_key"], r["connector_id"]
        rid = None
        if key.startswith("id:"):
            name = _norm(r["speaker_name"])
        elif key.startswith("label:"):
            parts = key.split(":", 2)
            if len(parts) < 3:
                continue
            rid, name = parts[1], _norm(parts[2]) or _norm(r["speaker_name"])
        else:
            continue
        if not name or LABEL_RX.match(name):
            continue
        new = f"name:{name}"[:200]
        speaker_name = r["speaker_name"]
        if r["state"] == "confirmed" and confirmed.get((cid, new), r["email"]) != r["email"]:
            if rid is None:
                deletes.append(r["id"])
                taken.discard((cid, key, r["email"]))
                continue
            new, speaker_name = f"rec:{rid}:{new}"[:200], ""
        if (cid, new, r["email"]) in taken:
            deletes.append(r["id"])
        else:
            updates.append((r["id"], new, speaker_name))
            taken.add((cid, new, r["email"]))
            if r["state"] == "confirmed" and not new.startswith("rec:"):
                confirmed[(cid, new)] = r["email"]
        taken.discard((cid, key, r["email"]))
    return updates, deletes


def upgrade() -> None:
    bind = op.get_bind()
    rows = [dict(r._mapping) for r in bind.execute(sa.text(
        "SELECT id, connector_id, speaker_key, speaker_name, email, state FROM speakr_speaker_people "
        "ORDER BY updated_at DESC, id DESC"))]
    updates, deletes = plan(rows)
    for i in deletes:
        bind.execute(sa.text("DELETE FROM speakr_speaker_people WHERE id = :i"), {"i": i})
    for i, key, name in updates:
        bind.execute(sa.text("UPDATE speakr_speaker_people SET speaker_key = :k, speaker_name = :n WHERE id = :i"),
                     {"k": key, "n": name, "i": i})


def downgrade() -> None:
    pass  # name keys are valid keys for the earlier code as well
