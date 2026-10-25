"""`mailr demo-seed`: a reproducible demo instance for documentation screenshots. Development only.

Everything is fictional (mailr/dev/demo_content.py): Alex Rivera of Northwind Studio, with a work
account, a personal account and a local-only clinic mailbox, colleagues, clients, bills, codes,
parcels, newsletters, a code host, spam, a calendar and tasks. Dates are relative to now (or to
`--now`), so the inbox always looks current. Run it again and the demo user is deleted and seeded
afresh: the same state each time (ids differ).

Mail goes through the normal ingest path (ingest_raw with the account's connector), so filters,
threading, people, signals, search and embeddings behave as they do for real mail; the triage
runs without a language model, and the seed then fixes the result the screenshots rely on
(category, importance, signals, the primary action). Calendars and task lists live in the dev
Radicale (:15232, user "alex.rivera"), written over CalDAV and synced through the real mirror;
events and tasks made from emails go through the API. The three mail accounts point at the dev
compose's fake upstream Stalwart (IMAP :12143, accounts created there) and at a local SMTP sink
(`python -m mailr.dev.demo_smtp`, :2526), so nothing can leave the machine.

The scripted assistant (mailr/dev/demo_model.py) is registered as the model endpoint "demo"; the
API serves it when started with MAILR_DEMO_ASSISTANT=1.

Scenario groups (`--scenario a,b`; "accounts" is always seeded):
  accounts   the user, three mail accounts, identities and signatures, settings
  organize   labels (colours, nesting), filters, inbox layout with tabs and sections, favorites,
             templates, VIP senders
  mail       conversations, invitations and RSVPs, bills, codes, parcels, categories, the code host,
             spam, attachments (a voice memo too), inline images, follow-ups, snoozed, muted, starred, drafts, a
             scheduled send, reply suggestions, remote images in the proxy cache
  people     contact groups, a suggested group, details found in signatures
  assistant  standing orders and routines, a brief, runs, approvals (pending and decided), label
             suggestions, the importance review queue, a chat, the demo model endpoint
  calendar   calendars (colours, recurring, all-day, a conflict, invitations, a counter proposal,
             events made from mail)
  tasks      task lists, subtasks, repeating, due and overdue, tags, tasks made from mail
  reminders  fired, missed, snoozed and dismissed reminders
  speakr     a Speakr connector (only with --speakr-url and --speakr-token)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import struct
import sys
import time
import zlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, formataddr
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from mailr.config import get_settings
from mailr.dev import demo_content as dc
from mailr.dev import demo_model
from mailr.dev.demo_model import fill as _fill
from mailr.dev.demo_model import next_weekday

SCENARIOS = ("accounts", "organize", "mail", "people", "assistant", "calendar", "tasks", "reminders", "speakr")
DEFAULT_SCENARIOS = tuple(s for s in SCENARIOS if s != "speakr")

RADICALE = "http://localhost:15232/"


def cal_user() -> str:
    """The Radicale user of this database's demo: "alex.rivera" for mailr_demo, a user of its own
    for any other database (a test run must not wipe the demo's calendars)."""
    db = get_settings().database_url.rsplit("/", 1)[-1].split("?")[0]
    return "alex.rivera" if db == "mailr_demo" else f"alex.rivera-{re.sub(r'[^a-z0-9]+', '-', db.lower())}"

UPSTREAM_JMAP = "http://localhost:18091"
UPSTREAM_SVC = ("up-svc@upstream.test", "up-svc-pass")
UPSTREAM_IMAP = ("localhost", 12143)
UPSTREAM_PASSWORD = "northwind-upstream-2026"
# The SMTP sink the screenshot runs start (MAILR_DEMO_SMTP_PORT moves it).
SMTP_SINK = ("127.0.0.1", int(os.environ.get("MAILR_DEMO_SMTP_PORT", "2526")))
DEMO_ENDPOINT = "demo"
DEMO_SEARCH = "Web (demo)"
MGMT = ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]


# ---------------------------------------------------------------- guards and the clock


def _local_target() -> None:
    s = get_settings()
    host = urlparse(s.base_url).hostname or ""
    if not (host in ("localhost", "127.0.0.1", "::1") or host.endswith((".localhost", ".test"))):
        raise SystemExit("demo-seed only runs against a local development server (MAILR_BASE_URL)")
    db = s.database_url.rsplit("/", 1)[-1].split("?")[0]
    if "demo" not in db and "test" not in db:
        raise SystemExit(f"demo-seed writes to a database named for it: {db!r} has neither 'demo' nor 'test' in its "
                         "name (set MAILR_DATABASE_URL to …/mailr_demo)")


@dataclass
class Clock:
    now: datetime
    zone: ZoneInfo = field(default_factory=lambda: ZoneInfo(dc.TZ))

    @property
    def today(self) -> date:
        return self.now.astimezone(self.zone).date()

    def ago(self, spec: str) -> datetime:
        parts = {u: int(n) for n, u in re.findall(r"(\d+)([dhm])", spec)}
        return self.now - timedelta(days=parts.get("d", 0), hours=parts.get("h", 0), minutes=parts.get("m", 0))

    def day(self, n: int | str) -> date:
        return next_weekday(n, self.today) if isinstance(n, str) else self.today + timedelta(days=n)

    def workday(self, n: int) -> date:
        """The n-th working day after today (meetings never land on a weekend)."""
        d = self.today
        while n > 0:
            d += timedelta(days=1)
            n -= d.weekday() < 5
        return d

    def at(self, n: int | str | date, hour: int, minute: int = 0) -> datetime:
        d = n if isinstance(n, date) else self.day(n)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=self.zone)

    def fill(self, text: str) -> str:
        return _fill(text, self.today)

    def synced(self, minutes: int) -> datetime:
        """A "last synced" stamp a few minutes before the later of the seed's now and the real time.
        The server judges an account stale by its own clock, which a pinned --now does not move; the
        shots harness shows a stamp after the pinned now as a few minutes before it."""
        return max(self.now, datetime.now(UTC)) - timedelta(minutes=minutes)

    def quarter_after(self, delta: timedelta) -> datetime:
        t = (self.now + delta).astimezone(self.zone)
        t = t.replace(second=0, microsecond=0)
        return t + timedelta(minutes=(15 - t.minute % 15) % 15)


# ---------------------------------------------------------------- generated files


def png(w: int, h: int, rgb: tuple[int, int, int], *, stripes: bool = True) -> bytes:
    """A small PNG: a vertical gradient from the colour, with soft bands so it reads as a picture."""
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    rows = bytearray()
    for y in range(h):
        f = 0.55 + 0.45 * (1 - y / max(1, h - 1))
        row = bytearray([0])
        for x in range(w):
            band = 1.0 if not stripes else (0.9 if (x // max(1, w // 6) + y // max(1, h // 4)) % 2 else 1.0)
            row += bytes(min(255, int(c * f * band + (255 - c) * (1 - f) * 0.25)) for c in rgb)
        rows += row
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b""))


def avatar_png(side: int = 512) -> bytes:
    """Alex's account picture: a flat illustration drawn here from circles and polygons (a person
    with glasses and a mustard sweater on the ocean blue), so the demo carries no real photo."""
    import io

    from PIL import Image, ImageDraw

    n = side * 4  # drawn at 4x and scaled down for smooth edges
    u = n / 100
    im = Image.new("RGB", (n, n), (46, 111, 158))
    d = ImageDraw.Draw(im)

    def ell(x0: float, y0: float, x1: float, y1: float, c: tuple[int, int, int]) -> None:
        d.ellipse([x0 * u, y0 * u, x1 * u, y1 * u], fill=c)

    skin, shade, hair, ink = (226, 176, 140), (204, 152, 118), (58, 44, 38), (40, 40, 48)
    ell(8, 8, 92, 92, (63, 135, 181))
    ell(14, 72, 86, 132, (240, 176, 76))  # shoulders
    d.rounded_rectangle([42 * u, 58 * u, 58 * u, 78 * u], radius=6 * u, fill=shade)  # neck
    d.polygon([(40 * u, 74 * u), (60 * u, 74 * u), (50 * u, 84 * u)], fill=(250, 238, 218))
    d.polygon([(43 * u, 74 * u), (57 * u, 74 * u), (50 * u, 80 * u)], fill=shade)
    ell(31, 22, 69, 66, skin)  # head
    ell(28, 40, 35, 50, shade)
    ell(65, 40, 72, 50, shade)
    d.pieslice([30 * u, 17 * u, 70 * u, 57 * u], 180, 360, fill=hair)
    d.polygon([(30 * u, 37 * u), (52 * u, 26 * u), (70 * u, 37 * u), (70 * u, 30 * u), (50 * u, 18 * u), (30 * u, 30 * u)],
              fill=hair)
    ell(29, 30, 37, 44, hair)
    ell(63, 30, 71, 44, hair)
    w = int(1.4 * u)
    d.ellipse([36 * u, 41 * u, 47 * u, 50 * u], outline=ink, width=w)
    d.ellipse([53 * u, 41 * u, 64 * u, 50 * u], outline=ink, width=w)
    d.line([47 * u, 45 * u, 53 * u, 45 * u], fill=ink, width=w)
    ell(40.5, 44.5, 42.7, 46.7, ink)
    ell(57.3, 44.5, 59.5, 46.7, ink)
    d.arc([43 * u, 50 * u, 57 * u, 59 * u], 20, 160, fill=(150, 80, 70), width=int(1.3 * u))
    out = io.BytesIO()
    im.resize((side, side), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


def wav(seconds: int, rate: int = 8000) -> bytes:
    """A short mono WAV (16-bit PCM): soft rising and falling tones with pauses, like a voice memo's
    rhythm. Generated, so the demo carries no real recording."""
    import io
    import math
    import wave

    frames = bytearray()
    for i in range(seconds * rate):
        t = i / rate
        beat = t % 1.6
        if beat > 1.2:  # a pause between "phrases"
            v = 0.0
        else:
            f = 180 + 60 * math.sin(2 * math.pi * 0.7 * t) + 25 * math.sin(2 * math.pi * 3.1 * t)
            env = math.sin(math.pi * beat / 1.2) ** 2
            v = 0.25 * env * (math.sin(2 * math.pi * f * t) + 0.3 * math.sin(4 * math.pi * f * t))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))
    return buf.getvalue()


def pdf(title: str, lines: list[str]) -> bytes:
    """A one-page PDF with a title and a few lines of text (Helvetica)."""
    def esc(s: str) -> str:
        return s.encode("latin-1", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = [f"BT /F1 20 Tf 72 720 Td ({esc(title)}) Tj ET"]
    y = 684
    for ln in lines:
        ops.append(f"BT /F1 12 Tf 72 {y} Td ({esc(ln)}) Tj ET")
        y -= 18
    stream = "\n".join(ops).encode("latin-1", "replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> "
             b"/Contents 4 0 R >>"),
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


PDF_LINES = ["Prepared by Northwind Studio for review.", "", "1. Summary", "2. Scope and deliverables",
             "3. Timeline", "4. Budget", "", "This document is part of the mailr demo data and is fictional."]


# ---------------------------------------------------------------- calendar objects


def _ical():
    from icalendar import Alarm, Calendar, Event, Todo, vCalAddress

    return Alarm, Calendar, Event, Todo, vCalAddress


def vcalendar(*comps: Any, method: str | None = None) -> bytes:
    _, Calendar, _, _, _ = _ical()
    cal = Calendar()
    cal.add("prodid", "-//mailr demo//EN")
    cal.add("version", "2.0")
    if method:
        cal.add("method", method)
    for c in comps:
        cal.add_component(c)
    cal.add_missing_timezones()
    return cal.to_ical()


def _who(addr: str) -> str:
    for p in (dc.PRIYA, dc.JORDAN, dc.SAM, dc.MEI, dc.DANA, dc.MARCO, dc.LUCIA, dc.THEO, dc.NORA, dc.BEN, dc.ELENA,
              dc.AMARA, dc.RUTH):
        if p[1] == addr:
            return p[0]
    return dc.ME if addr in (dc.WORK, dc.PERSONAL, dc.CLINIC) else ""


def vevent(uid: str, summary: str, start: datetime | date, end: datetime | date, *, stamp: datetime,
           rrule: dict[str, Any] | None = None, location: str = "", description: str = "",
           organizer: str | None = None, attendees: list[tuple[str, str]] = (), status: str | None = None,
           transp: str | None = None, alarms: list[int] = (), url: str | None = None, sequence: int = 0,
           exdate: list[datetime] | None = None) -> Any:
    Alarm, _, Event, _, vCalAddress = _ical()
    e = Event()
    e.add("uid", uid)
    e.add("dtstamp", stamp)
    e.add("summary", summary)
    e.add("dtstart", start)
    e.add("dtend", end)
    if sequence:
        e.add("sequence", sequence)
    if rrule:
        e.add("rrule", rrule)
    for x in exdate or []:
        e.add("exdate", x)
    if location:
        e.add("location", location)
    if description:
        e.add("description", description)
    if url:
        e.add("url", url)
    if status:
        e.add("status", status)
    if transp:
        e.add("transp", transp)
    if organizer:
        o = vCalAddress(f"mailto:{organizer}")
        o.params["CN"] = _who(organizer) or organizer
        e.add("organizer", o)
    for addr, partstat in attendees:
        a = vCalAddress(f"mailto:{addr}")
        a.params["CN"] = _who(addr) or addr
        a.params["PARTSTAT"] = partstat
        a.params["ROLE"] = "CHAIR" if addr == organizer else "REQ-PARTICIPANT"
        if partstat == "NEEDS-ACTION":
            a.params["RSVP"] = "TRUE"
        e.add("attendee", a)
    for m in alarms:
        al = Alarm()
        al.add("action", "DISPLAY")
        al.add("description", summary)
        al.add("trigger", timedelta(minutes=-m))
        e.add_component(al)
    return e


def vtodo(uid: str, summary: str, *, stamp: datetime, due: datetime | date | None = None, priority: int = 0,
          categories: list[str] = (), parent: str | None = None, rrule: dict[str, Any] | None = None,
          completed: datetime | None = None, notes: str = "", start: date | None = None, alarms: list[int] = ()) -> Any:
    Alarm, _, _, Todo, _ = _ical()
    t = Todo()
    t.add("uid", uid)
    t.add("dtstamp", stamp)
    t.add("summary", summary)
    t.add("status", "COMPLETED" if completed else "NEEDS-ACTION")
    if completed:
        t.add("completed", completed)
        t.add("percent-complete", 100)
    if due is not None:
        t.add("due", due)
    if start is not None:
        t.add("dtstart", start)
    if priority:
        t.add("priority", priority)
    if categories:
        t.add("categories", list(categories))
    if parent:
        t.add("related-to", parent, parameters={"RELTYPE": "PARENT"})
    if rrule:
        t.add("rrule", rrule)
    if notes:
        t.add("description", notes)
    for m in alarms:
        al = Alarm()
        al.add("action", "DISPLAY")
        al.add("description", summary)
        al.add("trigger", timedelta(minutes=-m), parameters={"RELATED": "END"})
        t.add_component(al)
    return t


# ---------------------------------------------------------------- the seed's state


@dataclass
class World:
    clock: Clock
    scenarios: set[str]
    api_port: int
    user: Any = None
    p: Any = None
    http: httpx.AsyncClient | None = None
    conns: dict[str, int] = field(default_factory=dict)
    idents: dict[str, int] = field(default_factory=dict)
    labels: dict[str, int] = field(default_factory=dict)
    msgs: dict[str, int] = field(default_factory=dict)  # spec key -> message id
    convs: dict[str, int] = field(default_factory=dict)  # spec key -> conversation id
    cal: dict[str, str] = field(default_factory=dict)  # collection key -> URL
    caldav_id: int | None = None
    events: dict[str, str] = field(default_factory=dict)  # key -> uid
    tasks: dict[str, str] = field(default_factory=dict)
    summary: dict[str, int] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def count(self, key: str, n: int = 1) -> None:
        self.summary[key] = self.summary.get(key, 0) + n

    def fill(self, text: str) -> str:
        return self.clock.fill(text)


def _pid(kind: str, i: int) -> int:
    from mailr.core import publicid

    return publicid.encode(kind, i) if publicid.enabled() else i


async def _api(w: World, method: str, path: str, **kw: Any) -> Any:
    r = await w.http.request(method, "/api/v1" + path, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:400]}")
    return r.json() if r.content else None


# ---------------------------------------------------------------- reset


async def reset() -> None:
    """Delete the demo user (every row is the user's or cascades from it), its mail store account,
    the demo model endpoint and the demo calendars in Radicale."""
    from sqlalchemy import delete, func, select, text

    from mailr.core.users import delete_store_account
    from mailr.db import session_scope
    from mailr.models import ModelEndpoint, User, WebSearchProvider

    async with session_scope() as db:
        users = list((await db.execute(select(User).where(func.lower(User.email).in_([dc.WORK])))).scalars())
        for u in users:
            try:
                await delete_store_account(u)
            except Exception as e:  # noqa: BLE001  a store reset elsewhere: the account is gone already
                print(f"demo-seed: store account of {u.email} not deleted: {e}", file=sys.stderr)
            for table in ("jobs", "change_log", "cal_occurrences"):
                await db.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": u.id})
            # The account and address pictures live in the data directory, not in a row.
            shutil.rmtree(Path(get_settings().data_dir) / "avatars" / f"user-{int(u.id)}", ignore_errors=True)
            await db.delete(u)
        await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name == DEMO_ENDPOINT))
        await db.execute(delete(WebSearchProvider).where(WebSearchProvider.name == DEMO_SEARCH))
    await asyncio.to_thread(_reset_radicale)


def _reset_radicale() -> None:
    import caldav

    try:
        httpx.get(RADICALE, timeout=2)
    except httpx.HTTPError:
        return
    pr = caldav.DAVClient(url=RADICALE, username=cal_user(), password="demo").principal()
    for c in pr.calendars():
        c.delete()
    # The address book (people scenario).
    httpx.request("DELETE", f"{RADICALE}{cal_user()}/contacts/", auth=(cal_user(), "demo"), timeout=10)


# ---------------------------------------------------------------- accounts


def upstream_login(key: str) -> str:
    """The IMAP login of an account on the fake upstream. Stalwart refuses reserved TLDs such as
    .example, so the mailboxes live on its own domain (upstream.test); the address stays Alex's."""
    return f"demo-{key}@upstream.test"


def _upstream_accounts() -> bool:
    """The three mailboxes on the fake upstream (dev compose `upstream`), created once and kept
    (the password is reset). Returns False when the upstream is not running."""
    def jmap(calls: list) -> list:
        r = httpx.post(f"{UPSTREAM_JMAP}/jmap/", json={"using": MGMT, "methodCalls": calls}, auth=UPSTREAM_SVC,
                       timeout=20)
        r.raise_for_status()
        return r.json()["methodResponses"]

    # Seeds running at the same time (the parallel screenshot workers) update the same three
    # accounts; Stalwart answers one of them serverUnavailable. That one waits and tries again.
    for attempt in range(6):
        try:
            dom = jmap([["x:Domain/query", {"filter": {"name": "upstream.test"}}, "q"]])[0][1]["ids"][0]
            for key in dc.ACCOUNTS:
                local = upstream_login(key).split("@")[0]
                found = jmap([["x:Account/query", {"filter": {"name": local}}, "q"]])[0][1].get("ids") or []
                if found:
                    res = jmap([["x:Account/set", {"update": {found[0]: {"credentials/0/secret": UPSTREAM_PASSWORD}}}, "u"]])
                    ok = found[0] in (res[0][1].get("updated") or {})
                else:
                    res = jmap([["x:Account/set", {"create": {"a": {
                        "@type": "User", "name": local, "domainId": dom,
                        "credentials": {"0": {"@type": "Password", "secret": UPSTREAM_PASSWORD}}}}}, "c"]])
                    ok = "a" in (res[0][1].get("created") or {})
                if not ok:
                    raise KeyError(f"upstream account {local}: {res[0][1]}")
            return True
        except (httpx.HTTPError, KeyError, IndexError) as e:
            if "serverUnavailable" in str(e) and attempt < 5:
                time.sleep(0.5 * (attempt + 1))
                continue
            print(f"demo-seed: fake upstream not available ({e}); accounts are created paused", file=sys.stderr)
            return False
    return False


async def seed_accounts(w: World) -> None:
    from sqlalchemy import select, update

    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.core.users import create_user
    from mailr.db import sessionmaker
    from mailr.main import create_app
    from mailr.models import Connector, Identity, Job

    async with sessionmaker()() as db:
        u = await create_user(db, dc.WORK, dc.ME, password=dc.PASSWORD, role="admin")
        uid = u.id
    app = create_app()
    w.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")
    r = await w.http.post("/api/v1/auth/login", json={"email": dc.WORK, "password": dc.PASSWORD})
    r.raise_for_status()
    w.http.headers["X-CSRF-Token"] = r.json()["csrf"]

    await _api(w, "PATCH", "/me/settings", json={
        "timezone": dc.TZ, "undo_send_seconds": 10, "time_format": "12h",
        "calendar_add_new_invites": "needs_action", "vip": [dc.PRIYA[1]], "images": "always",
    })
    # Alex's account picture (Settings → You → Your picture), uploaded like a user would.
    await _api(w, "PUT", "/me/picture", files={"file": ("alex.png", avatar_png(), "image/png")})

    upstream = await asyncio.to_thread(_upstream_accounts)
    for key, (name, addr, data_class, _domain) in dc.ACCOUNTS.items():
        body = {"kind": "imap", "name": name, "mode": "store", "data_class": data_class, "skip_test": True,
                "config": {"email": addr, "display_name": dc.ME,
                           "imap": {"host": UPSTREAM_IMAP[0], "port": UPSTREAM_IMAP[1], "tls": "none",
                                    "username": upstream_login(key)},
                           "smtp": {"host": SMTP_SINK[0], "port": SMTP_SINK[1], "tls": "none", "auth": False,
                                    "username": upstream_login(key)}},
                "secrets": {"password": UPSTREAM_PASSWORD}}
        c = await _api(w, "POST", "/connectors", json=body)
        w.conns[key] = c["id"]
    async with sessionmaker()() as db:
        for cid in w.conns.values():
            await db.execute(update(Connector).where(Connector.id == cid).values(
                status="idle" if upstream else "paused", enabled=upstream,
                status_detail="Up to date" if upstream else "The fake upstream server was not running",
                last_sync_at=w.clock.synced(3),
                # The first import is done: no "Bringing in your recent mail" bar in the shots.
                state={"initial_done": True}))
        # The first sync would only find the empty upstream mailboxes: done, so the state stays put.
        await db.execute(update(Job).where(Job.user_id == uid, Job.kind == "connector.sync_now",
                                           Job.status == "queued").values(status="done"))
        for ident in (await db.execute(select(Identity).where(Identity.user_id == uid))).scalars():
            ident.signature_html = dc.SIGNATURES.get(ident.email, "")
            ident.name = dc.ME
            ident.is_default = ident.email == dc.WORK
            w.idents[ident.email] = ident.id
        await db.commit()
        from mailr.models import User

        w.user = await db.get(User, uid)
    w.p = Principal(user=w.user, scopes=set(ALL_SCOPES))
    w.count("accounts", len(w.conns))


# ---------------------------------------------------------------- labels and filters


async def seed_labels(w: World) -> None:
    from mailr.core.store import user_store
    from mailr.db import sessionmaker
    from mailr.models import Label, User

    async with sessionmaker()() as db:
        user = await db.get(User, w.user.id)
        store = await user_store(db, user)
        for name, colour in dc.LABELS:
            lb = await store.create_label(db, name, colour)
            lb = await db.get(Label, lb.id)
            lb.color = colour
            w.labels[name] = lb.id
        await db.commit()
    w.count("labels", len(w.labels))


async def seed_rules(w: World) -> None:
    for r in dc.RULES:
        acts = dict(r["actions"])
        if "add_labels" in acts:
            acts["add_labels"] = [w.labels[n] for n in acts["add_labels"]]
        await _api(w, "POST", "/rules", json={"name": r["name"], "query": r["query"], "actions": acts})
        w.count("filters")


# ---------------------------------------------------------------- mail


_ADDR_NAMES = {p[1]: p[0] for p in (dc.PRIYA, dc.JORDAN, dc.SAM, dc.MEI, dc.OPS, dc.DANA, dc.MARCO, dc.LUCIA,
                                    dc.CASA_AP, dc.THEO, dc.NORA, dc.BEN, dc.ELENA, dc.AMARA, dc.RUTH, dc.VOLUNTEERS)}
_ADDR_NAMES.update({dc.WORK: dc.ME, dc.PERSONAL: dc.ME, dc.CLINIC: dc.ME, "team@northwind.example": "Northwind team",
                    "design-systems@lists.example.org": "design-systems"})


def _addr(a: str) -> str:
    return formataddr((_ADDR_NAMES.get(a, ""), a))


def _html_to_text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style|head).*?</\1>", "", html)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</tr>|</li>|</div>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("&middot;", "·").replace("&ndash;", "–").replace("&amp;", "&").replace("&nbsp;", " ")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(x.strip() for x in t.splitlines())).strip()


def _msgid(spec: dict[str, Any]) -> str:
    if spec.get("msgid"):
        return f"<{spec['msgid']}>"
    dom = spec["frm"][1].split("@", 1)[1]
    return f"<{spec['key']}.demo@{dom}>"


def itip_parts(w: World, kind: str) -> tuple[str, bytes]:
    """The calendar part of the invitation scenarios: (method, ics)."""
    c = w.clock
    stamp = c.now
    if kind == "kickoff-request":
        start = c.at(c.workday(2), 10)
        ev = vevent("bluefin-kickoff@northwind.example", "Bluefin kickoff", start, start + timedelta(hours=1),
                    stamp=stamp, location="Northwind Studio, big room · meet.northwind.example/bluefin",
                    description="Kickoff for the Bluefin Outdoor landing page: goals, timeline, owners.",
                    organizer=dc.JORDAN[1],
                    attendees=[(dc.JORDAN[1], "ACCEPTED"), (dc.WORK, "NEEDS-ACTION"), (dc.MARCO[1], "ACCEPTED"),
                               (dc.SAM[1], "NEEDS-ACTION")], url="https://meet.northwind.example/bluefin")
        return "REQUEST", vcalendar(ev, method="REQUEST")
    if kind == "kickoff-update":
        start = c.at(c.workday(2), 10)
        ev = vevent("bluefin-kickoff@northwind.example", "Bluefin kickoff", start, start + timedelta(hours=1),
                    stamp=stamp, sequence=1, location="Northwind Studio, roof terrace · meet.northwind.example/bluefin",
                    description="Kickoff for the Bluefin Outdoor landing page: goals, timeline, owners. "
                                "New room: the roof terrace.",
                    organizer=dc.JORDAN[1],
                    attendees=[(dc.JORDAN[1], "ACCEPTED"), (dc.WORK, "NEEDS-ACTION"), (dc.MARCO[1], "ACCEPTED"),
                               (dc.SAM[1], "NEEDS-ACTION")], url="https://meet.northwind.example/bluefin")
        return "REQUEST", vcalendar(ev, method="REQUEST")
    if kind in ("photo-request", "photo-cancel"):
        start = c.at(c.workday(3), 13)
        cancel = kind == "photo-cancel"
        ev = vevent("bluefin-photo-review@bluefin.example", "Bluefin photo review", start,
                    start + timedelta(minutes=45), stamp=stamp if cancel else stamp - timedelta(days=2),
                    sequence=1 if cancel else 0, location="Bluefin Outdoor, studio 2", organizer=dc.MARCO[1],
                    status="CANCELLED" if cancel else None,
                    attendees=[(dc.MARCO[1], "ACCEPTED"), (dc.WORK, "NEEDS-ACTION"), (dc.JORDAN[1], "NEEDS-ACTION")])
        return ("CANCEL", vcalendar(ev, method="CANCEL")) if cancel else ("REQUEST", vcalendar(ev, method="REQUEST"))
    start = c.at(c.workday(1), 15)
    if kind == "critique-reply":
        ev = vevent("design-critique@northwind.example", "Design critique", start, start + timedelta(hours=1),
                    stamp=stamp, organizer=dc.WORK, attendees=[(dc.SAM[1], "ACCEPTED")])
        return "REPLY", vcalendar(ev, method="REPLY")
    if kind == "critique-counter":
        ev = vevent("design-critique@northwind.example", "Design critique", start + timedelta(hours=1),
                    start + timedelta(hours=2), stamp=stamp, organizer=dc.WORK,
                    attendees=[(dc.MEI[1], "TENTATIVE")])
        ev.add("comment", "I have a client call at 3. Could we start at 4 instead?")
        return "COUNTER", vcalendar(ev, method="COUNTER")
    raise ValueError(kind)


def authserv_id() -> str:
    """The authserv-id the demo's receiving server writes: the store's own hostname
    (MAILR_IMAP_HOSTNAME, which Stalwart stamps) when it is trusted, else the first trusted id
    (the default `localhost` is not trusted: core/mime.trusted_authserv_ids)."""
    from mailr.core.mime import trusted_authserv_ids

    s = get_settings()
    trusted = trusted_authserv_ids()
    host = (s.imap_hostname or "").strip().lower()
    return host if host in trusted else min(trusted, default=host or "localhost")


def auth_results(spec: dict[str, Any]) -> str | None:
    """The Authentication-Results header the receiving server prepends to inbound demo mail:
    SPF, DKIM (signed by the sender's own domain) and DMARC pass for real correspondents; junk
    gets a soft SPF fail and no signature. None for mail Alex sent (no receiver checked it)."""
    if spec.get("role") in ("sent", "drafts") or spec["frm"][1] in (dc.WORK, dc.PERSONAL, dc.CLINIC):
        return None
    dom = spec["frm"][1].rsplit("@", 1)[1]
    if spec.get("auth") == "fail":
        return (f"{authserv_id()}; spf=fail smtp.mailfrom={dom}; dkim=fail header.d={dom}; "
                f"dmarc=fail header.from={dom}")
    if spec.get("role") == "junk":
        return (f"{authserv_id()}; spf=softfail smtp.mailfrom={dom}; dkim=none; "
                f"dmarc=none header.from={dom}")
    return (f"{authserv_id()}; spf=pass smtp.mailfrom={dom}; "
            f"dkim=pass header.d={dom} header.s=demo2026; dmarc=pass (p=quarantine) header.from={dom}")


def build_mime(w: World, spec: dict[str, Any], parents: dict[str, dict[str, Any]]) -> bytes:
    msg = EmailMessage()
    frm = spec["frm"]
    ar = auth_results(spec)
    if ar:
        msg["Authentication-Results"] = ar
    msg["From"] = formataddr((frm[0], frm[1]))
    msg["To"] = ", ".join(_addr(a) for a in spec["to"])
    if spec.get("cc"):
        msg["Cc"] = ", ".join(_addr(a) for a in spec["cc"])
    msg["Subject"] = w.fill(spec["subject"])
    msg["Date"] = format_datetime(w.clock.ago(spec["ago"]).astimezone(w.clock.zone))
    msg["Message-ID"] = _msgid(spec)
    chain = []
    p = spec.get("parent")
    while p:
        chain.insert(0, _msgid(parents[p]))
        p = parents[p].get("parent")
    if chain:
        msg["In-Reply-To"] = chain[-1]
        msg["References"] = " ".join(chain)
    if spec.get("list"):
        msg["List-Id"] = f"<{spec['list']}>"
    if spec.get("unsubscribe"):
        msg["List-Unsubscribe"] = spec["unsubscribe"]
        if spec.get("one_click"):
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if spec.get("itip"):
        method, ics = itip_parts(w, spec["itip"])
        words = {"REQUEST": f"{frm[0]} invited you to an event.", "REPLY": f"{frm[0]} accepted your invitation.",
                 "COUNTER": f"{frm[0]} proposed a new time for your event.",
                 "CANCEL": f"{frm[0]} cancelled this event."}[method]
        if spec["itip"] == "kickoff-update":
            words = f"{frm[0]} updated the event: it moves to the roof terrace."
        msg.set_content(words + "\n")
        msg.add_alternative(ics.decode(), subtype="calendar", params={"method": method})
        msg.add_attachment(ics, maintype="application", subtype="ics", filename="invite.ics")
        return msg.as_bytes(policy=SMTP)
    if spec.get("html"):
        html = w.fill(spec["html"])
        msg.set_content(_html_to_text(html))
        msg.add_alternative(html, subtype="html")
        if spec.get("inline"):
            part = msg.get_payload()[1]
            for cid, fname, rgb in spec["inline"]:
                part.add_related(png(480, 270, rgb), maintype="image", subtype="png", cid=f"<{cid}>", filename=fname)
    elif spec.get("text"):
        msg.set_content(w.fill(spec["text"]))
    if spec.get("forward"):
        # Forwarded as an attachment: the forwarded email as a message/rfc822 part.
        f = spec["forward"]
        inner = EmailMessage()
        inner["From"] = formataddr((f["frm"][0], f["frm"][1]))
        inner["To"] = ", ".join(_addr(a) for a in f["to"])
        inner["Subject"] = w.fill(f["subject"])
        inner["Date"] = format_datetime(w.clock.ago(f["ago"]).astimezone(w.clock.zone))
        inner["Message-ID"] = _msgid({"key": spec["key"] + "-forwarded", "frm": f["frm"]})
        inner.set_content(w.fill(f["text"]))
        msg.add_attachment(inner, disposition="attachment",
                           filename=w.fill(f["subject"]) + ".eml")
    if spec.get("ics") == "typeconf":
        d = w.clock.day(10)
        talks = [vevent(f"typeconf-2026-{i}@typeconf.example", title, datetime(d.year, d.month, d.day, h, tzinfo=w.clock.zone),
                        datetime(d.year, d.month, d.day, h + 1, tzinfo=w.clock.zone), stamp=w.clock.now,
                        location="Harbor Convention Center, hall B")
                 for i, (title, h) in enumerate((("TypeConf: Variable fonts at scale", 10),
                                                 ("TypeConf: Designing for small screens", 14)))]
        msg.add_attachment(vcalendar(*talks), maintype="text", subtype="calendar", filename="typeconf-schedule.ics")
    for fname, kind, arg in spec.get("attach") or []:
        if kind == "pdf":
            msg.add_attachment(pdf(arg, PDF_LINES), maintype="application", subtype="pdf", filename=fname)
        elif kind == "wav":
            msg.add_attachment(wav(arg), maintype="audio", subtype="wav", filename=fname)
        else:
            msg.add_attachment(png(640, 420, arg), maintype="image", subtype="png", filename=fname)
    return msg.as_bytes(policy=SMTP)


async def seed_mail(w: World) -> None:
    from sqlalchemy import delete, select

    from mailr.core import imageproxy, threading
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.db import sessionmaker
    from mailr.intel import embed, llm
    from mailr.intel.tasks import process_message
    from mailr.models import Connector, Conversation, Job, LabelDecision, Message, User

    specs = {s["key"]: s for s in dc.MAILS}
    async with sessionmaker()() as db:
        user = await db.get(User, w.user.id)
        w.p.user = user
        store = await user_store(db, user)
        conns = {k: await db.get(Connector, cid) for k, cid in w.conns.items()}
        for spec in sorted(dc.MAILS, key=lambda s: w.clock.ago(s["ago"])):
            for url, (rgb, wd, ht) in (spec.get("remote_images") or {}).items():
                imageproxy.put(url, png(wd, ht, rgb, stripes=False))
            raw = build_mime(w, spec, specs)
            role = spec.get("role", "inbox")
            # Archived mail counts as read unless the spec says otherwise (the backlog of unread
            # newsletters on the Subscriptions page).
            kw = {"seen": bool(spec["seen"]) if "seen" in spec else role in ("sent", "archive")}
            res = await ingest_raw(db, store, raw, IngestOptions(
                connector=conns[spec["acct"]], roles=[role], label_names=list(spec.get("labels") or []),
                keywords=kw, received_at=w.clock.ago(spec["ago"]),
                source="connector", notify=False))
            w.msgs[spec["key"]] = res.message.id
            w.convs[spec["key"]] = res.message.conversation_id
            w.count("messages")
        await db.commit()

        # Triage as the worker would, without a language model; embeddings from the demo model's
        # vectors (in-process: the API that serves them may not be running yet).
        real_embed = llm.embed_request
        llm.embed_request = demo_model.embed_request
        embed.reset_breaker()
        try:
            for mid in w.msgs.values():
                m = await db.get(Message, mid)
                await process_message(db, m, user, allow_llm=False)
                await db.commit()
        finally:
            llm.embed_request = real_embed

        # What the screenshots rely on: category, importance, signals, the primary action.
        for key, mid in w.msgs.items():
            spec = specs[key]
            m = await db.get(Message, mid)
            if m.sent_by_me:
                continue
            ruled = (await db.execute(select(LabelDecision.id).where(
                LabelDecision.message_id == mid, LabelDecision.kind == "category",
                LabelDecision.source == "rule"))).first()
            if spec.get("category"):
                cat = spec["category"]
                m.category = None if cat == "primary" else cat
            elif not ruled:
                m.category = None
            if "important" in spec:
                want = bool(spec["important"])
                if m.important != want:
                    await store.set_keywords([m], important=want)
                    m.important = want
                m.importance_score = 0.86 if want else 0.12
                db.add(LabelDecision(user_id=user.id, message_id=mid, kind="importance", target="important",
                                     value=want, source="heuristic", confidence=m.importance_score,
                                     reason="from someone you write to; asks you something" if want
                                     else "bulk or automated mail"))
            await db.execute(delete(LabelDecision).where(LabelDecision.message_id == mid,
                                                         LabelDecision.kind == "smart"))
            if spec.get("needs_reply"):
                db.add(LabelDecision(user_id=user.id, message_id=mid, kind="smart", target="needs_reply", value=True,
                                     source="heuristic", confidence=0.8, reason=w.fill(spec["needs_reply"]),
                                     status="applied"))
            if spec.get("deadline") is not None:
                d = w.clock.day(spec["deadline"]).isoformat()
                db.add(LabelDecision(user_id=user.id, message_id=mid, kind="smart", target="deadline", value=True,
                                     source="heuristic", source_ref=d, confidence=0.8,
                                     reason=f"Deadline: {w.fill('{D:' + str(spec['deadline']) + '}')}", status="applied"))
            if spec.get("action"):
                a = dict(spec["action"])
                if a.get("fact"):
                    a["fact"] = w.fill(a["fact"])
                m.primary_action = a
            elif spec.get("role") == "junk":
                m.primary_action = None
        await db.flush()
        await threading.refresh(db, user.id, sorted(set(w.convs.values())))
        await db.commit()

        # Conversation states, through the same actions the app uses.
        from mailr.core import actions as act

        ctx = act.ActionContext(db, store, w.p)
        future = max(w.clock.now, datetime.now(UTC))
        for key, spec in specs.items():
            cid = w.convs[key]
            msgs = await act.messages_for(db, w.p, conv_ids=[cid])
            if spec.get("starred"):
                await act.apply(ctx, msgs, "star", {})
            if spec.get("snooze"):
                until = (future + timedelta(days=1)).astimezone(w.clock.zone).replace(hour=8, minute=0, second=0,
                                                                                    microsecond=0)
                await act.apply(ctx, msgs, "snooze", {"until": until.isoformat()})
            if spec.get("muted"):
                await act.apply(ctx, msgs, "mute", {})
            if spec.get("follow_up"):
                await act.apply(ctx, msgs, "follow_up", {"until": (future + timedelta(days=1, hours=2)).isoformat()})
            if spec.get("nudged"):
                conv = await db.get(Conversation, cid)
                conv.follow_up_since = w.clock.ago(spec["ago"])
                conv.nudged_at = w.clock.now - timedelta(days=2)
                inbox = await store.role_label(db, "inbox")
                await store.set_labels(msgs, add=[inbox.id], remove=[], db=db)
            await db.commit()
        await threading.refresh(db, user.id, sorted(set(w.convs.values())))
        await db.commit()
        w.count("conversations", len(set(w.convs.values())))

        # Reply suggestions, as the reader would have kept them (no model needed to show them).
        from mailr.intel.routes import _replies_path

        for key, replies in dc.REPLY_SUGGESTIONS.items():
            if key in w.msgs:
                _replies_path(user.id, w.msgs[key]).write_text(json.dumps({"replies": replies}))

        # Jobs the inline triage made unnecessary, and pushes for mail that is not new.
        await db.execute(delete(Job).where(Job.user_id == user.id, Job.kind.in_(
            ("intel.message", "push.new_mail", "images.prefetch"))))
        await db.commit()

    await _drafts_and_scheduled(w)
    await _summaries(w)


async def seed_screener(w: World) -> None:
    """First-time senders waiting in the Screener (demo_content.SCREENED; core/screener.py): the
    screener is on while they arrive through the personal account, then off again, so every other
    screenshot stays as it was. The screener shots turn it on (PUT /screener/settings)."""
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.screener import SETTING
    from mailr.core.store import user_store
    from mailr.db import sessionmaker
    from mailr.models import Connector, User

    specs = {s["key"]: s for s in dc.SCREENED}
    async with sessionmaker()() as db:
        user = await db.get(User, w.user.id)
        user.settings = {**(user.settings or {}), SETTING: True}
        await db.commit()
        store = await user_store(db, user)
        for spec in sorted(dc.SCREENED, key=lambda s: w.clock.ago(s["ago"])):
            res = await ingest_raw(db, store, build_mime(w, spec, specs), IngestOptions(
                connector=await db.get(Connector, w.conns[spec["acct"]]), roles=["inbox"],
                received_at=w.clock.ago(spec["ago"]), source="connector", notify=False, run_intel=False))
            w.msgs[spec["key"]] = res.message.id
            w.convs[spec["key"]] = res.message.conversation_id
            w.count("screened")
        user.settings = {**(user.settings or {}), SETTING: False}
        await db.commit()


async def _summaries(w: World) -> None:
    """The reader's summary card, computed now with the scripted model (in-process), as the
    worker's job would; the API returns it from the cache."""
    from mailr.db import sessionmaker
    from mailr.intel import llm, summarize
    from mailr.models import User

    async def scripted_chat(db, user, messages, **kw):
        return {**demo_model.respond({"messages": messages, **({"response_format": kw["response_format"]}
                                                                   if kw.get("response_format") else {})}),
                "_model": demo_model.MODEL, "_endpoint": DEMO_ENDPOINT}

    real = llm.chat
    llm.chat = scripted_chat
    try:
        async with sessionmaker()() as db:
            user = await db.get(User, w.user.id)
            for cid in sorted(set(w.convs.values())):
                try:
                    await summarize.summarize(db, user, cid)
                    w.count("summaries")
                except LookupError:
                    pass
    finally:
        llm.chat = real


async def _drafts_and_scheduled(w: World) -> None:
    from mailr.core.drafts import save_draft
    from mailr.core.sendgate import SendGate
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        w.p.user = await db.get(User, w.user.id)
        for d in dc.DRAFTS:
            payload: dict[str, Any] = {"identity_id": w.idents[dc.WORK], "html": d["html"]}
            if d.get("reply_to"):
                from mailr.core.drafts import reply_payload

                base = await reply_payload(db, w.p, w.msgs[d["reply_to"]], "reply")
                payload = {**base, "identity_id": w.idents[dc.WORK], "html": d["html"] + (base.get("html") or "")}
            else:
                payload["to"] = [{"email": p[1], "name": p[0]} for p in d["to"]]
                payload["subject"] = d["subject"]
            m = await save_draft(db, w.p, payload, None)
            w.msgs[d["key"]] = m.id
            w.convs[d["key"]] = m.conversation_id
            await db.commit()
            w.count("drafts")
        s = dc.SCHEDULED
        day, hm = s["send"].split()
        hh, mm = (int(x) for x in hm.split(":"))
        when = w.clock.at(day, hh, mm)
        ob = await SendGate(db, w.p).submit({
            "identity_id": w.idents[dc.WORK], "to": [{"email": p[1], "name": p[0]} for p in s["to"]],
            "subject": s["subject"], "html": s["html"]}, send_at=max(when, datetime.now(UTC) + timedelta(hours=1)))
        await db.commit()
        w.extra["scheduled_send"] = ob.send_at.isoformat()


async def run_jobs(w: World, kind: str, module: str) -> int:
    """Run the user's queued jobs of one kind here, as the worker would (the seed runs no worker)."""
    import importlib

    from sqlalchemy import select

    from mailr.core.jobs import _HANDLERS
    from mailr.db import sessionmaker
    from mailr.models import Job

    importlib.import_module(module)  # registers the handler
    async with sessionmaker()() as db:
        jobs = list((await db.execute(select(Job).where(Job.user_id == w.user.id, Job.kind == kind,
                                                        Job.status == "queued").order_by(Job.id))).scalars())
        payloads = [j.payload for j in jobs]
        for j in jobs:
            j.status = "done"
        await db.commit()
    for pl in payloads:
        await _HANDLERS[kind](pl, None)
    return len(payloads)


async def run_itip(w: World) -> None:
    """The invitation messages, applied to the calendar as the worker's job would."""
    w.count("itip_applied", await run_jobs(w, "calendar.itip_incoming", "mailr.calendar.inbound"))


# ---------------------------------------------------------------- organize


async def seed_organize(w: World) -> None:
    L = w.labels
    layout = {
        "id": "studio", "name": "Studio",
        "tabs": [
            {"id": "inbox", "name": "Inbox", "icon": "inbox", "indicators": ["unread", "due"], "sections": [
                {"id": "now", "title": "Important and unread", "base": "inbox", "filter": "important_unread", "limit": 6},
                {"id": "clients", "title": "Clients", "base": f"label:{L['Clients']}", "limit": 6, "exclude_above": True},
                {"id": "rest", "title": "Everything else", "base": "inbox", "limit": None, "exclude_above": True},
            ]},
            {"id": "code", "name": "Code", "icon": "laptop", "indicators": ["unread"], "claims": True, "sections": [
                {"id": "forgehub", "title": "Forgehub", "base": f"label:{L['Dev/Forgehub']}", "limit": None}]},
            {"id": "money", "name": "Bills", "icon": "flag", "indicators": ["due", "all"], "claims": True, "sections": [
                {"id": "bills", "title": "Bills", "base": f"label:{L['Finance/Bills']}", "limit": None},
                {"id": "receipts", "title": "Receipts", "base": f"label:{L['Finance/Receipts']}", "limit": 10}]},
            {"id": "updates", "name": "Updates", "icon": "bell", "indicators": ["unread"], "sections": [
                {"id": "updates", "title": "Updates", "base": "inbox", "category": "updates", "limit": None}]},
            {"id": "reading", "name": "Reading", "icon": "book", "indicators": ["all"], "sections": [
                {"id": "promos", "title": "Promotions", "base": "inbox", "category": "promotions", "limit": 10},
                {"id": "reading", "title": "Newsletters", "base": f"label:{L['Reading']}", "limit": None}]},
        ],
    }
    # A second layout (not active): one tab per kind of mail, for the docs' tab-strip screenshots.
    everyday = {
        "id": "everyday", "name": "Everyday",
        "tabs": [
            {"id": "inbox", "name": "Inbox", "icon": "inbox", "indicators": ["unread", "due"], "sections": [
                {"id": "important", "title": "Important", "base": "inbox", "filter": "important", "q": "has:deadline",
                 "limit": 6},
                {"id": "unread", "title": "Unread", "base": "inbox", "filter": "unread", "limit": None,
                 "exclude_above": True},
            ]},
            {"id": "financial", "name": "Financial", "icon": "briefcase", "indicators": ["due", "all"], "claims": True,
             "sections": [
                {"id": "bills", "title": "Bills", "base": f"label:{L['Finance/Bills']}", "limit": None},
                {"id": "receipts", "title": "Receipts", "base": f"label:{L['Finance/Receipts']}", "limit": 10}]},
            {"id": "later", "name": "Later", "icon": "clock", "indicators": ["unread"], "sections": [
                {"id": "newsletters", "title": "Newsletters", "base": f"label:{L['Reading']}", "limit": None},
                {"id": "updates", "title": "Updates", "base": "inbox", "category": "updates", "limit": None}]},
            {"id": "code", "name": "Code", "icon": "laptop", "indicators": ["unread"], "claims": True, "sections": [
                {"id": "forgehub", "title": "Forgehub", "base": f"label:{L['Dev/Forgehub']}", "limit": None}]},
            {"id": "shopping", "name": "Shopping", "icon": "bag", "indicators": ["all"], "sections": [
                {"id": "orders", "title": "Orders", "base": "inbox", "category": "purchases", "limit": None},
                {"id": "deals", "title": "Deals", "base": "inbox", "category": "promotions", "limit": 10}]},
        ],
    }
    await _api(w, "PUT", "/layouts", json={"layouts": [layout, everyday], "active": "studio"})
    w.count("layouts", 2)
    favorites = ["inbox", "starred", f"label:{L['Clients/Lumen Coffee']}", f"label:{L['Finance/Bills']}",
                 "drafts", "sent"]
    await _api(w, "PATCH", "/me/settings", json={"nav_favorites": favorites})
    for t in dc.TEMPLATES:
        await _api(w, "POST", "/templates", json=t)
        w.count("templates")


# ---------------------------------------------------------------- people


async def seed_people(w: World) -> None:
    from sqlalchemy import select

    from mailr.db import sessionmaker
    from mailr.models import Person, PersonGroup, PersonGroupMember, PersonSuggestion

    await _api(w, "POST", "/groups", json={"name": "Northwind team", "colour": "#0f766e",
                                          "members": [{"email": p[1], "name": p[0]} for p in dc.TEAM]})
    await _api(w, "POST", "/groups", json={"name": "Clients", "colour": "#b45309",
                                          "members": [{"email": p[1], "name": p[0]} for p in (dc.DANA, dc.MARCO, dc.LUCIA)]})
    w.count("groups", 2)
    now = w.clock.now
    async with sessionmaker()() as db:
        for name, addr, org, title in (dc.PRIYA, dc.JORDAN, dc.SAM, dc.MEI, dc.DANA, dc.MARCO, dc.LUCIA, dc.THEO,
                                       dc.NORA, dc.BEN, dc.ELENA, dc.AMARA, dc.RUTH):
            p = (await db.execute(select(Person).where(Person.user_id == w.user.id, Person.email == addr))).scalar_one_or_none()
            if p is None:
                p = Person(user_id=w.user.id, email=addr, first_seen=now - timedelta(days=120), last_seen=now - timedelta(days=3),
                           received_count=2, sent_count=1, strength=1.0)
                db.add(p)
            p.name = name
            if org and not p.organization:
                p.organization = org
            if title and addr != dc.DANA[1] and not p.title:
                p.title = title
        elena = (await db.execute(select(Person).where(Person.user_id == w.user.id, Person.email == dc.ELENA[1]))).scalar_one()
        elena.birthday = w.clock.day(5).strftime("--%m-%d")
        elena.notes = "Mom. Birthday dinner: book a table."
        # A group mailr suggests from the book club thread, and details found in Dana's signature.
        g = PersonGroup(user_id=w.user.id, name="", source="suggested", state="suggested", name_hint="Book club",
                        score=6.0, evidence={"messages": 3, "last": now.isoformat(), "subjects": ["Book club: October pick"]})
        db.add(g)
        await db.flush()
        for name, addr, _o, _t in dc.BOOK_CLUB:
            db.add(PersonGroupMember(group_id=g.id, email=addr, name=name))
        for fld, value in (("title", "Marketing Director"), ("phone", "+1 555 0172")):
            db.add(PersonSuggestion(user_id=w.user.id, email=dc.DANA[1], field=fld, value=value, norm=value.lower(),
                                    source="signature", method="rules", message_id=w.msgs.get("lumen-1"),
                                    state="suggested"))
        await db.commit()
    w.count("suggested_groups")
    w.count("person_suggestions", 2)
    if w.caldav_id:
        await _api(w, "POST", "/address-books/sync")
        w.count("contact_cards", len(CARDS))


# ---------------------------------------------------------------- assistant


async def seed_assistant(w: World) -> None:

    from sqlalchemy import delete

    from mailr.agent.tools import ToolCtx, run_tool
    from mailr.assistant import orders as ord_
    from mailr.db import sessionmaker
    from mailr.models import (
        AssistantOrder,
        AssistantRun,
        ChatMessage,
        ChatSession,
        LabelDecision,
        Message,
        ModelEndpoint,
        User,
        WebSearchProvider,
    )

    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=DEMO_ENDPOINT, base_url=f"http://127.0.0.1:{w.api_port}/demo-model/v1",
                             locality="local", adapter="openai", models=["mailr-demo"],
                             defaults={"chat": "mailr-demo", "embed": "mailr-demo"}, enabled=True))
        # Web search: the demo's own degoog-style service (demo_model.py; reached on 127.0.0.1, which
        # MAILR_OUTBOUND_ALLOW must allow).
        await db.execute(delete(WebSearchProvider).where(WebSearchProvider.name == DEMO_SEARCH))
        db.add(WebSearchProvider(name=DEMO_SEARCH, kind="degoog", base_url=f"http://127.0.0.1:{w.api_port}/demo-search",
                                 network="local", priority=10, enabled=True))
        await db.commit()
        user = await db.get(User, w.user.id)
        L = w.labels
        made = {}
        for payload in (
            {"name": "Client asks", "query": "label:clients", "question": "Is the client asking me to decide or deliver something?",
             "actions": {"star": True, "important": True}, "errand": "Write a two-line brief: what they ask for and by when.",
             "deliver": ["brief"]},
            {"name": "Bills", "query": "has:deadline", "question": "Is this a bill or a payment reminder?",
             "actions": {"add_labels": [L["Finance/Bills"]]}},
            {"name": "Morning summary", "trigger": "schedule", "schedule": "0 8 * * 1-5", "timezone": dc.TZ,
             "query": "in:inbox newer_than:1d", "errand": "List what needs my attention today: replies I owe, "
             "deadlines this week and today's meetings.", "deliver": ["digest"]},
            {"name": "Newsletter digest", "trigger": "schedule", "schedule": "0 17 * * 5", "timezone": dc.TZ,
             "query": "label:reading newer_than:7d", "errand": "Summarise this week's newsletters in five bullets.",
             "deliver": ["digest"], "enabled": False},
        ):
            o = await ord_.apply_order_op(db, user, {"op": "create", "source": "user", "order": payload})
            made[payload["name"]] = o["id"]
            await db.commit()
        w.count("orders", len(made))

        # The brief the "Client asks" order left on the Lumen conversation, and its runs.
        if "lumen-3" in w.msgs:
            db.add(AssistantRun(user_id=user.id, order_id=made["Client asks"], trigger="message",
                                slot=f"m{w.msgs['lumen-3']}", message_id=w.msgs["lumen-3"],
                                conversation_id=w.convs["lumen-3"], status="done",
                                condition={"answer": True, "p": 0.93},
                                actions=[{"action": "star", "ok": True}, {"action": "important", "ok": True}],
                                output=("Dana asks you to confirm that both directions are ready for the board review "
                                        f"on {w.fill('{W:thu}')} at 2 PM (4 PM also works). The van wrap is in scope "
                                        "but can come last."),
                                delivered=["brief"], finished_at=w.clock.now - timedelta(hours=1)))
            w.count("briefs")
        for key in ("timeline", "lucia"):
            if key in w.msgs:
                db.add(AssistantRun(user_id=user.id, order_id=made["Client asks"], trigger="message",
                                    slot=f"m{w.msgs[key]}", message_id=w.msgs[key], conversation_id=w.convs[key],
                                    status="done", condition={"answer": key == "lucia", "p": 0.81},
                                    finished_at=w.clock.now - timedelta(hours=3)))
        if "energy-bill" in w.msgs:
            db.add(AssistantRun(user_id=user.id, order_id=made["Bills"], trigger="message",
                                slot=f"m{w.msgs['energy-bill']}", message_id=w.msgs["energy-bill"],
                                conversation_id=w.convs["energy-bill"], status="done",
                                condition={"answer": True, "p": 0.97},
                                actions=[{"action": "add_labels", "ok": True}],
                                finished_at=w.clock.now - timedelta(hours=9)))
        o = await db.get(AssistantOrder, made["Client asks"])
        o.runs, o.last_run_at = 3, w.clock.now - timedelta(hours=1)

        # Label suggestions (the classifier's, waiting for the user).
        for key, label in (("marco-quote", "Clients/Bluefin Outdoor"), ("offsite", "Studio"),
                           ("ops-closed", "Studio"), ("trailhead", "Reading")):
            if key in w.msgs:
                already = await db.get(Message, w.msgs[key])
                if L[label] in (already.label_ids or []):
                    continue
                db.add(LabelDecision(user_id=user.id, message_id=w.msgs[key], kind="label", target=str(L[label]),
                                     value=True, source="classifier", source_ref=f"label:{L[label]}", confidence=0.84,
                                     reason="similar to mail you labelled (p=0.84)", status="suggested"))
                w.count("label_suggestions")
        # Two borderline importance calls for the review queue.
        for key, val, score in (("circlet-mention", False, 0.47), ("flight", True, 0.58)):
            if key in w.msgs:
                db.add(LabelDecision(user_id=user.id, message_id=w.msgs[key], kind="importance", target="important",
                                     value=val, source="classifier", confidence=score,
                                     reason="similar to mail you marked" + (" important" if val else " not important")))
        # An earlier chat with the assistant.
        sid = "demo" + secrets.token_hex(8)
        db.add(ChatSession(id=sid, user_id=user.id, title="Lumen Coffee: where are we?", context={}))
        await db.flush()
        q = "Where are we with Lumen Coffee?"
        a = ("Dana sent the brief six days ago and asked this morning to confirm two directions for the board review "
             f"on {w.fill('{W:thu}')} at 2 PM" + (f" [[c:{w.convs['lumen-3']}]]" if "lumen-3" in w.convs else "") +
             ". The concept review is on your calendar, and the deck task is due the same day.")
        from mailr.agent.loop import renumber
        from mailr.agent.present import normalize_markdown
        from mailr.intel import citations

        text_, cites = await citations.resolve(db, w.p, a, known="")
        text_, cites = renumber(normalize_markdown(text_), cites, 1)
        db.add(ChatMessage(session_id=sid, role="user", content=q))
        db.add(ChatMessage(session_id=sid, role="assistant", content=text_, citations=cites, steps=[],
                           meta={"segment": 0, "final": True}))
        await db.commit()

    # Approvals: what the assistant asked to do, through its real tools.
    async with sessionmaker()() as db:
        user = await db.get(User, w.user.id)
        p = ord_.principal_for(user)

        def ctx(session: str):
            return ToolCtx(db=db, p=p, assistant=True, chat_session_id=session)

        pending: dict[str, int] = {}
        r = await run_tool(ctx("demo-orders"), "manage_order", {
            "action": "create", "name": "Receipts to Finance", "query": "subject:receipt",
            "question": "Is this a receipt or a payment confirmation?", "add_labels": ["Finance/Receipts"]})
        pending["order"] = r.data["approval_id"]
        r = await run_tool(ctx("demo-filters"), "manage_filter", {
            "action": "create", "name": "Kerning Weekly to Reading", "query": "from:hello@kerningweekly.example",
            "add_labels": ["Reading"], "archive": True})
        pending["filter"] = r.data["approval_id"]
        if "lumen-3" in w.msgs:
            d = await run_tool(ctx("demo-send"), "create_draft", {
                "reply_to_message_id": w.msgs["lumen-3"], "mode": "reply",
                "body": "Hi Dana,\n\nConfirmed: Thursday at 2 PM works. We will send the deck the day before.\n\nAlex"})
            r = await run_tool(ctx("demo-send"), "send_draft", {"draft_id": d.data["draft_id"]})
            if (r.data or {}).get("approval_id"):
                pending["send"] = r.data["approval_id"]
        for name, decision in (("Star mail from Priya", "approve"), ("Delete Circlet notifications", "deny")):
            if decision == "approve":
                r = await run_tool(ctx("demo-decided"), "manage_order", {
                    "action": "create", "name": name, "query": f"from:{dc.PRIYA[1]}",
                    "question": "Does Priya ask me for something?", "star": True})
            else:
                r = await run_tool(ctx("demo-decided"), "manage_filter", {
                    "action": "create", "name": name, "query": "from:notifications@circlet.example", "trash": True})
            await db.commit()
            await _api(w, "POST", f"/approvals/{r.data['approval_id']}/decide", json={"decision": decision})
            w.count("approvals_decided")
        await run_jobs(w, "approval.execute", "mailr.core.permissions")
        await db.commit()
        w.count("approvals_pending", len(pending))
        w.extra["approval_ids"] = pending
    await _seed_review(w)


# A finished bulk review (agent/review.py) of the shop mail, waiting in a chat of its own:
# conversation key -> (action, reason), as the model would have judged them.
REVIEW_SCOPE = {"view": "all", "q": "from:(fieldnote-paper.example OR atlas-outfitters.example)"}
REVIEW_DECISIONS = {
    "fieldnote": ("keep", "offer still runs this week"),
    "atlas": ("keep", "sale runs through the weekend"),
    **{f"fieldnote-old-{i}": ("trash", "old offer, expired") for i in range(1, 7)},
    **{f"atlas-old-{i}": ("trash", "sale is over") for i in range(1, 5)},
    "atlas-old-5": ("read", "preview, nothing to do"),
    "atlas-old-6": ("unsure", "may be a saved checklist"),
}


async def _seed_review(w: World) -> None:
    """`/sweep` in a chat of its own: the review_mail step, then the review judged (here, without a
    model) and finished as the job finishes it, so its card is the real one (review.finish → card)."""
    from mailr.agent import present, review
    from mailr.agent.loop import _with_msg
    from mailr.agent.tools import ToolCtx, run_tool
    from mailr.assistant import orders as ord_
    from mailr.db import sessionmaker
    from mailr.models import ChatMessage, ChatSession, MailReview, User

    if not all(k in w.convs for k in REVIEW_DECISIONS):
        return
    async with sessionmaker()() as db:
        user = await db.get(User, w.user.id)
        if user is None:
            return
        sid = "demo" + secrets.token_hex(8)
        db.add(ChatSession(id=sid, user_id=user.id, title="Clean up the shop mail", context={}))
        await db.flush()
        ctx = ToolCtx(db=db, p=ord_.principal_for(user), assistant=True, chat_session_id=sid, chat_context={})
        args = {"scope": REVIEW_SCOPE, "instructions": "keep offers that are still running"}
        res = await run_tool(ctx, "review_mail", args)
        rid = int(res.data["review_id"])
        step: dict[str, Any] = {"id": "demo-review", "name": "review_mail", "icon": present.step_icon("review_mail"),
                                "args": args, "segment": 0}
        step["label"] = _with_msg(step, "label_msg", await present.step_msg(db, user.id, "review_mail", args))
        summary, count = present.result_msg("review_mail", res)
        step.update(ok=True, summary=_with_msg(step, "summary_msg", summary), count=count, ms=180)
        db.add(ChatMessage(session_id=sid, role="user", content="/sweep keep offers that are still running"))
        db.add(ChatMessage(session_id=sid, role="assistant", steps=[step], meta={"segment": 0, "final": True},
                           content="I'm reviewing your mail from Fieldnote Paper Co. and Atlas Outfitters. The review "
                                   "card will appear in this chat when it is done."))
        await db.commit()

        # The judging, as the assistant.review job leaves it; then the card.
        r = await db.get(MailReview, rid)
        if r is None:
            return
        by_conv = {str(w.convs[k]): v for k, v in REVIEW_DECISIONS.items()}
        r.decisions = {str(i): {"a": by_conv[str(i)][0], "r": by_conv[str(i)][1],
                                **({"p": 2} if by_conv[str(i)][0] == "unsure" else {})}
                       for i in r.ids if str(i) not in (r.kept or {}) and str(i) in by_conv}
        r.done, r.model = len(r.ids), "mailr-demo"
        await review.finish(db, r)
        await db.commit()
        w.count("reviews")
        w.extra["review_approval_id"] = r.approval_id


# ---------------------------------------------------------------- calendar and tasks


def _radicale_setup(w: World) -> bool:
    """Calendars and task lists over CalDAV, with the events and tasks that do not come from mail."""
    import caldav
    from caldav.elements import ical as dav_ical

    try:
        httpx.get(RADICALE, timeout=2)
    except httpx.HTTPError:
        return False
    pr = caldav.DAVClient(url=RADICALE, username=cal_user(), password="demo").principal()
    cols = {}
    for key, name, comps, colour in (("work", "Work", ["VEVENT"], "#2563eb"), ("personal", "Personal", ["VEVENT"], "#16a34a"),
                                     ("clinic", "Clinic board", ["VEVENT"], "#64748b"),
                                     ("studio", "Studio", ["VTODO"], "#d97706"), ("errands", "Errands", ["VTODO"], "#7c3aed")):
        c = pr.make_calendar(name=name, cal_id=key, supported_calendar_component_set=comps)
        c.set_properties([dav_ical.CalendarColor(colour)])
        cols[key] = c
        w.cal[key] = str(c.url)

    if "people" in w.scenarios:
        _address_book()
    if "calendar" in w.scenarios:
        _events(w, cols)
    if "tasks" in w.scenarios:
        _tasks(w, cols)
    return True


CARDS = [
    # (person, phone, postal address)
    (dc.PRIYA, "+1 555 0101", "18 Foundry Lane;Riverton;IL;60601"),
    (dc.JORDAN, "+1 555 0102", "18 Foundry Lane;Riverton;IL;60601"),
    (dc.SAM, "+1 555 0103", ""),
    (dc.MEI, "+1 555 0104", ""),
    (dc.MARCO, "+1 555 0165", "400 Harbor Way;Bayport;OR;97001"),
    (dc.LUCIA, "+1 555 0188", "Calle del Olmo 12;Riverton;IL;60605"),
    (dc.THEO, "+1 555 0140", ""),
    (dc.ELENA, "+1 555 0150", "7 Linden Court;Riverton;IL;60610"),
]


def _vcard(person: tuple[str, str, str, str], phone: str, adr: str) -> str:
    name, addr, org, title = person
    first, _, last = name.partition(" ")
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"UID:{addr}", f"FN:{name}", f"N:{last};{first};;;",
             f"EMAIL;TYPE=INTERNET:{addr}"]
    if org:
        lines.append(f"ORG:{org}")
    if title:
        lines.append(f"TITLE:{title}")
    if phone:
        lines.append(f"TEL;TYPE=CELL:{phone}")
    if adr:
        street, city, region, code = adr.split(";")
        lines.append(f"ADR;TYPE=WORK:;;{street};{city};{region};{code};USA")
    return "\r\n".join([*lines, "END:VCARD", ""])


def _address_book() -> None:
    """A CardDAV address book next to the calendars (contacts.sync reads it)."""
    auth = (cal_user(), "demo")
    url = f"{RADICALE}{cal_user()}/contacts/"
    body = ('<?xml version="1.0" encoding="utf-8"?><D:mkcol xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
            "<D:set><D:prop><D:resourcetype><D:collection/><C:addressbook/></D:resourcetype>"
            "<D:displayname>Contacts</D:displayname></D:prop></D:set></D:mkcol>")
    httpx.request("MKCOL", url, content=body, auth=auth, timeout=10,
                  headers={"Content-Type": "application/xml"}).raise_for_status()
    for person, phone, adr in CARDS:
        slug = re.sub(r"[^a-z0-9]+", "-", person[1].lower())
        httpx.put(f"{url}{slug}.vcf", content=_vcard(person, phone, adr).encode(), auth=auth, timeout=10,
                  headers={"Content-Type": "text/vcard; charset=utf-8"}).raise_for_status()


def _events(w: World, cols: dict[str, Any]) -> None:
    c = w.clock
    st = c.now
    monday = c.today - timedelta(days=c.today.weekday())

    def put(col: str, key: str, ev: Any) -> None:
        cols[col].save_event(vcalendar(ev).decode())
        w.events[key] = str(ev.get("uid"))
        w.count("events")

    def on(d: date, h: int, m: int = 0) -> datetime:
        return datetime(d.year, d.month, d.day, h, m, tzinfo=c.zone)

    start = on(monday - timedelta(days=14), 9, 30)
    put("work", "standup", vevent("standup@northwind.example", "Studio standup", start, start + timedelta(minutes=15),
                                  stamp=st, rrule={"freq": "weekly", "byday": ["MO", "TU", "WE", "TH", "FR"]},
                                  location="meet.northwind.example/standup", alarms=[5],
                                  url="https://meet.northwind.example/standup"))
    start = on(monday - timedelta(days=13), 11)
    put("work", "one-on-one", vevent("priya-1on1@northwind.example", "1:1 with Priya", start, start + timedelta(minutes=30),
                                     stamp=st, rrule={"freq": "weekly", "byday": ["TU"]}, organizer=dc.PRIYA[1],
                                     attendees=[(dc.PRIYA[1], "ACCEPTED"), (dc.WORK, "ACCEPTED")], alarms=[10]))
    start = c.at(c.workday(1), 15)
    put("work", "critique", vevent("design-critique@northwind.example", "Design critique", start,
                                   start + timedelta(hours=1), stamp=st - timedelta(days=1), organizer=dc.WORK,
                                   location="Studio, project wall",
                                   description="Bluefin hero options and the Lumen packaging range.",
                                   attendees=[(dc.WORK, "ACCEPTED"), (dc.SAM[1], "NEEDS-ACTION"), (dc.MEI[1], "NEEDS-ACTION"),
                                              (dc.JORDAN[1], "ACCEPTED")], alarms=[10]))
    thu = c.day("thu")
    put("personal", "dentist", vevent("dentist@example.net", "Dentist", on(thu, 14, 30), on(thu, 15, 15), stamp=st,
                                      location="Bayside Dental, 210 Pier Street", alarms=[60]))
    put("work", "studio-review", vevent("studio-review@northwind.example", "Studio review", on(thu, 16, 30),
                                        on(thu, 17, 30), stamp=st, organizer=dc.PRIYA[1],
                                        attendees=[(dc.PRIYA[1], "ACCEPTED"), (dc.WORK, "ACCEPTED"), (dc.JORDAN[1], "ACCEPTED"),
                                                   (dc.SAM[1], "ACCEPTED"), (dc.MEI[1], "TENTATIVE")]))
    # Today.
    today = c.today
    put("work", "moodboard", vevent("lumen-moodboard@northwind.example", "Lumen moodboard session", on(today, 10, 30),
                                    on(today, 12), stamp=st, location="Studio, room 2", alarms=[10]))
    put("work", "lunch-mei", vevent("lunch-mei@northwind.example", "Lunch with Mei", on(today, 12, 15), on(today, 13),
                                    stamp=st, status="TENTATIVE"))
    put("work", "casa-call", vevent("casa-call@northwind.example", "Call with Casa Verde", on(today, 15), on(today, 15, 30),
                                    stamp=st, organizer=dc.WORK, location="meet.northwind.example/casaverde",
                                    url="https://meet.northwind.example/casaverde",
                                    attendees=[(dc.WORK, "ACCEPTED"), (dc.LUCIA[1], "ACCEPTED")], alarms=[10]))
    # Earlier this week and the weeks around it.
    put("work", "planning", vevent("sprint-planning@northwind.example", "Sprint planning", on(monday, 14), on(monday, 15),
                                   stamp=st, location="Studio, big room"))
    put("work", "content-review", vevent("bluefin-content@northwind.example", "Bluefin content review",
                                         on(monday + timedelta(days=2), 13), on(monday + timedelta(days=2), 14), stamp=st,
                                         organizer=dc.JORDAN[1],
                                         attendees=[(dc.JORDAN[1], "ACCEPTED"), (dc.WORK, "ACCEPTED"), (dc.MARCO[1], "ACCEPTED")]))
    put("work", "sprint", vevent("lumen-sprint@northwind.example", "Lumen design sprint", today - timedelta(days=1),
                                     today + timedelta(days=1), stamp=st, transp="TRANSPARENT"))
    wed = monday + timedelta(days=9)
    put("work", "offsite", vevent("offsite@northwind.example", "Northwind offsite", wed, wed + timedelta(days=2), stamp=st,
                                  location="Cedar Lodge", transp="TRANSPARENT",
                                  description="Two days: wholesale clients, brand systems, hiring."))
    sat = c.day("sat")
    put("personal", "climb", vevent("climb@example.net", "Climbing with Theo", on(sat, 10), on(sat, 12), stamp=st,
                                    location="Granite Works climbing gym"))
    put("clinic", "health-fair", vevent("health-fair@harborclinic.example", "Health fair (volunteer)", on(sat, 8, 30),
                                        on(sat, 13), stamp=st, location="Harbor Community Clinic, parking lot"))
    bday = c.day(5)
    put("personal", "birthday", vevent("mom-birthday@example.net", "Mom's birthday", bday, bday + timedelta(days=1),
                                       stamp=st, rrule={"freq": "yearly"}, transp="TRANSPARENT", alarms=[60 * 15]))
    last = today.replace(day=1) - timedelta(days=1)  # the last Tuesday of last month
    last -= timedelta(days=(last.weekday() - 1) % 7)
    start = on(last, 19)
    put("personal", "book-club", vevent("book-club@example.org", "Book club", start, start + timedelta(hours=2), stamp=st,
                                        rrule={"freq": "monthly", "byday": "-1TU"}, location="Ben's place"))
    board = c.day(8)
    put("clinic", "board", vevent("clinic-board@harborclinic.example", "Clinic board meeting", on(board, 18),
                                  on(board, 19, 30), stamp=st, organizer=dc.AMARA[1],
                                  attendees=[(dc.AMARA[1], "ACCEPTED"), (dc.CLINIC, "ACCEPTED")],
                                  location="Harbor Community Clinic, conference room"))
    # For the reminders scenario: an event starting soon whose reminder has fired, one later on.
    soon = c.quarter_after(timedelta(minutes=45))
    put("work", "printer", vevent("print-proofs@northwind.example", "Pick up the print proofs", soon,
                                  soon + timedelta(minutes=30), stamp=st, location="Copyhaus, 9 Mill Road", alarms=[60]))
    later = c.quarter_after(timedelta(hours=3))
    put("work", "photographer", vevent("photographer-call@northwind.example", "Call with the photographer", later,
                                       later + timedelta(minutes=30), stamp=st, alarms=[15]))


def _tasks(w: World, cols: dict[str, Any]) -> None:
    c = w.clock
    st = c.now

    def put(col: str, key: str, t: Any) -> str:
        cols[col].save_todo(vcalendar(t).decode())
        uid = str(t.get("uid"))
        w.tasks[key] = uid
        w.count("tasks")
        return uid

    put("studio", "invoice", vtodo("task-lumen-invoice@northwind.example", "Send the Lumen deposit invoice", stamp=st,
                                   due=c.day(0), priority=1, categories=["billing"]))
    deck = put("studio", "deck", vtodo("task-lumen-deck@northwind.example", "Prepare the Lumen concept deck", stamp=st,
                                       due=c.at("thu", 12), priority=5, categories=["lumen"],
                                       notes="Two directions, packaging mockups, the van wrap sketch."))
    put("studio", "deck-1", vtodo("task-deck-moodboard@northwind.example", "Export the moodboard", stamp=st, parent=deck,
                                  completed=c.now - timedelta(hours=20)))
    put("studio", "deck-2", vtodo("task-deck-rationale@northwind.example", "Write the design rationale", stamp=st,
                                  parent=deck, due=c.day(2)))
    put("studio", "deck-3", vtodo("task-deck-rehearse@northwind.example", "Rehearse with Jordan", stamp=st, parent=deck,
                                  due=c.day("wed")))
    put("studio", "insurance", vtodo("task-insurance@northwind.example", "Renew the studio insurance", stamp=st,
                                     due=c.day(-3), priority=1, categories=["admin"]))
    put("studio", "timesheet", vtodo("task-timesheet@northwind.example", "Weekly timesheet", stamp=st,
                                     due=c.at("fri", 16), rrule={"freq": "weekly", "byday": ["FR"]}, categories=["admin"]))
    put("studio", "portfolio", vtodo("task-portfolio@northwind.example", "Update the Bluefin case study", stamp=st,
                                     start=c.day(7), categories=["portfolio"]))
    put("studio", "venue", vtodo("task-venue@northwind.example", "Book the offsite venue", stamp=st, due=c.day(-2),
                                 completed=c.now - timedelta(days=2)))
    put("studio", "expenses", vtodo("task-expenses@northwind.example", "Submit the expense report", stamp=st,
                                    due=c.quarter_after(timedelta(hours=-4)), alarms=[0], categories=["admin"]))
    put("errands", "call-mom", vtodo("task-call-mom@example.net", "Call Mom", stamp=st, due=c.day("sun"),
                                     rrule={"freq": "weekly", "byday": ["SU"]}, categories=["family"]))
    put("errands", "library", vtodo("task-library@example.net", "Return the library books", stamp=st, due=c.day(-1),
                                    categories=["errands"]))
    put("errands", "climb", vtodo("task-climb@example.net", "Pack climbing shoes", stamp=st, due=c.day("sat"),
                                  categories=["weekend"]))
    put("errands", "gift", vtodo("task-gift@example.net", "Buy a birthday present for Mom", stamp=st, due=c.day(4),
                                 priority=1, categories=["family", "errands"]))


async def seed_calendar(w: World) -> None:
    """Radicale collections, events and tasks; the CalDAV account in mailr; the mirror synced."""
    from mailr.calendar import sync
    from mailr.db import sessionmaker
    from mailr.models import Connector

    if not await asyncio.to_thread(_radicale_setup, w):
        w.notes.append("Radicale (:15232) is not running: no calendar, tasks or reminders")
        return
    acct = await _api(w, "POST", "/calendar/connect", json={"url": RADICALE, "username": cal_user(), "password": "demo",
                                                            "name": "Calendar"})
    w.caldav_id = acct["id"]
    await _api(w, "PATCH", f"/calendar/accounts/{acct['id']}", json={
        "default_calendar": w.cal["work"], "default_tasklist": w.cal["studio"]})
    await sync.sync_connector(acct["id"], full=True)
    async with sessionmaker()() as db:
        c = await db.get(Connector, acct["id"])
        c.last_sync_at = w.clock.synced(2)
        await db.commit()
    w.count("calendars", 5)


async def seed_linked(w: World) -> None:
    """An event and tasks made from emails (mail.to_event, mail.to_task), through the API."""
    c = w.clock
    if "calendar" in w.scenarios and "lumen-3" in w.msgs:
        thu = c.day("thu")
        start = datetime(thu.year, thu.month, thu.day, 14, tzinfo=c.zone)
        ev = await _api(w, "POST", "/calendar/from-message/confirm", json={
            "message_id": _pid("message", w.msgs["lumen-3"]), "kind": "event", "title": "Lumen concept review",
            "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
            "location": "Lumen Coffee Roasters, board room", "calendar": w.cal["work"], "reminders": [30]})
        w.events["lumen-review"] = (ev or {}).get("uid", "")
        w.count("events_from_mail")
        # Meeting prep (event.prep): Dana and Jordan are its guests (no invitations go out), and memory
        # knows a little about Dana and the client.
        if w.events["lumen-review"]:
            await _api(w, "PATCH", f"/calendar/events/{w.events['lumen-review']}", json={
                "attendees": [{"email": dc.DANA[1], "name": dc.DANA[0]}, {"email": dc.JORDAN[1], "name": dc.JORDAN[0]}],
                "send_updates": False})
            await _seed_prep_memory(w)
    if "tasks" in w.scenarios:
        for key, title, due, lst in (("pr-142", "Review the pricing page PR", c.day(1), "studio"),
                                     ("water-bill", "Pay the water bill", c.day(8), "errands"),
                                     ("clinic-intake", "Revise the intake form", c.day(4), "studio")):
            if key not in w.msgs:
                continue
            t = await _api(w, "POST", "/tasks", json={
                "summary": title, "due": due.isoformat(), "list": w.cal[lst],
                "conversation_id": _pid("conversation", w.convs[key])})
            w.tasks[f"mail-{key}"] = (t or {}).get("uid", "")
            w.count("tasks_from_mail")


async def _seed_prep_memory(w: World) -> None:
    """Two people and a client in the assistant's memory, for the meeting prep shot."""
    from mailr.db import sessionmaker
    from mailr.models import Entity, Memory

    async with sessionmaker()() as db:
        dana = Entity(user_id=w.user.id, kind="person", name=dc.DANA[0], emails=[dc.DANA[1]],
                      summary=f"{dc.DANA[3]} at {dc.DANA[2]}")
        lumen = Entity(user_id=w.user.id, kind="organization", name="Lumen", aliases=[dc.DANA[2]],
                       domains=[dc.DANA[1].split("@")[1]], summary="Coffee roaster; rebrand client since September")
        db.add_all([dana, lumen])
        await db.flush()
        db.add_all([Memory(user_id=w.user.id, entity_id=dana.id, kind="preference", source="user",
                           text="Prefers two clear directions over many options"),
                    Memory(user_id=w.user.id, entity_id=lumen.id, kind="fact", source="user",
                           text="The board signs off on the final direction")])
        await db.commit()
    w.count("memories", 2)


async def seed_reminders(w: World) -> None:
    """Plan reminders, then put a few in the states the tray shows: fired, missed, snoozed, dismissed."""
    from sqlalchemy import select

    from mailr.calendar import planner
    from mailr.db import sessionmaker
    from mailr.models import ReminderSchedule, ReminderState

    now = w.clock.now
    async with sessionmaker()() as db:
        await planner.replan_user(db, w.user.id, now=now)
        await db.commit()
        rows = {r.uid: r for r in (await db.execute(select(ReminderSchedule).where(
            ReminderSchedule.user_id == w.user.id))).scalars()}
        fired = rows.get(w.events.get("printer", "-"))
        if fired is not None:  # fired on time, not acted on: shows in the tray
            fired.fired_at = min(now, fired.fire_at + timedelta(seconds=4))
            w.count("reminders_fired")
        missed = rows.get(w.tasks.get("expenses", "-"))
        if missed is not None:  # fired hours late (the server was down): "Missed"
            missed.fired_at = now - timedelta(minutes=20)
            w.count("reminders_missed")
        snoozed = rows.get(w.events.get("moodboard", "-"))
        if snoozed is not None:  # snoozed earlier; the snooze has come due
            snoozed.fired_at = snoozed.fire_at
            db.add(ReminderState(user_id=w.user.id, key=snoozed.key, kind=snoozed.kind, uid=snoozed.uid,
                                 occurrence=snoozed.occurrence, minutes=snoozed.minutes, state="snoozed",
                                 snoozed_until=now - timedelta(minutes=5), device="web", anchor_at=snoozed.anchor_at))
            w.count("reminders_snoozed")
        used = {id(x) for x in (fired, missed, snoozed)}
        handled = next((r for r in sorted(rows.values(), key=lambda r: r.fire_at)
                        if r.fire_at <= now and r.fired_at is None and id(r) not in used), None)
        if handled is not None:  # dismissed on the phone
            handled.fired_at = handled.fire_at
            db.add(ReminderState(user_id=w.user.id, key=handled.key, kind=handled.kind, uid=handled.uid,
                                 occurrence=handled.occurrence, minutes=handled.minutes, state="handled", device="android",
                                 anchor_at=handled.anchor_at))
            w.count("reminders_dismissed")
        await db.commit()
        later = rows.get(w.events.get("photographer", "-"))
    if later is not None:  # snoozed to a later time, through the API
        await _api(w, "POST", "/reminders/snooze", json={
            "key": later.key, "until": (max(now, datetime.now(UTC)) + timedelta(minutes=50)).isoformat()})
        w.count("reminders_snoozed")


# ---------------------------------------------------------------- Speakr


async def seed_speakr(w: World, url: str, token: str) -> None:
    from mailr.integrations.speakr.sync import sync_connector

    c = await _api(w, "POST", "/connectors", json={"kind": "speakr", "name": "Speakr", "data_class": "normal",
                                                    "config": {"base_url": url}, "secrets": {"token": token}})
    res = await sync_connector(c["id"])
    w.extra["speakr"] = {"connector": c["id"], "sync": res}


# ---------------------------------------------------------------- entry


async def seed(now: datetime | None = None, scenarios: list[str] | None = None, *, speakr_url: str | None = None,
               speakr_token: str | None = None, api_port: int = 8030) -> dict[str, Any]:
    from mailr.core.users import bootstrap

    _local_target()
    chosen = set(scenarios or DEFAULT_SCENARIOS) | {"accounts"}
    unknown = chosen - set(SCENARIOS)
    if unknown:
        raise SystemExit(f"unknown scenario(s): {', '.join(sorted(unknown))} (choose from {', '.join(SCENARIOS)})")
    if speakr_url and speakr_token:
        chosen.add("speakr")
    elif "speakr" in chosen:
        raise SystemExit("the speakr scenario needs --speakr-url and --speakr-token")
    now = (now or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
    w = World(Clock(now), chosen, api_port)

    await bootstrap()
    await reset()
    await seed_accounts(w)
    try:
        if chosen & {"organize", "mail", "assistant"}:
            await seed_labels(w)
        if chosen & {"organize", "mail"}:
            await seed_rules(w)
        if chosen & {"calendar", "tasks", "reminders", "people"}:
            await seed_calendar(w)
        if "mail" in chosen:
            await seed_mail(w)
            await seed_screener(w)
            if w.caldav_id:
                await run_itip(w)
        if "organize" in chosen:
            await seed_organize(w)
        if "people" in chosen:
            await seed_people(w)
        if "assistant" in chosen:
            await seed_assistant(w)
        if w.caldav_id and "mail" in chosen:
            await seed_linked(w)
        if w.caldav_id and "reminders" in chosen:
            await seed_reminders(w)
        if "speakr" in chosen:
            await seed_speakr(w, speakr_url, speakr_token)
        if w.caldav_id:
            from mailr.calendar import sync

            await sync.sync_connector(w.caldav_id, full=True)
    finally:
        await w.http.aclose()
    return {"email": dc.WORK, "password": dc.PASSWORD, "user_id": w.user.id, "now": now.isoformat(),
            "scenarios": sorted(chosen), "counts": w.summary, "notes": w.notes, **w.extra,
            "messages": {k: v for k, v in w.msgs.items()}, "conversations": dict(w.convs),
            "labels": w.labels, "calendars": w.cal, "events": w.events, "tasks": w.tasks}


def add_arguments(ap) -> None:
    ap.add_argument("--now", help="ISO time the demo is relative to (default: now; a time without a zone is in "
                                  "the demo user's zone)")
    ap.add_argument("--scenario", default="", help=f"comma list of {', '.join(SCENARIOS)} (default: all but speakr)")
    ap.add_argument("--speakr-url", help="a Speakr development server to connect (never a production one)")
    ap.add_argument("--speakr-token", help="an API token of that Speakr server")
    ap.add_argument("--api-port", type=int, default=8030, help="the port the demo API serves the scripted model on")
    ap.add_argument("--json", action="store_true", help="print the whole result as JSON")


def run(a) -> None:
    now = None
    if a.now:
        now = datetime.fromisoformat(a.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo(dc.TZ))
    scen = [s.strip() for s in a.scenario.split(",") if s.strip()] or None
    out = asyncio.run(seed(now, scen, speakr_url=a.speakr_url, speakr_token=a.speakr_token, api_port=a.api_port))
    if a.json:
        print(json.dumps(out, default=str, indent=2))
        return
    print(f"demo seeded for {out['email']} (password {out['password']}), now = {out['now']}")
    print("scenarios: " + ", ".join(out["scenarios"]))
    print("counts: " + ", ".join(f"{k} {v}" for k, v in sorted(out["counts"].items()) if isinstance(v, int)))
    for n in out["notes"]:
        print("note: " + n)
