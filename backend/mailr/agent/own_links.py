"""Links the user put in their own mail, which `read_page` may open.

The built-in assistant never fetches links found in mail: a link chosen by a
sender is the sender's instruction. A link in a message that is provably the user's own was
chosen by the user, so it may be opened like a link typed in the chat. "Provably" rests on
mailr's verified signals, never on the From header:

- Message.sent_by_me, which ingest sets only when mailr sent the message, when a copy is in the
  user's own Sent folder (their mail account's, their import's, or the mail store's), or when the
  message is From one of the user's addresses AND the receiving server's trusted
  Authentication-Results pass for that domain (core/ingest.is_sent_by_me). Mail with the user's
  address in From that failed authentication and has no Sent copy is inbound mail.
- And either own_words (origin is null: text the user wrote) or origin "android-share" (the
  phone's "Send to self" from the user's own signed-in device: the user chose to share the link;
  the page's text is still untrusted).

Never drafts (the assistant writes drafts too), never a message of a local-only account (its link
is local-only content), and only the user's own part of the message: text parts outside any
attached or forwarded message, with quoted history (">" lines, "On … wrote:", Outlook's
"From:" block, Gmail and Thunderbird quote markup) and everything after a forward marker removed.

Which messages. The conversation the chat is about (the session's conversation) and the
conversations whose text a tool showed the assistant in this turn (get_conversation,
get_message_body), so an errand on the user's note to self can open its link once it has read it.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import re
from collections.abc import Iterable

from sqlalchemy import or_, select

from mailr.models import Message

SHARED_ORIGINS = ("android-share",)
MAX_MESSAGES = 30
# Where the forwarded or quoted part of a message starts, wherever it is in the text.
_CUT = re.compile(
    r"^\s*(?:-{2,}\s*(?:Forwarded message|Original Message|Forwarded Message|Weitergeleitete Nachricht|"
    r"Message transféré|Mensaje reenviado)\s*-{2,}|Begin forwarded message:|"
    r"On .{5,200}wrote:|Le .{5,200}a écrit\s?:|Am .{5,200}schrieb .{0,100}:|From: .+|_{10,})\s*$",
    re.MULTILINE | re.IGNORECASE)
# A real forward: the marker line mail programs write above
# a forwarded message, in English, German, French, Spanish, Portuguese, Russian and Chinese (Gmail,
# Outlook, Apple Mail, Thunderbird; Outlook's "Original Appointment" for meetings).
_FWD_LINE = re.compile(
    r"^\s*(?:-{2,}\s*(?:Forwarded message|Forwarded Message|Original Message|Original Appointment|"
    r"Weitergeleitete Nachricht|Ursprüngliche Nachricht|Ursprünglicher Termin|"
    r"Message transféré|Message d'origine|Message original|Rendez-vous d'origine|"
    r"Mensaje reenviado|Mensaje original|Cita original|"
    r"Mensagem encaminhada|Mensagem reencaminhada|Mensagem original|Compromisso original|"
    r"Пересылаемое сообщение|Пересланное сообщение|Исходное сообщение|Исходная встреча|"
    r"转发的邮件|原始邮件|原始约会|轉寄的郵件|原始郵件)\s*-{2,}"
    r"|Begin forwarded message\s*:|Anfang der weitergeleiteten Nachricht\s*:|"
    r"Début du message (?:réexpédié|transféré)\s*:|Inicio del mensaje reenviado\s*:|"
    r"Início da mensagem (?:reencaminhada|encaminhada)\s*:|"
    r"Начало (?:переадресованного|пересылаемого) сообщения\s*:|(?:开始)?转发的邮件\s*[:：])\s*$",
    re.MULTILINE | re.IGNORECASE)
# Outlook's header block above a forwarded message ("From: … Sent: … To: … Subject: …"), also
# without its underscore line and with French spacing ("De : …"): a sender line followed within a
# few lines by another header line, in any of those languages.
_SP = r"[ \t\u00a0\u202f]*"
_FROM_KEY = re.compile(rf"^\s*\*?(?:From|Von|De|Da|Van|От|发件人|寄件者|Från|Fra|Od){_SP}\*?{_SP}[:：]{_SP}\S",
                       re.IGNORECASE)
_HDR_KEY = re.compile(
    rf"^\s*\*?(?:Sent|Date|To|Subject|Cc|Gesendet|Datum|An|Betreff|Envoyé|À|Objet|Enviado|Enviada em|Fecha|"
    rf"Para|Asunto|Assunto|Data|Отправлено|Дата|Кому|Тема|Копия|发送时间|日期|收件人|主题|抄送|寄件日期|主旨)"
    rf"{_SP}\*?{_SP}[:：]", re.IGNORECASE)


def _header_block(text: str) -> int | None:
    """Where an Outlook-style header block starts (its sender line), or None."""
    lines = text.splitlines(keepends=True)
    pos = 0
    for i, ln in enumerate(lines):
        if _FROM_KEY.match(ln) and any(_HDR_KEY.match(x) for x in lines[i + 1:i + 5] if x.strip()):
            return pos
        pos += len(ln)
    return None


# Any forward marker by its shape, in any language: a title between dashes
# ("---------- Messaggio inoltrato ----------", "-----転送メッセージ-----"), or a header block of
# consecutive `Key: value` lines (the From/Sent/To/Subject block in a language the lists above miss). Used
# only to end the note once a real forward is known to be there, never to decide that there is one.
_ANY_DASH_LINE = re.compile(r"^\s*-{2,}\s*[^\W\d_][^\n]{0,60}?\s*-{2,}\s*$", re.MULTILINE)
_ANY_KEY = re.compile(rf"^\s*\*?[^\W\d_][^\W_]*(?:[ .'-][^\W_]+){{0,3}}{_SP}\*?{_SP}[:：](?!//){_SP}\S")


def _any_header_block(text: str) -> int | None:
    """Where a header block starts: three or more consecutive `Key: value` lines with distinct keys,
    or None."""
    lines = text.splitlines(keepends=True)
    pos = 0
    run_start, keys = 0, set()
    for ln in lines:
        if _ANY_KEY.match(ln):
            if not keys:
                run_start = pos
            keys.add(re.split(r"[:：]", ln, maxsplit=1)[0].strip(" \t*\u00a0\u202f").lower())
            if len(keys) >= 3:
                return run_start
        else:
            keys = set()
        pos += len(ln)
    return None


def _first_marker(text: str) -> int | None:
    """Where the first forward or quote marker of any kind starts, or None."""
    found = [_forward_at(text), _any_header_block(text)]
    for rx in (_CUT, _ANY_DASH_LINE):
        m = rx.search(text)
        found.append(m.start() if m else None)
    found = [x for x in found if x is not None]
    return min(found) if found else None


def _forward_at(text: str) -> int | None:
    """Where a real forward starts in the text (a marker line or a header block), or None."""
    m = _FWD_LINE.search(text)
    found = [x for x in (m.start() if m else None, _header_block(text)) if x is not None]
    return min(found) if found else None


_QUOTE_CLASSES = re.compile(r"gmail_quote|gmail_extra|moz-cite-prefix|moz-forward-container|yahoo_quoted|"
                            r"protonmail_quote|OutlookMessageHeader")
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")


def _html_own(html: str) -> str:
    from bs4 import BeautifulSoup

    from mailr.core.mime import html_to_text

    soup = BeautifulSoup(html, "lxml")
    for el in soup.find_all("blockquote"):
        el.decompose()
    for el in soup.find_all(attrs={"class": _QUOTE_CLASSES}):
        el.decompose()
    for el in soup.find_all(id=re.compile(r"divRplyFwdMsg|appendonsend|mail-editor-reference-message-container")):
        # Outlook: the original message follows its reply header as siblings.
        for sib in list(el.find_next_siblings()):
            sib.decompose()
        el.decompose()
    return html_to_text(str(soup))


def own_text(raw: bytes) -> str:
    """The text the sender of `raw` wrote: its own text parts (never a part inside an attached or
    forwarded message, never an attachment), with quoted and forwarded history removed."""
    return own_note(raw)[0]


def own_note(raw: bytes) -> tuple[str, bool]:
    """(own_text, forwarded): forwarded is True only when the message carries a forwarded message
    below the sender's own part: an attached message (message/rfc822 and the like), a forward marker
    line in one of the supported languages, or an Outlook-style header block (_FWD_LINE,
    _header_block). A reply's quote is not a forward. The note on top of a forward is the own text
    (core/trusted.py)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    attached = False
    plain: list[str] = []
    html: list[str] = []

    def walk(part) -> None:
        nonlocal attached
        if part.get_content_maintype() == "message":
            attached = True
            return  # an attached or forwarded message: someone else's words
        if part.is_multipart():
            for sub in part.iter_parts():
                walk(sub)
            return
        if part.get_content_disposition() == "attachment" or part.get_filename():
            return
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            return
        try:
            content = part.get_content()
        except Exception:  # noqa: BLE001
            content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if isinstance(content, str):
            (plain if ctype == "text/plain" else html).append(content)

    walk(msg)
    from mailr.core.mime import html_to_text

    if any(t.strip() for t in plain):
        text = full = "\n".join(plain)
    elif html:
        joined = "\n".join(html)
        text = _html_own(joined)
        full = html_to_text(joined)
    else:
        text = full = ""
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(">"))
    # A forward only when there is one: an attached message, or a forward marker or header block in the
    # message's text outside ">" quotes. A reply's quote ("On … wrote:") is not a forward.
    full = "\n".join(ln for ln in full.splitlines() if not ln.lstrip().startswith(">"))
    forwarded = bool(attached or _forward_at(full) is not None)
    # Once there is a forward, the note is only the text above the first marker of any kind, known
    # or by shape: never text from between two markers.
    m = _CUT.search(text)
    cuts = [x for x in (m.start() if m else None, _forward_at(text)) if x is not None]
    if forwarded:
        first = _first_marker(text)
        cuts += [first] if first is not None else []
    own = text[: min(cuts)] if cuts else text
    return own.strip(), forwarded


def links_in(text: str) -> list[str]:
    out: list[str] = []
    for u in _URL.findall(text or ""):
        u = u.rstrip(".,;:!?")
        if u not in out:
            out.append(u)
    return out


def own_message(m: Message) -> bool:
    """The verified test: sent by the user (never a draft) and their own words or their own share."""
    return (bool(m.sent_by_me) and not m.draft and not m.deleted and not m.local_only
            and not (m.auth_results or {}).get("from_problem")  # a malformed From is nobody's
            and (m.origin is None or m.origin in SHARED_ORIGINS))


async def find(db, user, conversation_ids: Iterable[int], url: str) -> tuple[str, int] | None:
    """(the link as the user wrote it, the message id) when `url` is the same page as a link in the
    user's own part of one of their own messages in these conversations; None otherwise."""
    from mailr.core.store import user_store
    from mailr.websearch.adapters import site_of
    from mailr.websearch.pages import same_page

    ids = sorted({int(c) for c in conversation_ids if c})
    site = site_of(url)
    if not ids or not site:
        return None
    rows = (await db.execute(
        select(Message).where(Message.user_id == user.id, Message.conversation_id.in_(ids),
                              Message.sent_by_me.is_(True), Message.draft.is_(False), Message.deleted.is_(False),
                              Message.local_only.is_(False), Message.blob_id.is_not(None),
                              or_(Message.origin.is_(None), Message.origin.in_(SHARED_ORIGINS)))
        .order_by(Message.date.desc().nulls_last(), Message.id.desc()).limit(MAX_MESSAGES))).scalars().all()
    store = None
    for m in rows:
        if not own_message(m) or site not in (m.body_text or "").lower() + " " + (m.snippet or "").lower():
            continue  # the body cannot hold the link (body_text holds more than the user's part, never less)
        store = store or await user_store(db, user)
        try:
            raw = await store.get_raw(m)
        except Exception:  # noqa: BLE001  the store is down or the message is gone: not provable now
            continue
        for link in links_in(await asyncio.to_thread(own_text, raw)):
            if same_page(link, url):
                return link, m.id
    return None
