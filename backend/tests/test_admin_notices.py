"""Admin changes reach the audit log of the person they affect (core/audit.notice), so a
member's own activity (GET /audit) shows them in plain words."""

from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import AuditLog, Connector


async def _notices(user_id: int) -> list[AuditLog]:
    async with sessionmaker()() as db:
        return list((await db.execute(select(AuditLog).where(AuditLog.user_id == user_id,
                                                             AuditLog.action.like("admin_notice.%"))
                                      .order_by(AuditLog.id))).scalars())


async def test_member_sees_admin_changes_to_their_account(admin_client, client, user):
    r = await admin_client.patch(f"/api/v1/admin/users/{user.id}", json={"name": "Robin Example", "role": "admin"})
    assert r.status_code == 200, r.text
    r = await admin_client.patch(f"/api/v1/admin/users/{user.id}", json={"role": "user"})
    assert r.status_code == 200
    # The member's own audit list carries them, with the affected user's id and a plain sentence.
    items = (await client.get("/api/v1/audit")).json()["items"]
    mine = [i for i in items if i["action"].startswith("admin_notice.")]
    texts = [i["inputs"]["description"] for i in reversed(mine)]
    assert texts == ["Your administrator changed your name to Robin Example.",
                     "Your administrator made you an administrator.",
                     "Your administrator removed your administrator role."]
    assert all(i["actor"] == "admin" and i["inputs"]["by"] for i in mine)


async def test_password_reset_and_disable_are_recorded(admin_client, user):
    r = await admin_client.post(f"/api/v1/admin/users/{user.id}/password", json={"password": "a-new-password-42"})
    assert r.status_code == 200
    r = await admin_client.patch(f"/api/v1/admin/users/{user.id}", json={"disabled": True})
    assert r.status_code == 200
    r = await admin_client.patch(f"/api/v1/admin/users/{user.id}", json={"disabled": False})
    got = [(n.action, n.inputs["description"]) for n in await _notices(user.id)]
    assert got[0][0] == "admin_notice.password" and "reset your password" in got[0][1]
    assert ("admin_notice.disabled", "Your administrator turned off your account.") in got
    assert ("admin_notice.disabled", "Your administrator turned your account back on.") in got
    assert all("a-new-password-42" not in str(n.inputs) for n in await _notices(user.id))


async def test_group_and_policy_changes_reach_the_members(admin_client, user):
    r = await admin_client.put(f"/api/v1/admin/users/{user.id}/groups", json={"groups": ["household-test-x"]})
    assert r.status_code == 200, r.text
    r = await admin_client.post("/api/v1/admin/access/policies",
                                json={"group": "household-test-x", "description": "", "features": {}, "models": {}})
    assert r.status_code in (200, 201), r.text
    got = [(n.action, n.inputs.get("group"), n.inputs["description"]) for n in await _notices(user.id)]
    assert any(a == "admin_notice.groups" and "household-test-x" in d for a, _, d in got)
    assert any(a == "admin_notice.policy" and g == "household-test-x" for a, g, _ in got)


async def test_local_only_domain_policy_tells_the_account_owner(admin_client, user):
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="imap", name="University", mode="mirror", enabled=False, status="paused",
                      config={"email": "robin@uni-notice.example"}, data_class="normal")
        db.add(c)
        await db.commit()
    r = await admin_client.put("/api/v1/admin/policies", json={"local_only_domains": ["uni-notice.example"]})
    assert r.status_code == 200, r.text
    n = [x for x in await _notices(user.id) if x.action == "admin_notice.account_local_only"]
    assert len(n) == 1 and "University" in n[0].inputs["description"]
    await admin_client.put("/api/v1/admin/policies", json={"local_only_domains": []})


async def test_an_admins_change_to_their_own_account_is_not_repeated(admin_client):
    me = (await admin_client.get("/api/v1/me")).json()["user"]
    await admin_client.patch(f"/api/v1/admin/users/{me['id']}", json={"name": "Ada Admin"})
    assert await _notices(me["id"]) == []
