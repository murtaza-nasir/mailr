"""Cross-user sweep: a second signed-in user tries the owner's identifiers on every route that
takes one (from the OpenAPI schema, so new routes are covered automatically) and on every agent
tool argument that names an object. Nothing may succeed, leak the owner's data or change it.

A route whose identifier names something in the caller's own namespace (people/{email}: the
caller's own view of an address) may answer 2xx; it must still not show or change the
owner's data. Every route template must be mapped below, so an unmapped new route fails the
test until someone decides what it should be tried with.
"""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from mailr.db import sessionmaker

MARK = "OWNERSECRET"
PEOPLE_EMAIL = "alice-owner@example.org"

# Tables holding the owner's data; the rows must be identical before and after the sweep.
SNAPSHOT_TABLES = [
    "audit_log",
    "labels", "conversations", "messages", "rules", "permission_rules", "outbox", "api_tokens",
    "push_targets", "identities", "connectors", "sessions", "chat_sessions", "assistant_skills",
    "kb_entities", "kb_memories", "label_decisions", "pending_actions", "forward_addresses", "people",
    "assistant_orders", "assistant_runs", "templates", "speakr_recordings", "recording_links",
    "person_groups", "person_suggestions", "screener_senders", "note_labels", "note_viewer_state",
    "note_reminders", "note_templates", "trusted_addresses", "review_sender_choices",
]
# Columns that may move for reasons unrelated to the attacker (none expected, kept explicit).
VOLATILE = {"last_used_at", "last_seen_at"}


async def _snapshot(uid: int) -> dict[str, list]:
    out = {}
    async with sessionmaker()() as db:
        for t in SNAPSHOT_TABLES:
            rows = (await db.execute(text(f"select * from {t} where user_id = :u order by 1"), {"u": uid})).mappings().all()
            out[t] = [{k: str(v) for k, v in r.items() if k not in VOLATILE} for r in rows]
    return out


async def _owner_objects(client, user, raw):
    """Create one of everything for the owner, each carrying MARK where it has text."""
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.models import (
        AssistantOrder,
        ChatMessage,
        ChatSession,
        Connector,
        Entity,
        ForwardAddress,
        Identity,
        Job,
        LabelDecision,
        Memory,
        Outbox,
        PendingAction,
        PermissionRule,
        PushTarget,
        RecordingLink,
        Rule,
        Session,
        Skill,
        SpeakrRecording,
        Template,
        User,
    )

    ids: dict[str, object] = {}
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        res = await ingest_raw(db, store, raw(f"{MARK} budget", frm=f"Alice <{PEOPLE_EMAIL}>",
                                              body=f"{MARK} body text"), IngestOptions())
        await db.commit()
        ids["mid"], ids["conv"] = res.message.id, res.message.conversation_id

    r = await client.post("/api/v1/labels", json={"name": f"{MARK}-label"})
    assert r.status_code in (200, 201), r.text
    ids["label"] = r.json()["id"]
    r = await client.post("/api/v1/tokens", json={"name": f"{MARK}-token", "scopes": ["read"]})
    assert r.status_code == 200, r.text
    ids["token"] = r.json()["api_token"]["id"]
    r = await client.put("/api/v1/layouts", json={"layouts": [{"id": "ownerlayout", "name": MARK, "tabs": [
        {"id": "t1", "name": MARK, "sections": [{"id": "s1", "title": MARK, "base": "inbox"}]}]}]})
    assert r.status_code == 200, r.text
    ids["layout"] = "ownerlayout"

    now = datetime.now(UTC)
    async with sessionmaker()() as db:
        def add(o):
            db.add(o)
            return o
        conn = add(Connector(user_id=user.id, kind="imap", name=f"{MARK}-imap", mode="mirror", data_class="normal",
                             config={"host": "imap.example.org"}, state={}, enabled=False,
                             # already paused: a connector loop left running by another test pauses
                             # disabled connectors, which is not the attacker's doing
                             status="paused", status_detail="Paused"))
        ident = add(Identity(user_id=user.id, email=f"owner-{uuid.uuid4().hex[:6]}@example.org", name=MARK))
        rule = add(Rule(user_id=user.id, name=MARK, query=f"subject:{MARK}"))
        perm = add(PermissionRule(user_id=user.id, subject="*", action="mail.send", effect="ask",
                                   source="grant"))  # also what an approval card's Undo names
        push = add(PushTarget(user_id=user.id, endpoint=f"https://push.example.org/{MARK}"))
        sess = add(Session(id=uuid.uuid4().hex, user_id=user.id, csrf="c", expires_at=now + timedelta(days=1)))
        chat = add(ChatSession(id=uuid.uuid4().hex[:20], user_id=user.id, title=MARK))
        from mailr.models import MailReview

        mreview = add(MailReview(user_id=user.id, chat_session_id=chat.id, scope={"view": "inbox", "label": MARK},
                                 ids=[], kept={}, decisions={}, status="stopped"))
        from mailr.models import ReviewSenderChoice

        rchoice = add(ReviewSenderChoice(user_id=user.id, sender=f"{MARK.lower()}@example.org", action="trash"))
        skill = add(Skill(user_id=user.id, name="ownerskill", title=MARK, instructions=MARK))
        ent = add(Entity(user_id=user.id, name=MARK, kind="person"))
        mem = add(Memory(user_id=user.id, text=f"{MARK} memory"))
        fwd = add(ForwardAddress(user_id=user.id, email="fwd-owner@example.org"))
        await db.execute(text("update people set notes = :m, organization = :m2 where user_id = :u and email = :e"),
                         {"m": MARK, "m2": MARK, "u": user.id, "e": PEOPLE_EMAIL})
        pend = add(PendingAction(user_id=user.id, subject="agent", action="mail.send", expires_at=now + timedelta(days=1),
                                 summary=MARK) if hasattr(PendingAction, "summary") else
                   PendingAction(user_id=user.id, subject="agent", action="mail.send", expires_at=now + timedelta(days=1)))
        job = add(Job(kind="bulk.action", user_id=user.id, payload={"note": MARK}))
        # A Speakr connector with one mirrored recording and one link.
        spk = add(Connector(user_id=user.id, kind="speakr", name=f"{MARK}-speakr", mode="mirror", data_class="normal",
                            config={"base_url": "https://speakr.invalid"}, state={}, enabled=False,
                            status="paused", status_detail="Paused"))
        await db.flush()
        add(SpeakrRecording(user_id=user.id, connector_id=spk.id, recording_id=5, title=f"{MARK} meeting",
                            participants=[MARK], status="completed"))
        rlink = add(RecordingLink(user_id=user.id, connector_id=spk.id, recording_id=5, kind="task", target=MARK,
                                  state="confirmed", source="user"))
        order = add(AssistantOrder(user_id=user.id, name=MARK, query=f"subject:{MARK}", actions={"star": True}))
        tmpl = add(Template(user_id=user.id, name=MARK, shortcut="owner", subject=MARK, body_html=f"<p>{MARK}</p>"))
        # A contact group, a suggested group and a detail suggestion.
        from mailr.models import PersonGroup, PersonSuggestion

        grp = add(PersonGroup(user_id=user.id, name=MARK, source="manual", state="active", evidence={}))
        sgrp = add(PersonGroup(user_id=user.id, name="", name_hint=MARK, source="suggested", state="suggested",
                               evidence={"subjects": [MARK]}))
        psug = add(PersonSuggestion(user_id=user.id, email=PEOPLE_EMAIL, field="title", value=MARK, norm=MARK.lower()))
        await db.flush()
        add(ChatMessage(session_id=chat.id, role="user", content=f"{MARK} chat"))
        dec = add(LabelDecision(user_id=user.id, message_id=ids["mid"], kind="category", target="updates",
                                source="heuristic", status="suggested"))
        ob = add(Outbox(user_id=user.id, identity_id=ident.id, payload={"subject": MARK}, send_at=now + timedelta(days=1)))
        from mailr.models import ScreenerSender

        scr = add(ScreenerSender(user_id=user.id, key=f"{MARK.lower()}.example", email=f"x@{MARK.lower()}.example", name=MARK))
        # An import (core/imports.py) and a calendar link (calendar/webcal.py) of the owner's.
        from mailr.models import ImportBatch

        imp = add(ImportBatch(user_id=user.id, kind="vcf", source=MARK, counts={}, undo={"people": [], "filled": {}}))
        wcal = add(Connector(user_id=user.id, kind="webcal", name=f"{MARK}-link", mode="import", data_class="normal",
                             config={"url": "https://calendars.example.org/x.ics", "refresh_hours": 12}, state={}))
        # An address that speaks for the owner, waiting for its code (core/trusted.py).
        from mailr.models import TrustedAddress

        trusted_addr = add(TrustedAddress(user_id=user.id, email="trusted-owner@work.example", code_hash="0" * 64,
                                          attempts=0, code_expires_at=now + timedelta(minutes=20)))
        await db.flush()
        ids.update(import_batch=imp.id, webcal=wcal.id, trusted=trusted_addr.id)
        ids.update(connector=conn.id, identity=ident.id, rule=rule.id, perm=perm.id, push=push.id,
                   session=sess.id, chat=chat.id, skill=skill.id, entity=ent.id, memory=mem.id,
                   fwd=fwd.email, pending=pend.id, job=job.id, decision=dec.id, outbox=ob.id,
                   order=order.id, template=tmpl.id, speakr=spk.id, rlink=rlink.id, group=grp.id,
                   suggested_group=sgrp.id, person_suggestion=psug.id, screener=scr.id, review=mreview.id,
                   review_choice=rchoice.id)
        await db.commit()
    r = await client.post("/api/v1/drafts", json={"payload": {"identity_id": ids["identity"], "subject": f"{MARK} draft",
                                                              "to": [{"email": "x@example.org", "name": ""}], "text": MARK}})
    assert r.status_code in (200, 201), r.text
    ids["draft"] = r.json()["draft"]["id"]
    # An inline image the owner uploaded (mail.insert_image); uploads are kept per user.
    r = await client.post("/api/v1/uploads", files={"file": ("owner.png", PNG, "image/png")})
    ids["upload"] = r.json()["blob"]
    # A checklist with a section, an item, a link and a revision (notes/routes.py).
    r = await client.post("/api/v1/notes", json={"kind": "checklist", "title": f"{MARK} list",
                                                 "lines": f"{MARK} shop:\nmilk {MARK}", "labels": [MARK]})
    assert r.status_code == 200, r.text
    note = r.json()["note"]
    ids["note"], ids["note_item"], ids["note_section"] = note["id"], note["items"][0]["id"], note["sections"][0]["id"]
    r = await client.post(f"/api/v1/notes/{note['id']}/links", json={"target_kind": "conversation",
                                                                     "target_ref": {"conversation_id": ids["conv"]}})
    assert r.status_code == 200, r.text
    ids["note_link"] = r.json()["link"]["id"]
    # A reminder on it and a template of it (notes/reminders.py, notes/templates.py).
    r = await client.put(f"/api/v1/notes/{note['id']}/reminders",
                         json={"at": [(datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0).isoformat()]})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/notes/{note['id']}/save-template", json={"name": f"{MARK} template"})
    assert r.status_code == 200, r.text
    ids["note_template"] = r.json()["template"]["id"]
    # A table with a row, a view and a link (tables/routes.py).
    r = await client.post("/api/v1/tables", json={"name": f"{MARK} table", "columns": [{"name": f"{MARK} col",
                                                                                          "type": "text"}]})
    assert r.status_code == 200, r.text
    tbl = r.json()["table"]
    ids["table"], ids["table_col"], ids["table_view"] = tbl["id"], tbl["columns"][0]["id"], tbl["views"][0]["id"]
    r = await client.post(f"/api/v1/tables/{tbl['id']}/rows", json={"cells": {ids["table_col"]: f"{MARK} cell"}})
    ids["table_row"] = r.json()["rows"][0]["id"]
    r = await client.post(f"/api/v1/tables/{tbl['id']}/links", json={"target_kind": "conversation",
                                                                     "target_ref": {"conversation_id": ids["conv"]}})
    ids["table_link"] = r.json()["link"]["id"]
    # Settings → Activity (core/activity.py): an entry of the owner's, with an Undo.
    from mailr.models import AuditLog
    async with sessionmaker()() as db:
        row = AuditLog(user_id=user.id, actor="user", action="notes.note.create", target="n1", inputs={},
                       result={"activity": {"app": "notes", "text": f"Created “{MARK}”", "key": "note:n1",
                                            "undo": {"method": "DELETE", "path": "/notes/n1"}}})
        db.add(row)
        await db.commit()
        ids["activity"] = row.id
    return ids


def _fill(template: str, ids: dict) -> str | None:
    """The owner's object for each route template (None: not an owner-object route)."""
    m = {
        r"^/api/v1/conversations/\{cid\}": {"cid": ids["conv"]},
        r"^/api/v1/messages/\{mid\}/cid/\{cid\}": {"mid": ids["mid"], "cid": ids["conv"]},
        r"^/api/v1/messages/\{mid\}/attachments/\{part\}": {"mid": ids["mid"], "part": "1"},
        r"^/api/v1/messages/\{mid\}": {"mid": ids["mid"]},
        r"^/api/v1/drafts/\{mid\}": {"mid": ids["draft"]},
        # Live editing (core/live.py): the owner's draft by kind and id.
        r"^/api/v1/live/\{kind\}/\{item\}": {"kind": "draft", "item": ids["draft"]},
        r"^/api/v1/connectors/\{cid\}": {"cid": ids["connector"]},
        r"^/api/v1/calendar/accounts/\{cid\}": {"cid": ids["connector"]},
        r"^/api/v1/decisions/\{did\}": {"did": ids["decision"]},
        r"^/api/v1/admin/models/\{eid\}": {"eid": 1},
        r"^/api/v1/admin/websearch/providers/\{pid\}": {"pid": 1},
        r"^/api/v1/admin/users/\{uid\}": {"uid": ids["owner_id"]},
        r"^/api/v1/admin/access/policies/\{pid\}": {"pid": 1},
        r"^/api/v1/admin/calendars/groups/\{cid\}": {"cid": 1},
        r"^/api/v1/admin/onboarding/presets/\{pid\}": {"pid": "p0000000000"},
        # The Android apps (core/phone_apps.py): instance-wide, admins only.
        r"^/api/v1/admin/phone-apps/\{app\}": {"app": "notes"},
        r"^/api/v1/assistant/entities/\{eid\}": {"eid": ids["entity"]},
        r"^/api/v1/assistant/memories/\{mid\}": {"mid": ids["memory"]},
        r"^/api/v1/assistant/skills/\{sid\}": {"sid": ids["skill"]},
        r"^/api/v1/assistant/trusted-addresses/\{tid\}": {"tid": ids["trusted"]},
        r"^/api/v1/me/sessions/\{sid\}": {"sid": ids["session"]},
        # Phone app passwords (calendar/phones.py) live on the caller's own store account: an id
        # names a credential there only, so the owner's "b" is just a missing one for the attacker.
        r"^/api/v1/me/dav/app-passwords/\{pid\}": {"pid": "b"},
        r"^/api/v1/forwarding/\{email\}": {"email": ids["fwd"]},
        r"^/api/v1/people/\{email\}/suggestions/\{sid\}": {"email": PEOPLE_EMAIL, "sid": ids["person_suggestion"]},
        r"^/api/v1/people/\{email\}": {"email": PEOPLE_EMAIL},
        r"^/api/v1/groups/\{gid\}/(accept|dismiss)": {"gid": ids["suggested_group"]},
        r"^/api/v1/groups/\{gid\}": {"gid": ids["group"]},
        r"^/api/v1/avatars/\{email\}": {"email": PEOPLE_EMAIL},
        r"^/api/v1/identities/\{iid\}": {"iid": ids["identity"]},
        r"^/api/v1/bulk/\{job_id\}": {"job_id": ids["job"]},
        r"^/api/v1/layouts/\{layout_id\}": {"layout_id": ids["layout"]},
        r"^/api/v1/labels/\{lid\}": {"lid": ids["label"]},
        r"^/api/v1/outbox/\{oid\}": {"oid": ids["outbox"]},
        r"^/api/v1/rules/\{rid\}": {"rid": ids["rule"]},
        r"^/api/v1/screener/\{sid\}": {"sid": ids["screener"]},
        r"^/api/v1/activity/\{entry_id\}": {"entry_id": ids["activity"]},
        r"^/api/v1/permissions/\{rid\}": {"rid": ids["perm"]},
        r"^/api/v1/approvals/grants/\{rule_id\}/undo": {"rule_id": ids["perm"]},
        r"^/api/v1/agent/chat/\{session_id\}": {"session_id": ids["chat"]},
        r"^/api/v1/agent/sessions/\{session_id\}": {"session_id": ids["chat"]},
        r"^/api/v1/agent/reviews/\{rid\}": {"rid": ids["review"]},
        r"^/api/v1/agent/review-choices/\{cid\}": {"cid": ids["review_choice"]},
        r"^/api/v1/tokens/\{tid\}": {"tid": ids["token"]},
        r"^/api/v1/push/targets/\{tid\}": {"tid": ids["push"]},
        r"^/api/v1/approvals/\{aid\}": {"aid": ids["pending"]},
        r"^/api/v1/assistant/orders/\{oid\}": {"oid": ids["order"]},
        r"^/api/v1/templates/\{tid\}": {"tid": ids["template"]},
        r"^/api/v1/uploads/\{blob\}": {"blob": ids["upload"]},
        r"^/api/v1/imports/\{bid\}": {"bid": ids["import_batch"]},
        r"^/api/v1/calendar/subscriptions/\{cid\}": {"cid": ids["webcal"]},
        # Household invites (core/invites.py): admins only; a token is a random secret.
        r"^/api/v1/admin/invites/\{iid\}": {"iid": 1},
        r"^/api/v1/invites/\{token\}": {"token": "NotAnInviteTokenOfAnyone_xxxxxxxx"},
        # Calendar objects live in the owner's CalDAV account; the attacker has none. A made-up
        # uid is enough to prove the route resolves only within the caller's own accounts.
        r"^/api/v1/calendar/events/\{uid\}": {"uid": "owner-event-uid"},
        r"^/api/v1/tasks/\{uid\}": {"uid": "owner-task-uid"},
        # Sharing (calendar/sharing.py): the calendar must be the caller's own; a person id names
        # the owner, whom the attacker may not grant anything on a calendar that is not theirs.
        r"^/api/v1/calendar/collections/\{calendar_id\}/shares/\{user_id\}": {"calendar_id": 999999999,
                                                                           "user_id": ids["owner_id"]},
        r"^/api/v1/calendar/collections/\{calendar_id\}": {"calendar_id": 999999999},
        r"^/api/v1/speakr/recordings/\{cid\}/\{rid\}": {"cid": ids["speakr"], "rid": 5},
        r"^/api/v1/speakr/links/\{link_id\}": {"link_id": ids["rlink"]},
        r"^/api/v1/speakr/speakers/\{cid\}/person": {"cid": ids["speakr"]},
        r"^/api/v1/speakr/connectors/\{cid\}/webhook": {"cid": ids["speakr"]},
        # A note template (notes/templates.py): the owner's own; another user's id is a 404.
        r"^/api/v1/notes/templates/\{template_id\}": {"template_id": ids.get("note_template")},
        # Notes (notes/access.py): the owner's checklist, its item, section, link and first revision.
        r"^/api/v1/notes/\{note_id\}": {"note_id": ids.get("note"), "item_id": ids.get("note_item"),
                                        "section_id": ids.get("note_section"), "link_id": ids.get("note_link"),
                                        "rev": 1, "key": "milk", "principal": ids.get("owner_id")},
        # Tables (tables/access.py): the owner's table, its row, column, view and link.
        r"^/api/v1/tables/\{table_id\}": {"table_id": ids.get("table"), "row_id": ids.get("table_row"),
                                          "col_id": ids.get("table_col"), "view_id": ids.get("table_view"),
                                          "link_id": ids.get("table_link"), "principal": ids.get("owner_id")},
    }
    for pat, vals in m.items():
        if re.match(pat, template):
            path = template
            for k, v in vals.items():
                path = path.replace("{" + k + "}", str(v))
            return path
    return None


# Not identifiers of owned objects: a tool or smart-view name.
NOT_OWNER_OBJECTS = {"/api/v1/smart/{name}",
                     # A note label name: labels are per user, so it names the caller's own label only.
                     "/api/v1/notes/labels/{name}", "/api/v1/agent/tools/{name}", "/api/v1/me/models/{task}",
                     "/api/v1/admin/models/defaults/{task}",
                     # A tag name: the caller's own tasks are the only ones it can touch.
                     "/api/v1/tasks/tags/{name}",
                     # A domain on the caller's own spam sender list (users.settings): nothing of anyone else's.
                     "/api/v1/spam/domains/{key}",
                     # A phone calendar's key (calendar/phonecal.py) names one of the caller's own phone
                     # calendars: the same key from someone else is their own calendar
                     # (test_phone_calendars.py, test_other_users_cannot_read_or_write).
                     "/api/v1/calendar/phone-calendars/{key}",
                     # A Speakr webhook address: no session by design; a delivery needs the connector's
                     # HMAC secret (test_speakr_webhook.py), and the owner's connector has none here.
                     "/api/v1/integrations/speakr/{hook}/webhook"}
VALID_BODIES = {
    "POST /api/v1/assistant/trusted-addresses/{tid}/confirm": {"code": "123456"},
    "PATCH /api/v1/tables/{table_id}": {"name": "x"},
    "PUT /api/v1/tables/{table_id}/state": {"pinned": True, "labels": ["x"]},
    "POST /api/v1/tables/{table_id}/duplicate": {"rows": True},
    "POST /api/v1/tables/{table_id}/rows": {"cells": {}},
    "POST /api/v1/tables/{table_id}/rows/bulk": {"op": "delete", "rows": []},
    "POST /api/v1/tables/{table_id}/rows/restore": {"rows": []},
    "PATCH /api/v1/tables/{table_id}/rows/{row_id}": {"cells": {"c": "x"}},
    "POST /api/v1/tables/{table_id}/rows/{row_id}/move": {"direction": "up"},
    "POST /api/v1/tables/{table_id}/rows/{row_id}/restore": {"seq": 1},
    "POST /api/v1/tables/{table_id}/columns": {"name": "x", "type": "text"},
    "PATCH /api/v1/tables/{table_id}/columns/{col_id}": {"name": "y"},
    "POST /api/v1/tables/{table_id}/views": {"name": "x"},
    "PATCH /api/v1/tables/{table_id}/views/{view_id}": {"name": "x"},
    "POST /api/v1/tables/{table_id}/stats": {},
    "POST /api/v1/tables/{table_id}/restore-point": {"seq": 1, "preview": True},
    "POST /api/v1/tables/{table_id}/revert": {"batch": "00000000-0000-0000-0000-000000000000"},
    "POST /api/v1/tables/{table_id}/import": {"text": "a\n1"},
    "POST /api/v1/tables/{table_id}/links": {"target_kind": "conversation", "target_ref": {}},
    "PATCH /api/v1/notes/{note_id}": {"title": "x"},
    "POST /api/v1/notes/{note_id}/convert": {"to": "text"},
    "PUT /api/v1/notes/{note_id}/state": {"pinned": True, "labels": ["x"]},
    "POST /api/v1/notes/{note_id}/items": {"lines": "x"},
    "PATCH /api/v1/notes/{note_id}/items/{item_id}": {"checked": True},
    "POST /api/v1/notes/{note_id}/items/{item_id}/move": {"direction": "down"},
    "POST /api/v1/notes/{note_id}/items/{item_id}/to-task": {},
    "POST /api/v1/notes/{note_id}/sections": {"name": "x"},
    "PATCH /api/v1/notes/{note_id}/sections/{section_id}": {"name": "y"},
    "POST /api/v1/notes/{note_id}/links": {"target_kind": "conversation", "target_ref": {}},
    "POST /api/v1/notes/{note_id}/revert": {"ops": []},
    "POST /api/v1/notes/{note_id}/usual/{key}/dismiss": {"for": "never"},
    "PUT /api/v1/notes/{note_id}/reminders": {"at": []},
    "POST /api/v1/notes/{note_id}/save-template": {"name": "x"},
    "POST /api/v1/tables/{table_id}/save-template": {"name": "x"},
    # Sharing (notes/share_routes.py): the attacker has no share, so the item is a 404 before anything.
    "PUT /api/v1/notes/{note_id}/shares/{principal}": {"level": "edit"},
    "PUT /api/v1/tables/{table_id}/shares/{principal}": {"level": "edit"},
    "POST /api/v1/notes/{note_id}/move-to-group": {"group": "testers"},
    "POST /api/v1/tables/{table_id}/move-to-group": {"group": "testers"},
    "POST /api/v1/screener/{sid}/decide": {"decision": "allow"},
    "PATCH /api/v1/calendar/events/{uid}": {"summary": "x"},
    "PATCH /api/v1/tasks/{uid}": {"summary": "x"},
    "PUT /api/v1/drafts/{mid}": {"payload": {"subject": "x", "text": "x"}},
    "PATCH /api/v1/rules/{rid}": {"name": "x", "query": "subject:x"},
    "PATCH /api/v1/identities/{iid}": {"email": "x@example.org"},
    "POST /api/v1/connectors/{cid}/arm-deletion": {"confirm": "x"},
    "POST /api/v1/calendar/events/{uid}/rsvp": {"response": "accepted", "send": False},
    "PUT /api/v1/calendar/collections/{calendar_id}/shares/{user_id}": {"level": "view"},
    "PATCH /api/v1/permissions/{rid}": {"subject": "*", "action": "mail.send", "effect": "allow"},
    "PATCH /api/v1/assistant/orders/{oid}": {"name": "x"},
    "POST /api/v1/assistant/orders/{oid}/run": {"conversation_id": 1},
    "POST /api/v1/assistant/orders/{oid}/test": {"conversation_id": 1},
    "POST /api/v1/speakr/recordings/{cid}/{rid}/follow-up": {"mode": "summary"},
    "POST /api/v1/speakr/recordings/{cid}/{rid}/tasks": {"items": [{"text": "x"}]},
    "PATCH /api/v1/speakr/recordings/{cid}/{rid}": {"title": "x"},
    "POST /api/v1/speakr/speakers/{cid}/person": {"key": "id:1", "speaker_name": "x", "email": "x@example.org"},
    "POST /api/v1/messages/{mid}/importance": {"important": True},
}
QUERIES = {"GET /api/v1/messages/{mid}/body-highlight": "?q=x",
           "DELETE /api/v1/speakr/speakers/{cid}/person": "?key=id:1&email=x@example.org"}
UPLOADS = {"PUT /api/v1/people/{email}/avatar", "PUT /api/v1/identities/{iid}/avatar",
           "POST /api/v1/admin/phone-apps/{app}"}
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")

# Status codes seen by the route sweep (printed with MAILR_IDOR_REPORT=1).
STATUS: dict[int, list[str]] = {}

# The caller's own namespace: 2xx is fine as long as nothing of the owner shows or changes.
OWN_NAMESPACE = ("/api/v1/people/", "/api/v1/avatars/")


async def _attacker(app):
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        u = await create_user(db, f"atk-{uuid.uuid4().hex[:8]}@example.com", "Attacker", password="attack-password-123")
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "attack-password-123"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    c.user_id = u.id
    # A sending address of its own, so draft routes get past "add an address first".
    from mailr.models import Connector, Identity
    async with sessionmaker()() as db:
        conn = Connector(user_id=u.id, kind="imap", name="atk", mode="mirror", data_class="normal", config={},
                         state={}, enabled=False)
        db.add(conn)
        await db.flush()
        db.add(Identity(user_id=u.id, email=f"atk-{uuid.uuid4().hex[:6]}@example.org", name="Attacker",
                        is_default=True, connector_id=conn.id))
        await db.commit()
    return c


async def test_no_route_accepts_another_users_ids(app, client, user, raw):
    ids = await _owner_objects(client, user, raw)
    ids["owner_id"] = user.id
    before = await _snapshot(user.id)
    atk = await _attacker(app)
    spec = app.openapi()
    unmapped, leaks, successes = [], [], []
    tried = 0
    for template, ops in spec["paths"].items():
        if "{" not in template or template in NOT_OWNER_OBJECTS:
            continue
        path = _fill(template, ids)
        if path is None:
            unmapped.append(template)
            continue
        for method in ops:
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            tried += 1
            key = f"{method.upper()} {template}"
            body = None if method in ("get", "delete") else {"name": "x", "label": "x", "decision": "approve",
                                                              "subject": "x", "enabled": True}
            body = VALID_BODIES.get(key, body)
            if key == "POST /api/v1/conversations/{cid}/split":
                body = {"message_ids": [ids["mid"]]}
            # Valid requests, so every call reaches the ownership check rather than stopping
            # at validation (a 422 would prove nothing).
            if key in UPLOADS:
                r = await atk.request(method.upper(), path, files={"file": ("a.png", PNG, "image/png")})
            else:
                r = await atk.request(method.upper(), path + QUERIES.get(key, ""), json=body)
            STATUS.setdefault(r.status_code, []).append(f"{method.upper()} {template}  {r.text[:110]}")
            text_ = r.text
            if MARK in text_ or (MARK.lower() in text_.lower()):
                leaks.append(f"{method.upper()} {template} -> {r.status_code} shows the owner's data")
            if 200 <= r.status_code < 300 and not template.startswith(OWN_NAMESPACE):
                successes.append(f"{method.upper()} {template} -> {r.status_code}: {text_[:120]}")
    await atk.aclose()
    after = await _snapshot(user.id)
    changed = [t for t in SNAPSHOT_TABLES if before[t] != after[t]]
    assert not unmapped, f"routes with identifiers not covered by the sweep: {unmapped}"
    assert tried > 60, tried
    assert not leaks, "\n".join(leaks)
    assert not successes, "\n".join(successes)
    assert not changed, f"the attacker changed the owner's {changed}: " + "; ".join(
        f"{t}: {[{k: (a[k], b[k]) for k in a if a[k] != b.get(k)} for a, b in zip(before[t], after[t])]}"  # noqa: B905
        f" rows {len(before[t])}->{len(after[t])}" for t in changed)


async def test_no_agent_tool_accepts_another_users_ids(app, client, user, raw):
    ids = await _owner_objects(client, user, raw)
    before = await _snapshot(user.id)
    atk = await _attacker(app)
    calls = {
        "get_conversation": {"conversation_id": ids["conv"]},
        "build_context": {"conversation_id": ids["conv"]},
        "find_passages": {"conversation_id": ids["conv"], "q": MARK},
        "get_message_body": {"message_id": ids["mid"]},
        "apply_label": {"conversation_ids": [ids["conv"]], "label": "x"},
        "archive": {"conversation_ids": [ids["conv"]]},
        "modify_conversations": {"conversation_ids": [ids["conv"]], "action": "trash"},
        "get_draft": {"draft_id": ids["draft"]},
        "update_draft": {"draft_id": ids["draft"], "subject": "x"},
        "send_draft": {"draft_id": ids["draft"]},
        "create_draft": {"reply_to_message_id": ids["mid"], "body": "x"},
        "rsvp": {"message_id": ids["mid"], "response": "accepted"},
        "forget": {"memory_id": ids["memory"]},
        "remember": {"text": "x", "memory_id": ids["memory"]},
        "upsert_entity": {"name": "x", "entity_id": ids["entity"]},
        "get_approval_status": {"approval_id": ids["pending"]},
        "get_outbox_status": {"outbox_id": ids["outbox"]},
        "manage_filter": {"action": "delete", "filter_id": ids["rule"]},
        "manage_layout": {"action": "delete", "layout_id": ids["layout"]},
        "update_signature": {"identity_id": ids["identity"], "signature": "x"},
        "create_event": {"summary": "x", "start": "2030-01-01T10:00:00", "conversation_id": ids["conv"]},
        "create_task": {"summary": "x", "conversation_id": ids["conv"]},
        "get_recording": {"recording_id": 5, "connector_id": ids["speakr"]},
        "get_transcript": {"recording_id": 5},
        "link_recording": {"recording_id": 5, "conversation_id": ids["conv"]},
        "draft_follow_up": {"recording_id": 5},
        "propose_action_items": {"recording_id": 5},
        "get_table": {"table": ids["table"]},
        "table_stats": {"table": ids["table"]},
        "table_history": {"table": ids["table"]},
        "table_rows": {"table": ids["table"], "action": "edit", "rows": [{"row_id": ids["table_row"], "values": {"x": 1}}]},
        "table_schema": {"table": ids["table"], "action": "rename", "name": "x"},
        "table_state": {"table": ids["table"], "action": "pin"},
        "delete_table": {"table": ids["table"]},
    }
    problems = []
    for name, args in calls.items():
        r = await atk.post(f"/api/v1/agent/tools/{name}", json=args)
        body = r.text
        if MARK.lower() in body.lower():
            problems.append(f"{name} showed the owner's data: {body[:160]}")
        if r.status_code in (200, 202):
            data = r.json()
            # A tool may answer "nothing found" in a 200; it must not act or return the object.
            if r.status_code == 202 or json.dumps(data.get("result")).count(str(ids["conv"])) and name != "create_event":
                problems.append(f"{name} -> {r.status_code}: {body[:160]}")
    await atk.aclose()
    after = await _snapshot(user.id)
    changed = [t for t in SNAPSHOT_TABLES if before[t] != after[t]]
    assert not problems, "\n".join(problems)
    assert not changed, f"the attacker changed the owner's {changed}: " + "; ".join(
        f"{t}: {[{k: (a[k], b[k]) for k in a if a[k] != b.get(k)} for a, b in zip(before[t], after[t])]}"  # noqa: B905
        f" rows {len(before[t])}->{len(after[t])}" for t in changed)


async def test_no_request_body_accepts_another_users_ids(app, client, user, raw, fake_llm):
    """Routes that take identifiers in the body or query rather than the path."""
    from helpers.agent import _drop_endpoint, _endpoint
    from helpers.security_authz import _caldav, _radicale_up

    ids = await _owner_objects(client, user, raw)
    atk = await _attacker(app)
    # The attacker gets a model and (when the dev Radicale runs) a calendar, so routes get past
    # "no model" / "no calendar" and reach their identifier checks.
    ep = await _endpoint(atk.user_id, "local")
    fake_llm.replies = ['{"title": "x", "start": null, "end": null}'] * 10 if hasattr(fake_llm, "replies") else None
    if _radicale_up():
        await _caldav(atk.user_id)
    before = await _snapshot(user.id)
    conv, mid, label = ids["conv"], ids["mid"], ids["label"]
    calls = [
        ("POST", "/api/v1/conversations/actions", {"ids": [conv], "action": "trash"}),
        ("POST", "/api/v1/conversations/actions", {"ids": [conv], "action": "add_labels", "label_ids": [label]}),
        ("POST", "/api/v1/conversations/actions", {"ids": [conv], "action": "read"}),
        ("POST", "/api/v1/conversations/merge", {"ids": [conv, conv]}),
        ("POST", "/api/v1/send", {"draft_id": ids["draft"], "payload": {"subject": "x", "text": "x",
                                                                    "to": [{"email": "y@example.org", "name": ""}]}}),
        ("POST", "/api/v1/rules/reorder", {"ids": [ids["rule"]]}),
        ("POST", "/api/v1/context", {"conversation_id": conv}),
        ("POST", "/api/v1/agent/draft", {"conversation_id": conv, "message_id": mid, "mode": "reply", "instructions": "x"}),
        ("POST", "/api/v1/agent/draft", {"mode": "new", "draft_id": ids["draft"], "instructions": "x"}),
        ("POST", "/api/v1/tasks", {"summary": "x", "conversation_id": conv}),
        ("POST", "/api/v1/calendar/from-message", {"message_id": mid}),
        ("POST", "/api/v1/calendar/from-message/confirm", {"message_id": mid, "kind": "event", "title": "x",
                                                            "start": "2030-01-01T10:00:00"}),
        ("POST", "/api/v1/calendar/invite-reply", {"message_id": mid, "response": "accepted"}),
        ("POST", "/api/v1/assistant/skills/test", {"skill_id": ids["skill"], "sample": "x"}),
        ("GET", f"/api/v1/assistant/memories?entity_id={ids['entity']}", None),
        ("GET", f"/api/v1/assistant/entities?conversation_ids={conv}", None),
        ("POST", "/api/v1/tokens", {"name": "x", "scopes": ["read"], "connector_ids": [ids["connector"]]}),
        ("POST", "/api/v1/identities", {"email": "atk2@example.org", "connector_id": ids["connector"]}),
    ]
    problems = []
    for method, path, body in calls:
        r = await atk.request(method, path, json=body)
        STATUS.setdefault(r.status_code, []).append(f"body: {method} {path}  {r.text[:110]}")
        if MARK.lower() in r.text.lower():
            problems.append(f"{method} {path} showed the owner's data: {r.text[:160]}")
        # Only these may answer 2xx, and only as "nothing of yours matched".
        ok = r.status_code < 300 and (
            (path == "/api/v1/conversations/actions" and r.json().get("affected") == 0)
            or path == "/api/v1/rules/reorder"  # ignores ids that are not the caller's
            or (method == "GET" and not r.json().get("items")))
        if 200 <= r.status_code < 300 and not ok:
            problems.append(f"{method} {path} -> {r.status_code}: {r.text[:160]}")
    await atk.aclose()
    await _drop_endpoint(ep)
    after = await _snapshot(user.id)
    changed = [t for t in SNAPSHOT_TABLES if before[t] != after[t]]
    assert not problems, "\n".join(problems)
    assert not changed, f"the attacker changed the owner's {changed}: " + "; ".join(
        f"{t}: {[{k: (a[k], b[k]) for k in a if a[k] != b.get(k)} for a, b in zip(before[t], after[t])]}"  # noqa: B905
        f" rows {len(before[t])}->{len(after[t])}" for t in changed)


def teardown_module(module):
    import os

    if os.environ.get("MAILR_IDOR_REPORT"):
        for code, rs in sorted(STATUS.items()):
            print(f"\n{code}: {len(rs)}")
            if True:
                for r in rs:
                    print("   ", r)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Keep the sweep offline: no connector may reach a real server."""
    import socket

    real = socket.create_connection

    def guarded(addr, *a, **kw):
        host = addr[0] if isinstance(addr, tuple) else addr
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise OSError(f"network disabled in the sweep ({host})")
        return real(addr, *a, **kw)
    monkeypatch.setattr(socket, "create_connection", guarded)
