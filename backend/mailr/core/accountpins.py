"""Pinned folders in each account's group (8 Oct 2026).

With "Show each account separately" on, an account's group lists its system folders, then the
folders pinned to that account in the user's order, then one row "More folders" that opens to the
rest. Three user settings hold it:

- `account_folder_pins`: {"<account id>": [label id, …]}, the pinned folders of each account in
  order. Unknown accounts and labels are dropped when the setting is saved (nothing is migrated).
- `account_folders_more_open`: [account id, …], the accounts whose "More folders" is open.
- `account_folders_show_unread`: show a folder with unread mail above "More folders" until it is
  read (off by default).

A folder made with "New folder" in an account is pinned there (pin_new_folder).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import Label, User

PIN_KEYS = ("account_folder_pins", "account_folders_more_open")
MAX_ACCOUNTS = 50
MAX_PINS = 200


def _id(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and 0 < v < 10**12:
        return v
    if isinstance(v, str) and v.isdigit() and len(v) <= 12:
        return int(v)
    return None


def _shape_pins(value: Any) -> dict[int, list[int]]:
    if not isinstance(value, dict) or len(value) > MAX_ACCOUNTS:
        raise ValueError("account_folder_pins must be {account id: [label id, …]}")
    out: dict[int, list[int]] = {}
    for k, ids in value.items():
        acct = _id(k)
        if acct is None or not isinstance(ids, list) or len(ids) > MAX_PINS:
            raise ValueError("account_folder_pins must be {account id: [label id, …]}")
        if not all(isinstance(x, int) and _id(x) is not None for x in ids):
            raise ValueError("account_folder_pins must be {account id: [label id, …]}")
        out[acct] = list(dict.fromkeys(ids))
    return out


def _shape_open(value: Any) -> list[int]:
    if not isinstance(value, list) or len(value) > MAX_ACCOUNTS or not all(
            isinstance(v, int) and not isinstance(v, bool) and _id(v) is not None for v in value):
        raise ValueError("account_folders_more_open must be a list of account ids")
    return list(dict.fromkeys(value))


async def clean(db: AsyncSession, p, body: dict) -> dict:
    # ACT mail.account_folder_reorder @api (the whole ordered list is saved on each change)
    """Validate the pin settings in a settings patch; ValueError for a malformed value. Accounts the
    caller does not have and labels that are not the user's own folders are dropped."""
    if not any(k in body for k in (*PIN_KEYS, "account_folders_show_unread")):
        return body
    out = dict(body)
    if "account_folders_show_unread" in out and not isinstance(out["account_folders_show_unread"], bool):
        raise ValueError("account_folders_show_unread must be true or false")
    if not any(k in body for k in PIN_KEYS):
        return out
    from mailr.core import mailaccounts

    accounts = {a.id for a in await mailaccounts.mail_accounts(db, p)}
    if "account_folder_pins" in out:
        pins = _shape_pins(out["account_folder_pins"])
        wanted = {i for ids in pins.values() for i in ids}
        mine: set[int] = set()
        if wanted:
            mine = set((await db.execute(select(Label.id).where(
                Label.user_id == p.user_id, Label.role.is_(None), Label.id.in_(wanted)))).scalars())
        out["account_folder_pins"] = {str(a): kept for a, ids in pins.items()
                                      if a in accounts and (kept := [i for i in ids if i in mine])}
    if "account_folders_more_open" in out:
        out["account_folders_more_open"] = [a for a in _shape_open(out["account_folders_more_open"]) if a in accounts]
    return out


async def pin_new_folder(db: AsyncSession, user_id: int, account_id: int, label_id: int) -> None:
    """Pin a folder made in an account's group to that account (at the end of its pins). The
    caller commits."""
    # ACT mail.account_folder_pin @api (a new folder is pinned where it was made)
    u = await db.get(User, user_id, with_for_update=True, populate_existing=True)
    if u is None:
        return
    settings = dict(u.settings or {})
    pins = {k: list(v) for k, v in (settings.get("account_folder_pins") or {}).items() if isinstance(v, list)}
    mine = pins.setdefault(str(account_id), [])
    if label_id in mine:
        return
    mine.append(label_id)
    settings["account_folder_pins"] = pins
    u.settings = settings
