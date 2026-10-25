"""Agent tools that configure mailr: filters, labels, settings, inbox layouts, signatures and the
assistant profile.

A few typed tools rather than one per endpoint, so a mid-size model picks them reliably:

    get_configuration   read the current state (overview or one section)
    manage_filter       create / update / delete / apply a filter      rules.write    (ask)
    manage_label        create / update (rename, nest, colour) / delete labels.write (assistant: allow)
                                                                        labels.delete  (ask)
    update_settings     change allow-listed user settings              prefs.write    (ask)
    manage_layout       activate / save / delete an inbox layout       prefs.write    (ask)
    update_signature    set an identity's signature                    prefs.write    (ask)
    update_profile      the "About you" profile                        prefs.write    (ask)

Every change goes through the permission engine and then through the same code as the REST
routes. A change that ran at once returns an `undo` request ({method, path, body}) that the web
client offers as "Undo", and notifies open clients so the UI refreshes. Sending addresses,
sign-in and security settings are never changed by an agent.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.agent.tools import ToolCtx, ToolError, ToolResult, _audit, _obj, gate, tool
from mailr.core import mdmail, permissions
from mailr.models import Identity, Label, Rule, User

CATEGORIES = ("primary", "promotions", "social", "updates", "forums", "purchases")
# key -> allowed values (tuple), or a type. Everything else (sign-in, security, privacy lookups,
# spam thresholds, sending) is not changeable by an agent.
SETTINGS: dict[str, Any] = {
    "reading_pane": ("none", "right", "below"),
    "density": ("default", "comfortable", "compact"),
    "theme": ("system", "light", "dark"),
    "conversation_view": bool,
    "undo_send_seconds": (5, 10, 20, 30),
    "importance_markers": bool,
    "keyboard_shortcuts": bool,
    "images": ("always", "ask", "never"),
    "send_and_archive": bool,
    "default_reply": ("reply", "reply_all"),
    "page_size": (25, 50, 100),
    "mail_notifications": ("off", "vip", "important", "primary", "all"),
    "notify_approvals": bool,
    "notify_assistant": bool,
    "notify_accounts": bool,
    "auto_advance": ("list", "newer", "older"),
    "categories": list,
    "inbox_type": str,
    "inbox_count": ("first_tab", "all"),  # ACT settings.inbox_count @assistant
    "llm_triage": bool,
    "archive_removes_current_label": bool,
    "archive_also_removes": "label_ids",
    "sweep_rules": "text",  # ACT settings.sweep_rules @assistant
}
SETTINGS_HELP = {
    "reading_pane": "none | right | below", "density": "default | comfortable | compact",
    "theme": "system | light | dark", "undo_send_seconds": "5 | 10 | 20 | 30", "images": "always | ask | never",
    "default_reply": "reply | reply_all", "page_size": "25 | 50 | 100",
    "mail_notifications": "which new mail notifies on every device: off | vip | important | primary | all",
    "notify_approvals": "notify about approvals and failed sends (true/false)",
    "notify_assistant": "notify about assistant messages (true/false)",
    "notify_accounts": "notify when a mail account password is required again (true/false)",
    "auto_advance": "list | newer | older",
    "categories": "inbox tabs, subset of " + ", ".join(CATEGORIES),
    "inbox_type": "default | simple | important_first | unread_first | starred_first | priority | multiple | layout:<id>",
    "inbox_count": "the sidebar Inbox number: first_tab (unread in the layout's first tab) | all (every unread inbox conversation)",
    "llm_triage": "use the model to sort new mail (true/false)",
    "archive_removes_current_label": "archiving from a label's list also removes that label (true/false)",
    "archive_also_removes": "label ids that every archive also removes, e.g. [19]",
    "sweep_rules": "the user's standing rules for bulk reviews (/sweep), plain text up to 2000 characters; "
                   "change them only as the user worded them",
}


def _undo(method: str, path: str, body: Any = None) -> dict[str, Any]:
    return {"method": method, "path": path, "body": body}


async def _notify(db: AsyncSession, user_id: int, entity: str) -> None:
    from mailr.core.events import notify

    await notify(db, user_id, {"type": "changes", "entity": entity})


def _sys(user: User):
    from mailr.core.sendgate import _SystemPrincipal

    return _SystemPrincipal(user)


async def _settings_of(db: AsyncSession, user: User) -> dict[str, Any]:
    from mailr.api.auth_routes import DEFAULT_SETTINGS

    await db.refresh(user)
    return {**DEFAULT_SETTINGS, **(user.settings or {})}


def clean_sweep_rules(v: Any) -> str:
    """The setting sweep_rules: plain text of at most 2000 characters (agent/review.py RULES_MAX)."""
    from mailr.agent.review import RULES_MAX

    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError("sweep_rules must be text")
    v = v.replace("\r\n", "\n").strip()
    if len(v) > RULES_MAX:
        raise ValueError(f"sweep_rules can have at most {RULES_MAX} characters")
    return v


def validate_settings(changes: dict[str, Any], custom_layout_ids: set[str]) -> dict[str, Any]:
    from mailr.core.layouts import PRESET_IDS

    if not isinstance(changes, dict) or not changes:
        raise ValueError("give the settings to change, e.g. {\"density\": \"compact\"}")
    out: dict[str, Any] = {}
    for k, v in changes.items():
        rule = SETTINGS.get(k)
        if rule is None:
            raise ValueError(f"{k!r} cannot be changed by the assistant. Changeable: {', '.join(SETTINGS)}")
        if rule is bool:
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
        elif rule is list:
            vals = [v] if isinstance(v, str) else list(v or [])
            bad = [x for x in vals if x not in CATEGORIES]
            if bad or not vals:
                raise ValueError(f"categories must be a non-empty subset of {', '.join(CATEGORIES)}")
            v = [c for c in CATEGORIES if c in vals]
        elif rule == "label_ids":
            # Only this user's non-system labels are ever removed (core/actions.py filters).
            vals = v if isinstance(v, list) else [v]
            if not all(isinstance(x, int) and not isinstance(x, bool) for x in vals):
                raise ValueError(f"{k} must be a list of label ids")
            v = sorted(set(vals))
        elif rule == "text":
            v = clean_sweep_rules(v)
        elif k == "inbox_type":
            if not (v in PRESET_IDS or (isinstance(v, str) and v.startswith("layout:") and v[7:] in custom_layout_ids)):
                raise ValueError(f"inbox_type must be one of {', '.join(PRESET_IDS)} or layout:<custom layout id>")
        elif isinstance(rule, tuple):
            if isinstance(rule[0], int):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise ValueError(f"{k} must be one of {rule}") from None
            if v not in rule:
                raise ValueError(f"{k} must be one of {', '.join(map(str, rule))}")
        out[k] = v
    return out


# ---------------------------------------------------------------- executors (shared with approvals)


async def config_op(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    """Run one configuration change with the user's rights. Returns {"result", "undo", "title"}."""
    from mailr.api import mail_write

    op = payload["op"]
    sp = _sys(user)
    if op == "label.create":
        body = mail_write.LabelIn(name=payload["name"], color=payload.get("color"),
                                  home_account_id=payload.get("home_account_id"))
        out = await mail_write.create_label(body=body, p=sp, db=db)
        await _notify(db, user.id, "label")
        return {"result": out, "undo": _undo("DELETE", f"/labels/{out['id']}"), "title": f"Created label “{out['name']}”"}
    if op == "label.update":
        lb = await db.get(Label, int(payload["label_id"]))
        if lb is None or lb.user_id != user.id:
            raise ValueError("label not found")
        before = {"name": lb.name, "color": lb.color, "show_in_list": lb.show_in_list}
        # A colour only when the request named one (on PATCH a null colour clears it).
        body = mail_write.LabelIn(name=payload.get("name"),
                                  **({"color": payload["color"]} if payload.get("color") else {}),
                                  show_in_list=payload.get("show_in_list"),
                                  show_in_message_list=payload.get("show_in_message_list"),
                                  **({"home_account_id": payload["home_account_id"]}
                                     if "home_account_id" in payload else {}))
        out = await mail_write.update_label(lid=lb.id, body=body, p=sp, db=db)
        await _notify(db, user.id, "label")
        return {"result": out, "undo": _undo("PATCH", f"/labels/{lb.id}",
                                             {k: v for k, v in before.items() if v or (k == "color" and payload.get("color"))}),
                "title": f"Updated label “{out['name']}”"}
    if op == "label.delete":
        lb = await db.get(Label, int(payload["label_id"]))
        if lb is None or lb.user_id != user.id:
            raise ValueError("label not found")
        if lb.role:
            raise ValueError("system labels cannot be deleted")
        name = lb.name
        await mail_write.delete_label(lid=lb.id, p=sp, db=db)
        await _notify(db, user.id, "label")
        return {"result": {"deleted": name}, "undo": None, "title": f"Deleted label “{name}”"}
    if op == "settings":
        before = await _settings_of(db, user)
        from mailr.core import usersettings

        await usersettings.patch(db, user.id, payload["changes"])
        await db.commit()
        await _notify(db, user.id, "settings")
        await db.commit()
        prev = {k: before.get(k) for k in payload["changes"]}
        return {"result": {"changed": payload["changes"], "previous": prev}, "undo": _undo("PATCH", "/me/settings", prev),
                "title": "Changed settings: " + ", ".join(f"{k} → {v}" for k, v in payload["changes"].items())}
    if op == "layouts":
        from mailr.api import layout_routes

        settings = await _settings_of(db, user)
        prev = {"layouts": list(settings.get("inbox_layouts") or []),
                "active": settings.get("inbox_type") if not str(settings.get("inbox_type", "")).startswith("layout:")
                else settings["inbox_type"][7:]}
        body = layout_routes.LayoutsIn(layouts=payload["layouts"], active=payload.get("active"))
        out = await layout_routes.put_layouts(body=body, p=sp, db=db)
        await _notify(db, user.id, "layout")
        await _notify(db, user.id, "settings")
        await db.commit()
        return {"result": {"active": out["active"], "layouts": [x["id"] for x in out["layouts"]]},
                "undo": _undo("PUT", "/layouts", prev), "title": payload.get("title") or "Updated inbox layouts"}
    if op == "signature":
        from mailr.core.compose import clean_html

        i = await db.get(Identity, int(payload["identity_id"]))
        if i is None or i.user_id != user.id:
            raise ValueError("identity not found")
        prev = {"email": i.email, "name": i.name, "reply_to": i.reply_to, "signature_html": i.signature_html,
                "is_default": i.is_default, "connector_id": i.connector_id, "receives": list(i.receives or [])}
        i.signature_html = clean_html(payload["signature_html"])
        await db.commit()
        await _notify(db, user.id, "identity")
        await db.commit()
        return {"result": {"identity_id": i.id, "email": i.email}, "undo": _undo("PATCH", f"/identities/{i.id}", prev),
                "title": f"Updated the signature for {i.email}"}
    if op == "profile":
        from mailr.knowledge import profile

        row = await profile.get(db, user.id)
        fields = dict(payload["fields"])
        prev = {k: (profile.zone_name(user, row.data) if k == "timezone" else (row.data or {}).get(k, "")) for k in fields}
        if "timezone" in fields:  # the account's one time zone
            await profile.set_zone(db, user.id, fields.pop("timezone"))
        row.data = {k: v for k, v in {**(row.data or {}), **fields}.items() if k != "timezone"}
        await db.commit()
        await _notify(db, user.id, "assistant_profile")
        await db.commit()
        return {"result": {"changed": sorted(payload["fields"])}, "undo": _undo("PUT", "/assistant/profile", prev),
                "title": "Updated your profile: " + ", ".join(sorted(payload["fields"]))}
    raise ValueError(f"unknown configuration operation {op}")


async def _executor(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        res = await config_op(db, user, payload)
    except (ValueError, HTTPException) as e:
        return {"error": str(getattr(e, "detail", e))}
    return {"title": res["title"], **(res["result"] if isinstance(res["result"], dict) else {})}


for _cls in ("labels.write", "labels.delete", "prefs.write"):
    permissions.executor(_cls)(_executor)


async def _run(ctx: ToolCtx, action: str, payload: dict[str, Any], preview: dict[str, Any], target: str = "") -> ToolResult:
    pending = await gate(ctx, action, target=target or payload["op"], payload=payload, preview=preview)
    if pending is not None:
        await _audit(ctx, f"agent.{payload['op']}", target, payload, {"pending": pending.data["approval_id"]})
        await ctx.db.commit()
        return pending
    try:
        res = await config_op(ctx.db, ctx.p.user, payload)
    except ValueError as e:
        raise ToolError(str(e)) from None
    await _audit(ctx, f"agent.{payload['op']}", target, payload, {"title": res["title"]})
    await ctx.db.commit()
    out = res["result"] if isinstance(res["result"], dict) else {"result": res["result"]}
    return ToolResult({"status": "done", "summary": res["title"], **json.loads(json.dumps(out, default=str))},
                      events=[{"type": "config", "title": res["title"], "undo": res["undo"]}])


# ---------------------------------------------------------------- read


@tool("get_configuration",
      "Read how mailr is set up: section overview (default), settings (with allowed values), labels, filters, "
      "layouts, identities (send-as addresses and signatures) or skills.",
      _obj({"section": {"type": "string",
                        "enum": ["overview", "settings", "labels", "filters", "layouts", "identities", "skills"]}}))
async def get_configuration(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.org_routes import rule_out
    from mailr.core import layouts

    sec = args.get("section") or "overview"
    db, uid = ctx.db, ctx.p.user_id
    user = await db.get(User, uid)
    settings = await _settings_of(db, user)
    labels = list((await db.execute(select(Label).where(Label.user_id == uid).order_by(Label.name))).scalars())
    lname = {lb.id: lb.name for lb in labels}
    out: dict[str, Any] = {}
    if sec in ("overview", "settings"):
        out["settings"] = {k: settings.get(k) for k in SETTINGS}
        if sec == "settings":
            out["allowed_values"] = SETTINGS_HELP
    if sec in ("overview", "labels"):
        out["labels"] = [{"id": lb.id, "name": lb.name, **({"system": lb.role} if lb.role else {}),
                          **({"color": lb.color} if lb.color and sec == "labels" else {})} for lb in labels
                         if sec == "labels" or not lb.role]
    if sec in ("overview", "filters"):
        rules = list((await db.execute(select(Rule).where(Rule.user_id == uid).order_by(Rule.position))).scalars())
        items = []
        for r in rules:
            d = rule_out(r)
            acts = dict(d["actions"] or {})
            for k in ("add_labels", "remove_labels"):
                if acts.get(k):
                    acts[k] = [lname.get(i, i) for i in acts[k]]
            items.append({"id": r.id, "name": r.name, "query": r.query, "actions": acts, "enabled": r.enabled}
                         if sec == "filters" else {"id": r.id, "query": r.query, "enabled": r.enabled})
        out["filters"] = items
    if sec in ("overview", "layouts"):
        lays = layouts.all_layouts(settings)
        out["layouts"] = {"active": layouts.active_id(settings),
                          "available": [{"id": x["id"], "name": x["name"], "custom": not x.get("preset")} for x in lays]}
        if sec == "layouts":
            out["layouts"]["custom"] = settings.get("inbox_layouts") or []
            out["layouts"]["format"] = ("layout = {id, name, tabs: [{id, name, icon, indicators: [unread|all|due], sections: "
                                        "[{id, title, base: inbox|all|starred|important|label:<id>, category?, "
                                        "filter?: important|important_unread|unread|starred, q?, limit?, "
                                        "exclude_above, inbox_only?: only conversations in the Inbox "
                                        "(for a base other than inbox)}]}]}")
    if sec in ("overview", "identities"):
        ids = (await db.execute(select(Identity).where(Identity.user_id == uid).order_by(Identity.id))).scalars()
        from mailr.agent.screen import html_to_text

        out["identities"] = [{"id": i.id, "email": i.email, "name": i.name, "default": i.is_default,
                              "signature": html_to_text(i.signature_html)[:400] if sec == "identities"
                              else bool(i.signature_html)} for i in ids]
    if sec in ("overview", "skills"):
        from mailr.knowledge import skills

        out["skills"] = [f"/{s.name}" + ("" if s.enabled else " (off)") for s in await skills.all_for(db, uid)]
    return ToolResult(out)


# ---------------------------------------------------------------- filters


_FILTER_ACTIONS = {
    "add_labels": {"type": "array", "items": {"type": "string"}, "description": "label names to apply"},
    "remove_labels": {"type": "array", "items": {"type": "string"}},
    "archive": {"type": "boolean", "description": "skip the inbox"},
    "mark_read": {"type": "boolean"}, "star": {"type": "boolean"},
    "important": {"type": ["boolean", "null"], "description": "always (true) / never (false) important"},
    "category": {"type": "string", "enum": list(CATEGORIES)},
    "spam": {"type": "boolean"}, "never_spam": {"type": "boolean"}, "trash": {"type": "boolean"},
}


class _LabelPending(Exception):
    def __init__(self, result: ToolResult):
        self.result = result


async def _label_ids(ctx: ToolCtx, names: list[str], create: bool) -> list[int]:
    from mailr.core.store import user_store

    out = []
    for n in names or []:
        if isinstance(n, int) or str(n).isdigit():
            lb = await ctx.db.get(Label, int(n))
            lb = lb if lb and lb.user_id == ctx.p.user_id else None
        else:
            lb = (await ctx.db.execute(select(Label).where(Label.user_id == ctx.p.user_id,
                                                           Label.name.ilike(str(n).strip())))).scalars().first()
        if lb is None:
            if not create:
                raise ToolError(f"no label named {n!r}; pass create_missing_labels=true or create it first")
            # A new label is labels.write, whatever tool creates it.
            pending = await gate(ctx, "labels.write", target=str(n).strip(),
                                 payload={"op": "label.create", "name": str(n).strip()},
                                 preview={"title": f"Create label “{str(n).strip()}”", "kind": "label",
                                          "lines": [str(n).strip()]})
            if pending is not None:
                pending.data["note"] = ("The filter needs a new label, which waits for the user's approval; the "
                                        "filter was not saved. After it is approved, call manage_filter again.")
                raise _LabelPending(pending)
            store = await user_store(ctx.db, ctx.p.user)
            lb = await store.create_label(ctx.db, str(n).strip())
            await _notify(ctx.db, ctx.p.user_id, "label")
        out.append(lb.id)
    return out


# The match check (20 Oct 2026): before a filter or a message-triggered standing order waits for approval,
# how many of the user's emails of the last MATCH_DAYS its search matches, with the newest. A search that
# matches nothing is usually wrong (a mailing list's senders arrive as list+name@domain), so the model checks
# it with the user, and the card says so.
MATCH_DAYS = 183
MATCH_CAP = 10_000
NO_MATCH_NOTE = (
    "The search matches no email from the last 6 months. Before the user approves, check the search with "
    "them: for example, mail from a mailing list arrives from the list's own address (often list+name@domain "
    "or the list's domain), so match the list address with to: or from:domain, or use list:. Change the "
    "search if it is wrong, then call the tool again.")


async def match_check(ctx: ToolCtx, query: str, scope: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """How many of the user's received emails of the last MATCH_DAYS a search matches (up to MATCH_CAP),
    with the newest one's subject and date; None when the search does not compile. Spam, Trash, drafts
    and sent mail are left out (unless the search names Spam or Trash), and a token sees only its own
    mail."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, not_

    from mailr.core.rules import scope_condition, user_labels
    from mailr.core.search import compile_query, query_zone
    from mailr.core.visibility import visible_message_filter
    from mailr.models import Message

    uid = ctx.p.user_id
    labels = await user_labels(ctx.db, uid)
    try:
        c = compile_query(query, uid, labels, zone=await query_zone(ctx.db, uid, query))
    except ValueError:
        return None
    conds: list[Any] = [Message.user_id == uid, Message.deleted.is_(False), Message.draft.is_(False), c.where,
                        Message.date >= datetime.now(UTC) - timedelta(days=MATCH_DAYS)]
    skip = [lb.id for lb in labels if lb.role == "sent" or (lb.role in ("junk", "trash") and not c.includes_spam_trash)]
    if skip:
        conds.append(not_(Message.label_ids.overlap(skip)))
    if (sc := scope_condition(scope)) is not None:
        conds.append(sc)
    if ctx.p.token is not None:
        conds.append(visible_message_filter(ctx.p))
    async with ctx.db.begin_nested():
        n = (await ctx.db.execute(select(func.count()).select_from(
            select(Message.id).where(*conds).limit(MATCH_CAP + 1).subquery()))).scalar_one()
        newest = (await ctx.db.execute(select(Message.id, Message.subject, Message.date).where(*conds)
                                       .order_by(Message.date.desc()).limit(1))).first() if n else None
    # The newest message's id names the mail the card shows (core/visibility.py MAIL_REF_KEYS), so a
    # local-only subject never reaches a hosted model or a push service.
    return {"count": min(n, MATCH_CAP), "capped": n > MATCH_CAP, "days": MATCH_DAYS,
            **({"newest_subject": (newest.subject or "")[:200], "message_id": newest.id,
                "newest_date": newest.date.date().isoformat() if newest.date else None} if newest else {})}


def match_result(m: dict[str, Any] | None) -> dict[str, Any]:
    """The match check as the tool result gives it to the model: the count and the newest date (the
    subject stays on the card: it is email text)."""
    if m is None:
        return {}
    out: dict[str, Any] = {"matches_existing": {"emails": m["count"], **({"more_than": True} if m["capped"] else {}),
                                                "period": "the last 6 months",
                                                **({"newest_date": m["newest_date"]} if m.get("newest_date") else {})}}
    if not m["count"]:
        out["check_search"] = NO_MATCH_NOTE
    return out


@tool("manage_filter",
      "Create, update, delete or apply a filter (rule). A filter matches new mail with a search query (Gmail "
      "syntax: from:, to:, subject:, has:attachment, \"phrase\", OR, -word) and applies actions. Label names are "
      "used for add_labels/remove_labels. apply_existing also runs it on existing mail. Needs the user's approval.",
      _obj({"action": {"type": "string", "enum": ["create", "update", "delete", "apply"]},
            "filter_id": {"type": "integer", "description": "for update, delete, apply"},
            "name": {"type": "string"}, "query": {"type": "string"},
            **_FILTER_ACTIONS,
            "enabled": {"type": "boolean"}, "stop": {"type": "boolean", "description": "stop later filters"},
            "apply_existing": {"type": "boolean"},
            "create_missing_labels": {"type": "boolean"}}, ["action"]), scope="rules")
async def manage_filter(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.org_routes import RuleIn, _validate_rule, apply_rule_op

    action = args.get("action")
    rid = args.get("filter_id")
    existing = None
    if action in ("update", "delete", "apply"):
        if not rid:
            raise ToolError("filter_id is required (get_configuration section=filters)")
        existing = await ctx.db.get(Rule, int(rid))
        if existing is None or existing.user_id != ctx.p.user_id:
            raise ToolError("filter not found")
        if (existing.actions or {}).get("forward_to") and action != "apply":
            raise ToolError("forwarding filters are changed by the user in Settings → Mail → Filters & blocking")
    from mailr.core.rules import scope_of

    payload: dict[str, Any] = {"op": action, "source": "agent", "scope": scope_of(ctx.p)}
    if rid:
        payload["rule_id"] = int(rid)
    lines: list[str] = []
    undo: dict[str, Any] | None = None
    if action in ("create", "update"):
        base = {"name": existing.name, "query": existing.query, "actions": dict(existing.actions or {}),
                "enabled": existing.enabled, "stop": existing.stop} if existing else {"name": "", "query": "",
                                                                                   "actions": {}, "enabled": True,
                                                                                   "stop": False}
        rule = {**base}
        for k in ("name", "query", "enabled", "stop"):
            if args.get(k) is not None:
                rule[k] = args[k]
        if isinstance(rule.get("name"), str) and args.get("name") is not None:
            rule["name"] = mdmail.inline_text(rule["name"])  # a name, shown as plain text
        acts = dict(rule["actions"])
        create = bool(args.get("create_missing_labels"))
        for k in _FILTER_ACTIONS:
            if k not in args:
                continue
            v = args[k]
            if k in ("add_labels", "remove_labels"):
                try:
                    acts[k] = await _label_ids(ctx, v, create)
                except _LabelPending as e:
                    return e.result
            elif v in (None, False) and k != "important":
                acts.pop(k, None)
            else:
                acts[k] = v
        rule["actions"] = acts
        rule["apply_existing"] = bool(args.get("apply_existing"))
        if not str(rule.get("query") or "").strip():
            raise ToolError("a filter needs a query")
        if not acts:
            raise ToolError("a filter needs at least one action")
        try:
            await _validate_rule(ctx.db, ctx.p.user_id, RuleIn(**rule))
        except HTTPException as e:
            raise ToolError(str(e.detail)) from None
        if existing:
            # Built on the filter as it is now: a change the user saves before an approval is merged in.
            from mailr.api.org_routes import rule_snapshot
            from mailr.core import live

            rule["base_version"] = await live.current(ctx.db, ctx.p.user_id, "rule", existing.id,
                                                      live.loader(ctx.db, existing, rule_snapshot))
        payload["rule"] = rule
        lines = [f"Matches: {rule['query']}", "Do: " + ", ".join(f"{k}={v}" for k, v in acts.items())]
        if existing:
            undo = _undo("PATCH", f"/rules/{existing.id}", {**base, "apply_existing": False})
    elif action == "delete":
        lines = [f"Matches: {existing.query}"]
        undo = _undo("POST", "/rules", {"name": existing.name, "query": existing.query, "actions": existing.actions,
                                        "enabled": existing.enabled, "stop": existing.stop})
    elif action == "apply":
        lines = [f"Run on existing mail: {existing.query}"]
    else:
        raise ToolError("action must be create, update, delete or apply")
    title = {"create": "Create filter", "update": "Change filter", "delete": "Delete filter",
             "apply": "Apply filter to existing mail"}[action]
    # ACT agent.match_check @assistant: how much existing mail the filter's search matches, before asking.
    match = await match_check(ctx, payload["rule"]["query"], payload.get("scope")) \
        if action in ("create", "update") else None
    pending = await gate(ctx, "rules.write", target=title, payload=payload,
                         preview={"title": title, "kind": "filter", "lines": lines, "danger": action == "delete",
                                  **({"match": match} if match is not None else {})})
    if pending is not None:
        await _audit(ctx, f"agent.rules.{action}", str(rid or ""), payload, {"pending": pending.data["approval_id"]})
        await ctx.db.commit()
        pending.data.update(match_result(match))
        return pending
    from mailr.core import live

    try:
        res = await apply_rule_op(ctx.db, ctx.p.user, payload)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    except live.LiveConflict as e:
        # Refused before anything was written; the session stays usable (no rollback).
        raise ToolError(e.body()["detail"] + " Nothing changed; read the filter again.") from None
    if action == "create":
        undo = _undo("DELETE", f"/rules/{res['id']}")
    await _audit(ctx, f"agent.rules.{action}", str(rid or res.get("id", "")), payload)
    await _notify(ctx.db, ctx.p.user_id, "rule")
    await ctx.db.commit()
    summary = f"{title}: {payload.get('rule', {}).get('query') or (existing.query if existing else '')}"
    refs = [{**live.ref("rule", res["id"], res["version"]), "title": summary}] \
        if isinstance(res, dict) and res.get("id") and res.get("version") is not None else []
    return ToolResult({"status": "done", "summary": summary, "filter": json.loads(json.dumps(res, default=str)),
                       **match_result(match)},
                      events=[{"type": "config", "title": summary, "undo": undo if action != "apply" else None}],
                      refs=refs)


# ---------------------------------------------------------------- labels


async def _find_label(ctx: ToolCtx, ref: Any) -> Label | None:
    if ref in (None, ""):
        return None
    if isinstance(ref, int) or str(ref).isdigit():
        lb = await ctx.db.get(Label, int(ref))
        return lb if lb and lb.user_id == ctx.p.user_id else None
    return (await ctx.db.execute(select(Label).where(Label.user_id == ctx.p.user_id,
                                                     Label.name.ilike(str(ref).strip())))).scalars().first()


@tool("manage_label",
      "Create, update or delete a label. Nest a label with a '/' in the name (\"Clients/Acme\") or with parent. "
      "update renames (new_name), recolours (color: a scheme colour \"@<scheme>/<hue>/<shade>\" such as "
      "\"@default/teal/3\" (schemes default, muted, vivid, pastel, earth, ocean; hues red, orange, yellow, green, "
      "teal, blue, indigo, purple, pink, grey; shade 1 lightest to 4 solid), \"#rrggbb\" or \"#background,#text\") "
      "or sets show_in_list "
      "(show, hide, unread). Deleting needs the user's approval.",
      _obj({"action": {"type": "string", "enum": ["create", "update", "delete"]},
            "label": {"type": "string", "description": "the label's name (or id); for create, the new name"},
            "new_name": {"type": "string"}, "parent": {"type": "string", "description": "nest under this label"},
            "color": {"type": "string"},
            "show_in_list": {"type": "string", "enum": ["show", "hide", "unread"]}}, ["action", "label"]),
      scope="label")
async def manage_label(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.mail_write import normalize_color

    action = args.get("action")
    color = None
    if args.get("color"):
        try:
            color = normalize_color(str(args["color"]))
        except HTTPException as e:
            raise ToolError(str(e.detail)) from None
    parent = str(args.get("parent") or "").strip().strip("/")
    if action == "create":
        name = mdmail.inline_text(str(args.get("label") or "").strip()).strip("/")
        if parent:
            name = f"{parent}/{name.rsplit('/', 1)[-1]}"
        if not name:
            raise ToolError("give the label's name")
        if await _find_label(ctx, name):
            raise ToolError(f"a label named {name!r} already exists")
        payload = {"op": "label.create", "name": name, "color": color}
        return await _run(ctx, "labels.write", payload, {"title": f"Create label “{name}”", "kind": "label",
                                                          "lines": [name]}, name)
    lb = await _find_label(ctx, args.get("label"))
    if lb is None:
        raise ToolError(f"no label {args.get('label')!r} (get_configuration section=labels)")
    if action == "delete":
        return await _run(ctx, "labels.delete", {"op": "label.delete", "label_id": lb.id},
                          {"title": f"Delete label “{lb.name}”", "kind": "label", "danger": True,
                           "lines": [f"{lb.name} (conversations keep their other labels)"]}, lb.name)
    if action != "update":
        raise ToolError("action must be create, update or delete")
    new_name = mdmail.inline_text(str(args.get("new_name") or "").strip()).strip("/") or None
    if parent:
        new_name = f"{parent}/{(new_name or lb.name).rsplit('/', 1)[-1]}"
    if lb.role and new_name:
        raise ToolError("system labels cannot be renamed")
    if not (new_name or color or args.get("show_in_list")):
        raise ToolError("nothing to change: give new_name, parent, color or show_in_list")
    payload = {"op": "label.update", "label_id": lb.id, "name": new_name, "color": color,
               "show_in_list": args.get("show_in_list")}
    lines = [x for x in (new_name and f"rename to {new_name}", color and f"colour {color}",
                         args.get("show_in_list") and f"in the label list: {args['show_in_list']}") if x]
    return await _run(ctx, "labels.write", payload, {"title": f"Change label “{lb.name}”", "kind": "label",
                                                      "lines": lines}, lb.name)


# ---------------------------------------------------------------- settings, layouts, signature, profile


async def _custom_layout_ids(ctx: ToolCtx) -> set[str]:
    user = await ctx.db.get(User, ctx.p.user_id)
    return {x.get("id") for x in (user.settings or {}).get("inbox_layouts") or [] if isinstance(x, dict)}


@tool("update_settings",
      "Change mailr settings (only these keys): reading_pane, density, theme, conversation_view, undo_send_seconds, "
      "importance_markers, keyboard_shortcuts, images, send_and_archive, default_reply, page_size, "
      "mail_notifications, notify_approvals, notify_assistant, notify_accounts, auto_advance, categories, inbox_type, inbox_count, llm_triage, sweep_rules. get_configuration section=settings "
      "lists the allowed values. Needs the user's approval.",
      _obj({"changes": {"type": "object", "description": "setting → new value, e.g. {\"density\": \"compact\"}"}},
           ["changes"]), scope="rules")
async def update_settings(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    try:
        changes = validate_settings(args.get("changes") or {}, await _custom_layout_ids(ctx))
    except ValueError as e:
        raise ToolError(str(e)) from None
    return await _run(ctx, "prefs.write", {"op": "settings", "changes": changes},
                      {"title": "Change settings", "kind": "settings",
                       "lines": [f"{k}: {json.dumps(v)}" for k, v in changes.items()]}, ", ".join(changes))


@tool("manage_layout",
      "Inbox layouts (tabs and sections). activate: make a preset (default, important_first, unread_first, "
      "starred_first, priority, multiple) or a custom layout the inbox. save: create or replace one custom layout "
      "(see get_configuration section=layouts for the format). delete: remove a custom layout. Needs approval.",
      _obj({"action": {"type": "string", "enum": ["activate", "save", "delete"]},
            "layout_id": {"type": "string"}, "layout": {"type": "object"},
            "activate": {"type": "boolean", "description": "with save: also make it the inbox"}}, ["action"]),
      scope="rules")
async def manage_layout(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.core import layouts

    user = await ctx.db.get(User, ctx.p.user_id)
    settings = dict(user.settings or {})
    custom = [x for x in settings.get("inbox_layouts") or [] if isinstance(x, dict)]
    action = args.get("action")
    lid = str(args.get("layout_id") or "").strip()
    active = None
    if action == "activate":
        if lid not in layouts.PRESET_IDS and lid not in {x.get("id") for x in custom}:
            raise ToolError(f"no layout {lid!r}; presets: {', '.join(layouts.PRESET_IDS)}")
        new, active, title = custom, lid, f"Use the “{lid}” inbox layout"
    elif action == "save":
        lay = args.get("layout")
        if not isinstance(lay, dict):
            raise ToolError("give the layout object")
        lay = {**lay, "id": lay.get("id") or lid or None}
        new = [x for x in custom if x.get("id") != lay.get("id")] + [lay]
        labels = set((await ctx.db.execute(select(Label.id).where(Label.user_id == ctx.p.user_id))).scalars())
        try:
            new = layouts.validate(new, labels)
        except layouts.LayoutError as e:
            raise ToolError(str(e)) from None
        saved = new[-1]
        active = saved["id"] if args.get("activate") else None
        title = f"Save inbox layout “{saved.get('name')}”"
    elif action == "delete":
        if lid not in {x.get("id") for x in custom}:
            raise ToolError(f"no custom layout {lid!r}")
        new, title = [x for x in custom if x.get("id") != lid], f"Delete inbox layout “{lid}”"
    else:
        raise ToolError("action must be activate, save or delete")
    return await _run(ctx, "prefs.write", {"op": "layouts", "layouts": new, "active": active, "title": title},
                      {"title": title, "kind": "settings", "lines": [title]}, lid or title)


@tool("update_signature",
      "Set the signature of one of the user's send-as identities (get_configuration section=identities). Plain "
      "text; blank lines separate paragraphs. Addresses themselves cannot be changed. Needs approval.",
      _obj({"identity_id": {"type": "integer"}, "signature": {"type": "string"}}, ["identity_id", "signature"]),
      scope="rules")
async def update_signature(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent.tools import body_html

    i = await ctx.db.get(Identity, int(args.get("identity_id") or 0))
    if i is None or i.user_id != ctx.p.user_id:
        raise ToolError("identity not found")
    sig = str(args.get("signature") or "")
    if len(sig) > 4000:
        raise ToolError("a signature is limited to 4000 characters")
    from mailr.agent.agentwrite import strip_markdown_images, strip_remote_images_html

    # A signature goes out with every message: never a remote image (a tracking beacon).
    sig = strip_markdown_images(sig)
    html = strip_remote_images_html(body_html(sig)) if sig.strip() else ""
    # The card shows the whole signature (nothing hidden below line 8).
    return await _run(ctx, "prefs.write", {"op": "signature", "identity_id": i.id, "signature_html": html},
                      {"title": f"Change the signature for {i.email}", "kind": "settings",
                       "lines": sig.strip().splitlines() or ["(no signature)"]}, i.email)


@tool("update_profile",
      "Update what the assistant knows about the user (\"About you\"): preferred_name, signoff_name, role, timezone "
      "(IANA, e.g. Europe/London), languages, writing (style preferences), about. Needs approval.",
      _obj({k: {"type": "string"} for k in ("preferred_name", "signoff_name", "role", "timezone", "languages",
                                             "writing", "about")}), scope="rules")
async def update_profile(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.knowledge import profile

    try:
        fields = profile.clean(args)
    except ValueError as e:
        raise ToolError(str(e)) from None
    if not fields:
        raise ToolError("nothing to change")
    return await _run(ctx, "prefs.write", {"op": "profile", "fields": fields},
                      {"title": "Update your profile", "kind": "settings",
                       "lines": [f"{k}: {v}" for k, v in fields.items()]}, ", ".join(fields))
