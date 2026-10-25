"""FakeSpeakr: Speakr's API as an httpx.MockTransport handler.

No test talks to a real Speakr. Served by the fake: /api/v1/users/me, /stats, /recordings (paginated),
/recordings/{id} and its /transcript, /summary, /notes, /events, /speakers, /status and /chat, and
/api/inquire/search, the writes (/recordings/upload with title, meeting_date,
folder_id and tag_ids, PATCH /recordings/{id}, PUT /recordings/{id}/notes, POST /recordings/{id}/tags,
/api/recording/{id}/share), /tags and /folders, and /webhooks (create, delete, test with a delivery
carried by `deliver`, deliveries), with switches for 401, 403, 404, 413, 429 with
Retry-After, 500, an HTML login page,
both transcript key sets, plain-text transcripts, removed audio and deleted recordings. `calls` lists
every path asked for, so a test can check that Speakr's own models (/chat, /summarize, inquire) are
never used.

With the `speakr` fixture, Speakr requests go to the fake, and netguard accepts the fake host
`speakr.test`.

Old and new server. By default the fake is an older Speakr (`old = True`, the production image
of 2 Oct 2026): /capabilities and /tokens/current answer 404 and none of the routes of the mailr
API changes exist. `speakr.modern()` turns it into the Speakr of commit 93b9ebca:
capabilities (`features`, `models_local`), scoped tokens
(`token_scopes`, 403 insufficient_scope), the changes feed with tombstones and a cursor
(`touch(rid)` after a test changes a recording directly; `expire_cursor` answers 410 once), ETags
with 304 (`not_modified` counts them), v1 search (`searches` lists the modes asked), upload
participants, external_refs and idempotency_key, PUT tags (an old server answers 405), external
references, events with attendees and reminders, the canonical transcript shape, speakers with
ids and addresses, v1 share links, chat with sources, shared recordings (`share_with_me`,
`unshare`) and the webhook V2 header with two v1 values during a rotation (`previous_secret`).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

BASE = "https://speakr.test"
TOKEN = "spk_FAKE-token-0123456789-abcdefghij"


class FakeSpeakr:
    def __init__(self) -> None:
        self.token = TOKEN
        self.user = {"id": 7, "username": "dana", "email": "dana@speakr.example", "name": "Dana Speakr",
                     "preferences": {"extract_events": True}}
        self.recordings: dict[int, dict[str, Any]] = {}
        self.transcripts: dict[int, Any] = {}
        self.summaries: dict[int, str] = {}
        self.notes: dict[int, str] = {}
        self.speakers: dict[int, list[dict[str, Any]]] = {}
        self.events: dict[int, list[dict[str, Any]]] = {}
        self.calls: list[str] = []
        self.queries: list[tuple[str, dict[str, str]]] = []
        # Switches: a status for every request (or for paths matching a regex), Retry-After for 429,
        # and an HTML page instead of JSON.
        self.status: int | None = None
        self.status_for: dict[str, int] = {}
        self.retry_after: str | None = None
        self.html = False
        # Writes: uploads received, PATCH/PUT bodies, share links handed out, and
        # the upload size limit (a larger file gets 413).
        self.uploads: list[dict[str, Any]] = []
        self.writes: list[tuple[str, str, Any]] = []
        self.shares: dict[int, str] = {}
        self.max_upload = 50 * 1024 * 1024
        # An older Speakr reads only the file, notes and file_last_modified on upload.
        self.old_upload = False
        # Speakr's /speakers answers null names and ids (its placeholder loop) when this is on.
        self.null_speakers = False
        self.tags = [{"id": 1, "name": "Work", "color": "#888", "is_group_tag": False},
                     {"id": 2, "name": "Advising", "color": "#0a0", "is_group_tag": False}]
        self.folders = [{"id": 1, "name": "Meetings", "color": "#333", "group_id": None},
                        {"id": 2, "name": "Advising", "color": "#555", "group_id": None}]
        # Webhooks: id → {name, url, events, secret, enabled}; deliveries: id → the delivery row.
        # `deliver` is an async callable(url, body, headers) → status code, set by a test to carry a
        # test delivery to mailr (Speakr's dispatcher); `refuse_webhook_url` makes the create
        # call fail as Speakr does for a private address.
        self.webhooks: dict[int, dict[str, Any]] = {}
        self.deliveries: dict[int, dict[str, Any]] = {}
        self.deliver = None
        self.refuse_webhook_url: str | None = None
        self.v2 = False
        self.previous_secret: str | None = None
        # The newer Speakr (modern()).
        self.old = True
        self.features: dict[str, Any] = {}
        self.models_local = False
        self.token_scopes: list[str] = ["full"]
        self.seq = 0
        self.changed: dict[int, int] = {}  # rid → seq of its last change
        self.tombstones: list[dict[str, Any]] = []  # {id, seq, reason}
        self.expire_cursor = False
        self.not_modified = 0
        self.searches: list[str] = []
        self.refs: dict[int, list[dict[str, Any]]] = {}
        self.idem: dict[str, int] = {}
        self.shared: dict[int, dict[str, Any]] = {}  # rid → {owner, can_edit}
        self.public: dict[int, dict[str, Any]] = {}  # share id → {recording_id, share_summary, share_notes}
        self.profiles: list[dict[str, Any]] = [{"id": 1, "name": "Dana", "email": None, "aliases": []},
                                               {"id": 2, "name": "Evan", "email": "evan@example.org", "aliases": []}]
        self.chat_reply: dict[str, Any] | None = None

    def modern(self, **features: Any) -> FakeSpeakr:
        """The Speakr with the mailr API changes (commit 93b9ebca). Keyword arguments override features."""
        self.old = False
        self.features = {"token_scopes": True, "changes_feed": True, "etags": True, "webhook_signature_v2": True,
                         "external_refs": True, "upload_idempotency": True, "share_links": True, "chat_sources": True,
                         "internal_sharing": True, "public_sharing": True, "can_share_publicly": True,
                         "search": {"keyword": True, "semantic": True}, "inquire": {"enabled": True, "agent": True}}
        self.features.update(features)
        self.v2 = True
        for rid in self.recordings:
            self.touch(rid)
        return self

    def touch(self, rid: int) -> None:
        self.seq += 1
        self.changed[rid] = self.seq

    def share_with_me(self, rid: int, owner: str = "Evan Ross", can_edit: bool = False, **kw: Any) -> dict[str, Any]:
        rec = self.add(rid, **kw)
        self.shared[rid] = {"owner": owner, "can_edit": can_edit}
        self.touch(rid)
        return rec

    def unshare(self, rid: int) -> None:
        self.shared.pop(rid, None)
        self.recordings.pop(rid, None)
        self.seq += 1
        self.tombstones.append({"id": rid, "seq": self.seq, "reason": "access_revoked"})

    # ---------------------------------------------------------------- data

    def add(self, rid: int, title: str = "Weekly sync", *, meeting_date: datetime | None = None,
            duration: float = 1800, participants: str = "Dana, Evan", status: str = "COMPLETED",
            summary: str | None = "## Decisions\n- Ship the budget on Friday\n- Evan drafts the plan",
            segments: list[tuple[float, str, str]] | None = None, key_set: str = "new", plain: str | None = None,
            notes: str = "", tags: list[str] | None = None, audio: bool = True) -> dict[str, Any]:
        md = meeting_date or (datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0)
        rec = {"id": rid, "title": title, "status": status,
               "created_at": (md + timedelta(minutes=31)).replace(tzinfo=None).isoformat(),
               "completed_at": (md + timedelta(minutes=40)).replace(tzinfo=None).isoformat(),
               "meeting_date": md.replace(tzinfo=None).isoformat(), "participants": participants,
               "audio_duration": duration, "has_transcription": True, "has_summary": summary is not None,
               "audio_available": audio, "is_archived": False,
               "folder": {"id": 1, "name": "Meetings"},
               "tags": [{"id": i + 1, "name": t, "color": "#888"} for i, t in enumerate(tags or ["Work"])]}
        self.recordings[rid] = rec
        if summary is not None:
            self.summaries[rid] = summary
        self.notes[rid] = notes
        if plain is not None:
            self.transcripts[rid] = {"segments": [], "raw": plain}
        else:
            segs = segments or [(0, "Dana", "Welcome everyone to the weekly sync."),
                                (12, "Evan", "The budget numbers are almost done."),
                                (754, "Dana", "We agreed to ship the budget on Friday."),
                                (790, "Evan", "I will draft the plan by Monday.")]
            rows = []
            for i, (t, spk, text) in enumerate(segs):
                end = segs[i + 1][0] if i + 1 < len(segs) else t + 20
                rows.append({"speaker": spk, "sentence": text, "start_time": t, "end_time": end} if key_set == "new"
                            else {"speaker": spk, "text": text, "start": t, "end": end})
            self.transcripts[rid] = {"segments": rows}
        self.speakers[rid] = [{"label": "SPEAKER_00", "identified_name": "Dana", "speaker_id": 1, "segment_count": 2}]
        self.events[rid] = [{"id": 1, "title": "Budget review", "start_datetime": (md + timedelta(days=7)).isoformat(),
                             "end_datetime": (md + timedelta(days=7, hours=1)).isoformat(), "location": "Room 4",
                             "description": "Follow-up"}]
        self.touch(rid)
        return rec

    def delete(self, rid: int) -> None:
        self.recordings.pop(rid, None)
        self.seq += 1
        self.tombstones.append({"id": rid, "seq": self.seq, "reason": "deleted"})

    # ---------------------------------------------------------------- the handler

    def _json(self, data: Any, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(status, content=json.dumps(data).encode(),
                              headers={"content-type": "application/json", **(headers or {})})

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        self.queries.append((path, dict(request.url.params)))
        if self.html:
            return httpx.Response(200, text="<!doctype html><html><body>Sign in</body></html>",
                                  headers={"content-type": "text/html"})
        status = self.status
        for rx, st in self.status_for.items():
            if re.search(rx, path):
                status = st
        if status:
            headers = {"Retry-After": self.retry_after} if status == 429 and self.retry_after else {}
            if status == 401:
                headers["WWW-Authenticate"] = "Bearer"
            return self._json({"error": f"status {status}"}, status, headers)
        if request.headers.get("authorization") != f"Bearer {self.token}" or "token" in request.url.params:
            return self._json({"error": "Authentication required"}, 401, {"WWW-Authenticate": "Bearer"})
        q = request.url.params
        if not self.old:
            out = self._modern(request, path)
            if out is not None:
                return out
        if path == "/api/v1/users/me":
            return self._json(self.user)
        if path == "/api/v1/stats":
            done = sum(1 for r in self.recordings.values() if r["status"] == "COMPLETED")
            return self._json({"recordings": {"total": len(self.recordings), "completed": done,
                                              "processing": len(self.recordings) - done, "pending": 0, "failed": 0}})
        if path == "/api/v1/recordings":
            page, per = int(q.get("page", 1)), min(int(q.get("per_page", 25)), 100)
            rows = sorted((r for r in self.recordings.values() if r["id"] not in self.shared or q.get("scope") == "all"),
                          key=lambda r: r["created_at"], reverse=True)
            if not self.old:
                rows = [self._item(r) for r in rows]
            chunk = rows[(page - 1) * per: page * per]
            pages = max(1, -(-len(rows) // per))
            return self._json({"recordings": chunk, "pagination": {"page": page, "per_page": per, "total": len(rows),
                                                                   "total_pages": pages, "has_next": page < pages,
                                                                   "has_prev": page > 1}})
        if path == "/api/v1/tags" and request.method == "GET":
            return self._json({"tags": self.tags})
        if path == "/api/v1/folders" and request.method == "GET":
            return self._json(self.folders)
        if path.startswith("/api/v1/webhooks"):
            return self._webhooks(request, path)
        m = re.fullmatch(r"/api/v1/recordings/(\d+)/tags", path)
        if m and request.method == "POST":
            rid = int(m.group(1))
            if rid not in self.recordings:
                return self._json({"error": "Recording not found"}, 404)
            body = json.loads(request.content or b"{}")
            self.writes.append(("POST", path, body))
            for tid in body.get("tag_ids") or []:
                t = next((x for x in self.tags if x["id"] == int(tid)), None)
                if t and all(x["id"] != t["id"] for x in self.recordings[rid]["tags"]):
                    self.recordings[rid]["tags"].append({"id": t["id"], "name": t["name"], "color": t["color"]})
            return self._json({"success": True, "tags": self.recordings[rid]["tags"]})
        m = re.fullmatch(r"/api/v1/recordings/(\d+)/tags/(\d+)", path)
        if m and request.method == "DELETE":
            rid, tid = int(m.group(1)), int(m.group(2))
            self.writes.append(("DELETE", path, None))
            self.recordings[rid]["tags"] = [t for t in self.recordings[rid]["tags"] if t["id"] != tid]
            self.touch(rid)
            return self._json({"success": True})
        if re.fullmatch(r"/api/v1/recordings/\d+/tags", path) and request.method == "PUT":
            return self._json({"error": "Method Not Allowed"}, 405)  # an older Speakr has no PUT
        if path == "/api/v1/recordings/upload" and request.method == "POST":
            return self._upload(request)
        m = re.fullmatch(r"/api/recording/(\d+)/share", path)
        if m and request.method == "POST":
            rid = int(m.group(1))
            if rid not in self.recordings:
                return self._json({"error": "Recording not found"}, 404)
            existing = rid in self.shares
            self.shares.setdefault(rid, f"{BASE}/share/{rid}-abcdef")
            self.writes.append(("POST", path, json.loads(request.content or b"{}")))
            return self._json({"share_url": self.shares[rid], "existing": existing})
        m = re.fullmatch(r"/api/v1/recordings/(\d+)(/notes)?", path)
        if m and request.method in ("PATCH", "PUT"):
            rid = int(m.group(1))
            if rid not in self.recordings:
                return self._json({"error": "Recording not found"}, 404)
            body = json.loads(request.content or b"{}")
            self.writes.append((request.method, path, body))
            if m.group(2):
                self.notes[rid] = body.get("notes") or ""
                return self._json({"success": True})
            self.touch(rid)
            for k in ("title", "participants", "meeting_date"):
                if k in body:
                    self.recordings[rid][k] = body[k]
            if "folder_id" in body:
                f = next((x for x in self.folders if x["id"] == body["folder_id"]), None)
                self.recordings[rid]["folder_id"] = body["folder_id"]
                self.recordings[rid]["folder"] = {"id": f["id"], "name": f["name"]} if f else None
            return self._json({**self.recordings[rid]})
        if path == "/api/inquire/search" or path.endswith("/chat") or path.endswith("/summarize"):
            return self._json({"response": "Speakr's model answered", "sources": []})
        m = re.fullmatch(r"/api/v1/recordings/(\d+)(?:/(\w+))?", path)
        if m:
            rid, part = int(m.group(1)), m.group(2)
            if rid not in self.recordings:
                return self._json({"error": "Recording not found"}, 404)
            rec = self.recordings[rid]
            if part is None:
                return self._json({**rec, "summary": self.summaries.get(rid), "notes": self.notes.get(rid)})
            if part == "transcript":
                return self._json(self.transcripts.get(rid) or {"segments": []})
            if part == "summary":
                return self._json({"summary": self.summaries.get(rid), "has_summary": rid in self.summaries})
            if part == "notes":
                return self._json({"notes": self.notes.get(rid) or "", "has_notes": bool(self.notes.get(rid))})
            if part == "speakers":
                rows = self.speakers.get(rid, [])
                if self.null_speakers:
                    rows = [{**x, "identified_name": None, "speaker_id": None} for x in rows]
                return self._json({"speakers": rows, "suggestions": {}})
            if part == "events":
                return self._json({"events": self.events.get(rid, [])})
            if part == "status":
                return self._json({"status": rec["status"], "queue_position": None, "error_message": None})
        return self._json({"error": "not found"}, 404)


    # ---------------------------------------------------------------- the newer Speakr

    def _item(self, rec: dict[str, Any]) -> dict[str, Any]:
        """One recording as API v1.1 lists it: updated_at, the caller's refs and the sharing fields."""
        rid = rec["id"]
        sh = self.shared.get(rid)
        return {**rec, "updated_at": f"2026-10-02T10:00:{self.changed.get(rid, 0) % 60:02d}.000000Z",
                "external_refs": self.refs.get(rid, []), "is_shared": bool(sh),
                "owner": {"id": 3, "username": "evan", "name": sh["owner"]} if sh else None,
                "share": {"id": 51, "can_edit": sh["can_edit"], "can_reshare": False, "source": "manual",
                          "shared_at": "2026-09-20T10:00:00.000000Z"} if sh else None}

    def _scope_for(self, method: str, path: str) -> str | None:
        if path in ("/api/v1/tokens/current", "/api/v1/capabilities"):
            return None
        if path.startswith("/api/v1/webhooks"):
            return "webhooks"
        if path.endswith("/upload"):
            return "upload"
        if path.endswith("/chat") or path == "/api/v1/inquire":
            return "process"
        if "/share" in path:
            return "share"
        if method == "GET":
            return "read"
        if method == "DELETE" and re.fullmatch(r"/api/v1/recordings/\d+", path):
            return "delete"
        return "write"

    def _etag(self, request: httpx.Request, body: Any) -> httpx.Response:
        tag = 'W/"' + str(abs(hash(json.dumps(body, sort_keys=True, default=str)))) + '"'
        if request.headers.get("if-none-match") == tag:
            self.not_modified += 1
            return httpx.Response(304, headers={"etag": tag})
        return self._json(body, 200, {"etag": tag, "cache-control": "private, no-cache"})

    def _modern(self, request: httpx.Request, path: str) -> httpx.Response | None:
        """The routes of the mailr API changes; None hands the request to the older handler."""
        q = request.url.params
        method = request.method
        need = self._scope_for(method, path)
        if need and "full" not in self.token_scopes and need not in self.token_scopes:
            return self._json({"error": f"This token does not have the '{need}' scope", "code": "insufficient_scope",
                               "required_scopes": [need], "token_scopes": self.token_scopes}, 403,
                              {"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{need}"'})
        is_json = request.headers.get("content-type", "").startswith("application/json")
        body = json.loads(request.content) if is_json and request.content else {}
        if path == "/api/v1/capabilities":
            return self._json({"speakr_version": "v0.10.11-alpha", "api_version": "1.1", "features": self.features,
                               "models_local": self.models_local})
        if path == "/api/v1/tokens/current":
            return self._json({"id": 14, "name": "mailr", "scopes": self.token_scopes,
                               "created_at": "2026-10-02T09:00:00.000000Z", "expires_at": "2027-10-02T09:00:00.000000Z",
                               "last_used_at": None, "via": "header"})
        if path == "/api/v1/recordings/changes":
            if self.expire_cursor and q.get("cursor"):
                self.expire_cursor = False
                return self._json({"error": "The cursor is too old", "code": "cursor_expired"}, 410)
            since = int(q.get("cursor") or 0)
            limit = int(q.get("limit") or 100)
            scope = q.get("scope", "own")
            rows: list[tuple[int, dict[str, Any]]] = []
            for rid, rec in self.recordings.items():
                if rid in self.shared and scope != "all":
                    continue
                if since == 0 or self.changed.get(rid, 0) > since:
                    rows.append((self.changed.get(rid, 0), {"type": "upsert", "recording": self._item(rec)}))
            if since:
                rows += [(t["seq"], {"type": "delete", "id": t["id"], "deleted_at": "2026-10-02T10:05:00.000000Z",
                                     "reason": t["reason"]}) for t in self.tombstones if t["seq"] > since
                         and (t["reason"] != "access_revoked" or scope != "own")]
            rows.sort(key=lambda x: x[0])
            page = rows[:limit]
            nxt = page[-1][0] if page and len(rows) > limit else self.seq
            return self._json({"changes": [c for _, c in page], "next_cursor": str(nxt), "has_more": len(rows) > limit})
        if path == "/api/v1/search":
            mode = q.get("mode", "keyword")
            self.searches.append(mode)
            if mode == "semantic" and not (self.features.get("search") or {}).get("semantic"):
                return self._json({"error": "Semantic search is not available", "code": "semantic_unavailable"}, 409)
            terms = [t for t in q.get("q", "").lower().split() if t]
            ids = {int(x) for x in q.get("recording_ids", "").split(",") if x.strip()}
            results = []
            for rid, rec in sorted(self.recordings.items(), reverse=True):
                if (ids and rid not in ids) or (rid in self.shared and q.get("scope") != "all"):
                    continue
                segs = (self.transcripts.get(rid) or {}).get("segments") or []
                for i, sg in enumerate(segs):
                    text = sg.get("sentence") or sg.get("text") or ""
                    if all(t in text.lower() for t in terms):
                        results.append({"recording_id": rid, "title": rec["title"], "meeting_date": rec["meeting_date"],
                                        "field": "transcript", "segment_index": None if mode == "semantic" else i,
                                        "start_time": sg.get("start_time", sg.get("start")), "end_time": None,
                                        "speaker": sg.get("speaker"), "text": text, "match_spans": [],
                                        "score": 0.7})
                for line in (self.notes.get(rid) or "").split("\n"):
                    if line.strip() and all(t in line.lower() for t in terms):
                        results.append({"recording_id": rid, "title": rec["title"], "field": "notes", "segment_index": None,
                                        "start_time": None, "end_time": None, "speaker": None, "text": line,
                                        "match_spans": [], "score": 0.8})
            return self._json({"query": q.get("q"), "mode_used": mode, "results": results[: int(q.get("limit") or 20)],
                               "page": 1, "has_more": False})
        if path == "/api/v1/speakers" and method == "GET":
            return self._json({"speakers": [{**x, "use_count": 1, "updated_at": None} for x in self.profiles]})
        m = re.fullmatch(r"/api/v1/speakers/(\d+)", path)
        if m and method == "PUT":
            sp = next((x for x in self.profiles if x["id"] == int(m.group(1))), None)
            if sp is None:
                return self._json({"error": "Speaker not found"}, 404)
            self.writes.append(("PUT", path, body))
            if "email" in body:
                sp["email"] = (body["email"] or "").lower() or None
            return self._json(sp)
        m = re.fullmatch(r"/api/v1/recordings/(\d+)/tags", path)
        if m and method == "PUT":
            rid = int(m.group(1))
            if rid not in self.recordings:
                return self._json({"error": "Recording not found"}, 404)
            self.writes.append(("PUT", path, body))
            want = [next((x for x in self.tags if x["id"] == int(t)), None) for t in body.get("tag_ids") or []]
            if any(t is None for t in want):
                return self._json({"error": "Unknown tags", "code": "invalid_parameter"}, 400)
            before = {t["id"] for t in self.recordings[rid]["tags"]}
            self.recordings[rid]["tags"] = [{"id": t["id"], "name": t["name"], "color": t["color"]} for t in want]
            self.touch(rid)
            return self._json({"tags": self.recordings[rid]["tags"], "added": [t["id"] for t in want if t["id"] not in before],
                               "removed": [i for i in before if i not in {t["id"] for t in want}]})
        m = re.fullmatch(r"/api/v1/recordings/(\d+)/external-refs", path)
        if m and method == "PUT":
            rid = int(m.group(1))
            self.writes.append(("PUT", path + "?system=" + q.get("system", ""), body))
            keep = [r for r in self.refs.get(rid, []) if r["system"] != q.get("system")]
            self.refs[rid] = keep + [{"id": 70 + i, "created_at": "2026-10-02T10:00:00.000000Z", "url": None, "label": None,
                                      **r} for i, r in enumerate(body.get("external_refs") or [])]
            self.touch(rid)
            return self._json({"external_refs": self.refs[rid]})
        m = re.fullmatch(r"/api/v1/recordings/(\d+)/share", path)
        if m and method == "POST":
            rid = int(m.group(1))
            if rid not in self.recordings or rid in self.shared:
                return self._json({"error": "Recording not found", "code": "not_found"}, 404)
            self.writes.append(("POST", path, body))
            want = {"share_summary": body.get("share_summary", True), "share_notes": body.get("share_notes", False)}
            mine = [(i, x) for i, x in self.public.items() if x["recording_id"] == rid]
            if mine and not body.get("force_new"):
                sid, sh = mine[-1]
                differ = any(sh[k] != v for k, v in want.items())
                return self._json({"share_url": f"{BASE}/share/{sid}-x", "existing": True, "flags_differ": differ,
                                   "share": {"id": sid, **{k: sh[k] for k in want}, "created_at": "2026-10-02T09:00:00Z",
                                             "expires_at": None}})
            sid = max([*self.public, 30]) + 1
            self.public[sid] = {"recording_id": rid, **want}
            return self._json({"share_url": f"{BASE}/share/{sid}-x", "existing": False, "flags_differ": False,
                               "share": {"id": sid, **want, "created_at": "2026-10-02T09:00:00Z", "expires_at": None}}, 201)
        m = re.fullmatch(r"/api/v1/recordings/(\d+)/shares", path)
        if m and method == "GET":
            rid = int(m.group(1))
            return self._json({"shares": [{"id": i, "share_url": f"{BASE}/share/{i}-x", "share_summary": x["share_summary"],
                                           "share_notes": x["share_notes"], "created_at": "2026-10-02T09:00:00Z",
                                           "expires_at": None} for i, x in self.public.items() if x["recording_id"] == rid]})
        m = re.fullmatch(r"/api/v1/shares/(\d+)", path)
        if m and method == "DELETE":
            self.writes.append(("DELETE", path, None))
            if self.public.pop(int(m.group(1)), None) is None:
                return self._json({"error": "Share not found", "code": "not_found"}, 404)
            return httpx.Response(204)
        m = re.fullmatch(r"/api/v1/recordings/(\d+)/chat", path)
        if m and method == "POST":
            self.writes.append(("POST", path, body))
            return self._json(self.chat_reply or {"response": "Speakr's model answered", "sources": []})
        m = re.fullmatch(r"/api/v1/recordings/(\d+)(?:/(transcript|summary|notes|speakers|events))?", path)
        if m and method == "GET":
            rid, part = int(m.group(1)), m.group(2)
            if rid not in self.recordings:
                return self._json({"error": "Recording not found"}, 404)
            if part is None:
                return self._etag(request, {**self._item(self.recordings[rid]), "summary": self.summaries.get(rid),
                                            "notes": self.notes.get(rid)})
            if part == "transcript":
                t = self.transcripts.get(rid) or {"segments": []}
                if t.get("raw") and not t.get("segments"):
                    return self._etag(request, {"format": "json", "kind": "plain", "segments": [], "raw": t["raw"]})
                segs = []
                for i, sg in enumerate(t.get("segments") or []):
                    who = sg.get("speaker") or ""
                    sid = next((x["id"] for x in self.profiles if x["name"] == who), None)
                    text = sg.get("sentence") if sg.get("sentence") is not None else sg.get("text")
                    st = sg.get("start_time", sg.get("start"))
                    if q.get("start") and st is not None and st < float(q["start"]):
                        continue
                    if q.get("end") and st is not None and st > float(q["end"]):
                        continue
                    segs.append({"index": i, "speaker": who, "speaker_label": f"SPEAKER_{i % 2:02d}", "speaker_id": sid,
                                 "text": text, "sentence": text, "start_time": st, "end_time": sg.get("end_time", sg.get("end"))})
                return self._etag(request, {"format": "json", "kind": "segments", "segments": segs, "duration": 1800.0})
            if part == "speakers":
                rows = [{**x, "speaker_id": next((p["id"] for p in self.profiles if p["name"] == x.get("identified_name")), None),
                         "email": next((p["email"] for p in self.profiles if p["name"] == x.get("identified_name")), None)}
                        for x in self.speakers.get(rid, [])]
                return self._etag(request, {"speakers": rows, "suggestions": {}})
            if part == "events":
                return self._etag(request, {"events": [{**e, "recording_id": rid, "floating": True, "reminder_minutes": 15,
                                                        "attendees": [{"name": "Dana Lee", "email": None},
                                                                      {"name": "Priya Shah", "email": "priya@example.org"}],
                                                        "start_datetime": e["start_datetime"][:19],
                                                        "end_datetime": e["end_datetime"][:19]}
                                                       for e in self.events.get(rid, [])]})
            if part == "summary":
                return self._etag(request, {"summary": self.summaries.get(rid), "has_summary": rid in self.summaries})
            if part == "notes":
                return self._etag(request, {"notes": self.notes.get(rid) or "", "has_notes": bool(self.notes.get(rid))})
        return None

    def _upload(self, request: httpx.Request) -> httpx.Response:
        """Multipart: the file and the form fields as Speakr reads them; 202 with the new recording."""
        from email.parser import BytesParser
        from email.policy import default

        ctype = request.headers.get("content-type", "")
        msg = BytesParser(policy=default).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + request.content)
        fields: dict[str, Any] = {}
        file: dict[str, Any] | None = None
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if part.get_filename():
                file = {"filename": part.get_filename(), "type": part.get_content_type(),
                        "content": part.get_payload(decode=True)}
            else:
                fields[name] = part.get_content().strip() if hasattr(part, "get_content") else part.get_payload()
        if file is None:
            return self._json({"error": "No file"}, 400)
        if not self.old and fields.get("idempotency_key") in self.idem:
            rid = self.idem[fields["idempotency_key"]]
            return self._json({**self._item(self.recordings[rid]), "idempotent_replay": True}, 200)
        if len(file["content"]) > self.max_upload:
            return self._json({"error": "File too large"}, 413)
        rid = max([*self.recordings, 1000]) + 1
        self.uploads.append({"id": rid, "file": file, "fields": fields})
        title = file["filename"].rsplit(".", 1)[0]
        md = None
        if not self.old_upload:
            title = fields.get("title") or title
            if fields.get("meeting_date"):
                md = datetime.fromisoformat(fields["meeting_date"].replace("Z", "+00:00")).astimezone(UTC)
        rec = self.add(rid, title, status="PENDING", summary=None, meeting_date=md, participants="", notes=fields.get("notes") or "",
                       tags=[])
        rec["tags"] = []
        rec["folder"], rec["folder_id"] = None, None
        if not self.old_upload and fields.get("folder_id"):
            f = next((x for x in self.folders if x["id"] == int(fields["folder_id"])), None)
            if f:
                rec["folder"], rec["folder_id"] = {"id": f["id"], "name": f["name"]}, f["id"]
        i = 0
        while not self.old_upload and f"tag_ids[{i}]" in fields:
            t = next((x for x in self.tags if x["id"] == int(fields[f"tag_ids[{i}]"])), None)
            if t:
                rec["tags"].append({"id": t["id"], "name": t["name"], "color": t["color"]})
            i += 1
        out = {"id": rid, "title": rec["title"], "status": "PENDING", "participants": rec["participants"],
               "meeting_date": rec["meeting_date"], "folder_id": rec["folder_id"], "folder": rec["folder"],
               "tags": rec["tags"], "notes": rec.get("notes")}
        if not self.old:
            rec["participants"] = out["participants"] = fields.get("participants") or ""
            self.refs[rid] = [{"id": 70 + i, "url": None, "label": None, "created_at": "2026-10-02T10:00:00.000000Z", **r}
                              for i, r in enumerate(json.loads(fields.get("external_refs") or "[]"))]
            out["external_refs"] = self.refs[rid]
            out["ignored"] = {"tag_ids": [], "folder_id": None}
            if fields.get("idempotency_key"):
                self.idem[fields["idempotency_key"]] = rid
        return self._json(out, 202)

    def _webhooks(self, request: httpx.Request, path: str):
        """/api/v1/webhooks as Speakr's src/api/webhooks.py answers it."""
        import hashlib
        import hmac
        import uuid

        body = json.loads(request.content or b"{}") if request.content else {}
        if path == "/api/v1/webhooks" and request.method == "POST":
            self.writes.append(("POST", path, body))
            if self.refuse_webhook_url:
                return self._json({"error": self.refuse_webhook_url}, 400)
            if not body.get("name") or not body.get("url") or not body.get("events"):
                return self._json({"error": "name, url and events are required"}, 400)
            self._hook_seq = getattr(self, "_hook_seq", 0) + 1
            wid = self._hook_seq
            self.webhooks[wid] = {"id": wid, "name": body["name"], "url": body["url"], "events": body["events"],
                                  "secret": f"whsec-{wid}-0123456789abcdef", "enabled": True,
                                  "include_shared": bool(body.get("include_shared"))}
            return self._json({**{k: v for k, v in self.webhooks[wid].items()}, "allow_http": False}, 201)
        m = re.fullmatch(r"/api/v1/webhooks/(\d+)(?:/(test|deliveries/(\d+)))?", path)
        if not m or int(m.group(1)) not in self.webhooks:
            return self._json({"error": "Webhook not found"}, 404)
        wid = int(m.group(1))
        hook = self.webhooks[wid]
        if m.group(2) is None and request.method == "PATCH":
            self.writes.append(("PATCH", path, body))
            if "include_shared" in body:
                hook["include_shared"] = bool(body["include_shared"])
            return self._json({k: v for k, v in hook.items() if k != "secret"})
        if m.group(2) is None and request.method == "DELETE":
            self.writes.append(("DELETE", path, None))
            del self.webhooks[wid]
            return httpx.Response(204)
        if m.group(2) == "test" and request.method == "POST":
            event_id = str(uuid.uuid4())
            env = {"id": event_id, "type": "webhook.test", "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
                   "user_id": self.user["id"], "data": {"reason": "manual test", "webhook_id": wid}}
            raw = json.dumps(env, separators=(",", ":")).encode()
            did = max([*self.deliveries, 0]) + 1
            row = {"id": did, "webhook_id": wid, "event_id": event_id, "event_type": "webhook.test",
                   "status": "pending", "response_status": None, "error_message": None, "attempt_count": 0}
            self.deliveries[did] = row
            headers = {"Content-Type": "application/json", "Speakr-Delivery-Id": event_id,
                       "Speakr-Event": "webhook.test",
                       "Speakr-Signature": "sha256=" + hmac.new(hook["secret"].encode(), raw, hashlib.sha256).hexdigest()}
            if self.v2:
                import time as _t

                t = int(_t.time())
                headers["Speakr-Signature-V2"] = f"t={t},v1=" + hmac.new(hook["secret"].encode(), f"{t}.".encode() + raw,
                                                                       hashlib.sha256).hexdigest()
                if self.previous_secret:
                    # A rotation: the old secret signs a second v1 value.
                    headers["Speakr-Signature-V2"] += ",v1=" + hmac.new(self.previous_secret.encode(),
                                                                        f"{t}.".encode() + raw, hashlib.sha256).hexdigest()

            async def go():
                if self.deliver is None:
                    return
                code = await self.deliver(hook["url"], raw, headers)
                row["attempt_count"] += 1
                row["response_status"] = code
                row["status"] = "success" if 200 <= code < 300 else "permanent_failure"

            import asyncio

            asyncio.get_running_loop().create_task(go())
            return self._json(row, 202)
        if m.group(3) and request.method == "GET":
            row = self.deliveries.get(int(m.group(3)))
            return self._json(row) if row else self._json({"error": "Delivery not found"}, 404)
        return self._json({"error": "not found"}, 404)


@pytest.fixture
def speakr(monkeypatch):
    from mailr.core import netguard
    from mailr.integrations.speakr import client, store

    fake = FakeSpeakr()
    client.use_transport(httpx.MockTransport(fake.handler))
    hosts, nets = netguard._allow_list()
    monkeypatch.setattr(netguard, "_allow_list", lambda: (set(hosts) | {"speakr.test"}, nets))
    store._CACHE.clear()
    store._LISTS.clear()
    yield fake
    client.use_transport(None)
    store._CACHE.clear()
    store._LISTS.clear()


async def connect(client, fake: FakeSpeakr | None = None, **extra: Any) -> dict[str, Any]:
    """Add the fake Speakr through the API (as the Settings wizard does) and run one sync."""
    from mailr.integrations.speakr.sync import sync_connector

    body = {"kind": "speakr", "name": extra.pop("name", "Speakr"), "config": {"base_url": BASE},
            "secrets": {"token": TOKEN}, **extra}
    r = await client.post("/api/v1/connectors", json=body)
    assert r.status_code == 200, r.text
    conn = r.json()
    await sync_connector(conn["id"])
    return conn
