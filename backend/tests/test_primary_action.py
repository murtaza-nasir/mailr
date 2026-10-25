"""mail.primary_action: the main action of an email, found at triage."""

from mailr.intel.actions import candidates, key_fact

AMAZON = """<html><body><a href="https://www.amazon.com/gp/css/your-orders">Your Orders</a>
<a href="https://www.amazon.com/gp/css/account">Your Account</a>
<h2>Rowan, your package is out for delivery!</h2><p>Arriving today 1:30 PM - 4:30 PM</p>
<table><tr><td bgcolor="#FFD814"><a href="https://www.amazon.com/progress-tracker/package?orderId=114" style="padding:8px">Track package</a></td></tr></table>
<a href="https://www.amazon.com/gp/help/customer/display.html">please add delivery instructions</a></body></html>"""

BILL = """<a href="https://connect.secure.wellsfargo.com/auth/login">Sign On</a>
<a href="https://wf.com/pay">Make a Payment</a> <a href="https://wf.com/statement">View statement</a>"""

PHISH = """<a href="https://wells-fargo-security.example/verify">Verify your account</a>
<a href="https://wells-fargo-security.example/pay">Update payment details</a>"""

MARKUP = """<script type="application/ld+json">{"@context": "http://schema.org", "@type": "ParcelDelivery",
"potentialAction": {"@type": "TrackAction", "name": "Track shipment", "target": "https://ups.example/track/1Z"}}</script>
<a href="https://ups.example/home">Home</a>"""


def test_link_candidates_follow_the_allowlist_and_never_offer_sign_in_links():
    markup, c = candidates(AMAZON)
    assert markup is None and [(x["kind"], x["label"]) for x in c] == [("track", "Track package")]
    assert c[0]["url"].startswith("https://www.amazon.com/progress-tracker")
    _, c = candidates(BILL)
    assert [x["kind"] for x in c] == ["pay", "statement"]  # "Sign On" is not an action chip
    assert candidates(PHISH) == (None, [])  # verify / update payment: never
    markup, _ = candidates(MARKUP)
    assert markup == {"kind": "track", "label": "Track shipment", "url": "https://ups.example/track/1Z", "source": "markup"}


def test_key_facts():
    assert key_fact("Rowan, your package is out for delivery!\nArriving today 1:30 PM - 4:30 PM\n1 item") \
        == "Arriving today 1:30 PM - 4:30 PM"
    assert key_fact("Minimum payment due: $35.00 by the due date") == "Minimum payment due: $35.00"
    assert key_fact("Your credit card payment is due on Oct 4, 2026.") == "due on Oct 4, 2026"
    assert key_fact("Your Wells Fargo credit card payment is due on 10/04/2026") == "due on 10/04/2026"
    assert key_fact("Payment due date: 10/04/2026") == "Payment due date: 10/04/2026"
    assert key_fact("Thanks for being a member") is None


async def test_triage_puts_the_action_on_the_message_and_its_conversation(client, user, raw):
    """mail.primary_action end to end: an HTML delivery notice gets a Track chip; a promotion gets none."""
    from helpers.core_flow import _ingest

    from mailr.db import sessionmaker
    from mailr.intel.tasks import process_message
    from mailr.models import Conversation, Message, User

    def html_mail(subject, frm, html, text):
        return (f"From: {frm}\r\nTo: {user.email}\r\nSubject: {subject}\r\nMessage-ID: <{abs(hash(subject))}@x.org>\r\n"
                "Date: Wed, 30 Sep 2026 16:22:00 +0000\r\nMIME-Version: 1.0\r\n"
                'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
                f"--b\r\nContent-Type: text/plain\r\n\r\n{text}\r\n--b\r\nContent-Type: text/html\r\n\r\n{html}\r\n--b--\r\n").encode()

    mid, cid = await _ingest(user, html_mail("Out for delivery", "Amazon.com <shipment-tracking@amazon.com>", AMAZON,
                                             "Arriving today 1:30 PM - 4:30 PM"), roles=["inbox"], source="connector")
    async with sessionmaker()() as db:
        m, u = await db.get(Message, mid), await db.get(User, user.id)
        m.category = "purchases"
        await process_message(db, m, u, allow_llm=False)
        from mailr.core import threading

        await threading.refresh(db, u.id, [cid])
        await db.commit()
    async with sessionmaker()() as db:
        pa = (await db.get(Conversation, cid)).primary_action
    assert pa["kind"] == "track" and pa["fact"] == "Arriving today 1:30 PM - 4:30 PM", pa
    row = next(c for c in (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"] if c["id"] == cid)
    assert row["primary_action"]["label"] == "Track package"


def test_verification_codes():
    from mailr.intel.actions import verification_code as v

    assert v("AAdvantage® login verification",
             "Verify your AAdvantage® login Your verification code is: 480204 Enter this code to sec") == "480204"
    assert v("Your Google code", "G-123456 is your Google verification code.") == "123456"
    assert v("Sign in", "123456 is your Microsoft account code") == "123456"
    assert v("Security code", "Your security code: K7Q-4PZ") == "K7Q-4PZ"
    assert v("Your one-time passcode", "Passcode 8841 expires in 10 minutes") == "8841"
    assert v("Invoice", "Amount due $1,234.00 by 10/04/2026. Order 112-3341") is None
    assert v("Meeting", "Use PIN 2026 to join") is None
    assert v("Sale", "Use code SAVE20 at checkout") is None  # a promo code, not a sign-in
    # Behind kilobytes of tracking links and table markup (the American Airlines mail).
    junk = "|  |  |  [ ](https://url2268.connect.example/ls/click?upn=" + "x" * 4000 + ")\n| | |\n"
    assert v("AAdvantage® login verification", junk + "Your verification code is: 480204") == "480204"


async def test_a_code_email_gets_a_copy_chip(client, user, raw):
    from helpers.core_flow import _ingest

    from mailr.db import sessionmaker
    from mailr.intel.tasks import process_message
    from mailr.models import Conversation, Message, User

    mid, cid = await _ingest(user, raw("AAdvantage® login verification", frm="American Airlines <aa@info.aa.example>",
                                       body="Your verification code is: 480204\nEnter this code to securely sign in."),
                             roles=["inbox"], source="connector")
    async with sessionmaker()() as db:
        m, u = await db.get(Message, mid), await db.get(User, user.id)
        await process_message(db, m, u, allow_llm=False)
        from mailr.core import threading

        await threading.refresh(db, u.id, [cid])
        await db.commit()
    async with sessionmaker()() as db:
        pa = (await db.get(Conversation, cid)).primary_action
    assert pa == {"kind": "code", "label": "Copy code", "code": "480204", "source": "rules", "fact": None}
