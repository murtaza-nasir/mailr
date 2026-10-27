"""Spam: training on user verdicts, and optional scoring with Stalwart's filter.

Layering: the upstream provider decides first (its Junk folder or
X-Spam headers, applied at ingest). Stalwart's spam filter does not run on JMAP import, so
mailr asks it to score a message with the admin `ClassifySpam` action, using the connecting
IP and HELO name from the provider's topmost external Received header.

User setting `spam_classifier`:
  "score"            score recent inbound mail and show the score; never move anything.
  "junk" (default)   also move mail to Spam when the score is at least `spam_junk_score`
                     (default 15), the provider did not already decide, and the sender is not
                     someone the user corresponds with. Never for mail Gmail delivered outside Spam,
                     nor for mail whose sender passes DMARC for the From domain (`keep_reason`):
                     the score is recorded and shown, and the mail stays where it is.
  "off"              no scoring.
Scoring does live DNS lookups (0.2–2 s per message), so history imports are not scored. Live mail
is scored at the start of triage (intel/tasks.py), before any model reads it.
Every user "Spam" / "Not spam" click sets $junk / $notjunk, which trains Stalwart's Bayes filter.

No model reads likely spam (`model_skip_reason`): mail in Spam, mail from a blocked sender, and
mail from a new sender whose spam score (Stalwart's or the provider's) is at least
`spam_suspect_score` (default 5; 0 turns the check off) get rules-only triage, and the Why panel
says "Not checked by a model: likely spam".

Spam sender domains (`learn_domains`): Stalwart's Bayes filter needs hundreds of examples before
it moves anything, so mailr also learns from the user's own marks. When the user reports mail
from 2 or more conversations from one sender domain as spam within 30 days, later mail from that
domain goes to Spam at ingest (core/ingest.py), unless the sender is someone the user
corresponds with. Shared mail providers (gmail.com and the like) are learned by address instead.
"Not spam" on mail from a learned domain, or Remove in Settings → Mail → Spam & Screener, takes the domain off the
list; only marks made after that count again.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from datetime import UTC, datetime, timedelta
from email.parser import BytesHeaderParser
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.core.jmap import CORE
from mailr.core.store import service_client, user_store
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Label, LabelDecision, Message, Person, User

log = logging.getLogger("mailr.spam")
MGMT = [CORE, "urn:stalwart:jmap"]
DEFAULT_MODE = "junk"
DEFAULT_JUNK_SCORE = 15.0
SCORE_WINDOW = timedelta(days=2)
# A spam score at which no model reads the message (Settings → Mail → Spam & Screener, `spam_suspect_score`).
DEFAULT_SUSPECT_SCORE = 5.0
SKIP_REASON = "Not checked by a model: likely spam"
# Spam sender domains: this many conversations reported as spam within DOMAIN_WINDOW.
DOMAIN_MARKS = 2
DOMAIN_WINDOW = timedelta(days=30)
MAX_DOMAINS = 500
DOMAINS_KEY = "spam_domains"
REMOVED_KEY = "spam_domains_removed"
# Shared mail providers: one spammer there says nothing about the provider's other users, so
# these are learned by address.
SHARED_PROVIDERS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "ymail.com", "rocketmail.com", "aol.com", "icloud.com", "me.com", "mac.com", "proton.me",
    "protonmail.com", "pm.me", "gmx.com", "gmx.net", "gmx.de", "web.de", "mail.com", "yandex.com",
    "yandex.ru", "mail.ru", "zoho.com", "qq.com", "163.com", "126.com", "fastmail.com", "tutanota.com",
    "hey.com", "duck.com", "naver.com", "rediffmail.com", "comcast.net", "att.net", "verizon.net",
})

router = APIRouter(tags=["spam"])

_RECEIVED = re.compile(r"from\s+(?P<helo>[^\s(;]+)(?P<rest>.*?)\bby\b", re.I | re.S)
_IP = re.compile(r"\[(?:IPv6:)?([0-9a-fA-F:.]+)\]")


def settings_for(user: User) -> tuple[str, float]:
    st = user.settings or {}
    mode = st.get("spam_classifier", DEFAULT_MODE)
    if mode not in ("off", "score", "junk"):
        mode = DEFAULT_MODE
    return mode, float(st.get("spam_junk_score", DEFAULT_JUNK_SCORE))


def suspect_score(user: User | None) -> float | None:
    """The spam score from which no model reads a message (None: the check is off)."""
    v = ((user.settings or {}) if user else {}).get("spam_suspect_score", DEFAULT_SUSPECT_SCORE)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return DEFAULT_SUSPECT_SCORE
    return v if v > 0 else None


def provider_score(raw: bytes | None) -> float | None:
    """The provider's SpamAssassin score from the message's X-Spam-Status header, if any."""
    if not raw:
        return None
    from mailr.core.spamrules import upstream_score

    try:
        msg = BytesHeaderParser().parsebytes(raw[:200_000])
    except Exception:  # noqa: BLE001
        return None
    return upstream_score({"X-Spam-Status": str(msg.get("X-Spam-Status", "") or "")})


async def model_skip_reason(db: AsyncSession, user: User, m: Message, raw: bytes | None) -> str | None:
    """Why no model may read this message ("likely spam: ..."), or None.

    Mail in Spam, mail from a blocked sender, and mail from a new sender with a spam score at or
    above `spam_suspect_score` get rules-only triage. The user's own "Not spam" on the message
    lifts it."""
    if m.sent_by_me:
        return None
    last_user = (await db.execute(select(LabelDecision.value).where(
        LabelDecision.message_id == m.id, LabelDecision.kind == "spam", LabelDecision.source == "user")
        .order_by(LabelDecision.id.desc()).limit(1))).scalar_one_or_none()
    if last_user is False:
        return None
    junk = (await db.execute(select(Label.id).where(Label.user_id == m.user_id, Label.role == "junk"))).scalar()
    if junk is not None and junk in (m.label_ids or []):
        return f"{SKIP_REASON} (in Spam)"
    sender = (m.from_email or "").lower()
    if sender and (await db.execute(select(Person.id).where(
            Person.user_id == m.user_id, Person.email == sender, Person.blocked.is_(True)).limit(1))).first():
        return f"{SKIP_REASON} (a blocked sender)"
    limit = suspect_score(user)
    if limit is None:
        return None
    scores = [(m.spam_score, "spam score"), (provider_score(raw), "provider spam score")]
    hit = next(((v, what) for v, what in scores if v is not None and v >= limit), None)
    if hit is None:
        return None
    from mailr.core.spamrules import known_sender

    if sender and await known_sender(db, m.user_id, sender):
        return None
    return f"{SKIP_REASON} ({hit[1]} {hit[0]:.1f}, at least {limit:g})"


def envelope_from_received(raw: bytes) -> dict[str, Any] | None:
    """Find the first Received header (top-down) whose connecting IP is public."""
    try:
        msg = BytesHeaderParser().parsebytes(raw[:200_000])
    except Exception:  # noqa: BLE001
        return None
    for hdr in msg.get_all("Received", []) or []:
        h = " ".join(str(hdr).split())
        m = _RECEIVED.search(h)
        if not m:
            continue
        ipm = _IP.search(m.group("rest"))
        if not ipm:
            continue
        try:
            ip = ipaddress.ip_address(ipm.group(1))
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            continue
        return {"remoteIp": str(ip), "ehloDomain": m.group("helo").strip("[]"),
                "isTls": "tls" in h.lower() or "esmtps" in h.lower()}
    return None


async def train(db: AsyncSession, m: Message, spam: bool) -> None:
    user = await db.get(User, m.user_id)
    store = await user_store(db, user)
    if not m.jmap_id:
        return
    patch = {"keywords/$junk": True, "keywords/$notjunk": None} if spam else \
        {"keywords/$notjunk": True, "keywords/$junk": None}
    await store.jmap.one("Email/set", {"update": {m.jmap_id: patch}})


async def classify(db: AsyncSession, m: Message, raw: bytes) -> dict[str, Any] | None:
    env = envelope_from_received(raw)
    if env is None:
        return None
    rcpt = [a for a in (m.delivered_to or [])] or [a["email"] for a in (m.to or [])][:5]
    req = {"@type": "ClassifySpam", "message": raw.decode("utf-8", "replace"),
           "remoteIp": env["remoteIp"], "ehloDomain": env["ehloDomain"],
           "envFrom": m.from_email or "", "envRcptTo": {r: True for r in rcpt if r},
           "isTls": env["isTls"]}
    res = await service_client().one("x:Action/set", {"create": {"c": req}}, using=MGMT)
    created = (res.get("created") or {}).get("c")
    if not created:
        log.info("ClassifySpam refused: %s", res.get("notCreated"))
        return None
    return created


async def keep_reason(db: AsyncSession, m: Message) -> str | None:
    """Why a high score must not move this message to Spam (the score is still recorded and shown):
    - Gmail delivered it outside Spam. Gmail's own filter decided it is not spam, and a second check
      on headers alone overrode it (a DigitalOcean sign-in code, 27 Oct 2026: Reply-To the
      recipient's own address, which some services set, scored 19.6).
    - The sender is authenticated: DMARC passes for a domain that matches the From address."""
    from mailr.models import Connector

    conn = await db.get(Connector, m.connector_id) if m.connector_id else None
    if conn is not None and conn.kind == "gmail":
        return "not moved: Gmail delivered it outside Spam"
    ar = m.auth_results or {}
    frm = (m.from_email or "").rpartition("@")[2].lower()
    dfrom = str(ar.get("dmarc_from") or "").lower()
    if ar.get("dmarc") == "pass" and frm and dfrom and (frm == dfrom or frm.endswith("." + dfrom)):
        return f"not moved: DMARC passes for {dfrom}"
    return None


async def score_and_act(db: AsyncSession, m: Message, raw: bytes) -> None:
    from mailr.core import threading

    user = await db.get(User, m.user_id)
    mode, junk_score = settings_for(user)
    if mode == "off" or m.sent_by_me or m.deleted:
        return
    res = await classify(db, m, raw)
    if not res or "score" not in res:
        return
    score = float(res["score"])
    m.spam_score = score
    tags = sorted((res.get("tags") or {}).items(), key=lambda kv: -float((kv[1] or {}).get("score", 0) or 0))[:5]
    reason = f"Spam score {score:.1f}" + (f" ({', '.join(k for k, _ in tags)})" if tags else "")
    store = await user_store(db, user)
    junk = await store.role_label(db, "junk")
    already_junk = junk.id in (m.label_ids or [])
    from mailr.core.spamrules import known_sender

    known = await known_sender(db, m.user_id, m.from_email)
    keep = await keep_reason(db, m) if score >= junk_score and not already_junk else None
    if keep:
        reason += f"; {keep}"
    act = mode == "junk" and score >= junk_score and not already_junk and not known \
        and not (m.upstream or {}).get("upstream_verdict") and not keep
    db.add(LabelDecision(user_id=m.user_id, message_id=m.id, kind="spam", target="spam", value=score >= junk_score,
                         source="classifier", source_ref="stalwart", confidence=min(1.0, max(0.0, score / 20)),
                         reason=reason, status="applied" if act else "suggested"))
    if act:
        # Not core.actions.apply: that would record the move as a user correction. A label a
        # filter gave is kept aside for "Not spam" (classify.move_to_spam).
        from mailr.intel.classify import move_to_spam

        await move_to_spam(db, store, [m], junk.id)
    elif score >= junk_score:
        # Labels never apply to spam: the label classifier's suggestions on it are withdrawn and
        # the labels it applied come off (intel/classify.py labels_blocked).
        from mailr.intel.classify import withdraw_labels

        await withdraw_labels(db, store, [m])
    if m.conversation_id:
        await threading.refresh(db, m.user_id, [m.conversation_id])


def eligible_for_scoring(m: Message) -> bool:
    if m.sent_by_me or m.draft or not m.connector_id:
        return False
    when = m.received_at or m.date
    return bool(when and when > datetime.now(UTC) - SCORE_WINDOW)


# ---------------------------------------------------------------- spam sender domains


def sender_key(email: str | None) -> str | None:
    """What a spam mark teaches: the sender's domain, or the address at a shared mail provider."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    dom = email.rsplit("@", 1)[1]
    if not dom:
        return None
    return email if dom in SHARED_PROVIDERS else dom


def _key_sql(key: str) -> tuple[str, dict[str, Any]]:
    if "@" in key:
        return "lower(m.from_email) = :key", {"key": key}
    return "lower(split_part(m.from_email, '@', 2)) = :key", {"key": key}


async def _protected(db: AsyncSession, user: User, key: str) -> bool:
    """Never learn the user's own domains, or a domain where the user writes to someone."""
    from mailr.core.ingest import my_addresses

    mine = await my_addresses(db, user)
    if "@" in key:
        return key in mine
    if any(a.rsplit("@", 1)[-1] == key for a in mine):
        return True
    return bool((await db.execute(text(
        "SELECT 1 FROM people WHERE user_id = :u AND sent_count > 0 AND lower(split_part(email, '@', 2)) = :key "
        "LIMIT 1"), {"u": user.id, "key": key})).first())


async def spam_marks(db: AsyncSession, user: User, key: str) -> int:
    """Conversations from `key` the user reported as spam within DOMAIN_WINDOW (after any
    removal of the key), whose messages are still in Spam or deleted: an Undo or "Not spam"
    takes the mark back."""
    since = datetime.now(UTC) - DOMAIN_WINDOW
    removed = ((user.settings or {}).get(REMOVED_KEY) or {}).get(key)
    if removed:
        try:
            since = max(since, datetime.fromisoformat(removed))
        except ValueError:
            pass
    cond, params = _key_sql(key)
    return int((await db.execute(text(
        "SELECT count(DISTINCT m.conversation_id) FROM ("
        "  SELECT DISTINCT ON (d.message_id) d.message_id, d.value FROM label_decisions d"
        "  WHERE d.user_id = :u AND d.kind = 'spam' AND d.source = 'user' AND d.created_at >= :since"
        "  ORDER BY d.message_id, d.id DESC) last "
        "JOIN messages m ON m.id = last.message_id "
        f"WHERE last.value AND {cond} AND (m.deleted OR EXISTS (SELECT 1 FROM labels l "
        "  WHERE l.id = ANY(m.label_ids) AND l.role = 'junk'))"),
        {"u": user.id, "since": since, **params})).scalar() or 0)


def learned(user: User | None) -> dict[str, dict[str, Any]]:
    return dict(((user.settings or {}) if user else {}).get(DOMAINS_KEY) or {})


async def _save(db: AsyncSession, user_id: int, *, add: dict[str, Any] | None = None, drop: list[str] | tuple = (),
                removed: dict[str, str] | None = None) -> None:
    """Set the entries in `add`, drop those in `drop` and note `removed` times, on the list as stored
    now (core/usersettings.py): the user's copy may be older (loaded at the start of a bulk job's
    chunk, or before the store's answer), and the other settings stay as they are."""
    from mailr.core import usersettings

    fresh = await usersettings.current(db, user_id)
    domains = {**(fresh.get(DOMAINS_KEY) or {}), **(add or {})}
    for k in drop:
        domains.pop(k, None)
    changes: dict[str, Any] = {
        DOMAINS_KEY: dict(sorted(domains.items(), key=lambda kv: kv[1].get("added", ""))[-MAX_DOMAINS:])}
    if removed:
        rem = {**(fresh.get(REMOVED_KEY) or {}), **removed}
        changes[REMOVED_KEY] = dict(sorted(rem.items(), key=lambda kv: kv[1])[-MAX_DOMAINS:])
    await usersettings.patch(db, user_id, changes)


async def learn_domains(db: AsyncSession, user_id: int, msgs: list[Message]) -> list[str]:
    """After a "Report spam": add each sender key with DOMAIN_MARKS marks to the user's list."""
    user = await db.get(User, user_id)
    keys = {k for k in (sender_key(m.from_email) for m in msgs if not m.sent_by_me) if k}
    if user is None or not keys:
        return []
    await db.flush()
    domains, added, changed = learned(user), [], {}
    for key in sorted(keys):
        n = await spam_marks(db, user, key)
        if n < DOMAIN_MARKS or await _protected(db, user, key):
            continue
        prev = domains.get(key) or {}
        changed[key] = {"added": prev.get("added") or datetime.now(UTC).isoformat(), "marks": n}
        if not prev:
            added.append(key)
    if changed:
        await _save(db, user_id, add=changed)
    return added


async def forget_domains(db: AsyncSession, user_id: int, msgs: list[Message], *, removed: bool) -> list[str]:
    """After "Not spam" (removed=True: only later marks count again) or an Undo of "Report spam"
    (removed=False: the key goes when its marks drop below DOMAIN_MARKS)."""
    user = await db.get(User, user_id)
    domains = learned(user)
    keys = {k for k in (sender_key(m.from_email) for m in msgs) if k and k in domains}
    if user is None or not keys:
        return []
    await db.flush()
    gone = []
    for key in sorted(keys):
        if removed or await spam_marks(db, user, key) < DOMAIN_MARKS:
            domains.pop(key, None)
            gone.append(key)
    if gone:
        now = datetime.now(UTC).isoformat()
        await _save(db, user_id, drop=gone, removed={k: now for k in gone} if removed else None)
    return gone


async def learned_reason(db: AsyncSession, user: User, email: str | None) -> str | None:
    """At ingest: why mail from this sender goes to Spam by the user's own marks, or None."""
    key = sender_key(email)
    entry = learned(user).get(key) if key else None
    if not entry:
        return None
    what = "this address" if "@" in key else key
    return f"you reported mail from {what} as spam"


@router.get("/spam/domains")
async def list_domains(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Sender domains and addresses whose mail goes to Spam because you reported it (Settings → Mail → Spam & Screener).

    At shared mail providers (gmail.com and the like) the entry is an address."""
    user = await db.get(User, p.user_id)
    items = [{"key": k, "added": v.get("added"), "marks": v.get("marks", 0)} for k, v in learned(user).items()]
    items.sort(key=lambda x: x["added"] or "", reverse=True)
    return {"items": items, "marks_needed": DOMAIN_MARKS, "window_days": DOMAIN_WINDOW.days}


@router.delete("/spam/domains/{key}")
async def remove_domain(key: str, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    """Stop sending a learned domain's (or address's) mail to Spam; only later marks count again."""
    if p.is_token:
        raise ApiError(403, "spam.user_only")
    key = key.strip().lower()
    user = await db.get(User, p.user_id)
    domains = learned(user)
    if key not in domains:
        raise ApiError(404, "spam.not_on_list")
    await _save(db, p.user_id, drop=[key], removed={key: datetime.now(UTC).isoformat()})
    await audit(db, p, "spam.domain_remove", key)
    await db.commit()
    return {"ok": True, "key": key}
