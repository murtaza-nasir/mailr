"""A production-shaped mailbox for the scale tests (test_scale_budgets.py).

One user gets SCALE_CONVERSATIONS conversations of two messages each, written with bulk
INSERT ... SELECT over generate_series, so the whole mailbox takes a few seconds. The mix
follows the production mailbox at about 1:4.5: a tenth in the Inbox, most of the rest archived
without a category, some Sent, Trash, Spam and user labels. Every 25th conversation's reply
has two attachments (a PDF and a photo), for the attachments page. A third of the mail carries a
List-Unsubscribe header and a sixth a List-Id (50 lists), for the subscriptions and cleanup pages,
which read the mail tally counted at the end.
One colleague (PERSON) is in "to" of every second message in Sent and in cc of a twentieth of the
mail, and a notification sender (ALERTS) writes a fiftieth of the conversations, for the person
page. The mail store is never called:
`FakeStore` stands in for the JMAP client and counts its calls.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event, text

# MAILR_SCALE_CONVERSATIONS=45000 builds the production size (90k messages) for measurements.
SCALE_CONVERSATIONS = int(os.environ.get("MAILR_SCALE_CONVERSATIONS") or 10_000)
SENDERS = 500
# A word in about 1 % of the subjects, and one sender among 500.
RARE_WORD = "quarterly"
SENDER = "sender42"
# A colleague who never writes first in the fixture but is in "to" of every reply in Sent and in
# cc of a twentieth of the mail: the person page finds her conversations through to/cc.
PERSON = "dana@northwind.example"
# A notification sender with a fiftieth of the conversations (900 at production size), none answered.
ALERTS = "alerts@ci.northwind.example"


@dataclass
class Scale:
    user_id: int
    labels: dict[str, int]
    user_labels: list[int]
    conversations: int
    messages: int
    seconds: float


async def build(db, user_id: int) -> Scale:
    """Fill the user's mailbox. `db` is an AsyncSession; it is committed."""
    t0 = time.perf_counter()
    labels: dict[str, int] = {}
    for role, name in (("inbox", "Inbox"), ("sent", "Sent"), ("drafts", "Drafts"), ("junk", "Spam"),
                       ("trash", "Trash"), ("archive", "Archive")):
        lid = (await db.execute(text("SELECT id FROM labels WHERE user_id=:u AND role=:r"),
                                {"u": user_id, "r": role})).scalar()
        if lid is None:
            lid = (await db.execute(text(
                "INSERT INTO labels (user_id, jmap_id, name, role, show_in_list, show_in_message_list, sort, "
                "created_at) VALUES (:u, :j, :n, :r, 'show', true, 0, now()) RETURNING id"),
                {"u": user_id, "j": f"mb-{role}", "n": name, "r": role})).scalar_one()
        labels[role] = lid
    user_labels = []
    for i in range(5):
        user_labels.append((await db.execute(text(
            "INSERT INTO labels (user_id, jmap_id, name, show_in_list, show_in_message_list, sort, created_at) "
            "VALUES (:u, :j, :n, 'show', true, 0, now()) RETURNING id"),
            {"u": user_id, "j": f"mb-user-{i}", "n": f"Project {i}"})).scalar_one())

    n = SCALE_CONVERSATIONS
    p = {"u": user_id, "n": n, "inbox": labels["inbox"], "archive": labels["archive"], "sent": labels["sent"],
         "trash": labels["trash"], "junk": labels["junk"], "ul": user_labels, "senders": SENDERS,
         "rare": RARE_WORD, "alerts": ALERTS, "person_to": json.dumps([{"email": PERSON, "name": "Dana Whitfield"}])}
    # g % 10 = 0 → Inbox (spread over the whole time range, as in a real inbox), 1 → Sent,
    # 2 → Trash, 3 → Spam, 4 → the user label "Project 4" (and Archive), the rest → Archive,
    # uncategorised.
    await db.execute(text("""
        INSERT INTO conversations (user_id, subject, norm_subject, snippet, last_date, first_date,
            message_count, unread_count, draft_count, has_attachments, starred, important, importance_score,
            category, label_ids, participants, muted, pinned, updated_at)
        SELECT :u,
               s.subj, lower(s.subj), 'Snippet ' || g,
               now() - (g * interval '4 hours'), now() - (g * interval '4 hours') - interval '1 hour',
               2, (g % 3 = 0)::int, 0, g % 25 = 0, g % 50 = 0, g % 7 = 0, (g % 7 = 0)::int::float,
               CASE WHEN g % 10 = 0 THEN (ARRAY['primary','updates','promotions'])[1 + g % 3] END,
               CASE g % 10 WHEN 0 THEN ARRAY[:inbox]::int[] WHEN 1 THEN ARRAY[:sent]::int[]
                           WHEN 2 THEN ARRAY[:trash]::int[] WHEN 3 THEN ARRAY[:junk]::int[]
                           WHEN 4 THEN ARRAY[:archive, (:ul ::int[])[1 + g % 5]]::int[]
                           ELSE ARRAY[:archive]::int[] END,
               CASE WHEN g % 50 = 5 THEN jsonb_build_array(jsonb_build_object('email', CAST(:alerts AS text), 'name', 'CI'))
                    ELSE jsonb_build_array(jsonb_build_object('email', 'sender' || (g % :senders) || '@domain' || (g % 40) || '.com',
                                                              'name', 'Sender ' || (g % :senders))) END,
               false, false, now()
        FROM generate_series(1, :n) g,
             LATERAL (SELECT CASE WHEN g % 97 = 0 THEN 'The ' || :rare || ' report ' || g
                                  ELSE 'Topic w' || (g % 2000) || ' about w' || (g % 311) END AS subj) s
    """), p)
    await db.execute(text("""
        INSERT INTO messages (user_id, conversation_id, jmap_id, jmap_thread_id, blob_id, upstream,
            message_id_hdr, "references", subject, norm_subject, from_email, from_name, "to", cc, bcc,
            reply_to, delivered_to, date, received_at, size, snippet, body_text, has_attachments,
            attachments, auth_results, seen, flagged, answered, draft, important, label_ids, deleted,
            category, importance_score, sent_by_me, local_only, intel_state, created_at, updated_at, tsv,
            list_id, list_unsubscribe, unsub_method)
        SELECT c.user_id, c.id, 'e' || c.id || '-' || k, 't' || c.id, 'b' || c.id || '-' || k, '{}'::jsonb,
               '<' || c.id || '.' || k || '@scale.test>', ARRAY[]::varchar[], c.subject, c.norm_subject,
               c.participants->0->>'email', c.participants->0->>'name',
               CASE WHEN c.label_ids @> ARRAY[:sent]::int[] AND k = 2 THEN CAST(:person_to AS jsonb)
                    ELSE '[{"email": "me@example.com", "name": "Me"}]'::jsonb END,
               CASE WHEN c.id % 20 = 5 THEN CAST(:person_to AS jsonb) ELSE '[]'::jsonb END, '[]'::jsonb, '[]'::jsonb,
               ARRAY[]::varchar[],
               c.last_date - (2 - k) * interval '30 minutes', c.last_date - (2 - k) * interval '30 minutes',
               4000 + (c.id % 50000), 'Snippet ' || c.id,
               b.body, c.has_attachments AND k = 2,
               CASE WHEN c.has_attachments AND k = 2 THEN jsonb_build_array(
                   jsonb_build_object('part', '2', 'filename', 'report-' || c.id || '.pdf', 'type', 'application/pdf',
                                      'size', 20000 + c.id % 5000),
                   jsonb_build_object('part', '3', 'filename', 'photo-' || c.id || '.jpg', 'type', 'image/jpeg',
                                      'size', 90000)) ELSE '[]'::jsonb END, '{}'::jsonb,
               NOT (c.unread_count > 0 AND k = 2), c.starred AND k = 2, false, false, c.important,
               c.label_ids, false, c.category, c.importance_score, c.label_ids @> ARRAY[:sent]::int[],
               false, 'done', now(), now(),
               to_tsvector('simple', c.subject || ' ' || b.body),
               CASE WHEN c.id % 6 = 0 THEN 'list' || (c.id % 50) || '.lists.example' END,
               CASE WHEN c.id % 3 = 0 THEN '<https://unsub.example/' || c.id || '>' END,
               CASE WHEN c.id % 3 = 0 THEN (ARRAY['one_click','mailto','link'])[1 + (c.id / 3) % 3] END
        FROM conversations c, generate_series(1, 2) k,
             LATERAL (SELECT repeat('Lorem ipsum dolor sit amet w' || (c.id % 1700) || ' consectetur adipiscing '
                                    || 'elit sed do eiusmod tempor w' || (c.id % 907) || '. ', 12) AS body) b
        WHERE c.user_id = :u
    """), p)
    # Inbox mail carries a 512-dimension embedding, as in production after the backfill (the
    # rest go without: each one is also an HNSW index insert, which would slow the fixture).
    await db.execute(text(
        "UPDATE messages SET embedding = (SELECT array_agg(random())::vector FROM generate_series(1, 512)) "
        "WHERE user_id = :u AND label_ids @> ARRAY[:inbox]::int[]"), p)
    await db.commit()
    await db.commit()
    # VACUUM as autovacuum would after a load this size: the visibility map lets the covering
    # indexes (ix_msg_groups, the cleanup and subscriptions pages) answer without the heap.
    from mailr.db import engine

    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM ANALYZE conversations"))
        await conn.execute(text("VACUUM ANALYZE messages"))
    # The mail tally (core/tally.py), as the worker's build job counts it after an upgrade.
    from mailr.core import tally

    await tally.rebuild(user_id)
    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM ANALYZE mail_tally"))
        await conn.execute(text("VACUUM ANALYZE mail_tally_units"))
    counts = (await db.execute(text(
        "SELECT (SELECT count(*) FROM conversations WHERE user_id=:u), "
        "(SELECT count(*) FROM messages WHERE user_id=:u)"), p)).one()
    return Scale(user_id, labels, user_labels, counts[0], counts[1], time.perf_counter() - t0)


@dataclass
class Counter:
    statements: list[str] = field(default_factory=list)
    params: list[Any] = field(default_factory=list)
    started: list[float] = field(default_factory=list)
    durations: list[tuple[float, str]] = field(default_factory=list)
    jmap: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def sql(self) -> int:
        return len(self.statements)


@contextmanager
def counting():
    """Count the SQL statements the app sends (every engine connection) and the time taken."""
    from mailr.db import engine

    c = Counter()

    def on_exec(conn, cursor, statement, parameters, context, executemany):
        c.statements.append(statement)
        c.params.append(parameters)
        c.started.append(time.perf_counter())

    def on_done(conn, cursor, statement, parameters, context, executemany):
        c.durations.append((time.perf_counter() - c.started[-1], statement))

    eng = engine().sync_engine
    event.listen(eng, "before_cursor_execute", on_exec)
    event.listen(eng, "after_cursor_execute", on_done)
    t0 = time.perf_counter()
    try:
        yield c
    finally:
        c.seconds = time.perf_counter() - t0
        event.remove(eng, "before_cursor_execute", on_exec)
        event.remove(eng, "after_cursor_execute", on_done)


class FakeStore:
    """Stands in for the JMAP client of mailr.core.store (`_Scoped.one`): Email/set updates
    succeed, and every call is recorded on the active Counter."""

    def __init__(self):
        self.counter: Counter | None = None

    def install(self, monkeypatch) -> None:
        from mailr.core import store

        fake = self

        async def one(scoped, method: str, args: dict[str, Any], using=None) -> dict:
            if fake.counter is not None:
                fake.counter.jmap.append(method)
            if method == "Email/set":
                return {"updated": {k: None for k in (args.get("update") or {})},
                        "destroyed": list(args.get("destroy") or [])}
            if method == "Mailbox/get":
                return {"list": []}
            return {}

        monkeypatch.setattr(store._Scoped, "one", one)
