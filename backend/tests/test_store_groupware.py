"""Stalwart's calendar, contacts and directory settings (core/store_groupware.py): the reconcile
writes only what differs, keeps larger limits and custom names an admin set, takes the directory
permissions out of the default user role, and matches provision-groupware.json. Runs against a
fake management API; tests/test_stalwart_dav.py checks a real Stalwart."""

import json
from pathlib import Path

from mailr.core import store_groupware as sg

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "stalwart"
PLAN = DEPLOY / "provision-groupware.json"
PLAN_V1 = DEPLOY / "provision-groupware.v1.json"

STOCK = {
    "x:CalendarScheduling": {"enable": True, "httpRsvpEnable": True, "maxRecipients": 100},
    "x:CalendarAlarm": {"enable": True, "allowExternalRcpts": False},
    "x:WebDav": {"enableAssistedDiscovery": True, "maxResults": 2000},
    "x:Calendar": {"defaultDisplayName": "Stalwart Calendar", "maxAttendees": 20},
    "x:AddressBook": {"defaultDisplayName": "Stalwart Address Book"},
    "x:Sharing": {"allowDirectoryQueries": False, "maxShares": 10},
    "x:Http": {"useXForwarded": False},
    "x:Search": {"indexEmail": True},
}
# Stalwart 1.x: x:Http has clientIpSource, and the search index settings matter.
STOCK_V1 = {**STOCK, "x:Http": {"clientIpSource": "peer", "forwardedHops": 1},
            "x:Search": {"indexEmail": True, "indexEmailFields": {"body": True}, "indexContacts": True}}


class FakeMgmt:
    def __init__(self, singletons, role_perms):
        self.s = {k: dict(v) for k, v in singletons.items()}
        self.role = dict(role_perms)
        self.writes: list[str] = []
        self.tasks: list[dict] = []

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
            elif obj in ("x:Action", "x:Task"):
                self.writes += [a["@type"] for a in args["create"].values()]
                if obj == "x:Task":
                    self.tasks += list(args["create"].values())
                out.append([method, {"created": {}}, tag])
            elif verb == "get":
                out.append([method, {"list": [{"id": "singleton", **self.s[obj]}]}, tag])
            else:
                self.s[obj].update(args["update"]["singleton"])
                self.writes.append(obj)
                out.append([method, {"updated": {"singleton": None}}, tag])
        return out


def test_plan_file_matches_the_reconcile():
    for path, v1 in ((PLAN, False), (PLAN_V1, True)):
        plan = json.loads(path.read_text())
        got = {m.split("/")[0]: a["update"]["singleton"] for m, a, _ in plan["methodCalls"]}
        assert got == sg.wanted_singletons(v1), path.name
        assert len(plan["methodCalls"]) <= 16  # Stalwart's maxMethodCalls


def test_v1_provisioning_differs_only_by_the_renames():
    """provision.v1.json is provision.json with Stalwart 1.0's field renames
    (docs/design/stalwart-1.0-migration.md, section c), and nothing else."""
    old = (DEPLOY / "provision.json").read_text()
    renamed = (old.replace('"tlsImplicit"', '"implicitTls"').replace('"enabled": false', '"enable": false')
               .replace("releases/latest/download/webui.zip", "releases/download/v2/webui.zip"))
    assert renamed != old
    assert (DEPLOY / "provision.v1.json").read_text() == renamed


async def test_search_index_off_only_on_stalwart_1():
    m = FakeMgmt(STOCK_V1, {})
    changed = await sg.ensure_groupware_settings(m)
    assert "x:Search.indexEmail" in changed and m.s["x:Search"]["indexEmail"] is False
    assert m.s["x:Search"]["indexEmailFields"] == {"body": True}  # empty would mean "every field"
    assert m.s["x:Search"]["indexContacts"] is True
    m.writes.clear()
    assert await sg.ensure_groupware_settings(m) == [] and m.writes == []
    assert "StoreMaintenance" not in m.writes
    m0 = FakeMgmt({**STOCK, "x:Search": {"indexEmail": True}}, {})
    assert not any(c.startswith("x:Search") for c in await sg.ensure_groupware_settings(m0))
    assert m0.s["x:Search"]["indexEmail"] is True


async def test_search_index_back_on_with_a_reindex_on_0_16():
    """A 0.16 store rolled back from 1.x carries indexEmail false: mailr turns it on and asks for a
    reindex of every account, once."""
    m = FakeMgmt({**STOCK, "x:Search": {"indexEmail": False}}, {})
    changed = await sg.ensure_groupware_settings(m)
    assert "x:Search.indexEmail" in changed and m.s["x:Search"]["indexEmail"] is True
    assert m.writes.count("StoreMaintenance") == 1 and m.tasks == [{"@type": "StoreMaintenance",
                                                                   "maintenanceType": "reindexAccounts"}]
    m.writes.clear()
    assert await sg.ensure_groupware_settings(m) == [] and m.writes == []


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
