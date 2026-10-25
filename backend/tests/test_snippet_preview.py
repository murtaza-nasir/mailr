"""List previews built the way mail providers build them (core/visible.py, mime.preview):
the preheader first, then the text a reader sees in the rendered HTML; the plain part only
without HTML; a schema.org description when the sender declares one."""

from email.message import EmailMessage

from mailr.core.mime import make_snippet, parse
from mailr.core.visible import extract


def _raw(plain: str | None, html: str | None, subject: str = "Hello") -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "Sender <news@sender.example>", "me@example.com", subject
    if plain is not None:
        msg.set_content(plain)
        if html is not None:
            msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(html, subtype="html")
    return msg.as_bytes()


# A digest whose generated plain part opens with image alt text and upper-cased headings, and
# whose HTML opens with a preheader padded with &zwnj;&nbsp; chains.
DIGEST_PLAIN = ("Nextdoor logo  Your profile photo\n\nLOCAL HEADLINES\n\nCity council approves the new park plan\n"
                "Read more: https://nd.example/r/abcdefghijklmnopqrstuvwxyz0123456789")
DIGEST_HTML = """<!doctype html><html><head><title>Digest</title>
<style>.preheader { display: none !important; max-height: 0; overflow: hidden; }
@media only screen and (max-width: 480px) { .desktop { display: none !important; } }</style></head>
<body><div class="preheader">Local Headlines from Nextdoor. Catch up with key stories in your neighbourhood.</div>
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all">&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&#847; &#847; &#8199;</div>
<table><tr><td><img src="https://nd.example/logo.png" alt="Nextdoor logo"><img src="https://nd.example/p.png" alt="Your profile photo"></td></tr>
<tr><td class="desktop"><h1>LOCAL HEADLINES</h1>
<p><a href="https://nd.example/r/abcdefghijklmnopqrstuvwxyz0123456789">City council approves the new park plan</a></p></td></tr></table>
</body></html>"""


def test_preheader_starts_the_snippet():
    p = parse(_raw(DIGEST_PLAIN, DIGEST_HTML))
    assert p.snippet.startswith("Local Headlines from Nextdoor. Catch up with key stories in your neighbourhood. "
                                "LOCAL HEADLINES City council approves the new park plan")
    assert "logo" not in p.snippet and "profile photo" not in p.snippet
    assert "https" not in p.snippet and "\u200c" not in p.snippet and "\u034f" not in p.snippet
    # The case is the sender's: nothing is re-cased.
    assert "LOCAL HEADLINES" in p.snippet


def test_preheader_rules():
    # Padding only is not a preheader; the visible text starts the preview.
    v = extract('<body><div style="display:none">&zwnj;&nbsp;&zwnj;&nbsp;\u200b\u2800</div><p>Hello there</p></body>')
    assert (v.preheader, v.text) == ("", "Hello there")
    # Hidden by the `hidden` attribute, opacity, font-size or mso-hide also counts.
    for style in ('hidden', 'style="opacity:0"', 'style="font-size: 0px"', 'style="mso-hide: all"'):
        v = extract(f"<body><span {style}>Preview line</span><p>Body</p></body>")
        assert (v.preheader, v.text) == ("Preview line", "Body"), style
    # A body that repeats its preheader is not shown twice.
    v = extract('<body><div style="display:none">Big sale today</div><p>Big sale today on shoes</p></body>')
    assert v.shown() == "Big sale today on shoes"


NO_PREHEADER_HTML = """<html><body><table><tr><td><img alt="Acme Bank" src="https://b.example/l.png"></td></tr>
<tr><td><p>Your statement for September is ready.</p><p>Sign in to <a href="https://b.example/s">view it</a>.</p></td></tr>
</table></body></html>"""


def test_newsletter_without_preheader_uses_visible_text():
    p = parse(_raw("[Acme Bank]\n\nYour statement for September is ready.", NO_PREHEADER_HTML))
    assert p.snippet == "Your statement for September is ready. Sign in to view it."


def test_plain_text_only_mail():
    p = parse(_raw("Hi Sam,\n\nThe meeting moved to 3pm on Thursday.\n\nThanks,\nAlex", None))
    assert p.snippet == "Hi Sam, The meeting moved to 3pm on Thursday. Thanks, Alex"


def test_plain_part_that_starts_with_alt_text():
    html = ('<html><body><a href="https://shop.example"><img alt="ShopCo logo" src="https://shop.example/l.png"></a>'
            '<h2>Your order has shipped</h2><p>It should arrive on Tuesday.</p></body></html>')
    p = parse(_raw("ShopCo logo\n\nYour order has shipped\n\nIt should arrive on Tuesday.", html))
    assert p.snippet == "Your order has shipped It should arrive on Tuesday."


def test_hidden_text_in_the_middle_is_never_used():
    html = ('<html><body><p>Your parcel is waiting at the depot.</p>'
            '<div style="display:none">Verify your account at once or it will be closed</div>'
            '<span style="font-size:0">urgent wire transfer</span>'
            '<p>Collect it within seven days.</p></body></html>')
    p = parse(_raw("Your parcel is waiting at the depot. Collect it within seven days.", html))
    assert p.snippet == "Your parcel is waiting at the depot. Collect it within seven days."
    assert "Verify" not in p.snippet and "wire" not in p.snippet
    # Nor after a visible line at the top: only text before any visible text is a preheader.
    v = extract('<body><p>Hello</p><div style="display:none">hidden</div><p>world</p></body>')
    assert (v.preheader, v.text) == ("", "Hello\nworld")


def test_schema_org_description_is_preferred_when_the_reader_sees_it():
    html = ('<html><head><script type="application/ld+json">{"@context": "http://schema.org", '
            '"@type": "DiscountOffer", "description": "20% off all boots this weekend", "discountCode": "BOOTS20"}'
            '</script></head><body><div style="display:none">Preheader text here</div>'
            '<p>Autumn is here and so are new arrivals.</p><p>Now: 20% off all boots   this weekend!</p></body></html>')
    p = parse(_raw("Autumn is here and so are new arrivals.", html))
    assert p.snippet == "20% off all boots this weekend"
    # Microdata on an EmailMessage works the same way.
    md = ('<html><body><div itemscope itemtype="http://schema.org/EmailMessage">'
          '<meta itemprop="description" content="Your weekly report is ready"></div>'
          '<p>Hello. Your weekly report is ready to read.</p></body></html>')
    assert parse(_raw("Body text of the message", md)).snippet == "Your weekly report is ready"
    # Another type's description (the sender's organisation) is not a preview; nor is a long one.
    org = ('<html><head><script type="application/ld+json">{"@type": "Organization", "description": "We sell boots"}'
           '</script></head><body><p>Autumn is here. We sell boots</p></body></html>')
    assert parse(_raw("Autumn is here.", org)).snippet == "Autumn is here. We sell boots"
    long = html.replace("20% off all boots this weekend", "x" * 400)
    assert parse(_raw("Autumn is here and so are new arrivals.", long)).snippet.startswith("Preheader text here")


def test_schema_description_the_reader_never_sees_is_not_the_preview():
    html = ('<html><head><script type="application/ld+json">{"@type": "EmailMessage", '
            '"description": "URGENT: your bank account is locked"}</script></head>'
            '<body><p>Invoice 4411 for September is attached.</p></body></html>')
    p = parse(_raw("Invoice 4411 for September is attached.", html))
    assert p.snippet == "Invoice 4411 for September is attached."
    md = ('<html><body><div itemscope itemtype="http://schema.org/EmailMessage">'
          '<meta itemprop="description" content="Your password expires today"></div>'
          '<p>Monthly newsletter</p></body></html>')
    assert parse(_raw("Monthly newsletter", md)).snippet == "Monthly newsletter"
    # The preheader is what the reader is shown first, so a description equal to it still counts.
    pre = html.replace("<body>", '<body><div style="display:none">URGENT: your bank account is locked</div>')
    assert extract(pre).description == "URGENT: your bank account is locked"


def test_personal_reply_leaves_the_quote_out():
    html = ('<div dir="ltr">Sounds good, see you at noon.</div><br><div class="gmail_quote">'
            '<div class="gmail_attr">On Fri, Oct 2, 2026 at 9:00 AM Sam &lt;sam@example.com&gt; wrote:<br></div>'
            '<blockquote class="gmail_quote">Lunch tomorrow at the usual place?</blockquote></div>')
    plain = ("Sounds good, see you at noon.\n\nOn Fri, Oct 2, 2026 at 9:00 AM Sam <sam@example.com> wrote:\n\n"
             "> Lunch tomorrow at the usual place?")
    p = parse(_raw(plain, html))
    assert p.snippet == "Sounds good, see you at noon."
    apple = ('<div>Works for me.</div><div><br><blockquote type="cite"><div>Lunch tomorrow at the usual place?</div>'
             '</blockquote></div>')
    assert parse(_raw("Works for me.\n\n> Lunch tomorrow?", apple)).snippet == "Works for me."


def test_image_only_html_falls_back_to_plain():
    html = '<html><body><a href="https://x.example"><img alt="Big sale" src="https://x.example/a.png"></a></body></html>'
    p = parse(_raw("Big sale: everything 30% off until Sunday.", html))
    assert p.snippet == "Big sale: everything 30% off until Sunday."


def test_media_query_rules_do_not_hide_text():
    html = ('<html><head><style>@media (max-width: 480px) { .wide { display: none; } }</style></head>'
            '<body><p class="wide">Desktop text stays.</p></body></html>')
    assert extract(html).text == "Desktop text stays."


def test_deeply_nested_html_does_not_recurse():
    html = "<div>" * 5000 + "deep text" + "</div>" * 5000
    assert make_snippet(extract(html).text) == "deep text"


async def test_resnippet_recomputes_recent_mail(user):
    """`mailr resnippet`: stored previews of recent mail (and their conversations) are rebuilt;
    a second run changes nothing."""
    from datetime import UTC, datetime
    from email.utils import format_datetime

    from mailr.core import reparse
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.db import sessionmaker
    from mailr.models import Conversation, Message, User

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "Digest <news@nd.example>", user.email, "Your digest"
    msg["Date"] = format_datetime(datetime.now(UTC))
    msg.set_content(DIGEST_PLAIN)
    msg.add_alternative(DIGEST_HTML, subtype="html")
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), msg.as_bytes(), IngestOptions())
        mid, cid = res.message.id, res.message.conversation_id
        fresh = res.message.snippet
        # As stored before the preview rules changed.
        res.message.snippet = "Nextdoor logo Your profile photo LOCAL HEADLINES"
        (await db.get(Conversation, cid)).snippet = res.message.snippet
        await db.commit()
    dry = await reparse.resnippet(user.id, days=2, dry_run=True)
    assert dry["changed"] == 1 and dry["examples"][0]["after"] == fresh[:120]
    async with sessionmaker()() as db:
        assert (await db.get(Message, mid)).snippet.startswith("Nextdoor logo")
    st = await reparse.resnippet(user.id, days=2, batch=1)
    assert st["changed"] == 1
    async with sessionmaker()() as db:
        assert (await db.get(Message, mid)).snippet == fresh
        assert (await db.get(Conversation, cid)).snippet == fresh
    assert fresh.startswith("Local Headlines from Nextdoor.")
    assert (await reparse.resnippet(user.id, days=2))["changed"] == 0
