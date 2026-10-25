"""Search operators never answer 500.

Values that cannot be read (a date past year 9999, a size past the int4 column, a relative date
of a trillion days, NUL) fall back to plain text search or are clamped; a search that is too long
or too deeply nested is a 400. The fuzz part compiles and runs a few thousand generated queries
against the database: every one either compiles and executes, or raises ValueError (the 400)."""

import random
import string

from sqlalchemy import func, select

from mailr.core import search
from mailr.db import sessionmaker
from mailr.models import Label, Message

OPS = ["from", "to", "cc", "bcc", "subject", "label", "in", "is", "has", "filename", "after", "before",
       "newer", "older", "newer_than", "older_than", "larger", "smaller", "size", "category", "list",
       "deliveredto", "rfc822msgid", "recording"]
NASTY = ["99999999999999999999", "99999999999d", "3000000000", "4294967296", "9" * 400, "-1", "0", "0d",
         "1e9", "10000-01-01", "0000-00-00", "2026-13-45", "2026/02/30", "1y", "999999999999y", "20k",
         "999999999999999m", "1.5", "", "'", '"', "\\", "%", "_", "\x00", "\x00\x00x", "(", ")", "{", "}",
         "OR", "-", "--", "::", "a:b:c", "me", "ñ", "日本", "\u202e", "📧", "%%%", "\\%", "a\\", "'; DROP"]


def _value(rng: random.Random) -> str:
    if rng.random() < 0.6:
        return rng.choice(NASTY)
    return "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 12)))


def _query(rng: random.Random) -> str:
    parts = []
    for _ in range(rng.randint(1, 6)):
        r = rng.random()
        if r < 0.6:
            parts.append(f"{'-' if rng.random() < 0.2 else ''}{rng.choice(OPS)}:{_value(rng)}")
        elif r < 0.8:
            parts.append(_value(rng))
        else:
            parts.append(rng.choice(["OR", "(", ")", "{", "}", "AND", "AROUND", "|"]))
    return " ".join(parts)


async def _run(db, uid: int, labels, q: str) -> None:
    try:
        compiled = search.compile_query(q, uid, labels)
    except ValueError:
        return  # the API answers 400
    sub = search.conversation_ids_query(uid, compiled, labels)
    await db.execute(select(func.count()).select_from(sub.subquery()))
    await db.execute(select(func.count()).select_from(Message).where(Message.user_id == uid, compiled.where))


async def test_reported_operators_answer_200(client, user):
    for q in ("after:99999999999999999999", "before:99999999999999999999", "older_than:99999999999d",
              "newer_than:99999999999d", "larger:3000000000", "smaller:3000000000", "size:99999999999999m",
              "larger:" + "9" * 400, "after:10000-01-01", "after:0000-00-00", "subject:a\x00b", "\x00",
              "rfc822msgid:" + "x" * 2000):
        for path, params in (("/api/v1/conversations", {"view": "all", "q": q}), ("/api/v1/attachments", {"q": q})):
            r = await client.get(path, params=params)
            assert r.status_code == 200, (path, q, r.status_code, r.text[:200])


async def test_too_complex_searches_are_400(client, user):
    assert (await client.get("/api/v1/conversations", params={"q": "a " * 300})).status_code == 400
    assert (await client.get("/api/v1/conversations", params={"q": "(" * 30 + "a" + ")" * 30})).status_code == 400


def test_values_are_clamped_or_refused():
    assert search._size("3000000000") == search.MAX_SIZE
    assert search._size("99999999999m") == search.MAX_SIZE
    for bad in ("9" * 40, "x", "-1"):
        try:
            search._size(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    assert search._rel("99999999999d").year == 1
    for bad in ("99999999999999999999", "10000-01-01", "2026-13-45"):
        try:
            search._parse_date(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)


async def test_fuzz_search_parser(user):
    rng = random.Random(20261003)
    async with sessionmaker()() as db:
        labels = list((await db.execute(select(Label).where(Label.user_id == user.id))).scalars())
        for _ in range(1500):
            q = _query(rng)
            try:
                await _run(db, user.id, labels, q)
            except Exception as e:
                raise AssertionError(f"{q!r}: {type(e).__name__}: {e}") from e
