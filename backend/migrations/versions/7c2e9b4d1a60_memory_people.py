"""memory and people: "About you", contacts linked to people, topics, four kinds, the learning cursor

- kb_entities.is_self: the user's own entity (notes under "About you"); at most one per user
  (uq_kb_entity_self). A person entity that carries one of the user's own addresses becomes it;
  a second such entity is merged into it (its notes move first, nothing is lost).
- kb_entities.person_id: the contact (people row) a person entity is, matched by address, the most
  written-to first. A person whose addresses match no contact gets one (name from the entity);
  a person without an address stays a contact without mail.
- Notes that say "The user …" are rewritten to the user: "You …" (their vectors are cleared and
  a kb.embed job is queued).
- Kinds: relationship and status become fact, habit becomes routine; thread entities become topics.
- The learning cursor (assistant_profiles.state.extract_since) goes back to the newest message a
  kept note came from, so mail read by passes that proposed nothing is read again (30 Sep–4 Oct
  2026 on the first install). Recorded in state.cursor_reset; done once per user.

Never deletes a note. Safe to run again: columns and the index are added only when missing, and
each data step changes only rows still in the old shape.

Revision ID: 7c2e9b4d1a60
Revises: b8e1f4a7c302
Create Date: 2026-10-07 23:30:00

"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op
from sqlalchemy import text

revision: str = "7c2e9b4d1a60"
down_revision: str | Sequence[str] | None = "b8e1f4a7c302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESET_KEY = "cursor_reset"
RESET_WHY = "passes that proposed nothing advanced the cursor (migration 7c2e9b4d1a60)"


def upgrade() -> None:
    op.execute("ALTER TABLE kb_entities ADD COLUMN IF NOT EXISTS is_self BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE kb_entities ADD COLUMN IF NOT EXISTS person_id INTEGER "
               "REFERENCES people(id) ON DELETE SET NULL")
    reshape(op.get_bind())
    with op.get_context().autocommit_block():
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                       WHERE c.relname = 'uq_kb_entity_self' AND NOT i.indisvalid) THEN
                DROP INDEX uq_kb_entity_self;
            END IF; END $$""")
        op.execute("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_kb_entity_self ON kb_entities (user_id) "
                   "WHERE is_self")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_kb_entity_self")
    op.execute("ALTER TABLE kb_entities DROP COLUMN IF EXISTS person_id")
    op.execute("ALTER TABLE kb_entities DROP COLUMN IF EXISTS is_self")


# ---------------------------------------------------------------- the data steps (tests call reshape)


def reshape(conn, *, now: datetime | None = None) -> dict[str, int]:
    """Every data step, in order, on a sync connection. Returns counts (for the test and the log)."""
    now = now or datetime.now(UTC)
    out = {"kinds": 0, "topics": 0, "self": 0, "merged": 0, "rewritten": 0, "linked": 0, "contacts": 0,
           "cursors": 0}
    out["kinds"] += conn.execute(text(
        "UPDATE kb_memories SET kind = 'fact' WHERE kind IN ('relationship', 'status')")).rowcount
    out["kinds"] += conn.execute(text("UPDATE kb_memories SET kind = 'routine' WHERE kind = 'habit'")).rowcount
    out["topics"] = conn.execute(text("UPDATE kb_entities SET kind = 'topic' WHERE kind = 'thread'")).rowcount
    users = [r[0] for r in conn.execute(text("SELECT DISTINCT user_id FROM kb_entities "
                                             "UNION SELECT DISTINCT user_id FROM kb_memories "
                                             "UNION SELECT user_id FROM assistant_profiles"))]
    touched: set[int] = set()
    for uid in users:
        own = _own_addresses(conn, uid)
        s, m = _self_entity(conn, uid, own)
        out["self"] += s
        out["merged"] += m
        n = _rewrite(conn, uid)
        out["rewritten"] += n
        if n or m:
            touched.add(uid)
        linked, made = _link_contacts(conn, uid)
        out["linked"] += linked
        out["contacts"] += made
        out["cursors"] += _reset_cursor(conn, uid, now)
    for uid in touched:
        conn.execute(text(
            "INSERT INTO jobs (kind, user_id, payload, dedupe_key, run_at, status, attempts, max_attempts, error, "
            "created_at) VALUES ('kb.embed', :u, CAST(:p AS jsonb), :k, now(), 'queued', 0, 5, '', now()) "
            "ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'running') DO NOTHING"),
            {"u": uid, "p": json.dumps({"user_id": uid}), "k": f"kb.embed:{uid}"})
    return out


def _own_addresses(conn, uid: int) -> list[str]:
    rows = conn.execute(text("SELECT lower(email) FROM users WHERE id = :u AND email <> '' "
                             "UNION SELECT lower(email) FROM identities WHERE user_id = :u AND email <> ''"),
                        {"u": uid})
    return sorted({r[0] for r in rows if r[0]})


def _self_entity(conn, uid: int, own: list[str]) -> tuple[int, int]:
    """(1 if an entity became the user's own, entities merged into it)."""
    cur = conn.execute(text("SELECT id FROM kb_entities WHERE user_id = :u AND is_self ORDER BY id"), {"u": uid}).all()
    cands = conn.execute(text(
        "SELECT e.id FROM kb_entities e WHERE e.user_id = :u AND NOT e.is_self AND e.kind = 'person' "
        "AND e.emails && CAST(:own AS varchar[]) "
        "ORDER BY (SELECT count(*) FROM kb_memories m WHERE m.entity_id = e.id) DESC, e.id"),
        {"u": uid, "own": own}).all() if own else []
    ids = [r[0] for r in cur] + [r[0] for r in cands]
    if not ids:
        return 0, 0
    keep, rest = ids[0], ids[1:]
    made = 0
    if not cur:
        conn.execute(text("UPDATE kb_entities SET is_self = true, kind = 'person', person_id = NULL WHERE id = :e"),
                     {"e": keep})
        made = 1
    for other in rest:
        # Its notes move to the user's own entity first; then the duplicate goes (nothing is lost).
        conn.execute(text("UPDATE kb_memories SET entity_id = :k WHERE entity_id = :o"), {"k": keep, "o": other})
        conn.execute(text(
            "UPDATE kb_entities k SET "
            "aliases = ARRAY(SELECT DISTINCT x FROM unnest(k.aliases || o.aliases || ARRAY[o.name]) x "
            "               WHERE lower(x) <> lower(k.name)), "
            "emails = ARRAY(SELECT DISTINCT x FROM unnest(k.emails || o.emails) x), "
            "summary = CASE WHEN k.summary = '' THEN o.summary ELSE k.summary END "
            "FROM kb_entities o WHERE k.id = :k AND o.id = :o"), {"k": keep, "o": other})
        conn.execute(text("DELETE FROM kb_entities WHERE id = :o AND NOT EXISTS "
                          "(SELECT 1 FROM kb_memories WHERE entity_id = :o)"), {"o": other})
    return made, len(rest)


_IRREGULAR = {"is": "are", "was": "were", "has": "have", "does": "do", "goes": "go", "isn't": "aren't",
              "wasn't": "weren't", "hasn't": "haven't", "doesn't": "don't"}
_ADVERBS = ("also", "often", "usually", "always", "never", "sometimes", "still", "now", "currently", "already",
            "regularly", "mostly", "rarely", "only", "generally")
_MODALS = ("can", "will", "would", "should", "may", "might", "must", "could", "had", "did", "cannot")
_CLAUSE = re.compile(r"(?:^|[.;:!?(]\s*|,\s*|\b(?:and|but|or|that|because|when|while|where|so|if|since|as|who)\s+)$",
                     re.I)


def _base_verb(w: str) -> str:
    low = w.lower()
    if low in _IRREGULAR:
        return _IRREGULAR[low]
    if re.fullmatch(r"[a-z]+ies", low) and len(low) > 4:
        return w[:-3] + "y"
    if re.fullmatch(r"[a-z]+(ches|shes|sses|xes|zzes|oes)", low):
        return w[:-2]
    if re.fullmatch(r"[a-z]+[^s]s", low) and len(low) > 3:
        return w[:-1]
    return w


def _looks_verb(w: str) -> bool:
    low = w.lower()
    return low in _IRREGULAR or low in _MODALS or (low.endswith("s") and not low.endswith("ss") and len(low) > 2)


def to_second_person(t: str) -> str:
    """A frozen copy of knowledge/memory.to_second_person (a migration must not change later)."""
    if not t or not re.search(r"\bthe user\b", t, re.I):
        return t
    out = re.sub(r"\bthe user(?:'s|’s)", lambda m: "Your" if m.group(0)[0] == "T" else "your", t, flags=re.I)

    def subject(m: re.Match) -> str:
        you = "You" if (m.group(0)[0] == "T" or m.start() == 0) else "you"
        words = m.group(1).split()
        if not _CLAUSE.search(out[: m.start()]):
            return f"{you} {m.group(1)}"
        for i, w in enumerate(words):
            if w.lower() in _ADVERBS:
                continue
            if _looks_verb(w):
                words[i] = _base_verb(w)
            break
        return f"{you} {' '.join(words)}"

    out = re.sub(r"\bthe user\s+((?:[a-z]+\s+)?[a-z']+)", subject, out, flags=re.I)
    return re.sub(r"\bthe user\b", lambda m: "You" if m.group(0)[0] == "T" else "you", out, flags=re.I)


def _rewrite(conn, uid: int) -> int:
    rows = conn.execute(text("SELECT id, text FROM kb_memories WHERE user_id = :u AND text ~* '\\mthe user\\M'"),
                        {"u": uid}).all()
    n = 0
    for mid, t in rows:
        new = to_second_person(t)
        if new != t:
            conn.execute(text("UPDATE kb_memories SET text = :t, embedding = NULL, embed_model = NULL WHERE id = :i"),
                         {"t": new, "i": mid})
            n += 1
    return n


def _link_contacts(conn, uid: int) -> tuple[int, int]:
    """(people linked, contacts made). Only persons not linked yet are looked at."""
    rows = conn.execute(text(
        "SELECT id, name, emails FROM kb_entities WHERE user_id = :u AND kind = 'person' AND NOT is_self "
        "AND person_id IS NULL AND cardinality(emails) > 0 ORDER BY id"), {"u": uid}).all()
    linked = made = 0
    for eid, name, emails in rows:
        emails = [e.lower() for e in emails or [] if e and "@" in e]
        if not emails:
            continue
        pid = conn.execute(text(
            "SELECT id FROM people WHERE user_id = :u AND email = ANY(CAST(:e AS varchar[])) "
            "ORDER BY sent_count * 5 + received_count DESC, id LIMIT 1"), {"u": uid, "e": emails}).scalar()
        if pid is None:
            pid = conn.execute(text(
                "INSERT INTO people (user_id, email, name, organization, received_count, sent_count, notes, blocked, "
                "strength, title, phones, addresses, birthday, provenance) VALUES (:u, :e, :n, '', 0, 0, '', false, "
                "0, '', '[]', '[]', '', '{}') ON CONFLICT (user_id, email) DO NOTHING RETURNING id"),
                {"u": uid, "e": emails[0], "n": (name or "")[:300]}).scalar()
            if pid is None:
                pid = conn.execute(text("SELECT id FROM people WHERE user_id = :u AND email = :e"),
                                   {"u": uid, "e": emails[0]}).scalar()
            else:
                made += 1
        conn.execute(text("UPDATE kb_entities SET person_id = :p WHERE id = :e"), {"p": pid, "e": eid})
        linked += 1
    return linked, made


def _reset_cursor(conn, uid: int, now: datetime) -> int:
    row = conn.execute(text("SELECT state FROM assistant_profiles WHERE user_id = :u"), {"u": uid}).first()
    if row is None:
        return 0
    state = dict(row[0] or {})
    if state.get(RESET_KEY) or not state.get("extract_since"):
        return 0
    old = state["extract_since"]
    newest = conn.execute(text(
        "SELECT max(msg.date) FROM kb_memories m "
        "CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(m.refs) = 'array' THEN m.refs ELSE '[]' END) r "
        "JOIN messages msg ON msg.id = (r->>'message_id')::bigint AND msg.user_id = m.user_id "
        "WHERE m.user_id = :u AND m.source = 'extracted' AND (r->>'message_id') ~ '^[0-9]+$'"), {"u": uid}).scalar()
    if newest is None:
        to = None
    else:
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=UTC)
        cur = datetime.fromisoformat(old)
        cur = cur if cur.tzinfo else cur.replace(tzinfo=UTC)
        to = newest.astimezone(UTC).isoformat() if newest < cur else old
    if to is None:
        state.pop("extract_since", None)  # no pass ever proposed a kept note: the next pass reads the latest mail
    else:
        state["extract_since"] = to
    state[RESET_KEY] = {"at": now.isoformat(), "from": old, "to": to, "why": RESET_WHY}
    conn.execute(text("UPDATE assistant_profiles SET state = CAST(:s AS jsonb) WHERE user_id = :u"),
                 {"s": json.dumps(state), "u": uid})
    return 1
