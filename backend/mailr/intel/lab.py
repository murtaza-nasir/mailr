"""The decisions lab: every triage decision run on chosen emails, side by side for the rules and
each model that can sort mail (decision models, chat models), with the answer, its confidence and the time it
took. Nothing is stored or changed; it is for checking what the models would decide.

Decisions: category, signals (needs reply / asks a question / deadline), the main action when an
email offers several, and an optional yes/no question (what a standing order would ask).
Local-only mail never goes to a hosted model: those runs report "local-only" instead.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import outbound
from mailr.intel import llm
from mailr.models import LabelDecision, Message, ModelEndpoint, User

MAX_MESSAGES = 15


def _ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


async def endpoints(db: AsyncSession, user: User | None = None) -> list[ModelEndpoint]:
    """The endpoints the lab runs. For a user under an access policy (core/access.py), only the
    classify endpoints the policy allows: the lab never offers "all endpoints" to them, and becomes
    a place to see how their own instructions and labels sort."""
    from mailr.core import access

    eps = (await db.execute(select(ModelEndpoint).where(ModelEndpoint.enabled.is_(True))
                            .order_by(ModelEndpoint.id))).scalars().all()
    acc = await access.for_user(db, user)
    if not acc.unrestricted:
        return [e for e in eps if llm.can(e, "classify") and e.id in acc.allowed.get("classify", {})]
    # Endpoints set up to sort or chat; an embedding-only endpoint cannot answer these.
    return [e for e in eps if llm.can(e, "classify") and (e.defaults.get("classify") or e.defaults.get("chat")
                                                         or e.adapter == "jev")]


async def _stored(db: AsyncSession, m: Message) -> dict[str, Any]:
    rows = (await db.execute(select(LabelDecision).where(
        LabelDecision.message_id == m.id, LabelDecision.kind.in_(("category", "importance", "smart")))
        .order_by(LabelDecision.id))).scalars().all()
    out: dict[str, Any] = {"signals": []}
    for d in rows:  # the latest of each kind wins
        if d.kind == "smart":
            out["signals"].append({"name": d.target, "source": d.source, "reason": d.reason})
        else:
            out[d.kind] = {"value": d.target if d.kind == "category" else bool(d.value), "source": d.source,
                           "confidence": d.confidence, "reason": d.reason}
    return out


async def _run(fn, *a, **kw) -> tuple[Any, int, str | None]:
    t0 = time.monotonic()
    try:
        return await fn(*a, **kw), _ms(t0), None
    except llm.LocalOnlyViolation:
        return None, _ms(t0), "local-only mail never goes to a hosted model"
    except llm.NoModel:
        return None, _ms(t0), "this endpoint cannot sort mail"
    except Exception as e:  # noqa: BLE001
        return None, _ms(t0), f"{type(e).__name__}: {str(e)[:160]}"


async def _deadline_gate(db: AsyncSession, user: User, m: Message, s, category: str | None,
                         deadline_date: str | None) -> str | None:
    """Why the rules would drop a deadline found in this email (intel/deadlines.py), or None. A sample
    email has no history, so only its wording, bulk headers and category count."""
    from mailr.intel import deadlines

    if m.id:
        return await deadlines.blocked(db, user, m, s, deadline_date)
    if deadlines.muted(user, m.from_email or ""):
        return "You said mail from this sender has no deadlines"
    from mailr.calendar.tz import user_zone

    return deadlines.period_reason(m, deadline_date, await user_zone(db, user)) \
        or deadlines.marketing_reason(m, s, category)


async def _evaluate(db: AsyncSession, user: User, m: Message, raw: bytes | None, eps: list[ModelEndpoint],
                    question: str, stored: dict[str, Any] | None) -> dict[str, Any]:
    """One email through every decision: the rules, then each endpoint (pinned in turn)."""
    from mailr.assistant import decide as decider
    from mailr.core.visibility import touches_local_only
    from mailr.intel import actions, categories, classify, features

    s = await features.compute(db, m, raw)
    sensitive = bool(m.id) and await touches_local_only(db, user.id, message_ids=[m.id])
    h = categories.heuristic(m, s)
    lg = await categories.learned(db, m, s) if m.id else None
    from mailr.calendar.tz import user_zone

    zone = await user_zone(db, user)
    hs = classify.heuristic_smart(m, s, zone)
    if hs["deadline"] and await _deadline_gate(db, user, m, s, h.category, hs["deadline_date"]):
        hs["deadline"] = None
    _, cands = actions.candidates(actions._html(raw))
    code = actions.verification_code(m.subject or "", m.body_text or "")
    item: dict[str, Any] = {
        "message": {"id": m.id, "conversation_id": m.conversation_id, "subject": m.subject, "from": m.from_name or m.from_email,
                    "date": m.date.isoformat() if m.date else None, "local_only": sensitive,
                    "bulk": features.is_bulk(s), "to_me": bool(s.values.get("direct_to_me") or s.values.get("cc_me"))},
        "stored": stored or {"signals": []},
        "rules": {
            "category": {"value": h.category, "confidence": h.confidence, "reason": h.reason},
            "learned": {"value": lg.category, "confidence": lg.confidence, "reason": lg.reason} if lg else None,
            "signals": {"needs_reply": bool(hs["needs_reply"]), "waiting_on": bool(hs["waiting_on"]),
                        "deadline": hs["deadline"], "reason": hs["reason"]},
            "actions": ([{"kind": "code", "label": f"Copy code {code}"}] if code else [])
            + [{"kind": c["kind"], "label": c["label"]} for c in cands],
        },
        "models": {},
    }
    for ep in eps:
        if not outbound.allowed(sensitive, ep):
            skip = {"error": "local-only mail never goes to a hosted model", "ms": 0}
            item["models"][ep.name] = {"category": skip, "signals": skip}
            continue
        tok = llm.FORCE_ENDPOINT.set(ep.name)
        try:
            res: dict[str, Any] = {}
            g, ms, err = await _run(categories.llm_guess, db, user, m, sensitive=sensitive)
            res["category"] = {"value": g.category if g else None, "confidence": g.confidence if g else None,
                               "reason": g.reason if g else None, "ms": ms, "error": err}
            sm, ms, err = await _run(classify.llm_smart, db, user, m, sensitive=sensitive, category=h.category,
                                     bulk=features.is_bulk(s))
            res["signals"] = {**({k: sm.get(k) for k in ("needs_reply", "asks_question", "request", "deadline")}
                                 if sm else {}), "ms": ms, "error": err}
            dropped = sm and sm.get("deadline") and await _deadline_gate(
                db, user, m, s, h.category, sm.get("deadline_date") or classify.parse_deadline(sm["deadline"], m.date, zone))
            if dropped:
                # What the user would see: a marketing date is dropped whatever the model answered.
                res["signals"].update(deadline=None, deadline_model=sm["deadline"], deadline_dropped=dropped)
            if len(cands) > 1:
                ch, ms, err = await _run(actions._model_choice, db, user, m, cands, sensitive=sensitive)
                res["action"] = {"value": (ch or {}).get("kind") if ch else ("none" if ch == {} else None),
                                 "ms": ms, "error": err,
                                 "note": None if ep.adapter == "jev" else "only Jev chooses between actions; the rules' order is used"}
            if question.strip():
                a, ms, err = await _run(decider.decide, db, user, m, question.strip())
                if a is not None and a.source == "none":  # the model gave no usable answer
                    a, err = None, err or (a.reason or "no answer")
                res["question"] = {**(a.as_dict() if a else {}), "ms": ms, "error": err}
        finally:
            llm.FORCE_ENDPOINT.reset(tok)
        item["models"][ep.name] = res
    return item


def _out(eps: list[ModelEndpoint], question: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"endpoints": [{"name": e.name, "adapter": e.adapter, "locality": e.locality,
                           "model": e.defaults.get("classify") or (e.models[0] if e.models else "")} for e in eps],
            "question": question.strip() or None, "items": items}


async def run(db: AsyncSession, user: User, ids: list[int], *, endpoint_names: list[str] | None,
              question: str = "") -> dict[str, Any]:
    from mailr.core.store import user_store

    eps = [e for e in await endpoints(db, user) if not endpoint_names or e.name in endpoint_names]
    store = await user_store(db, user)
    msgs = (await db.execute(select(Message).where(Message.user_id == user.id, Message.id.in_(ids[:MAX_MESSAGES]),
                                                   Message.deleted.is_(False)))).scalars().all()
    items = []
    for m in sorted(msgs, key=lambda x: ids.index(x.id)):
        try:
            raw = await store.get_raw(m)
        except Exception:  # noqa: BLE001
            raw = None
        items.append(await _evaluate(db, user, m, raw, eps, question, await _stored(db, m)))
    return _out(eps, question, items)


async def run_samples(db: AsyncSession, user: User, *, endpoint_names: list[str] | None,
                      question: str = "") -> dict[str, Any]:
    """The built-in sample emails (intel/lab_samples.py), each with what a good answer is. They are
    built in memory, addressed to the user, and never stored."""
    from mailr.core.mime import parse
    from mailr.intel.lab_samples import SAMPLES, raw_for

    eps = [e for e in await endpoints(db, user) if not endpoint_names or e.name in endpoint_names]
    items = []
    for smp in SAMPLES:
        raw = raw_for(smp, user.email)
        p = parse(raw)
        m = Message(id=None, user_id=user.id, conversation_id=None, subject=p.subject, from_email=p.from_email,
                    from_name=p.from_name, to=p.to, cc=p.cc, body_text=p.body_text, snippet=p.snippet,
                    list_id=p.list_id, list_unsubscribe=p.list_unsubscribe, date=p.date, sent_by_me=False,
                    draft=False, deleted=False, label_ids=[], delivered_to=p.delivered_to, attachments=[],
                    has_attachments=False, local_only=False, category=None, auth_results={})
        item = await _evaluate(db, user, m, raw, eps, question, None)
        item["sample"] = {"id": smp["id"], "tests": smp["tests"], "expected": smp["expected"]}
        items.append(item)
    return _out(eps, question, items)
