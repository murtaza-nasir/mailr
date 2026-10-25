"""Every place that hands out a link to one approval uses the same form, <base>/approvals/<id>,
which the web app opens as the approvals drawer on that card (frontend router: /approvals/:id)."""

from datetime import UTC, datetime, timedelta

from mailr.agent import tools
from mailr.config import get_settings
from mailr.core import permissions, push
from mailr.db import sessionmaker
from mailr.models import PendingAction


def _expected(pid: int) -> str:
    return f"{get_settings().base_url.rstrip('/')}/approvals/{pid}"


async def _pending(user_id: int) -> PendingAction:
    async with sessionmaker()() as db:
        pa = PendingAction(user_id=user_id, subject="token:1", actor="Test agent", action="mail.trash",
                           preview={"title": "Move 1 message to Trash"},
                           expires_at=datetime.now(UTC) + timedelta(hours=1))
        db.add(pa)
        await db.commit()
        await db.refresh(pa)
        return pa


async def test_approval_links_agree(user, monkeypatch):
    pa = await _pending(user.id)
    url = _expected(pa.id)
    assert permissions.approval_url(pa.id) == url
    assert tools.approval_url(pa.id) == url
    assert tools.pending_result(pa).data["approval_url"] == url
    # REST routes that answer 202 carry the same id and link.
    out = permissions.pending_response(pa)
    assert out["approval_id"] == pa.id and out["approval_url"] == url and out["approval"]["id"] == pa.id

    # The push notification for the approval opens the same link.
    sent: list[dict] = []

    async def fan_out(db, uid, event, title, body, data, priority="default"):
        sent.append(data)
        return 1

    async def web(db, u, level, payload):
        sent.append(payload)

    monkeypatch.setattr(push, "fan_out", fan_out)
    monkeypatch.setattr(push, "_web", web)
    await push.push_approval({"pending_id": pa.id}, None)
    assert sent and all(d["click"] == url for d in sent)
    assert all(d["pending_id"] == pa.id for d in sent)
