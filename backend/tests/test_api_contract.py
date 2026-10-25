"""The typed API contract (mailr/api/contract, backend/README.md, "Typed API contract").

For every route that declares a response model, a real response from a seeded account validates
against the model, lists no field the model leaves out, and fails to validate once a required
field is removed or renamed. The generated files (frontend/src/api/openapi.json and
schema.gen.ts) must be current, and conversation and message ids keep their public form.

The account holds what the most-used screens read: a thread of two messages and one with an
attachment, a user label, a layout with two tabs, a calendar event, a task with a due date and a
checklist note.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import mail_date
from helpers.calendar import _connect
from helpers.core_flow import _ingest
from helpers.public_ids import pub  # noqa: F401
from pydantic import BaseModel, TypeAdapter, ValidationError

from mailr.api.contract import account, calendar, mail, notes

REPO = Path(__file__).resolve().parents[2]
OPENAPI = REPO / "frontend/src/api/openapi.json"
SCHEMA_TS = REPO / "frontend/src/api/schema.gen.ts"

# (route path as declared, its model). Every typed GET route is here (test_every_typed_route_is_listed).
MODELS: dict[str, Any] = {
    "/api/v1/conversations": mail.ConversationPage,
    "/api/v1/conversations/{cid}": mail.ConversationDetail,
    "/api/v1/messages/{mid}/body": mail.MessageBody,
    "/api/v1/counts": mail.SidebarCounts,
    "/api/v1/inbox/count": mail.InboxCount,
    "/api/v1/labels": mail.LabelList,
    "/api/v1/layouts": mail.LayoutsResponse,
    "/api/v1/layouts/{layout_id}/counts": mail.TabCounts,
    "/api/v1/changes": mail.Changes,
    "/api/v1/calendar/agenda": calendar.Agenda,
    "/api/v1/calendar/calendars": calendar.CollectionList,
    "/api/v1/tasks": calendar.TaskPage,
    "/api/v1/tasks/counts": calendar.TaskCounts,
    "/api/v1/notes": notes.NoteList,
    "/api/v1/notes/{note_id}": notes.Note,
    "/api/v1/me": account.Me,
}

ATTACHED = (
    "From: Dana <dana@example.org>\r\nTo: me@example.com\r\nSubject: Slides\r\nMessage-ID: <ct3@x.org>\r\n"
    "Date: {date}\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=b1\r\n\r\n"
    "--b1\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nThe slides.\r\n"
    "--b1\r\nContent-Type: application/pdf; name=slides.pdf\r\nContent-Disposition: attachment; "
    "filename=slides.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERi0xLjQK\r\n--b1--\r\n")


async def _seed(client, user, raw) -> dict[str, Any]:
    m1 = raw("Budget 2027", msgid="<ct1@x.org>", body="Numbers for the budget.")
    m2 = raw("Re: Budget 2027", frm="Bob <bob@example.org>", msgid="<ct2@x.org>", irt="<ct1@x.org>",
             date=mail_date(1, "12:00"), body="Updated numbers.")
    mid, cid = await _ingest(user, m1)
    await _ingest(user, m2)
    await _ingest(user, ATTACHED.format(date=mail_date(2, "09:00")).encode())
    lb = (await client.post("/api/v1/labels", json={"name": "Projects"})).json()
    await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "add_labels",
                                                             "label_ids": [lb["id"]]})
    lay = {"name": "Mine", "tabs": [
        {"name": "Inbox", "icon": "inbox", "badge": "unread", "sections": [{"title": "All", "base": "inbox"}]},
        {"name": "Projects", "icon": "tag", "badge": "unread", "claims": True,
         "sections": [{"title": "All", "base": f"label:{lb['id']}"}]}]}
    assert (await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "mine"})).status_code == 200
    await _connect(client)
    start = (datetime.now(UTC) + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    ev = await client.post("/api/v1/calendar/events", json={
        "summary": "Budget review", "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
        "location": "Room 4"})
    assert ev.status_code == 200, ev.text
    t = await client.post("/api/v1/tasks", json={"summary": "Send numbers", "due": start.date().isoformat()})
    assert t.status_code == 200, t.text
    note = (await client.post("/api/v1/notes", json={"kind": "checklist", "title": "Groceries",
                                                     "items": [{"text": "Milk"}]})).json()["note"]
    return {"cid": cid, "mid": mid, "note": note["id"],
            "from": start.date().isoformat(), "to": (start + timedelta(days=1)).date().isoformat()}


def _requests(s: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        ("/api/v1/conversations", "/api/v1/conversations", {"view": "inbox"}),
        ("/api/v1/conversations/{cid}", f"/api/v1/conversations/{s['cid']}", {}),
        ("/api/v1/messages/{mid}/body", f"/api/v1/messages/{s['mid']}/body", {}),
        ("/api/v1/counts", "/api/v1/counts", {}),
        ("/api/v1/inbox/count", "/api/v1/inbox/count", {}),
        ("/api/v1/labels", "/api/v1/labels", {}),
        ("/api/v1/layouts", "/api/v1/layouts", {}),
        ("/api/v1/layouts/{layout_id}/counts", "/api/v1/layouts/mine/counts", {"detail": True}),
        ("/api/v1/layouts/{layout_id}/counts", "/api/v1/layouts/mine/counts", {}),
        ("/api/v1/changes", "/api/v1/changes", {"since": 0}),
        ("/api/v1/calendar/agenda", "/api/v1/calendar/agenda", {"from": s["from"], "to": s["to"]}),
        ("/api/v1/calendar/calendars", "/api/v1/calendar/calendars", {}),
        ("/api/v1/tasks", "/api/v1/tasks", {}),
        ("/api/v1/tasks/counts", "/api/v1/tasks/counts", {}),
        ("/api/v1/notes", "/api/v1/notes", {}),
        ("/api/v1/notes/{note_id}", f"/api/v1/notes/{s['note']}", {}),
        ("/api/v1/me", "/api/v1/me", {}),
    ]


def _undeclared(v: Any, at: str = "") -> list[str]:
    """Fields the response carried that the model does not list (they pass, but are untyped)."""
    if isinstance(v, BaseModel):
        out = [f"{at}.{k}" for k in (v.__pydantic_extra__ or {})]
        for name in type(v).model_fields:
            out += _undeclared(getattr(v, name), f"{at}.{name}")
        return out
    if isinstance(v, list):
        return [x for i, item in enumerate(v) for x in _undeclared(item, f"{at}[{i}]")]
    if isinstance(v, dict):
        return [x for k, item in v.items() for x in _undeclared(item, f"{at}[{k!r}]")]
    return []


def _required_target(model: Any, body: Any) -> tuple[dict[str, Any], str] | None:
    """A dict in `body` and one of its required fields: the top object, or the first item of a list."""
    cls = model
    obj = body
    if isinstance(body, list):
        cls = model.model_fields["root"].annotation.__args__[0]
        obj = body[0] if body else None
    if not isinstance(obj, dict) or not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        return None
    for name, f in cls.model_fields.items():
        key = f.alias or name
        if f.is_required() and key in obj:
            return obj, key
    return None


async def test_every_typed_route_is_listed(app):
    from mailr.api.contract.export import document

    typed = {p for p, ops in document()["paths"].items() if "get" in ops}
    # Routes typed by a return annotation before the contract started are not in MODELS.
    missing = {p for p in typed if p not in MODELS} - {"/api/v1/assistant/trusted-addresses",
                                                       "/api/v1/connectors/{cid}/migration",
                                                       "/api/v1/connectors/{cid}/migration/{job_id}"}
    assert not missing, f"typed routes without a contract check here: {sorted(missing)}"
    assert set(MODELS) <= typed, f"listed here but not typed: {sorted(set(MODELS) - typed)}"


async def test_real_responses_match_their_models(client, user, raw):
    s = await _seed(client, user, raw)
    seen: set[str] = set()
    for route, url, params in _requests(s):
        r = await client.get(url, params=params)
        assert r.status_code == 200, (url, r.text)
        body = r.json()
        model = MODELS[route]
        value = TypeAdapter(model).validate_python(body)
        assert not _undeclared(value), f"{route}: fields the model does not list: {_undeclared(value)}"
        seen.add(route)
        # A required field removed, or renamed, no longer validates.
        target = _required_target(model, body)
        if target is None:
            continue
        for change in ("remove", "rename"):
            broken = copy.deepcopy(body)
            obj, key = _required_target(model, broken)  # type: ignore[misc]
            value_of = obj.pop(key)
            if change == "rename":
                obj[key + "_renamed"] = value_of
            with pytest.raises(ValidationError):
                TypeAdapter(model).validate_python(broken)
    assert seen == set(MODELS)


async def test_a_mismatch_is_a_500_in_development(client, user, monkeypatch):
    """The safety net in main.py: in development a route that breaks its model answers 500 with the
    reason (in production the answer goes out unchanged and the mismatch is reported)."""
    from mailr.api import layout_routes

    async def not_a_number(*a, **kw):
        return "many"

    monkeypatch.setattr(layout_routes, "first_tab_unread", not_a_number)
    r = await client.get("/api/v1/inbox/count")
    assert r.status_code == 500 and "does not match its model" in r.json()["detail"]


async def test_public_ids_survive_the_models(client, user, raw, pub):
    """Conversation and message ids go out in their public form through the typed routes (the
    models keep ConvId and MsgId, mailr/api/contract/base.PublicId)."""
    m = raw("Budget 2027", msgid="<pub1@x.org>")
    mid, cid = await _ingest(user, m)
    lst = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    assert [c["id"] for c in lst] == [pub.encode("conversation", cid)]
    pcid = lst[0]["id"]
    full = (await client.get(f"/api/v1/conversations/{pcid}")).json()
    assert full["conversation"]["id"] == pcid
    assert [x["id"] for x in full["messages"]] == [pub.encode("message", mid)]
    assert [x["conversation_id"] for x in full["messages"]] == [pcid]
    ch = (await client.get("/api/v1/changes", params={"since": 0})).json()["changes"]
    assert {"entity": "conversation", "id": pcid, "op": "upsert"} in ch


def test_generated_files_are_current():
    """tools/api-types writes frontend/src/api/openapi.json and schema.gen.ts from the models."""
    from mailr.api.contract import export, typescript

    assert OPENAPI.read_text(encoding="utf-8") == export.render(), "openapi.json is stale: run tools/api-types"
    assert SCHEMA_TS.read_text(encoding="utf-8") == typescript.render(json.loads(OPENAPI.read_text(encoding="utf-8"))), \
        "schema.gen.ts is stale: run tools/api-types"
