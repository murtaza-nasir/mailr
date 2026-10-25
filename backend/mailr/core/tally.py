"""The mail tally: the grouped mail of the Subscriptions and Clean up pages (core/mailgroups.py),
counted once and kept up to date, so a page reads a few thousand rows instead of grouping every
message of the mailbox on each request.

What it counts. The mail those pages group: the user's messages that are not deleted, not their
own (sent_by_me) and not in Spam. A row of mail_tally holds the messages of one sender
(from_email) and list (list_id) that share
  - the category;
  - the place: in the Inbox, in the Trash (by their labels);
  - the age band: BANDS days before the user's anchor date (mail_tally_users.anchor); 7 for no date;
  - List-Unsubscribe (unsub_method set);
  - the data class (local_only);
  - the conversation's protection: starred, important, the user wrote in it,
with their number, size, unread count, oldest and newest date, newest read date and newest
message. A conversation's share of a row is a unit (mail_tally_units): `units` counts the
conversations with mail in the row, `solo` those with mail in that row only.

Conversations. The pages count distinct conversations. One with mail in a single row is counted
by summing `solo`; the few with mail in several rows (`spread`) are counted from their units,
distinct. Both are exact for any choice of rows.

Age. The bands end at fixed dates (anchor - BANDS); a page's cutoff (now - N days) is later than
that by the time since the anchor. The messages in between (about a day of mail, as the nightly
check moves the anchor) are read from the messages themselves (ix_msg_user_date).

Upkeep. A change that can move a message to another row marks its conversation: the session
events below see ORM changes and ORM UPDATE and DELETE statements on messages and conversations;
code that changes them with raw SQL calls touch(). Just before the transaction commits, `apply`
locks the marked conversations (mail_tally_convs, in order), counts their messages again and
writes the difference: in the same transaction, idempotent, and with the locks held only until
that commit. Users whose tally is stale are skipped.

Jobs. mail.tally_build counts a user's mail a batch at a time (after the upgrade, or when the
tally was marked stale); mail.tally_check moves the anchor forward a day at a time and compares
the tally with a recount, once a day per user (core/tasks.py SPREAD). `mailr tally rebuild`
counts again from scratch.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import event, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

log = logging.getLogger("mailr.tally")

# The age bands, in days: the cutoffs of the Clean up page (30 days to 5 years) and the
# Subscriptions page's 30 and 90 days.
BANDS = (30, 90, 180, 365, 730, 1825)
NO_DATE = 7
INBOX, TRASH = 1, 2
STARRED, IMPORTANT, REPLIED = 1, 2, 4
# pg_advisory_xact_lock(LOCK, user id): shared for the upkeep, exclusive while the anchor moves or
# the tally is emptied.
LOCK = 727300
# More conversations than this in one transaction: the user's tally is counted again by a job.
MAX_MARKS = 20_000
BATCH = 2000
# The anchor never passes this long ago, so a server whose clock is slightly behind the worker's
# still finds it in the past.
ANCHOR_MARGIN = timedelta(minutes=5)
_KEY = "mailr.tally"
# Tests: an upkeep error fails the commit (otherwise the user's tally is marked stale and logged).
STRICT = False

_MSG_COLS = ("user_id", "conversation_id", "label_ids", "seen", "category", "unsub_method", "date", "size",
             "local_only", "deleted", "sent_by_me", "from_email", "list_id")
_CONV_COLS = ("starred", "important")


# ------------------------------------------------------------------ marks


def _marks(info: dict) -> dict[str, set[int]]:
    return info.setdefault(_KEY, {"c": set(), "m": set(), "stale": set()})


def touch(db, conversation_ids=(), message_ids=()) -> None:
    """Mark conversations (and messages, by id) that raw SQL changed; the tally counts them again
    when the transaction commits."""
    mk = _marks(db.info)
    mk["c"].update(int(c) for c in conversation_ids if c)
    mk["m"].update(int(m) for m in message_ids if m)


def invalidate(db, user_id: int) -> None:
    """The user's tally is counted again from scratch by a job (after a change too large to count
    per conversation); the pages count live meanwhile."""
    _marks(db.info)["stale"].add(int(user_id))


def _history(st, name: str) -> list:
    h = st.attrs[name].history
    return list(h.added or ()) + list(h.deleted or ()) + list(h.unchanged or ())


def _after_flush(session: Session, _ctx) -> None:
    from mailr.models import Conversation, Message

    mk = None
    for obj in session.new:
        if isinstance(obj, Message):
            mk = mk or _marks(session.info)
            st = sa_inspect(obj)
            if st.dict.get("conversation_id"):
                mk["c"].add(int(st.dict["conversation_id"]))
            if st.dict.get("id"):
                mk["m"].add(int(st.dict["id"]))
    for obj in session.dirty:
        if isinstance(obj, Message):
            st = sa_inspect(obj)
            if not any(st.attrs[a].history.has_changes() for a in _MSG_COLS):
                continue
            mk = mk or _marks(session.info)
            mk["c"].update(int(c) for c in _history(st, "conversation_id") if c)
            if st.identity:
                mk["m"].add(int(st.identity[0]))
        elif isinstance(obj, Conversation):
            st = sa_inspect(obj)
            if st.identity and any(st.attrs[a].history.has_changes() for a in _CONV_COLS):
                mk = mk or _marks(session.info)
                mk["c"].add(int(st.identity[0]))
    for obj in session.deleted:
        if isinstance(obj, Message | Conversation):
            st = sa_inspect(obj)
            mk = mk or _marks(session.info)
            if isinstance(obj, Message):
                mk["c"].update(int(c) for c in _history(st, "conversation_id") if c)
                if st.identity:
                    mk["m"].add(int(st.identity[0]))
            elif st.identity:
                mk["c"].add(int(st.identity[0]))


def _set_keys(stmt, params) -> tuple[set[str], dict[str, Any]]:
    """The columns an ORM UPDATE sets, and their values where they are plain."""
    keys: set[str] = set()
    vals: dict[str, Any] = {}
    pairs = list((getattr(stmt, "_values", None) or {}).items()) + list(getattr(stmt, "_ordered_values", None) or ())
    for k, v in pairs:
        name = getattr(k, "key", None) or getattr(k, "name", None) or str(k)
        keys.add(name)
        vals[name] = getattr(v, "value", v)
    if isinstance(params, dict):
        keys |= set(params)
    elif isinstance(params, list | tuple) and params and isinstance(params[0], dict):
        keys |= set(params[0])
    return keys, vals


def _orm_execute(state) -> None:
    """ORM UPDATE and DELETE statements on messages and conversations: the rows they are about to
    change are looked up first and marked (one SELECT, only for the columns the tally counts)."""
    if not (state.is_update or state.is_delete):
        return
    from sqlalchemy import select

    from mailr.models import Conversation, Message

    mapper = state.bind_mapper
    cls = getattr(mapper, "class_", None)
    if cls not in (Message, Conversation):
        return
    stmt, params = state.statement, state.parameters
    keys, vals = _set_keys(stmt, params) if state.is_update else (set(), {})
    if state.is_update and not keys & set(_MSG_COLS if cls is Message else _CONV_COLS):
        return
    where = getattr(stmt, "whereclause", None)
    ids = None
    if where is None:
        rows = params if isinstance(params, list | tuple) else [params or {}]
        ids = [r["id"] for r in rows if isinstance(r, dict) and r.get("id") is not None]
        if not ids:
            return
    mk = _marks(state.session.info)
    if cls is Message:
        q = select(Message.id, Message.conversation_id)
        q = q.where(Message.id.in_(ids) if ids is not None else where)  # pyright: ignore[reportArgumentType]
        for mid, cid in state.session.execute(q).all():
            mk["m"].add(int(mid))
            if cid:
                mk["c"].add(int(cid))
        if isinstance(vals.get("conversation_id"), int):
            mk["c"].add(vals["conversation_id"])
    else:
        q = select(Conversation.id)
        q = q.where(Conversation.id.in_(ids) if ids is not None else where)  # pyright: ignore[reportArgumentType]
        mk["c"].update(int(c) for c in state.session.execute(q).scalars())


def _before_commit(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        session.flush()
    mk = session.info.pop(_KEY, None)
    if not mk or not (mk["c"] or mk["m"] or mk["stale"]):
        return
    conn = session.connection()
    try:
        with conn.begin_nested():
            stale = set(mk["stale"])
            if len(mk["c"]) + len(mk["m"]) > MAX_MARKS:
                stale |= _users_of(conn, sorted(mk["c"]), sorted(mk["m"]))
            else:
                apply(conn, sorted(mk["c"]), sorted(mk["m"]))
            if stale:
                _mark_stale(conn, sorted(stale))
    except Exception:
        if STRICT:
            raise
        log.exception("mail tally upkeep failed; the user's tally is counted again")
        try:
            with conn.begin_nested():
                _mark_stale(conn, sorted(_users_of(conn, sorted(mk["c"]), sorted(mk["m"])) | mk["stale"]))
        except Exception:  # the nightly check finds it
            log.exception("could not mark the mail tally stale")


def _after_transaction_end(session: Session, transaction) -> None:
    if transaction.parent is None:
        session.info.pop(_KEY, None)


_installed = False


def install() -> None:
    """Register the session events (once per process; mailr.db.engine calls it)."""
    global _installed
    if _installed:
        return
    _installed = True
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "do_orm_execute", _orm_execute)
    event.listen(Session, "before_commit", _before_commit)
    event.listen(Session, "after_transaction_end", _after_transaction_end)


# ------------------------------------------------------------------ counting


def _users_of(conn, convs: list[int], mids: list[int]) -> set[int]:
    return set(conn.execute(text(
        "SELECT user_id FROM conversations WHERE id = ANY(:c) UNION SELECT user_id FROM messages WHERE id = ANY(:m)"),
        {"c": convs, "m": mids}).scalars())


def _mark_stale(conn, users: list[int]) -> None:
    if users:
        conn.execute(text("UPDATE mail_tally_users SET state = 'stale' WHERE user_id = ANY(:u)"), {"u": users})


# The keys (conversation ids, minus the id of a message without one) of these marks, for the users
# with a tally that is not stale, locked: each user's shared advisory lock, then the keys' rows in
# mail_tally_convs (made when missing), in (user, key) order. ON CONFLICT DO UPDATE waits for a
# transaction that is making or holding the same row, so two changes to one conversation are
# counted one after the other.
_LOCK_KEYS = """
WITH k(conv, user_id) AS ({keys}),
s AS (
  SELECT conv, user_id, pg_advisory_xact_lock_shared(:cls, user_id) AS locked
  FROM (SELECT k.conv, k.user_id FROM k {where} ORDER BY k.user_id, k.conv OFFSET 0) x
)
INSERT INTO mail_tally_convs (conv, user_id) SELECT conv, user_id FROM s
ON CONFLICT (conv) DO UPDATE SET user_id = excluded.user_id
RETURNING conv, user_id
"""
_MARKED = _LOCK_KEYS.format(keys="""
  SELECT id, user_id FROM conversations WHERE id = ANY(:c)
  UNION SELECT coalesce(conversation_id, -id), user_id FROM messages WHERE id = ANY(:m)
  UNION SELECT conv, user_id FROM mail_tally_convs WHERE conv = ANY(:c) OR conv = ANY(:nm)""",
                            where="JOIN mail_tally_users t ON t.user_id = k.user_id AND t.state <> 'stale'")
_KNOWN = _LOCK_KEYS.format(keys="SELECT * FROM unnest(CAST(:k AS bigint[]), CAST(:u AS int[]))", where="")

_BAND = " + ".join(f"(m.date < r.anchor - interval '{d * 24} hours')::int" for d in BANDS)

# The units of the locked keys as their messages make them now ('n'), and as stored ('o', with
# their row's extremes).
_UNITS = f"""
WITH r AS (
  SELECT u AS user_id,
         coalesce((SELECT array_agg(id) FROM labels WHERE user_id = u AND role = 'inbox'), '{{}}') AS inbox,
         coalesce((SELECT array_agg(id) FROM labels WHERE user_id = u AND role = 'trash'), '{{}}') AS trash,
         coalesce((SELECT array_agg(id) FROM labels WHERE user_id = u AND role = 'junk'), '{{}}') AS junk,
         (SELECT anchor FROM mail_tally_users WHERE user_id = u) AS anchor
  FROM unnest(CAST(:users AS int[])) AS u
), p AS (
  SELECT c.id AS conv,
         (CASE WHEN c.starred THEN 1 ELSE 0 END) | (CASE WHEN c.important THEN 2 ELSE 0 END)
         | (CASE WHEN EXISTS (SELECT 1 FROM messages s WHERE s.user_id = c.user_id AND s.conversation_id = c.id
                              AND s.sent_by_me IS true AND s.deleted IS false) THEN 4 ELSE 0 END) AS prot
  FROM conversations c WHERE c.id = ANY(:pos)
), m AS (
  SELECT coalesce(m.conversation_id, -m.id) AS conv, m.user_id, m.from_email, m.list_id, m.category,
         ((m.label_ids && r.inbox)::int | ((m.label_ids && r.trash)::int << 1)) AS loc,
         CASE WHEN m.date IS NULL THEN {NO_DATE} ELSE {_BAND} END AS band,
         m.unsub_method IS NOT NULL AS unsub, m.local_only, coalesce(p.prot, 0) AS prot,
         m.size, m.seen, m.date, m.id
  FROM messages m JOIN r ON r.user_id = m.user_id LEFT JOIN p ON p.conv = m.conversation_id
  WHERE m.user_id = ANY(:users) AND (m.conversation_id = ANY(:pos) OR (m.id = ANY(:neg) AND m.conversation_id IS NULL))
    AND m.deleted IS false AND m.sent_by_me IS false AND NOT (m.label_ids && r.junk)
)
SELECT 'n' AS side, conv, NULL::bigint AS cell_id, user_id, from_email, list_id, category, loc, band, unsub,
       local_only, prot, count(*) AS messages, sum(size) AS size, count(*) FILTER (WHERE NOT seen) AS unread,
       min(date) AS oldest, max(date) AS newest, max(date) FILTER (WHERE seen) AS newest_read,
       (array_agg(id ORDER BY date DESC NULLS LAST, id DESC))[1] AS newest_id, NULL::boolean AS spread,
       NULL::timestamptz AS c_oldest, NULL::timestamptz AS c_newest, NULL::timestamptz AS c_newest_read,
       NULL::bigint AS c_newest_id
FROM m GROUP BY conv, user_id, from_email, list_id, category, loc, band, unsub, local_only, prot
UNION ALL
SELECT 'o', u.conv, u.cell_id, t.user_id, t.from_email, t.list_id, t.category, t.loc, t.band, t.unsub, t.local_only,
       t.prot, u.messages, u.size, u.unread, u.oldest, u.newest, u.newest_read, u.newest_id, u.spread,
       t.oldest, t.newest, t.newest_read, t.newest_id
FROM mail_tally_units u JOIN mail_tally t ON t.id = u.cell_id WHERE u.conv = ANY(:keys)
"""

_DIMS = ("user_id", "from_email", "list_id", "category", "loc", "band", "unsub", "local_only", "prot")
_DIM_TYPES = ("int", "varchar", "varchar", "varchar", "smallint", "smallint", "boolean", "boolean", "smallint")
_MEAS = ("messages", "size", "unread", "oldest", "newest", "newest_read", "newest_id")
_SAME = " AND ".join(f"c.{d} IS NOT DISTINCT FROM n.{d}" if d in ("list_id", "category") else f"c.{d} = n.{d}"
                     for d in _DIMS)


def _unnest(cols: list[tuple[str, str]]) -> str:
    return "unnest(" + ", ".join(f"CAST(:{n} AS {t}[])" for n, t in cols) + ") AS x(" + \
        ", ".join(n for n, _t in cols) + ")"


_D_COLS: list[tuple[str, str]] = [*zip(_DIMS, _DIM_TYPES, strict=True),
    ("messages", "int"), ("size", "bigint"), ("unread", "int"), ("units", "int"), ("solo", "int"),
    ("oldest", "timestamptz"), ("newest", "timestamptz"), ("newest_read", "timestamptz"), ("newest_id", "bigint"),
    ("ord", "int")]
_N_COLS: list[tuple[str, str]] = [("n_conv", "bigint"), *((f"n_{d}", t) for d, t in zip(_DIMS, _DIM_TYPES, strict=True)),
    ("n_messages", "int"), ("n_size", "bigint"), ("n_unread", "int"), ("n_oldest", "timestamptz"),
    ("n_newest", "timestamptz"), ("n_newest_read", "timestamptz"), ("n_newest_id", "bigint"), ("n_spread", "boolean")]

# The difference, in one statement: the rows (added to, in a fixed order), the new or changed
# units (on the rows' ids), and the units that are gone.
_WRITE = f"""
WITH d AS (SELECT * FROM {_unnest(_D_COLS)}),
c AS (
  INSERT INTO mail_tally AS t ({", ".join(_DIMS)}, messages, size, unread, units, solo, oldest, newest,
                               newest_read, newest_id)
  SELECT {", ".join(_DIMS)}, messages, size, unread, units, solo, oldest, newest, newest_read, newest_id
  FROM d ORDER BY ord
  ON CONFLICT ({", ".join(_DIMS)}) DO UPDATE SET
    messages = t.messages + excluded.messages, size = t.size + excluded.size, unread = t.unread + excluded.unread,
    units = t.units + excluded.units, solo = t.solo + excluded.solo,
    oldest = LEAST(t.oldest, excluded.oldest), newest = GREATEST(t.newest, excluded.newest),
    newest_read = GREATEST(t.newest_read, excluded.newest_read),
    newest_id = CASE WHEN excluded.newest_id IS NOT NULL AND (t.newest_id IS NULL OR
                  ROW(excluded.newest IS NOT NULL, coalesce(excluded.newest, '-infinity'), excluded.newest_id) >
                  ROW(t.newest IS NOT NULL, coalesce(t.newest, '-infinity'), t.newest_id))
                THEN excluded.newest_id ELSE t.newest_id END
  RETURNING id, {", ".join(_DIMS)}
),
n0 AS (SELECT * FROM {_unnest(_N_COLS)}),
n AS (SELECT n_conv AS conv, {", ".join(f"n_{d} AS {d}" for d in _DIMS)}, n_messages, n_size, n_unread, n_oldest,
             n_newest, n_newest_read, n_newest_id, n_spread FROM n0),
up AS (
  INSERT INTO mail_tally_units (conv, cell_id, user_id, messages, size, unread, oldest, newest, newest_read,
                                newest_id, spread)
  SELECT n.conv, c.id, n.user_id, n.n_messages, n.n_size, n.n_unread, n.n_oldest, n.n_newest, n.n_newest_read,
         n.n_newest_id, n.n_spread
  FROM n JOIN c ON {_SAME}
  ON CONFLICT (conv, cell_id) DO UPDATE SET messages = excluded.messages, size = excluded.size,
    unread = excluded.unread, oldest = excluded.oldest, newest = excluded.newest,
    newest_read = excluded.newest_read, newest_id = excluded.newest_id, spread = excluded.spread
),
del AS (
  DELETE FROM mail_tally_units WHERE (conv, cell_id) IN
    (SELECT * FROM unnest(CAST(:del_conv AS bigint[]), CAST(:del_cell AS bigint[])))
)
SELECT count(*) FROM c
"""

# Rows left without units go; rows that lost a unit holding one of their extremes get them again.
_TIDY = """
WITH gone AS (DELETE FROM mail_tally WHERE id = ANY(:touched) AND units <= 0 RETURNING id)
UPDATE mail_tally t SET oldest = s.oldest, newest = s.newest, newest_read = s.newest_read, newest_id = s.newest_id
FROM (SELECT cell_id, min(oldest) AS oldest, max(newest) AS newest, max(newest_read) AS newest_read,
             (array_agg(newest_id ORDER BY newest DESC NULLS LAST, newest_id DESC))[1] AS newest_id
      FROM mail_tally_units WHERE cell_id = ANY(:re) GROUP BY cell_id) s
WHERE t.id = s.cell_id
"""


def apply(conn, convs: list[int], mids: list[int]) -> dict[str, Any]:
    """Count these conversations (and messages, by id) again and write the difference to the tally.
    `conn` is the transaction's connection (a sync Connection). Returns {keys, changed, senders}:
    the conversations counted, those whose share changed, and the (from_email, list_id) of the
    rows that changed."""
    if not (convs or mids):
        return {"keys": 0, "changed": 0, "senders": set()}
    rows = conn.execute(text(_MARKED), {"c": convs, "m": mids, "nm": [-m for m in mids], "cls": LOCK}).all()
    return _recount(conn, {int(k): int(u) for k, u in rows})


def recount(conn, owner: dict[int, int]) -> dict[str, Any]:
    """`apply` for keys whose users are known ({key: user id}), whatever the state of their tally
    (the build and the nightly check)."""
    if not owner:
        return {"keys": 0, "changed": 0, "senders": set()}
    keys = sorted(owner)
    rows = conn.execute(text(_KNOWN), {"k": keys, "u": [owner[k] for k in keys], "cls": LOCK}).all()
    return _recount(conn, {int(k): int(u) for k, u in rows})


def _recount(conn, owner: dict[int, int]) -> dict[str, Any]:
    """The keys are locked: count their messages and write the difference."""
    out: dict[str, Any] = {"keys": 0, "changed": 0, "senders": set()}
    if not owner:
        return out
    users = sorted(set(owner.values()))
    keys = sorted(owner)
    pos = [k for k in keys if k > 0]
    neg = [-k for k in keys if k < 0]
    got = conn.execute(text(_UNITS), {"users": users, "pos": pos, "neg": neg, "keys": keys}).mappings().all()
    out["keys"] = len(keys)
    new: dict[tuple, dict] = {}
    old: dict[tuple, dict] = {}
    n_new: dict[int, int] = {}
    for r in got:
        dims = tuple(r[d] for d in _DIMS)
        if r["side"] == "n":
            new[(r["conv"], dims)] = dict(r)
            n_new[r["conv"]] = n_new.get(r["conv"], 0) + 1
        else:
            old[(r["conv"], dims)] = dict(r)
    delta: dict[tuple, dict[str, Any]] = {}

    def cell(dims: tuple) -> dict[str, Any]:
        return delta.setdefault(dims, {"messages": 0, "size": 0, "unread": 0, "units": 0, "solo": 0,
                                       "oldest": None, "newest": None, "newest_read": None, "newest_id": None,
                                       "newest_key": None})

    def same(n: dict, o: dict, spread: bool) -> bool:
        return all(n[k] == o[k] for k in _MEAS) and bool(o["spread"]) == spread

    write_new: list[tuple[tuple, dict, bool]] = []
    for (conv, dims), u in new.items():
        spread = conv > 0 and n_new[conv] > 1
        o = old.get((conv, dims))
        if o is not None and same(u, o, spread):
            continue
        write_new.append(((conv, dims), u, spread))
        c = cell(dims)
        c["messages"] += u["messages"]
        c["size"] += int(u["size"] or 0)
        c["unread"] += u["unread"]
        c["units"] += 1
        c["solo"] += 1 if conv > 0 and not spread else 0
        if u["oldest"] is not None and (c["oldest"] is None or u["oldest"] < c["oldest"]):
            c["oldest"] = u["oldest"]
        if u["newest"] is not None and (c["newest"] is None or u["newest"] > c["newest"]):
            c["newest"] = u["newest"]
        if u["newest_read"] is not None and (c["newest_read"] is None or u["newest_read"] > c["newest_read"]):
            c["newest_read"] = u["newest_read"]
        nk = (u["newest"] is not None, u["newest"] or datetime.min.replace(tzinfo=UTC), u["newest_id"])
        if c["newest_key"] is None or nk > c["newest_key"]:
            c["newest_key"], c["newest_id"] = nk, u["newest_id"]
        out["senders"].add((dims[1], dims[2]))
    gone: list[tuple[int, int]] = []
    touched: set[int] = set()
    redo: set[int] = set()
    for (conv, dims), o in old.items():
        u = new.get((conv, dims))
        if u is not None and same(u, o, conv > 0 and n_new[conv] > 1):
            continue
        c = cell(dims)
        c["messages"] -= o["messages"]
        c["size"] -= int(o["size"] or 0)
        c["unread"] -= o["unread"]
        c["units"] -= 1
        c["solo"] -= 1 if conv > 0 and not o["spread"] else 0
        if u is None:
            gone.append((conv, o["cell_id"]))
        touched.add(o["cell_id"])
        if (o["oldest"] == o["c_oldest"] or o["newest"] == o["c_newest"] or o["newest_read"] == o["c_newest_read"]
                or o["newest_id"] == o["c_newest_id"]):
            redo.add(o["cell_id"])
        out["senders"].add((dims[1], dims[2]))
    out["changed"] = len({k[0] for k, _u, _s in write_new} | {c for c, _i in gone})
    if not delta:
        return out
    order = sorted(delta, key=lambda d: tuple("" if x is None else x for x in d))
    p: dict[str, list] = {n: [] for n, _t in _D_COLS}
    for i, dims in enumerate(order):
        c = delta[dims]
        for n, v in zip(_DIMS, dims, strict=True):
            p[n].append(v)
        for n in ("messages", "size", "unread", "units", "solo", "oldest", "newest", "newest_read", "newest_id"):
            p[n].append(c[n])
        p["ord"].append(i)
    for (conv, dims), u, spread in write_new:
        p.setdefault("n_conv", []).append(conv)
        for n, v in zip(_DIMS, dims, strict=True):
            p.setdefault(f"n_{n}", []).append(v)
        for n in ("messages", "size", "unread", "oldest", "newest", "newest_read", "newest_id"):
            p.setdefault(f"n_{n}", []).append(int(u[n]) if n == "size" else u[n])
        p.setdefault("n_spread", []).append(spread)
    for n, _t in _N_COLS:
        p.setdefault(n, [])
    p["del_conv"] = [c for c, _i in gone]
    p["del_cell"] = [i for _c, i in gone]
    conn.execute(text(_WRITE), p)
    if touched or redo:
        conn.execute(text(_TIDY), {"touched": sorted(touched), "re": sorted(redo)})
    return out


# ------------------------------------------------------------------ state, build, check


async def state(db, user_id: int) -> tuple[str, datetime] | None:
    """(state, anchor) of the user's tally, or None."""
    row = (await db.execute(text("SELECT state, anchor FROM mail_tally_users WHERE user_id = :u"),
                            {"u": user_id})).first()
    return (row[0], row[1]) if row else None


async def start_empty(db, user_id: int) -> None:
    """A new user's tally: empty and ready (in the caller's transaction)."""
    await db.execute(text(
        "INSERT INTO mail_tally_users (user_id, state, anchor, cursor, drifted, drifted_total) "
        "VALUES (:u, 'ready', :a, 0, 0, 0) ON CONFLICT (user_id) DO NOTHING"),
        {"u": user_id, "a": datetime.now(UTC) - ANCHOR_MARGIN})


def _wipe(conn, user_id: int, anchor: datetime) -> None:
    conn.execute(text("SELECT pg_advisory_xact_lock(:cls, :u)"), {"cls": LOCK, "u": user_id})
    for t in ("mail_tally_units", "mail_tally", "mail_tally_convs"):
        conn.execute(text(f"DELETE FROM {t} WHERE user_id = :u"), {"u": user_id})
    conn.execute(text(
        "INSERT INTO mail_tally_users (user_id, state, anchor, cursor, drifted, drifted_total) "
        "VALUES (:u, 'building', :a, 0, 0, 0) ON CONFLICT (user_id) DO UPDATE SET state = 'building', "
        "anchor = :a, cursor = 0"), {"u": user_id, "a": anchor})


async def build_step(user_id: int, *, batch: int = BATCH, anchor: datetime | None = None) -> bool:
    """One step of counting a user's mail: empty a missing or stale tally, then one batch of
    conversations, then the messages without one. True when the tally is ready."""
    from mailr.db import session_scope

    async with session_scope() as db:
        st = await state(db, user_id)
        if st is None or st[0] == "stale":
            a = anchor or datetime.now(UTC) - ANCHOR_MARGIN
            await db.run_sync(lambda s: _wipe(s.connection(), user_id, a))
            return False
        if st[0] == "ready":
            return True
        cursor = (await db.execute(text("SELECT cursor FROM mail_tally_users WHERE user_id = :u"),
                                   {"u": user_id})).scalar_one()
        ids = list((await db.execute(text(
            "SELECT id FROM conversations WHERE user_id = :u AND id > :c ORDER BY id LIMIT :n"),
            {"u": user_id, "c": cursor, "n": batch})).scalars())
        if ids:
            await db.run_sync(lambda s: recount(s.connection(), {i: user_id for i in ids}))
            await db.execute(text("UPDATE mail_tally_users SET cursor = :c WHERE user_id = :u"),
                             {"c": ids[-1], "u": user_id})
            return False
        loose = list((await db.execute(text(
            "SELECT id FROM messages WHERE user_id = :u AND conversation_id IS NULL AND deleted IS false"),
            {"u": user_id})).scalars())
        for i in range(0, len(loose), batch):
            part = loose[i:i + batch]
            await db.run_sync(lambda s, part=part: recount(s.connection(), {-m: user_id for m in part}))
        await db.execute(text("UPDATE mail_tally_users SET state = 'ready', built_at = now() WHERE user_id = :u "
                              "AND state = 'building'"), {"u": user_id})
        return True


async def rebuild(user_id: int, *, anchor: datetime | None = None, batch: int = BATCH) -> None:
    """Count the user's mail again from scratch, here (`mailr tally rebuild`, tests)."""
    from mailr.db import session_scope

    async with session_scope() as db:
        a = anchor or datetime.now(UTC) - ANCHOR_MARGIN
        await db.run_sync(lambda s: _wipe(s.connection(), user_id, a))
    while not await build_step(user_id, batch=batch):
        pass


def _reband(conn, user_id: int, now: datetime) -> bool:
    """Move the user's anchor forward by at most a day and count again the conversations whose
    mail changed band. False when the anchor is current (or the tally is not ready)."""
    conn.execute(text("SELECT pg_advisory_xact_lock(:cls, :u)"), {"cls": LOCK, "u": user_id})
    row = conn.execute(text("SELECT state, anchor FROM mail_tally_users WHERE user_id = :u"), {"u": user_id}).first()
    target = now - ANCHOR_MARGIN
    if row is None or row[0] != "ready" or row[1] >= target - timedelta(minutes=55):
        return False
    old = row[1]
    new = min(target, old + timedelta(days=1))
    windows = " OR ".join(f"(date >= :o{i} AND date < :n{i})" for i in range(len(BANDS)))
    params: dict[str, Any] = {"u": user_id}
    for i, d in enumerate(BANDS):
        params[f"o{i}"], params[f"n{i}"] = old - timedelta(days=d), new - timedelta(days=d)
    keys = conn.execute(text(
        f"SELECT DISTINCT coalesce(conversation_id, -id) FROM messages WHERE user_id = :u AND deleted IS false "
        f"AND ({windows})"), params).scalars().all()
    conn.execute(text("UPDATE mail_tally_users SET anchor = :a WHERE user_id = :u"), {"a": new, "u": user_id})
    recount(conn, {int(k): user_id for k in keys})
    return True


# Every unit as the user's messages make it now, against the stored ones: the keys that differ.
_EXPECTED = f"""
WITH r AS (
  SELECT u.user_id,
         coalesce((SELECT array_agg(id) FROM labels WHERE user_id = u.user_id AND role = 'inbox'), '{{}}') AS inbox,
         coalesce((SELECT array_agg(id) FROM labels WHERE user_id = u.user_id AND role = 'trash'), '{{}}') AS trash,
         coalesce((SELECT array_agg(id) FROM labels WHERE user_id = u.user_id AND role = 'junk'), '{{}}') AS junk,
         u.anchor
  FROM mail_tally_users u WHERE u.user_id = :u
), sent AS (
  SELECT DISTINCT conversation_id AS conv FROM messages
  WHERE user_id = :u AND sent_by_me IS true AND deleted IS false AND conversation_id IS NOT NULL
), p AS (
  SELECT c.id AS conv, (CASE WHEN c.starred THEN 1 ELSE 0 END) | (CASE WHEN c.important THEN 2 ELSE 0 END)
         | (CASE WHEN s.conv IS NOT NULL THEN 4 ELSE 0 END) AS prot
  FROM conversations c LEFT JOIN sent s ON s.conv = c.id WHERE c.user_id = :u
), m AS (
  SELECT coalesce(m.conversation_id, -m.id) AS conv, m.from_email, m.list_id, m.category,
         ((m.label_ids && r.inbox)::int | ((m.label_ids && r.trash)::int << 1)) AS loc,
         CASE WHEN m.date IS NULL THEN {NO_DATE} ELSE {_BAND} END AS band,
         m.unsub_method IS NOT NULL AS unsub, m.local_only, coalesce(p.prot, 0) AS prot, m.size, m.seen, m.date, m.id
  FROM messages m CROSS JOIN r LEFT JOIN p ON p.conv = m.conversation_id
  WHERE m.user_id = :u AND m.deleted IS false AND m.sent_by_me IS false AND NOT (m.label_ids && r.junk)
), e AS (
  SELECT conv, from_email, coalesce(list_id, '') AS li, list_id IS NULL AS li_null, coalesce(category, '') AS ca,
         category IS NULL AS ca_null, loc, band, unsub, local_only, prot,
         count(*) AS messages, sum(size) AS size, count(*) FILTER (WHERE NOT seen) AS unread, min(date) AS oldest,
         max(date) AS newest, max(date) FILTER (WHERE seen) AS newest_read,
         (array_agg(id ORDER BY date DESC NULLS LAST, id DESC))[1] AS newest_id
  FROM m GROUP BY conv, from_email, list_id, category, loc, band, unsub, local_only, prot
), e2 AS (SELECT e.*, (conv > 0 AND count(*) OVER (PARTITION BY conv) > 1) AS spread FROM e),
s AS (
  SELECT u.conv, t.from_email, coalesce(t.list_id, '') AS li, t.list_id IS NULL AS li_null,
         coalesce(t.category, '') AS ca, t.category IS NULL AS ca_null, t.loc, t.band, t.unsub, t.local_only, t.prot,
         u.messages, u.size, u.unread, u.oldest, u.newest, u.newest_read, u.newest_id, u.spread
  FROM mail_tally_units u JOIN mail_tally t ON t.id = u.cell_id WHERE u.user_id = :u
)
SELECT DISTINCT coalesce(e2.conv, s.conv) FROM e2 FULL JOIN s
  ON e2.conv = s.conv AND e2.from_email = s.from_email AND e2.li = s.li AND e2.li_null = s.li_null AND e2.ca = s.ca
     AND e2.ca_null = s.ca_null AND e2.loc = s.loc AND e2.band = s.band AND e2.unsub = s.unsub
     AND e2.local_only = s.local_only AND e2.prot = s.prot
WHERE e2.conv IS NULL OR s.conv IS NULL
   OR (e2.messages, e2.size, e2.unread, e2.oldest, e2.newest, e2.newest_read, e2.newest_id, e2.spread)
      IS DISTINCT FROM (s.messages, s.size, s.unread, s.oldest, s.newest, s.newest_read, s.newest_id, s.spread)
"""

# Rows that do not add up to their units, and units whose row is gone.
_ROWS_OFF = """
WITH a AS (
  SELECT cell_id, sum(messages) AS messages, sum(size) AS size, sum(unread) AS unread, count(*) AS units,
         count(*) FILTER (WHERE conv > 0 AND NOT spread) AS solo, min(oldest) AS oldest, max(newest) AS newest,
         max(newest_read) AS newest_read,
         (array_agg(newest_id ORDER BY newest DESC NULLS LAST, newest_id DESC))[1] AS newest_id
  FROM mail_tally_units WHERE user_id = :u GROUP BY cell_id
)
SELECT t.id, t.from_email, t.list_id FROM mail_tally t LEFT JOIN a ON a.cell_id = t.id
WHERE t.user_id = :u AND (a.cell_id IS NULL OR (t.messages, t.size, t.unread, t.units, t.solo, t.oldest, t.newest,
      t.newest_read, t.newest_id) IS DISTINCT FROM (a.messages, a.size, a.unread, a.units, a.solo, a.oldest,
      a.newest, a.newest_read, a.newest_id))
"""

_FIX_ROWS = """
WITH a AS (
  SELECT cell_id, sum(messages) AS messages, sum(size) AS size, sum(unread) AS unread, count(*) AS units,
         count(*) FILTER (WHERE conv > 0 AND NOT spread) AS solo, min(oldest) AS oldest, max(newest) AS newest,
         max(newest_read) AS newest_read,
         (array_agg(newest_id ORDER BY newest DESC NULLS LAST, newest_id DESC))[1] AS newest_id
  FROM mail_tally_units WHERE cell_id = ANY(:ids) GROUP BY cell_id
), gone AS (DELETE FROM mail_tally WHERE id = ANY(:ids) AND id NOT IN (SELECT cell_id FROM a))
UPDATE mail_tally t SET messages = a.messages, size = a.size, unread = a.unread, units = a.units, solo = a.solo,
  oldest = a.oldest, newest = a.newest, newest_read = a.newest_read, newest_id = a.newest_id
FROM a WHERE t.id = a.cell_id
"""


def _repair_rows(conn, user_id: int) -> set[tuple[str, str | None]]:
    """Under the exclusive lock: units without a row go (the recount that follows counts their
    conversations again), and rows that do not add up to their units are summed again."""
    conn.execute(text("SELECT pg_advisory_xact_lock(:cls, :u)"), {"cls": LOCK, "u": user_id})
    senders: set[tuple[str, str | None]] = set()
    conn.execute(text(
        "DELETE FROM mail_tally_units u WHERE u.user_id = :u AND NOT EXISTS "
        "(SELECT 1 FROM mail_tally t WHERE t.id = u.cell_id)"), {"u": user_id})
    off = conn.execute(text(_ROWS_OFF), {"u": user_id}).all()
    if off:
        conn.execute(text(_FIX_ROWS), {"ids": [r[0] for r in off]})
        senders |= {(r[1], r[2]) for r in off}
    # Lock rows of conversations and messages that are gone, with no units left.
    conn.execute(text(
        "DELETE FROM mail_tally_convs h WHERE h.user_id = :u AND NOT EXISTS (SELECT 1 FROM mail_tally_units u "
        "WHERE u.conv = h.conv) AND NOT EXISTS (SELECT 1 FROM conversations c WHERE c.id = h.conv) "
        "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.id = -h.conv)"), {"u": user_id})
    return senders


async def check(user_id: int, *, now: datetime | None = None) -> dict[str, Any] | None:
    """The nightly check of one user's tally: move the anchor forward, then compare the tally with a
    recount and correct what differs. Returns {senders, conversations}, or None when the tally is
    not ready."""
    from mailr.db import session_scope

    now = now or datetime.now(UTC)
    while True:
        async with session_scope() as db:
            st = await state(db, user_id)
            if st is None or st[0] != "ready":
                return None
            if not await db.run_sync(lambda s: _reband(s.connection(), user_id, now)):
                break
    async with session_scope() as db:
        senders = await db.run_sync(lambda s: _repair_rows(s.connection(), user_id))
    async with session_scope() as db:
        keys = [int(k) for k in (await db.execute(text(_EXPECTED), {"u": user_id})).scalars()]
    changed = 0
    for i in range(0, len(keys), BATCH):
        part = keys[i:i + BATCH]
        async with session_scope() as db:
            res = await db.run_sync(lambda s, part=part: recount(s.connection(), {k: user_id for k in part}))
        changed += res["changed"]
        if res["changed"]:
            senders |= res["senders"]
    n = len(senders)
    async with session_scope() as db:
        await db.execute(text(
            "UPDATE mail_tally_users SET checked_at = now(), drifted = :n, drifted_total = drifted_total + :n "
            "WHERE user_id = :u"), {"n": n, "u": user_id})
    if n:
        log.warning("mail tally check: user %s had %d senders counted wrong (%d conversations), corrected",
                    user_id, n, changed)
    else:
        log.info("mail tally check: user %s counted right", user_id)
    return {"senders": n, "conversations": changed}


# ------------------------------------------------------------------ jobs


def _register() -> None:
    from mailr.core.jobs import Retry, handler

    @handler("mail.tally_build")
    async def tally_build(payload: dict[str, Any], ctx) -> None:
        """Count a user's mail for the tally, a batch at a time (the next batch is this job again)."""
        if not await build_step(int(payload["user_id"])):
            raise Retry(delay=1)

    @handler("mail.tally_check")
    async def tally_check(payload: dict[str, Any], ctx) -> None:
        """The nightly check of a user's tally (`check`)."""
        await check(int(payload["user_id"]))


_register()


async def queue_builds() -> int:
    """A build job for every enabled user whose tally is missing or stale (the scheduler, every
    few minutes): after the upgrade, and after a change too large to count per conversation."""
    from mailr.core.jobs import enqueue
    from mailr.db import session_scope

    async with session_scope() as db:
        users = list((await db.execute(text(
            "SELECT u.id FROM users u LEFT JOIN mail_tally_users t ON t.user_id = u.id "
            "WHERE u.disabled IS false AND (t.user_id IS NULL OR t.state <> 'ready')"))).scalars())
        for uid in users:
            await enqueue(db, "mail.tally_build", {"user_id": uid}, user_id=uid, dedupe_key=f"mail.tally_build:{uid}")
    return len(users)
