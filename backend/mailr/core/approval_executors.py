"""Executors for approved agent actions.

Each runs with the user's full rights on exactly the targets fixed when the agent asked.
Sends and calendar actions register their executors next to their own code
(core/sendgate.py, calendar/routes.py).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import actions as act
from mailr.core import (
    permissions,
    screener,  # noqa: F401  registers the mail.screener executor
)
from mailr.core.sendgate import _SystemPrincipal
from mailr.core.store import user_store
from mailr.models import User


async def _mail_action(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("op") in ("block", "unblock"):
        from mailr.api.org_routes import set_blocked

        await set_blocked(db, user.id, str(payload["email"]), payload["op"] == "block")
        return {"ok": True, payload["op"]: payload["email"]}
    if payload.get("op") == "cleanup":
        return await _cleanup_plan(db, user, payload)
    if payload.get("op") == "review":
        from mailr.agent.review import apply_plan

        return await apply_plan(db, user, payload)
    # Acts as the requesting token: its connector limits and local-only flag still apply.
    sp = await permissions.requester(db, user, payload)
    msgs = await act.messages_for(db, sp, msg_ids=[int(i) for i in payload.get("message_ids") or []])
    store = await user_store(db, user)
    res = await act.apply(act.ActionContext(db, store, sp), msgs, payload["action"], payload.get("args") or {})
    return {"affected": res["affected"]}


async def _cleanup_plan(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    """An approved cleanup plan (agent/cleanup_tools.py propose_cleanup): the action runs on
    exactly the conversations shown on the card, as a bulk job with progress and Undo
    (GET /bulk/{job}). A token's connector limits and local-only flag still apply in the job."""
    from mailr.core.jobs import enqueue_returning

    ids = [int(i) for i in payload.get("conversation_ids") or []]
    if not ids:
        return {"affected": 0}
    job_payload = {"ids": ids, "action": payload["action"], "args": payload.get("args") or {}, "done": 0,
                   "total": len(ids)}
    if payload.get(permissions.REQUESTER_KEY) is not None:
        job_payload[permissions.REQUESTER_KEY] = payload[permissions.REQUESTER_KEY]
    job = await enqueue_returning(db, "bulk.conversations", job_payload, user_id=user.id)
    await db.commit()
    return {"job": job, "conversations": len(ids), "title": f"Cleanup started: {len(ids)} conversations"}


for _cls in ("mail.label", "mail.trash", "mail.spam", "mail.delete"):
    permissions.executor(_cls)(_mail_action)


@permissions.executor("mail.draft")
async def _save_draft(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    """An agent's draft write the user approved (agent/draft_tools.py _draft_gate): saves exactly the
    payload shown on the card, as the requesting token when a token asked."""
    if payload.get("op") == "brief":
        # A brief rewrite the user approved (assistant/tools.py update_brief).
        from mailr.assistant.tools import apply_brief_change
        from mailr.models import AssistantRun

        run = await db.get(AssistantRun, int(payload.get("run_id") or 0))
        if run is None or run.user_id != user.id:
            raise RuntimeError("the brief no longer exists")
        what = apply_brief_change(run, payload)
        await db.commit()
        return {"title": what, "conversation_id": run.conversation_id}
    if payload.get("op") != "save" or not isinstance(payload.get("payload"), dict):
        raise RuntimeError("this approval has nothing to save; ask the agent to write the draft again")
    from mailr.core import live
    from mailr.core.drafts import save_draft

    sp = await permissions.requester(db, user, payload)
    did = payload.get("draft_id")
    # Made from the version the agent saw: what the user typed since then is merged in, never lost.
    try:
        m = await save_draft(db, sp, payload["payload"], int(did) if did else None,
                             base_version=payload.get("base_version"), by=payload.get("by"))
    except live.LiveConflict as e:
        raise RuntimeError(f"{e.body()['detail']} Ask the agent to make the change again.") from None
    await db.commit()
    return {"draft_id": m.id, "conversation_id": m.conversation_id, "title": "Draft saved"}


@permissions.executor("mail.unsubscribe")
async def _unsubscribe(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException

    from mailr.api.mail_read import load_message
    from mailr.core import unsubscribe as unsub

    # The message must still be visible to whoever asked (a token's limits apply); the send
    # itself was approved by the user, so it goes out with the user's rights.
    try:
        m = await load_message(db, await permissions.requester(db, user, payload), int(payload["message_id"]))
    except HTTPException:
        return {"error": "message no longer exists"}
    pl = await unsub.plan(db, user, m)
    if pl["method"] not in ("mailto", "one_click"):
        return {"error": pl.get("reason") or "this message has no usable unsubscribe method"}
    result = await unsub.execute(db, _SystemPrincipal(user), m, pl)
    if result.get("ok") and payload.get("subscription_key"):
        # Asked from the subscriptions tools (agent/cleanup_tools.py): the page shows it unsubscribed.
        from datetime import UTC, datetime

        from mailr.core import mailgroups as mg

        st = await mg.state(db, user.id, mg.SUB, str(payload["subscription_key"]))
        st.unsubscribed_at, st.unsubscribe_method = datetime.now(UTC), pl["method"]
        await db.commit()
    return result


@permissions.executor("rules.write")
async def _rules_write(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    from mailr.api.org_routes import apply_rule_op

    return await apply_rule_op(db, user, payload)


# Calendar and task executors (calendar.create/update/delete, calendar.rsvp,
# tasks.create/update/complete/delete; moves are updates with a new calendar/list) live next to
# the calendar code and register on import. Importing here makes the worker, which runs
# approved actions, register them at start-up as well.
import mailr.calendar.routes  # noqa: E402,F401
