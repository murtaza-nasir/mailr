"""Language identification at ingest (fastText lid.176, core/langid.py) and the "Translate"
offer: a language the model is sure of that is not one of the user's own."""

from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from mailr.core import langid
from mailr.core.mime import parse


@pytest.fixture(autouse=True, scope="module")
def _model():
    """The image ships the model; a development checkout fetches it once (checksum pinned)."""
    if not langid.MODEL_PATH.is_file():
        langid.fetch_model()
    assert langid.available()


def _raw(body: str, html: str | None = None, frm: str = "Sender <sender@example.org>", to: str = "me@example.com",
         subject: str = "Hello") -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = frm, to, subject
    msg["Date"] = format_datetime(datetime.now(UTC))
    msg.set_content(body)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


PRD = ("Product Requirements Document (PRD) Feature Set: TV-Shared Board & Mobile Controller Architecture "
       "(Terraforming Mars Digital) Status: Draft Target Platform: Smart TV (Host) + iOS/Android (Client)")
TERSE = "Q3 numbers attached. Pls review before EOD and flag variances >5%.\n\n-- \nDana Lee | Finance | +1 555 0100"
FOREIGN = {
    "es": "Hola Rowan, te escribo para confirmar la reunión del jueves a las tres de la tarde en la oficina. "
          "Avísame si necesitas cambiar la hora.",
    "vi": "Chào anh, em gửi anh bản báo cáo tháng này. Anh xem giúp em và phản hồi trước thứ Sáu nhé. Cảm ơn anh.",
    "ar": "مرحبا، أرسل لك التقرير الشهري مع الملاحظات. يرجى مراجعته والرد قبل يوم الجمعة. شكرا جزيلا.",
    "ur": "السلام علیکم، میں آپ کو اس مہینے کی رپورٹ بھیج رہا ہوں۔ براہ کرم جمعہ سے پہلے جواب دیں۔ شکریہ۔",
    "ja": "お世話になっております。今月の報告書をお送りします。金曜日までにご確認いただけますと幸いです。",
}


def test_english_headings_and_jargon_offer_nothing():
    p = parse(_raw(PRD))
    assert p.lang == "en" and p.lang_confidence < langid.OFFER_CONFIDENCE
    assert not langid.offer_translate(p.lang, p.lang_confidence)
    # The same heading as an HTML spec document.
    html = "<h1>Product Requirements Document (PRD)</h1><p>" + PRD.split("(PRD) ", 1)[1] + "</p>"
    p = parse(_raw(PRD, html))
    assert not langid.offer_translate(p.lang, p.lang_confidence)


def test_terse_english_business_mail_offers_nothing():
    p = parse(_raw(TERSE))
    assert p.lang == "en"
    assert not langid.offer_translate(p.lang, p.lang_confidence)


@pytest.mark.parametrize("code", sorted(FOREIGN))
def test_other_languages_offer_translation(code):
    p = parse(_raw(FOREIGN[code]))
    assert p.lang == code, (p.lang, p.lang_confidence)
    assert p.lang_confidence >= langid.OFFER_CONFIDENCE
    assert langid.offer_translate(p.lang, p.lang_confidence)
    assert langid.offer_translate(p.lang, p.lang_confidence, frozenset({"en"}))
    # Not when it is one of the user's own languages.
    own = langid.own_languages_from({"es": "English, Spanish", "vi": "Vietnamese", "ar": "Arabic (fluent)",
                                     "ur": "English, Urdu", "ja": "日本語"}[code])
    assert not langid.offer_translate(p.lang, p.lang_confidence, own)


def test_quoted_history_and_signature_do_not_count():
    # An English reply quoting Spanish mail is English.
    body = ("Thanks, that works for me. I will bring the signed contract and the budget sheet on Thursday.\n\n"
            "On Mon, Sep 28, 2026 at 9:00 AM Ana <ana@example.org> wrote:\n> " + FOREIGN["es"])
    p = parse(_raw(body))
    assert p.lang == "en"
    assert not langid.offer_translate(p.lang, p.lang_confidence)


def test_short_body_stores_null():
    for body in ("Thanks!", "See attached.", "Gracias, nos vemos.", "了解です。"):
        p = parse(_raw(body))
        assert (p.lang, p.lang_confidence) == (None, 0.0), body
        assert not langid.offer_translate(p.lang, p.lang_confidence)


def test_language_text_is_one_clean_line():
    t = langid.language_text("Hello there https://x.example/a?b=c and mail me at a@b.example\n123 456\nbye\n-- \nSig line")
    assert "\n" not in t and "http" not in t and "@" not in t and "123" not in t and "Sig" not in t
    assert t == "Hello there and mail me at bye"


def test_users_languages_mapping():
    f = langid.parse_languages
    assert f("English, Urdu") == {"en", "ur"}
    assert f("English (native); some Arabic / basic French") == {"en", "ar", "fr"}
    assert f("Spanish and Portuguese") == {"es", "pt"}
    assert f("en, ur") == {"en", "ur"}
    assert f("Español, Deutsch, 中文, اردو") == {"es", "de", "zh", "ur"}
    assert f("Mandarin Chinese, Cantonese") == {"zh"}
    assert f("Norwegian") == {"no", "nn"}
    assert f("Tiếng Việt") == {"vi"}
    assert f("Klingon") == frozenset() and f("") == frozenset() and f(None) == frozenset()
    # Nothing recognised: English, the UI language.
    assert langid.own_languages_from(None) == {"en"}
    assert langid.own_languages_from("Klingon") == {"en"}
    assert langid.own_languages_from("Urdu") == {"ur"}
    # Regional labels of the model fold into their ISO 639-1 language.
    assert langid.normalize("__label__arz") == "ar" and langid.normalize("pnb") == "pa"
    assert not langid.offer_translate("arz", 0.95, frozenset({"ar"}))


async def _ingest(user, data):
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), data, IngestOptions())
        await db.commit()
        return res.message.id, res.message.conversation_id


async def test_ingest_stores_language_and_the_reader_gets_the_offer(client, user):
    from mailr.core import publicid
    from mailr.db import sessionmaker
    from mailr.models import AssistantProfile, Message

    es_id, es_cid = await _ingest(user, _raw(FOREIGN["es"], to=user.email, subject="Reunión"))
    en_id, en_cid = await _ingest(user, _raw(PRD, to=user.email, subject="PRD"))
    short_id, _ = await _ingest(user, _raw("Thanks!", to=user.email, subject="Short"))
    async with sessionmaker()() as db:
        es, en, short = (await db.get(Message, es_id), await db.get(Message, en_id), await db.get(Message, short_id))
        assert es.lang == "es" and es.lang_confidence >= 0.9
        assert en.lang == "en"
        assert (short.lang, short.lang_confidence) == (None, 0.0)

    def cid(c):
        return publicid.encode("conversation", c) if publicid.enabled() else c

    def mid(m):
        return publicid.encode("message", m) if publicid.enabled() else m

    r = await client.get(f"/api/v1/conversations/{cid(es_cid)}")
    msg = r.json()["messages"][0]
    assert (msg["lang"], msg["offer_translate"]) == ("es", True)
    r = await client.get(f"/api/v1/conversations/{cid(en_cid)}")
    assert r.json()["messages"][0]["offer_translate"] is False
    # Spanish is one of the user's languages: no offer.
    async with sessionmaker()() as db:
        db.add(AssistantProfile(user_id=user.id, data={"languages": "English, Spanish"}, options={}, state={}))
        await db.commit()
    r = await client.get(f"/api/v1/messages/{mid(es_id)}")
    assert (r.json()["lang"], r.json()["offer_translate"]) == ("es", False)


async def test_backfill_fills_recent_mail_once(user):
    from sqlalchemy import update

    from mailr.core import reparse
    from mailr.db import sessionmaker
    from mailr.models import Message

    vi_id, _ = await _ingest(user, _raw(FOREIGN["vi"], to=user.email, subject="Báo cáo"))
    short_id, _ = await _ingest(user, _raw("Ok, thanks.", to=user.email, subject="Ok"))
    async with sessionmaker()() as db:  # as stored before language identification
        await db.execute(update(Message).where(Message.user_id == user.id).values(lang=None, lang_confidence=None))
        await db.commit()
    st = await reparse.langid_backfill(user.id, days=2, batch=1)
    assert (st["seen"], st["detected"], st["short"], st["languages"]) == (2, 1, 1, {"vi": 1})
    async with sessionmaker()() as db:
        vi, short = await db.get(Message, vi_id), await db.get(Message, short_id)
        assert vi.lang == "vi" and vi.lang_confidence >= 0.9
        assert (short.lang, short.lang_confidence) == (None, 0.0)
    # Idempotent: a second run finds nothing left to do.
    assert (await reparse.langid_backfill(user.id, days=2))["seen"] == 0
    assert (await reparse.langid_backfill(user.id, days=2, redo=True))["detected"] == 1
