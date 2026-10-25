"""Stalwart's calendar, contacts and directory settings (core/store_groupware.py): the reconcile
writes only what differs, keeps larger limits and custom names an admin set, takes the directory
permissions out of the default user role, and matches provision-groupware.json. Runs against a
fake management API; tests/test_stalwart_dav.py checks a real Stalwart."""

import json
from pathlib import Path

from mailr.core import store_groupware as sg

PLAN = Path(__file__).resolve().parents[2] / "deploy" / "stalwart" / "provision-groupware.json"

STOCK = {
    "x:CalendarScheduling": {"enable": True, "httpRsvpEnable": True, "maxRecipients": 100},
    "x:CalendarAlarm": {"enable": True, "allowExternalRcpts": False},
    "x:WebDav": {"enableAssistedDiscovery": True, "maxResults": 2000},
    "x:Calendar": {"defaultDisplayName": "Stalwart Calendar", "maxAttendees": 20},
    "x:AddressBook": {"defaultDisplayName": "Stalwart Address Book"},
    "x:Sharing": {"allowDirectoryQueries": False, "maxShares": 10},
}


class FakeMgmt:
    def __init__(self, singletons, role_perms):
        self.s = {k: dict(v) for k, v in singletons.items()}
        self.role = dict(role_perms)
        self.writes: list[str] = []

    async def call(self, calls, using=None):
        out = []
        for method, args, tag in calls:
            obj, _, verb = method.partition("/")
            if obj == "x:Authentication":
                out.append([method, {"list": [{"id": "singleton", "defaultUserRoleIds": {"b": True}}]}, tag])
            elif obj == "x:Role" and verb == "get":
                out.append([method, {"list": [{"id": "b", "enabledPermissions": dict(self.role)}]}, tag])
            elif obj == "x:Role" and verb == "set":
                for k in args["update"]["b"]:
                    self.role.pop(k.split("/", 1)[1], None)
                self.writes.append("x:Role")
                out.append([method, {"updated": {"b": None}}, tag])
            elif obj == "x:Action":
                self.writes += [a["@type"] for a in args["create"].values()]
                out.append([method, {"created": {}}, tag])
            elif verb == "get":
                out.append([method, {"list": [{"id": "singleton", **self.s[obj]}]}, tag])
            else:
                self.s[obj].update(args["update"]["singleton"])
                self.writes.append(obj)
                out.append([method, {"updated": {"singleton": None}}, tag])
        return out


def test_plan_file_matches_the_reconcile():
    plan = json.loads(PLAN.read_text())
    got = {m.split("/")[0]: a["update"]["singleton"] for m, a, _ in plan["methodCalls"]}
    assert got == sg.wanted_singletons()
    assert len(plan["methodCalls"]) <= 16  # Stalwart's maxMethodCalls


async def test_reconcile_fixes_a_stock_store_once():
    perms = {p: True for p in sg.DIRECTORY_PERMISSIONS} | {"davCalPropFind": True, "jmapPrincipalCreate": True}
    m = FakeMgmt(STOCK, perms)
    changed = await sg.ensure_groupware_settings(m)
    assert m.s["x:CalendarScheduling"]["enable"] is False and m.s["x:CalendarScheduling"]["httpRsvpEnable"] is False
    assert m.s["x:CalendarAlarm"]["enable"] is False
    assert m.s["x:WebDav"]["maxResults"] == 50000
    assert m.s["x:Calendar"] == {"defaultDisplayName": "Calendar", "maxAttendees": 100}
    assert m.s["x:AddressBook"]["defaultDisplayName"] == "Contacts"
    assert m.s["x:Sharing"]["maxShares"] == 50
    assert set(m.role) == {"davCalPropFind", "jmapPrincipalCreate"}
    assert "ReloadSettings" in m.writes and "InvalidateCaches" in m.writes
    assert any(c.startswith("x:Role[b]") for c in changed)
    m.writes.clear()
    assert await sg.ensure_groupware_settings(m) == [] and m.writes == []


async def test_reconcile_keeps_what_an_admin_chose():
    custom = {k: dict(v) for k, v in STOCK.items()}
    custom["x:WebDav"]["maxResults"] = 100000
    custom["x:Sharing"]["maxShares"] = 200
    custom["x:Calendar"].update(defaultDisplayName="Family calendar", maxAttendees=500)
    m = FakeMgmt(custom, {"davCalPropFind": True})
    changed = await sg.ensure_groupware_settings(m)
    assert m.s["x:WebDav"]["maxResults"] == 100000 and m.s["x:Sharing"]["maxShares"] == 200
    assert m.s["x:Calendar"] == {"defaultDisplayName": "Family calendar", "maxAttendees": 500}
    assert "InvalidateCaches" not in m.writes  # the role had nothing to remove
    assert not any("x:WebDav" in c or "x:Calendar." in c for c in changed)
