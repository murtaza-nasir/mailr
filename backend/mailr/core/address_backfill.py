"""Clean addresses and names stored before the address cleaning (`mailr address-clean-backfill`).

Newly parsed mail stores addresses without bidi, invisible or control characters or white
space (core/textsafe.clean_address) and names without bidi, invisible or control characters
(textsafe.clean); calendar ORGANIZER and ATTENDEE addresses and names are cleaned too. Rows stored
before keep them: the web cleans what it shows, but search, matching and the phone read the rows.
This rewrites those rows in place:

- messages: from_email, from_name, to, cc, bcc, reply_to;
- conversations: participants;
- the calendar mirror (cal_objects): organizer, organizer_name, attendees.

Only rows whose stored text holds such a character are read (a regular-expression filter in the
database), and only changed values are written, so a second run changes nothing. The calendar
server's own copy is left alone: every reading of it goes through ical.addr and ical.cn.
Counts only: never an address, a name or any other text.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import String, cast, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import textsafe
from mailr.models import CalObject, Conversation, Message


def _pg_class() -> str:
    """A PostgreSQL bracket expression of the characters the cleaning removes."""
    chars = textsafe.BIDI + textsafe.INVISIBLE + textsafe.JOINERS
    out = "".join(f"\\u{ord(c):04x}" for c in chars)
    return f"[{out}\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f]"


# A stored value with such a character; in JSON text a control character is written \u00XX.
PATTERN = _pg_class()
JSON_ESCAPE = r"\\u00[01][0-9a-fA-F]"


def _addr(s: Any) -> Any:
    return textsafe.clean_address(s) if isinstance(s, str) else s


def _name(s: Any) -> Any:
    return textsafe.clean(s).strip() if isinstance(s, str) else s


def clean_list(items: Any) -> Any:
    """[{"email", "name", …}] with each email and name cleaned; anything else as it is."""
    if not isinstance(items, list):
        return items
    out = []
    for it in items:
        if isinstance(it, dict):
            it = dict(it)
            if "email" in it:
                it["email"] = _addr(it["email"])
            if "name" in it:
                it["name"] = _name(it["name"])
        out.append(it)
    return out


def _dirty(*cols: Any) -> Any:
    conds = []
    for c in cols:
        text = cast(c, String)
        conds += [text.op("~")(PATTERN), text.op("~")(JSON_ESCAPE)]
    return or_(*conds)


async def clean_user(db: AsyncSession, user_id: int, *, batch: int = 500, dry_run: bool = False,
                     progress: Callable[[dict[str, int]], None] | None = None) -> dict[str, int]:
    """Clean one user's rows; committed per batch. Returns counts."""
    st = {"messages": 0, "conversations": 0, "calendar": 0}
    specs: list[tuple[str, Any, list[tuple[str, Callable[[Any], Any]]]]] = [
        ("messages", Message, [("from_email", _addr), ("from_name", _name), ("to", clean_list), ("cc", clean_list),
                               ("bcc", clean_list), ("reply_to", clean_list)]),
        ("conversations", Conversation, [("participants", clean_list)]),
        ("calendar", CalObject, [("organizer", _addr), ("organizer_name", _name), ("attendees", clean_list)]),
    ]
    for key, model, fields in specs:
        cols = [getattr(model, f) for f, _ in fields]
        last = 0
        while True:
            rows = (await db.execute(
                select(model.id, *cols).where(model.user_id == user_id, model.id > last, _dirty(*cols))
                .order_by(model.id).limit(batch))).all()
            if not rows:
                break
            for row in rows:
                last = row[0]
                changes = {}
                for (f, fn), old in zip(fields, row[1:], strict=True):
                    new = fn(old)
                    if new != old:
                        changes[f] = new
                if changes:
                    st[key] += 1
                    if not dry_run:
                        await db.execute(update(model).where(model.id == row[0]).values(**changes))
            if not dry_run:
                await db.commit()
            if progress:
                progress(st)
    return st


def line(email: str, st: dict[str, int], dry_run: bool) -> str:
    verb = "would clean" if dry_run else "cleaned"
    return (f"{email}: {verb} {st['messages']} messages, {st['conversations']} conversations, "
            f"{st['calendar']} calendar items")
