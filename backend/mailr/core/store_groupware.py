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
- The full-text index of mail (`x:Search.indexEmail`): off on Stalwart 1.x, on for 0.16. mailr
  searches its own index and never asks Stalwart to search, and the index costs disk and CPU for
  every message (docs/design/stalwart-1.0-migration.md, section a). An empty `indexEmailFields`
  would mean "index every field", so the switch is `indexEmail`. On 0.16 every `Email/query` sort
  and date filter needs the index (without it the store sorts by message number and a date filter
  finds nothing), so mailr turns it back on there, for a store rolled back from 1.x, and asks for
  a reindex of every account.
`ensure_phone_settings(on)` follows the phone access switch (calendar/phones.py):
- On: Stalwart takes the client's address from X-Forwarded-For. On 0.16 the field is
  `x:Http.useXForwarded` (true); on 1.x it is `x:Http.clientIpSource` ("xForwardedFor", with
  `forwardedHops` 1, the last entry). Off: false, or "peer".
- `x:AddressBook.defaultHrefName` null while on: Stalwart makes an address book in every account the
  first time anyone lists it, a group account included, and every member of a group sees and edits
  the group's, on a phone too. mailr makes a user's own "Contacts" book itself
  (contacts/carddav.ensure_own_book). Off: back to Stalwart's "default". Calendars keep the
  automatic default: a group's is kept away by a placeholder (calendar/groups.seal_home).
"""

from __future__ import annotations

import logging
import time
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
# Values that depend on the Stalwart version (is_v1): True for 1.x, False for 0.16.
EXACT_BY_VERSION: dict[bool, dict[str, dict[str, Any]]] = {
    True: {"x:Search": {"indexEmail": False}},
    False: {"x:Search": {"indexEmail": True}},
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


_V1_TTL = 300.0
_v1_seen: dict[str, tuple[bool, float]] = {}


async def is_v1(client: Any, cache: bool = True) -> bool:
    """True when the store runs Stalwart 1.x, false on 0.16. The server does not report its version
    over JMAP, so the shape of `x:Http` tells: 1.x has `clientIpSource` where 0.16 has
    `useXForwarded`. Kept five minutes per server URL, so a store replaced under a running mailr is
    seen again soon."""
    key = getattr(client, "base_url", None) if cache else None
    if key is not None and (hit := _v1_seen.get(key)) and time.monotonic() - hit[1] < _V1_TTL:
        return hit[0]
    got = await _calls(client, [["x:Http/get", {"ids": ["singleton"]}, "h"]])
    v1 = "clientIpSource" in ((got[0][1].get("list") or [{}])[0])
    if key is not None:
        _v1_seen[key] = (v1, time.monotonic())
    return v1


def forget_version() -> None:
    _v1_seen.clear()


def wanted_singletons(v1: bool = False) -> dict[str, dict[str, Any]]:
    """The singleton settings a new store is provisioned with (provision-groupware.json, and
    provision-groupware.v1.json on Stalwart 1.x)."""
    out: dict[str, dict[str, Any]] = {}
    for table in (EXACT, AT_LEAST, EXACT_BY_VERSION[v1]):
        for obj, vals in table.items():
            out.setdefault(obj, {}).update(vals)
    for obj, (_stock, name) in NAMES.items():
        out.setdefault(obj, {})["defaultDisplayName"] = name
    return out


def singleton_patch(obj: str, cur: dict[str, Any], v1: bool = False) -> dict[str, Any]:
    """What to write to one singleton so it meets mailr's needs (empty: nothing)."""
    patch: dict[str, Any] = {}
    exact = EXACT.get(obj, {}) | EXACT_BY_VERSION[v1].get(obj, {})
    for k, v in exact.items():
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
    v1 = await is_v1(client, cache=False)
    objs = list(wanted_singletons(v1))
    got = await _calls(client, [[f"{o}/get", {"ids": ["singleton"]}, o] for o in objs])
    updates = []
    for _name, args, tag in got:
        cur = (args.get("list") or [{}])[0]
        patch = singleton_patch(tag, cur, v1)
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
    if not v1 and "x:Search.indexEmail" in changed:
        # Mail stored while the index was off (a store rolled back from 1.x) is in no index.
        await _calls(client, [["x:Task/set", {"create": {"r": {"@type": "StoreMaintenance",
                                                               "maintenanceType": "reindexAccounts"}}}, "reindex"]])
        log.warning("Stalwart: mail index turned on; every account is being reindexed")
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

    On, Stalwart takes the client's address from X-Forwarded-For of every HTTP request, so its
    auto-ban and app-password IP limits see the phone, not the proxy. 0.16 (`useXForwarded`) reads
    the first entry or `Forwarded: for=`; 1.x (`clientIpSource` "xForwardedFor", `forwardedHops` 1)
    reads the last entry. It trusts the header from anyone, which is why only the DAV gateway may
    reach port 8080 from outside, and the gateway overwrites the header with one entry
    (deploy/dav-gateway), which both versions read the same way."""
    if client is None:
        from mailr.core.store import service_client

        client = service_client()
    got = await _calls(client, [["x:Http/get", {"ids": ["singleton"]}, "x:Http"],
                                ["x:AddressBook/get", {"ids": ["singleton"]}, "x:AddressBook"]])
    cur = {tag: (args.get("list") or [{}])[0] for _, args, tag in got}
    patch: dict[str, dict[str, Any]] = {}
    http = cur["x:Http"]
    if "clientIpSource" in http:  # Stalwart 1.x
        want = {"clientIpSource": "xForwardedFor" if on else "peer"} | ({"forwardedHops": 1} if on else {})
        if diff := {k: v for k, v in want.items() if http.get(k) != v}:
            patch["x:Http"] = diff
    elif bool(http.get("useXForwarded")) != bool(on):
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
