"""Background jobs of the Subscriptions and Clean up pages (core/mailgroups.py).

subscriptions.probe   reads the headers of each subscription's newest message whose one-click
                      support is unknown (mail stored before Message.unsub_method), so the page
                      can say which subscriptions unsubscribe in one click.
cleanup.suggest       asks the user's classify model for a category of each uncategorised group.
                      The model is the one the user's access policy allows (intel/llm.resolve,
                      core/access.py); local-only mail goes only to a local model. It runs
                      off-hours (01:00 to 06:00 in the user's time zone) unless the user asked
                      for now, and it only stores suggestions: the user applies one or not.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import not_, select, update

from mailr.core import mailgroups as mg
from mailr.core.jobs import Retry, handler
from mailr.db import session_scope
from mailr.models import Job, Message, User

log = logging.getLogger("mailr.mailgroups")

# Messages the model reads per group, and how long a suggestion stays fresh (a later run skips it).
SAMPLE = 3
FRESH = timedelta(days=7)


def _principal(user: User):
    from mailr.core.auth import ALL_SCOPES, Principal

    return Principal(user=user, scopes=set(ALL_SCOPES) - {"admin"})


@handler("subscriptions.probe")
async def probe(payload: dict[str, Any], ctx) -> None:
    from mailr.core import unsubscribe as unsub
    from mailr.models import light_message

    async with session_scope() as db:
        user = await db.get(User, ctx.user_id)
        if user is None:
            return
        ids = await mg.unknown_methods(db, _principal(user))
    for i in range(0, len(ids), 20):
        async with session_scope() as db:
            user = await db.get(User, ctx.user_id)
            msgs = (await db.execute(select(Message).options(*light_message())
                                     .where(Message.id.in_(ids[i:i + 20])))).scalars().all()
            for m in msgs:
                await unsub.plan(db, user, m)
                if m.unsub_method == "unknown":
                    # Not readable from the store: the indexed header is all there is, a web link.
                    m.unsub_method = "link"


async def in_quiet_hours(db, user: User) -> tuple[bool, datetime]:
    from mailr.api.mailgroup_routes import QUIET_END, QUIET_START, next_quiet_start
    from mailr.calendar import tz as tzmod

    local = datetime.now(UTC).astimezone(await tzmod.user_zone(db, user))
    return QUIET_START <= local.hour < QUIET_END, await next_quiet_start(db, user)


@handler("cleanup.suggest")
async def suggest(payload: dict[str, Any], ctx) -> None:
    from mailr.intel import categories, llm

    by = payload.get("by") or "sender"
    sc = mg.Scope(**{**(payload.get("scope") or {}), "uncategorised": True})
    async with session_scope() as db:
        user = await db.get(User, ctx.user_id)
        if user is None:
            return
        if not payload.get("now"):
            quiet, start = await in_quiet_hours(db, user)
            if not quiet:
                raise Retry(delay=max(60.0, (start - datetime.now(UTC)).total_seconds()))
        # The access policy may have changed since the user asked.
        await llm.resolve(db, user, "classify", sensitive=False, probe=True)
        res = await mg.groups(db, _principal(user), by, sc, limit=min(int(payload.get("groups") or 100), 200))
        keys = [it["key"] for it in res["items"]]
        fresh = await mg._states(db, user.id, by, keys)
    cutoff = datetime.now(UTC) - FRESH
    todo = [k for k in keys if not (fresh.get(k) and fresh[k].suggested_at and fresh[k].suggested_at > cutoff)]
    done = 0
    for key in todo:
        async with session_scope() as db:
            user = await db.get(User, ctx.user_id)
            p = _principal(user)
            if not payload.get("now"):
                quiet, start = await in_quiet_hours(db, user)
                if not quiet:
                    # Morning: the rest waits for the next night (done groups are fresh and skipped).
                    raise Retry(delay=max(60.0, (start - datetime.now(UTC)).total_seconds()))
            r = await mg.roles(db, user.id)
            prot = mg.protected(user.id, sc)
            msgs = (await db.execute(select(Message).where(
                *mg.base_conditions(p, r), *mg.scope_conditions(by, sc, r), mg.key_match(by, key),
                *([not_(prot)] if prot is not None else []))
                .order_by(Message.date.desc().nulls_last()).limit(SAMPLE))).scalars().all()
            votes: list[tuple[str, float, str]] = []
            for m in msgs:
                g = await categories.llm_guess(db, user, m, sensitive=bool(m.local_only))
                if g is not None:
                    votes.append((g.category, g.confidence, g.reason))
            if votes:
                top, _n = Counter(v[0] for v in votes).most_common(1)[0]
                conf = sum(c for cat, c, _r in votes if cat == top) / len(msgs) if msgs else 0.0
                st = await mg.state(db, user.id, by, key)
                st.suggested_category, st.suggestion_confidence = top, round(conf, 3)
                st.suggestion_model = next(r for cat, _c, r in votes if cat == top)[:200]
                st.suggestion_samples, st.suggested_at = len(msgs), datetime.now(UTC)
            done += 1
            await db.execute(update(Job).where(Job.id == ctx.job_id).values(progress=done))
    log.info("cleanup.suggest user %s: %d groups read", ctx.user_id, done)
