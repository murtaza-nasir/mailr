"""Stalwart's calendar, contacts and directory settings, kept equal to what mailr needs.

New stores get these settings from `deploy/stalwart/provision-groupware.json`. Provisioning runs
only on a blank store, so `ensure_groupware_settings` (called by `users.bootstrap` at every start
of the API and the worker) applies the same settings to existing stores. It is idempotent: it
reads each setting first and writes only what differs, so a normal start sends no writes.

- Scheduling off (`x:CalendarScheduling`): Stalwart would otherwise mail invitations through its
  own MTA. mailr's SendGate stays the only path that sends invitations.
- Alarms off (`x:CalendarAlarm`): mailr sends its own reminders.
- `x:WebDav.maxResults` at least 50000: a listing past the limit comes back cut short.
- Default calendar and address book names "Calendar" and "Contacts", unless an admin changed them.
- No directory: `x:Sharing.allowDirectoryQueries` false, and the principal permissions removed
  from the default user role. Stalwart's setting alone does not stop a user from listing every
  account (it only applies to users without these permissions, and its stock User role has them).
`ensure_phone_settings(on)` follows the phone access switch (calendar/phones.py):
- `x:Http.useXForwarded` = on: Stalwart takes the client's address from X-Forwarded-For.
- `x:AddressBook.defaultHrefName` null while on: Stalwart makes an address book in every account the
  first time anyone lists it, a group account included, and every member of a group sees and edits
  the group's, on a phone too. mailr makes a user's own "Contacts" book itself
  (contacts/carddav.ensure_own_book). Off: back to Stalwart's "default". Calendars keep the
  automatic default: a group's is kept away by a placeholder (calendar/groups.seal_home).
"""

from __future__ import annotations

import logging
from typing import Any

from mailr.core.jmap import CORE

log = logging.getLogger("mailr.store.groupware")
MGMT = [CORE, "urn:stalwart:jmap"]

# Values mailr requires: exact. Keep equal to deploy/stalwart/provision-groupware.json (a test
# compares them).
EXACT: dict[str, dict[str, Any]] = {
    "x:CalendarScheduling": {"enable": False, "httpRsvpEnable": False},
    "x:CalendarAlarm": {"enable": False},
    "x:WebDav": {"enableAssistedDiscovery": True},
    "x:Sharing": {"allowDirectoryQueries": False},
}
# Limits mailr needs at least; a larger value an admin set stays.
AT_LEAST: dict[str, dict[str, int]] = {
    "x:WebDav": {"maxResults": 50000},
    "x:Calendar": {"maxAttendees": 100},
    "x:Sharing": {"maxShares": 50},
}
# Names that replace only Stalwart's stock value ("Stalwart Calendar (alice@…)" in every list).
NAMES: dict[str, tuple[str, str]] = {
    "x:Calendar": ("Stalwart Calendar", "Calendar"),
    "x:AddressBook": ("Stalwart Address Book", "Contacts"),
}
# A user who holds any of these can list every account on the store, with names, addresses and
# descriptions, through JMAP Principal/* or the DAV principal reports.
DIRECTORY_PERMISSIONS = (
    "jmapPrincipalGet", "jmapPrincipalQuery", "jmapPrincipalQueryChanges", "jmapPrincipalChanges",
    "jmapPrincipalGetAvailability",
    "davPrincipalList", "davPrincipalSearch", "davPrincipalMatch", "davPrincipalAcl", "davPrincipalSearchPropSet",
)


def wanted_singletons() -> dict[str, dict[str, Any]]:
    """The singleton settings a new store is provisioned with (provision-groupware.json)."""
    out: dict[str, dict[str, Any]] = {}
    for table in (EXACT, AT_LEAST):
        for obj, vals in table.items():
            out.setdefault(obj, {}).update(vals)
    for obj, (_stock, name) in NAMES.items():
        out.setdefault(obj, {})["defaultDisplayName"] = name
    return out


def singleton_patch(obj: str, cur: dict[str, Any]) -> dict[str, Any]:
    """What to write to one singleton so it meets mailr's needs (empty: nothing)."""
    patch: dict[str, Any] = {}
    for k, v in EXACT.get(obj, {}).items():
        if cur.get(k) != v:
            patch[k] = v
    for k, v in AT_LEAST.get(obj, {}).items():
        have = cur.get(k)
        if not isinstance(have, int) or have < v:
            patch[k] = v
    if obj in NAMES:
        stock, name = NAMES[obj]
        if cur.get("defaultDisplayName") == stock:
            patch["defaultDisplayName"] = name
    return patch


async def _calls(client: Any, calls: list[list[Any]]) -> list[list[Any]]:
    return await client.call(calls, using=MGMT)


async def ensure_groupware_settings(client: Any = None) -> list[str]:
    """Apply the settings above to the store. Returns what changed (empty on a normal start)."""
    if client is None:
        from mailr.core.store import service_client

        client = service_client()
    changed: list[str] = []
    objs = list(wanted_singletons())
    got = await _calls(client, [[f"{o}/get", {"ids": ["singleton"]}, o] for o in objs])
    updates = []
    for _name, args, tag in got:
        cur = (args.get("list") or [{}])[0]
        patch = singleton_patch(tag, cur)
        if patch:
            updates.append([f"{tag}/set", {"update": {"singleton": patch}}, tag])
            changed += [f"{tag}.{k}" for k in patch]
    if updates:
        for _name, args, tag in await _calls(client, updates):
            if "singleton" not in (args.get("updated") or {}):
                raise RuntimeError(f"could not update {tag}: {args.get('notUpdated')}")
    roles_changed = await _restrict_user_roles(client, changed)
    actions = []
    if updates:
        actions.append({"@type": "ReloadSettings"})
    if roles_changed:
        # Access tokens keep the permissions they were built with until the caches are cleared.
        actions.append({"@type": "InvalidateCaches"})
    if actions:
        await _calls(client, [["x:Action/set", {"create": {f"a{i}": a for i, a in enumerate(actions)}}, "act"]])
        log.warning("Stalwart groupware settings updated: %s", ", ".join(changed))
    return changed


async def _restrict_user_roles(client: Any, changed: list[str]) -> bool:
    """Take the directory permissions out of the roles every user account gets by default."""
    auth = await _calls(client, [["x:Authentication/get", {"ids": ["singleton"],
                                                          "properties": ["defaultUserRoleIds"]}, "a"]])
    role_ids = list(((auth[0][1].get("list") or [{}])[0].get("defaultUserRoleIds") or {}).keys())
    if not role_ids:
        return False
    roles = await _calls(client, [["x:Role/get", {"ids": role_ids, "properties": ["enabledPermissions"]}, "r"]])
    updates: dict[str, dict[str, Any]] = {}
    for role in roles[0][1].get("list") or []:
        have = role.get("enabledPermissions") or {}
        drop = [p for p in DIRECTORY_PERMISSIONS if have.get(p)]
        if drop:
            updates[role["id"]] = {f"enabledPermissions/{p}": None for p in drop}
            changed.append(f"x:Role[{role['id']}] -" + ",".join(drop))
    if not updates:
        return False
    res = await _calls(client, [["x:Role/set", {"update": updates}, "s"]])
    missing = set(updates) - set(res[0][1].get("updated") or {})
    if missing:
        raise RuntimeError(f"could not update roles {sorted(missing)}: {res[0][1].get('notUpdated')}")
    return True


async def ensure_phone_settings(on: bool, client: Any = None) -> list[str]:
    """The store settings of phone access (above). Returns what changed (empty: nothing).

    useXForwarded on: Stalwart takes the client's address from the first X-Forwarded-For entry (or
    `Forwarded: for=`) of every HTTP request, so its auto-ban and app-password IP limits see the
    phone, not the proxy. It trusts the header from anyone, which is why only the DAV gateway may
    reach port 8080 from outside, and the gateway overwrites the header (deploy/dav-gateway)."""
    if client is None:
        from mailr.core.store import service_client

        client = service_client()
    got = await _calls(client, [["x:Http/get", {"ids": ["singleton"]}, "x:Http"],
                                ["x:AddressBook/get", {"ids": ["singleton"]}, "x:AddressBook"]])
    cur = {tag: (args.get("list") or [{}])[0] for _, args, tag in got}
    patch: dict[str, dict[str, Any]] = {}
    if bool(cur["x:Http"].get("useXForwarded")) != bool(on):
        patch["x:Http"] = {"useXForwarded": bool(on)}
    href = cur["x:AddressBook"].get("defaultHrefName")
    if on and href is not None:
        patch["x:AddressBook"] = {"defaultHrefName": None}
    elif not on and href is None:
        patch["x:AddressBook"] = {"defaultHrefName": "default"}
    if not patch:
        return []
    calls = [[f"{o}/set", {"update": {"singleton": v}}, o] for o, v in patch.items()]
    calls.append(["x:Action/set", {"create": {"a": {"@type": "ReloadSettings"}}}, "act"])
    res = await _calls(client, calls)
    for _name, args, tag in res[:-1]:
        if "singleton" not in (args.get("updated") or {}):
            raise RuntimeError(f"could not update {tag}: {args.get('notUpdated')}")
    changed = [f"{o}.{k}" for o, v in patch.items() for k in v]
    log.warning("Stalwart phone access settings updated: %s", ", ".join(changed))
    return changed
