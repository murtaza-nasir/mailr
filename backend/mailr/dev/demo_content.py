"""The fictional world of the demo instance (`mailr demo-seed`, mailr/dev/demo.py). Development only.

Alex Rivera is the design lead at Northwind Studio, a small branding and web studio. Alex reads
three accounts in mailr: work (alex@northwind.example), personal (alex.rivera@example.net) and
the Harbor Community Clinic board mailbox (alex@harborclinic.example), which is local-only
because it carries health information. Every person, company and product here is made up, and
every address is on a reserved domain (example.com/.net/.org, *.example, *.test).

Text placeholders, filled relative to the seed's "now" in the user's time zone:
  {D:n}  "Thursday, October 8"     {S:n}  "Oct 8"     {W:n}  "Thursday"
where n is a day offset (0 today, 2, -3) or a weekday name (the next one, at least a day ahead).
"""

from __future__ import annotations

TZ = "America/Chicago"
PASSWORD = "northwind-demo-2026"

ME = "Alex Rivera"
WORK = "alex@northwind.example"
PERSONAL = "alex.rivera@example.net"
CLINIC = "alex@harborclinic.example"

ACCOUNTS = {
    # key: (connector name, address, data class, upstream domain)
    "work": ("Northwind Studio", WORK, "normal", "northwind.example"),
    "personal": ("Personal", PERSONAL, "normal", "example.net"),
    "clinic": ("Clinic", CLINIC, "local_only", "harborclinic.example"),
}

SIGNATURES = {
    WORK: ("<div>Alex Rivera</div><div>Design Lead, Northwind Studio</div>"
           "<div>northwind.example &middot; +1 555 0134</div>"),
    PERSONAL: "<div>Alex</div>",
    CLINIC: ("<div>Alex Rivera</div><div>Board member (volunteer), Harbor Community Clinic</div>"
             "<div>Confidential: please do not forward outside the board.</div>"),
}

# People. (name, address, organization, title)
PRIYA = ("Priya Shah", "priya.shah@northwind.example", "Northwind Studio", "Studio Director")
JORDAN = ("Jordan Lee", "jordan.lee@northwind.example", "Northwind Studio", "Project Manager")
SAM = ("Sam Okafor", "sam.okafor@northwind.example", "Northwind Studio", "Front-end Developer")
MEI = ("Mei Tanaka", "mei.tanaka@northwind.example", "Northwind Studio", "Designer")
OPS = ("Northwind Ops", "ops@northwind.example", "Northwind Studio", "")
DANA = ("Dana Whitfield", "dana@lumencoffee.example", "Lumen Coffee Roasters", "Marketing Director")
MARCO = ("Marco Silva", "marco.silva@bluefin.example", "Bluefin Outdoor", "Head of Digital")
LUCIA = ("Lucía Romero", "lucia@casaverde.example", "Casa Verde Interiores", "Fundadora")
CASA_AP = ("Casa Verde Accounts", "cuentas@casaverde.example", "Casa Verde Interiores", "")
THEO = ("Theo Martin", "theo.martin@example.org", "", "")
NORA = ("Nora Kim", "nora.kim@example.org", "", "")
BEN = ("Ben Alvarez", "ben.alvarez@example.com", "", "")
ELENA = ("Elena Rivera", "elena.rivera@example.net", "", "")
AMARA = ("Dr. Amara Osei", "amara.osei@harborclinic.example", "Harbor Community Clinic", "Medical Director")
RUTH = ("Ruth Feld", "ruth.feld@harborclinic.example", "Harbor Community Clinic", "Nurse Manager")
VOLUNTEERS = ("Harbor Clinic Volunteers", "volunteers@harborclinic.example", "Harbor Community Clinic", "")

TEAM = [PRIYA, JORDAN, SAM, MEI]
BOOK_CLUB = [NORA, THEO, BEN]

FORGEHUB = ("Forgehub", "notifications@forgehub.example")
FORGEHUB_LIST = "website.northwind.forgehub.example"
BRANDKIT_LIST = "brand-kit.northwind.forgehub.example"

LABELS = [
    # (name, colour token, show in label list)
    ("Clients", "@default/teal/3"),
    ("Clients/Lumen Coffee", "@default/orange/3"),
    ("Clients/Bluefin Outdoor", "@default/blue/3"),
    ("Clients/Casa Verde", "@default/green/3"),
    ("Finance", "@default/green/2"),
    ("Finance/Bills", "@default/red/3"),
    ("Finance/Receipts", "@default/green/1"),
    ("Dev", "@default/indigo/2"),
    ("Dev/Forgehub", "@default/indigo/3"),
    ("Reading", "@default/yellow/3"),
    ("Studio", "@default/purple/2"),
    ("Travel", "@default/purple/3"),
    ("Family", "@default/pink/3"),
    ("Clinic board", "@default/grey/3"),
]

# Filters, created before the mail arrives so they apply as it is ingested. Label names are
# resolved to ids by the seed.
RULES = [
    {"name": "Forgehub notifications", "query": "from:notifications@forgehub.example",
     "actions": {"add_labels": ["Dev/Forgehub"], "category": "updates"}},
    {"name": "Receipts", "query": "subject:(receipt OR \"payment received\")",
     "actions": {"add_labels": ["Finance/Receipts"], "category": "purchases"}},
    {"name": "Bills", "query": "from:(billing@brightline-energy.example OR statements@cobaltmobile.example "
                               "OR billing@riverbendwater.example)",
     "actions": {"add_labels": ["Finance/Bills"], "important": True}},
    {"name": "Design reading", "query": "list:thegrid.example OR from:hello@kerningweekly.example",
     "actions": {"add_labels": ["Reading"], "category": "updates"}},
    {"name": "Clinic board", "query": "to:alex@harborclinic.example",
     "actions": {"add_labels": ["Clinic board"]}},
]

TEMPLATES = [
    {"name": "Got it, thanks", "shortcut": "thx", "subject": "",
     "body_html": "<p>Hi {first_name},</p><p>Thanks, got it. I will get back to you by end of day.</p><p>{my_name}</p>"},
    {"name": "Meeting follow-up", "shortcut": "fu", "subject": "Notes from today",
     "body_html": "<p>Hi {first_name},</p><p>Thanks for your time today. Here is what we agreed:</p>"
                  "<ul><li></li><li></li></ul><p>Next check-in: </p><p>{my_name}</p>"},
    {"name": "Invoice reminder", "shortcut": "inv", "subject": "Invoice reminder",
     "body_html": "<p>Hi {first_name},</p><p>A friendly reminder that our invoice is still open. "
                  "The PDF is attached again for convenience.</p><p>Best,<br>{my_name}</p>"},
]

# ---------------------------------------------------------------------------------------------
# Mail. Each entry is one message; `parent` threads it under an earlier key. `ago` is how long
# before "now" it arrived ("40m", "3h", "2d4h"). `role` is where it lands: inbox (default),
# archive, sent, junk. Attachments: (filename, kind, title) with kind pdf | png.
# Seed-side fields: important (True/False: the importance the triage shows), deadline (a day
# offset or weekday name: a Due chip), needs_reply / waiting (signals), action (a primary action),
# category (primary | updates | promotions | social | forums | purchases).
# ---------------------------------------------------------------------------------------------

LUMEN_BRIEF = """Hi Alex,

Great to meet you and Jordan last week. As promised, our brand brief is attached.

The short version: we are moving from a single café to wholesale, and the current logo does not
hold up on bags, cups and the delivery van. We want something warmer than the big chains and more
confident than what we have now.

Budget and timeline are in the brief. Our board meets on {D:fri}, so we would love to see a first
direction before then.

Dana Whitfield
Marketing Director, Lumen Coffee Roasters
+1 555 0172"""

LUMEN_REPLY = """Hi Dana,

Thanks for the brief, it is a great starting point. We will share two directions on {D:thu} so you
have time to look before the board meeting. Mei is already pulling together a moodboard.

One question: is the van wrap in scope for this phase, or later?

Alex"""

LUMEN_LATEST = """Hi Alex,

Quick answers and one ask:

- The van wrap is in scope, but it can be the last deliverable.
- Our updated packaging guidelines are attached (new bag sizes).

Could you confirm you can share the two directions by {D:thu}, 2 PM? I want to book the board room
for the review. If that slot does not work, {D:thu} at 4 PM is also fine.

Thanks!
Dana"""

PRIYA_REVIEW = """Hi Alex,

Could you present the Lumen concepts at Thursday's studio review? 20 minutes is plenty, and it
would help the team see how we are handling the wholesale range.

Let me know by tomorrow so I can finalise the agenda.

Priya"""

TIMELINE = """Hi Alex,

Attached is the revised Bluefin timeline. The big change is that content entry moves a week
earlier, so design sign-off on the product page templates is due {D:2}.

Please review and sign off by {D:2}; Marco wants to lock the launch date on our next call.

Thanks,
Jordan"""

MEI_HERO = """<p>Hi Alex,</p>
<p>Two hero directions for the Bluefin homepage. Option A leads with the trail photography, option B
with the product grid. I lean towards A for the launch.</p>
<p><b>Option A: trail first</b></p>
<p><img src="cid:hero-a@northwind.example" alt="Option A: trail first" width="480" height="270"></p>
<p><b>Option B: products first</b></p>
<p><img src="cid:hero-b@northwind.example" alt="Option B: products first" width="480" height="270"></p>
<p>Happy to walk through them after standup.</p>
<p>Mei</p>"""

MARCO_QUOTE = """Hi Marco,

As discussed, here is our quote for the Bluefin landing page: design, build and two rounds of
revisions, with launch support for the first week.

Could you confirm by {D:mon} so we can hold the team for the {S:mon} start?

Best,
Alex"""

LUCIA_ES = """Hola Alex,

Gracias por la llamada de ayer. Te escribo para confirmar los próximos pasos del nuevo catálogo.

- Necesitamos 24 páginas en lugar de 16, con una sección nueva para la colección de exterior.
- Las fotos llegarán el {D:3}; el fotógrafo es el mismo de la temporada pasada.
- ¿Podrías enviarnos un presupuesto actualizado antes del viernes?

Un abrazo,
Lucía Romero
Casa Verde Interiores"""

LUCIA_EN = """Hi Alex,

Thanks for the call yesterday. I am writing to confirm the next steps for the new catalogue.

- We need 24 pages instead of 16, with a new section for the outdoor collection.
- The photos will arrive on {D:3}; the photographer is the same as last season.
- Could you send us an updated quote before Friday?

Warm regards,
Lucía Romero
Casa Verde Interiores"""

INVOICE_REMINDER = """Hello,

A quick note that invoice NW-2026-031 (catalogue design, phase 1) was due last week. The PDF is
attached again. Could you let me know when we can expect payment?

Thank you,
Alex Rivera
Northwind Studio"""

HOTEL = """Your reservation is confirmed.

Harborline Hotel, Portland
Check-in: {D:12} from 3 PM
Check-out: {D:14} by 11 AM
Confirmation: HLX-48213

Need to change something? Reply to this email or call the front desk."""

LUNCH = [
    ("Jordan Lee", "Friday lunch: tacos or the noodle place? Reply by 11."),
    ("Sam Okafor", "Noodles. Always noodles."),
    ("Mei Tanaka", "Tacos please, the noodle place is too loud for a call afterwards."),
    ("Priya Shah", "Tacos, and I am buying. Jordan, can you order for 6?"),
]

PR_142 = """@sam-okafor requested your review on northwind/website#142.

Add pricing page

- New /pricing route with three plans and an FAQ accordion
- Uses the new type scale tokens from brand-kit
- Audit: performance 98, accessibility 100

View it on Forgehub: https://forgehub.example/northwind/website/pull/142"""

PR_142_COMMENT = """sam-okafor commented on northwind/website#142:

> The plan cards wrap awkwardly between 900 and 1000 px. I added a breakpoint, but I would like
> a design check before merging.

Reply to this email directly or view it on Forgehub:
https://forgehub.example/northwind/website/pull/142#comment-3381"""

RUN_FAILED = """Run failed: deploy-preview (main)

northwind/website · commit 4e1a9c2 "Tighten hero spacing" by mei-tanaka
Job "build" failed after 1m 42s: Type error in src/components/PriceCard.tsx line 18.

View the run: https://forgehub.example/northwind/website/actions/runs/9921"""

ISSUE_37 = """mei-tanaka opened northwind/brand-kit#37:

Icons render blurry at 1x

The 16 px icons are exported at fractional coordinates, so they blur on standard-density screens.
Could we snap the artboards to whole pixels before the next release?

View it on Forgehub: https://forgehub.example/northwind/brand-kit/issues/37"""

ENERGY_BILL = """<div style="font-family:sans-serif;max-width:560px">
<h2 style="color:#14532d">Your studio energy bill is ready</h2>
<p>Account 4471-209-88 &middot; Northwind Studio, 18 Foundry Lane</p>
<table style="width:100%;border-collapse:collapse">
<tr><td>Billing period</td><td style="text-align:right">Sep 1 &ndash; Sep 30</td></tr>
<tr><td>Usage</td><td style="text-align:right">2,184 kWh</td></tr>
<tr><td><b>Amount due</b></td><td style="text-align:right"><b>$412.80</b></td></tr>
<tr><td><b>Payment due date</b></td><td style="text-align:right"><b>{D:2}</b></td></tr>
</table>
<p style="margin:24px 0"><a href="https://pay.brightline-energy.example/bill/4471-209-88"
style="background:#15803d;color:#fff;padding:12px 20px;border-radius:6px;text-decoration:none">Pay bill</a></p>
<p style="color:#666;font-size:12px">Brightline Energy &middot; This is an automated message.</p>
</div>"""

MOBILE_BILL = """<div style="font-family:sans-serif;max-width:560px">
<h2>Your Cobalt Mobile statement</h2>
<p>Business plan, 4 lines</p>
<p>Amount due: <b>$86.40</b><br>Payment due date: <b>{D:12}</b></p>
<p>Autopay is off for this account.</p>
<p><a href="https://my.cobaltmobile.example/statement"
style="background:#1d4ed8;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">View statement</a></p>
</div>"""

WATER_BILL = """Riverbend Water: your bill is ready.

Service address: 44 Alder Street, Apt 3
Amount due: $38.15
Payment due date: {D:9}

Pay online at https://riverbendwater.example/pay or set up autopay to never miss a due date."""

FONTS_RECEIPT = """Thanks for your purchase, Northwind Studio.

Receipt PF-2026-8812
Halden Sans Family (desktop + web, 5 seats)   $349.00
Payment received: card ending 4242

Your licence files are ready in your Pixelforge account."""

PARCEL_HTML = """<html><head>
<script type="application/ld+json">
{"@context": "http://schema.org", "@type": "ParcelDelivery",
 "trackingNumber": "PP7731940562",
 "potentialAction": {"@type": "TrackAction", "name": "Track package",
   "target": "https://parcelpoint.example/track/PP7731940562"}}
</script></head><body style="font-family:sans-serif">
<h2>Your package arrives today</h2>
<p>Order 20817 from Fieldnote Paper Co. is out for delivery.</p>
<p><b>Arriving today, 1:30 PM &ndash; 4:30 PM</b></p>
<p><a href="https://parcelpoint.example/track/PP7731940562"
style="background:#7c3aed;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">Track package</a></p>
<p>Tracking number PP7731940562</p>
</body></html>"""

NEWSLETTER_HTML = """<div style="font-family:Georgia,serif;max-width:600px">
<img src="https://cdn.thegrid.example/issue-118/hero.png" alt="The Grid, issue 118" width="600" height="200">
<h1>The Grid #118: Variable fonts in production</h1>
<p>This week: what we learned shipping variable fonts on three client sites, a field guide to
optical sizes, and five portfolios that use motion with restraint.</p>
<h3>1. Variable fonts, one year in</h3>
<p>File sizes went down, but the real win was fewer design compromises at small sizes.</p>
<h3>2. Optical sizes, explained</h3>
<p>Why your caption text looks heavier than your headline, and how to fix it.</p>
<p><a href="https://thegrid.example/118">Read the full issue</a></p>
<img src="https://pixel.thegrid.example/o.gif?u=8812" width="1" height="1" alt="">
<p style="font-size:12px;color:#777">You are receiving this because you subscribed at thegrid.example.
<a href="https://thegrid.example/unsubscribe">Unsubscribe</a></p>
</div>"""

FIELDNOTE_PROMO = """<div style="font-family:sans-serif;max-width:560px;text-align:center">
<h1 style="color:#b45309">25% off notebooks this week</h1>
<p>Dot grid, ruled and blank, in our new recycled covers. Use code AUTUMN25 at checkout.</p>
<p><a href="https://fieldnote-paper.example/shop" style="background:#b45309;color:#fff;padding:12px 24px;
border-radius:24px;text-decoration:none">Shop notebooks</a></p>
<p style="font-size:12px;color:#888">Offer ends Sunday. Fieldnote Paper Co.</p>
</div>"""

ATLAS_PROMO = """The fall layering sale is here.

Fleece, shells and base layers: up to 40% off through the weekend. Members get free shipping.

Shop the sale: https://atlas-outfitters.example/sale

Atlas Outfitters"""

FLIGHT = """<div style="font-family:sans-serif">
<h2>Check in for your flight to Portland</h2>
<p>Flight SK 412 &middot; {D:12} &middot; departs 8:05 AM</p>
<p>Passenger: Alex Rivera &middot; Confirmation QX7P2M</p>
<p><a href="https://skyway-air.example/checkin/QX7P2M" style="background:#0e7490;color:#fff;padding:10px 18px;
border-radius:6px;text-decoration:none">Check in</a></p>
</div>"""

CIRCLET_MENTION = """Mei Tanaka mentioned you in a comment:

"@alex this is the packaging mockup I was talking about, the kraft texture works so well."

See the post on Circlet: https://circlet.example/p/88123"""

CIRCLET_PHOTO = """Theo Martin shared a photo: "New route at the gym, who is in on Saturday?"

See it on Circlet: https://circlet.example/p/88190"""

FORUM_1 = """How are people naming colour tokens for dark mode? We have surface-1..4 and it falls
apart as soon as a component needs an elevated surface in one theme only.

-- Rosa (design-systems list)"""

FORUM_2 = """We moved to role names (surface.base, surface.raised, surface.overlay) and let each theme
map them. Took a week to migrate, never looked back.

-- Kai"""

STORAGE = """Your Cloudnest storage is 85% full.

Northwind Studio shared drive: 850 GB of 1 TB used. Large files this month: Lumen-photoshoot-raw.zip
(48 GB), Bluefin-trail-video.mov (22 GB).

Manage storage: https://cloudnest.example/storage"""

SPAM_1 = """CONGRATULATIONS!!!

You have been selected to receive a $500 reward card. To claim your reward, confirm your details
within 24 hours at the link below.

http://rewards.prize-center.example/claim?id=88213"""

SPAM_2 = """Dear user,

Your mailbox has exceeded its storage limit. You will stop receiving new messages unless you
verify your account today.

Verify now: http://secure-mailbox.example/verify"""

SPAM_3 = """Hi friend, we are looking for remote assistants. Earn $900 a week working one hour a day.
Reply with your phone number to start."""

THEO_CLIMB = """Hey! The new bouldering wall opens Saturday. Want to go around 10 and grab lunch
after? I can drive.

Theo"""

BOOK_1 = """Hi all,

October pick time! Options:
1. The Glass Orchard
2. Small Hours at the Lighthouse
3. A Field Guide to Leaving

Vote by Sunday. We meet on the last Tuesday as usual.

Nora"""

BOOK_2 = """Voting for The Glass Orchard. I can host this month.

Ben"""

BOOK_3 = """Glass Orchard for me too. Ben, I will bring the snacks.

Theo"""

MOM = """Hi sweetheart,

Here are the photos from Sunday. The one with the dog is my favourite.

Are you still coming for my birthday dinner? Let me know so I can book a table.

Love,
Mom"""

TRAILHEAD = """Trailhead Journal, autumn issue

Five shoulder-season hikes within two hours of the city, how to layer for a cold start, and a
reader's guide to reading trail maps without an app.

Read online: https://trailheadjournal.example/autumn"""

TANDEM_CODE = """Your Tandem Bank sign-in code is 715204.

It expires in 10 minutes. If you did not try to sign in, call us at the number on your card."""

CLOUDNEST_CODE = """Cloudnest verification code: 482913

Enter this code to finish signing in on a new device (a browser on Linux). The code expires in 15 minutes."""

CLINIC_PACKET = """Dear board members,

The packet for the {D:8} meeting is attached. Highlights:

- Q3 visits were up 14%, mostly in pediatric care.
- The health fair needs four more volunteers.
- A draft policy on patient feedback for your review (section 4).

Please read it before the meeting. The packet contains aggregated patient data; keep it within the
board.

Amara Osei, MD
Medical Director, Harbor Community Clinic"""

CLINIC_INTAKE = """Hi Alex,

Thank you again for redesigning the intake form. Our nurses tried the second draft this week:

- The medication section needs more space; patients run out of lines.
- Can the allergy question move to the first page?
- Spanish and Vietnamese versions are needed before we print.

Could you send a revised draft by {D:5}? Happy to talk it through on the phone.

Ruth Feld, RN
Nurse Manager"""

CLINIC_VOLUNTEER = """Hello volunteers,

The fall health fair is on {D:sat}, 9 AM to 1 PM, in the clinic parking lot. Shifts are below.
Reply to this email if you can take one.

- 8:30 Set-up (2 people)
- 9:00 Registration desk (3 people)
- 11:00 Kids' corner (2 people)

Thank you!
The volunteer team"""

OPS_CLOSED = """Hi everyone,

The studio is closed on {D:mon} while the floors are refinished. Please take laptops home on
{W:fri}. The server room stays on.

Northwind Ops"""

OFFSITE = """Hi Alex,

Draft agenda for the offsite is attached. Main topics: the wholesale clients we want next year,
our process for brand systems, and the hiring plan.

Could you lead the brand systems session? 45 minutes.

Priya"""

RETRO = """Notes from the Q3 retrospective:

What went well: client reviews ran on time; the new handoff checklist cut rework.
What to change: estimates for illustration work were too low; we need a buffer week before launches.

Jordan"""

VOICE_MEMO = """Hi Alex,

I recorded my notes from the Bluefin call on the drive home rather than typing them up. The
memo is attached (about ten seconds). Short version: they like timeline v4, want the landing page
copy a week earlier, and asked whether we can reuse the trail photos from the spring catalogue.

Jordan"""

ACCESS_CARDS = """Hello Northwind team,

The building moves to new access cards on Monday. Old cards stop working at 6 PM on Friday.

Please collect your new card from the front desk on the ground floor, between 8 AM and 5 PM.
Bring a photo ID. Cards for new staff can be ordered through your office manager.

Thank you,
Harbor Property Management"""

MAILS: list[dict] = [
    # ---------------- work: correspondence ----------------
    {"key": "lumen-1", "acct": "work", "frm": DANA, "to": [WORK], "cc": [JORDAN[1]], "ago": "6d3h",
     "subject": "Lumen Coffee: rebrand brief", "text": LUMEN_BRIEF, "role": "archive", "seen": True,
     "labels": ["Clients/Lumen Coffee"], "attach": [("Lumen-brand-brief.pdf", "pdf", "Lumen Coffee Roasters: brand brief")]},
    {"key": "lumen-2", "acct": "work", "parent": "lumen-1", "frm": (ME, WORK), "to": [DANA[1]], "cc": [JORDAN[1]],
     "ago": "6d1h", "subject": "Re: Lumen Coffee: rebrand brief", "text": LUMEN_REPLY, "role": "sent",
     "labels": ["Clients/Lumen Coffee"]},
    {"key": "lumen-3", "acct": "work", "parent": "lumen-2", "frm": DANA, "to": [WORK], "cc": [JORDAN[1]], "ago": "1h20m",
     "subject": "Re: Lumen Coffee: rebrand brief", "text": LUMEN_LATEST, "labels": ["Clients/Lumen Coffee"],
     "attach": [("Lumen-packaging-guidelines.pdf", "pdf", "Packaging guidelines v3: bag sizes")],
     "important": True, "starred": True, "needs_reply": "Asks you to confirm the {W:thu} 2 PM review",
     "deadline": "thu"},
    {"key": "priya-review", "acct": "work", "frm": PRIYA, "to": [WORK], "ago": "3h",
     "subject": "Thursday studio review", "text": PRIYA_REVIEW, "important": True,
     "needs_reply": "Asks you to present the Lumen concepts on Thursday"},
    {"key": "timeline", "acct": "work", "frm": JORDAN, "to": [WORK], "cc": [SAM[1]], "ago": "5h",
     "subject": "Bluefin timeline: sign-off needed", "text": TIMELINE, "important": True,
     "labels": ["Clients/Bluefin Outdoor"], "deadline": 2, "needs_reply": "Asks for your sign-off on the timeline",
     "attach": [("Bluefin-timeline-v4.pdf", "pdf", "Bluefin Outdoor: project timeline v4")]},
    {"key": "mei-hero", "acct": "work", "frm": MEI, "to": [WORK], "ago": "1d2h", "seen": True,
     "subject": "Homepage hero options for Bluefin", "html": MEI_HERO, "labels": ["Clients/Bluefin Outdoor"],
     "inline": [("hero-a@northwind.example", "hero-a.png", (34, 87, 64)), ("hero-b@northwind.example", "hero-b.png", (180, 83, 9))]},
    {"key": "marco-quote", "acct": "work", "frm": (ME, WORK), "to": [MARCO[1]], "ago": "3d4h", "role": "sent",
     "subject": "Quote for the Bluefin landing page", "text": MARCO_QUOTE,
     "attach": [("Northwind-quote-Bluefin-landing.pdf", "pdf", "Quote Q-2026-114: Bluefin landing page")],
     "follow_up": "1d"},
    {"key": "lucia", "acct": "work", "frm": LUCIA, "to": [WORK], "ago": "4h",
     "subject": "Propuesta para el nuevo catálogo", "text": LUCIA_ES, "labels": ["Clients/Casa Verde"],
     "needs_reply": "Asks for an updated quote before Friday", "translate": LUCIA_EN},
    {"key": "invoice-reminder", "acct": "work", "frm": (ME, WORK), "to": [CASA_AP[1]], "cc": [LUCIA[1]], "ago": "9d",
     "role": "sent", "subject": "Invoice NW-2026-031", "text": INVOICE_REMINDER,
     "attach": [("Invoice-NW-2026-031.pdf", "pdf", "Invoice NW-2026-031: catalogue design, phase 1")],
     "nudged": True},
    {"key": "offsite", "acct": "work", "frm": PRIYA, "to": [WORK], "cc": [JORDAN[1], SAM[1], MEI[1]], "ago": "2d5h",
     "seen": True, "subject": "Offsite agenda (draft)", "text": OFFSITE, "important": True,
     "attach": [("Offsite-agenda-draft.pdf", "pdf", "Northwind offsite: draft agenda")]},
    # An audio attachment, so "Send to Speakr" has something to send.
    {"key": "voice-memo", "acct": "work", "frm": JORDAN, "to": [WORK], "ago": "7h",
     "subject": "Voice memo: Bluefin call notes", "text": VOICE_MEMO, "labels": ["Clients/Bluefin Outdoor"],
     "attach": [("Bluefin-call-notes.wav", "wav", 10)]},
    {"key": "ops-closed", "acct": "work", "frm": OPS, "to": ["team@northwind.example"], "ago": "1d6h", "seen": True,
     "subject": "Studio closed on Monday", "text": OPS_CLOSED, "important": False},
    # Forwarded as an attachment (an attached email, message/rfc822): the reader's attached-email
    # card, open because the outer message has no text of its own (shot mail-attached-email).
    {"key": "fwd-access-cards", "acct": "work", "frm": OPS, "to": ["team@northwind.example"], "ago": "20d", "seen": True,
     "role": "archive", "subject": "Fwd: New access cards from Monday", "text": "",
     "forward": {"frm": ("Harbor Property Management", "office@harborproperty.example"), "to": [OPS[1]],
                 "ago": "20d2h", "subject": "New access cards from Monday", "text": ACCESS_CARDS}},
    {"key": "retro", "acct": "work", "frm": JORDAN, "to": ["team@northwind.example"], "ago": "19d", "seen": True,
     "role": "archive", "subject": "Q3 retrospective notes", "text": RETRO},
    {"key": "hotel", "acct": "work", "frm": ("Harborline Hotel", "reservations@harborline-hotel.example"), "to": [WORK],
     "ago": "2d", "seen": True, "subject": "Reservation confirmed: Harborline Hotel, Portland", "text": HOTEL,
     "labels": ["Travel"], "category": "updates", "snooze": "1d"},
    {"key": "flight", "acct": "work", "frm": ("Skyway Air", "no-reply@skyway-air.example"), "to": [WORK], "ago": "7h",
     "subject": "Check in for your flight to Portland", "html": FLIGHT, "labels": ["Travel"], "category": "updates",
     "action": {"kind": "checkin", "label": "Check in", "url": "https://skyway-air.example/checkin/QX7P2M",
                "source": "markup", "fact": "Flight SK 412 departs 8:05 AM"}},
    # ---------------- work: the iTIP scenarios (the seed builds the calendar parts) ----------------
    {"key": "invite-kickoff", "acct": "work", "frm": JORDAN, "to": [WORK, MARCO[1], SAM[1]], "ago": "2h",
     "subject": "Invitation: Bluefin kickoff", "itip": "kickoff-request", "labels": ["Clients/Bluefin Outdoor"]},
    {"key": "invite-kickoff-update", "acct": "work", "parent": "invite-kickoff", "frm": JORDAN,
     "to": [WORK, MARCO[1], SAM[1]], "ago": "40m", "seen": True, "subject": "Updated invitation: Bluefin kickoff",
     "itip": "kickoff-update", "labels": ["Clients/Bluefin Outdoor"]},
    {"key": "invite-photo", "acct": "work", "frm": MARCO, "to": [WORK, JORDAN[1]], "ago": "2d1h", "seen": True,
     "subject": "Invitation: Bluefin photo review", "itip": "photo-request", "labels": ["Clients/Bluefin Outdoor"]},
    {"key": "cancel-photo", "acct": "work", "parent": "invite-photo", "frm": MARCO, "to": [WORK, JORDAN[1]],
     "ago": "5h20m", "subject": "Cancelled: Bluefin photo review", "itip": "photo-cancel",
     "labels": ["Clients/Bluefin Outdoor"]},
    {"key": "typeconf", "acct": "work", "frm": ("TypeConf", "hello@typeconf.example"), "to": [WORK], "ago": "1d10h",
     "seen": True, "subject": "Your TypeConf 2026 schedule", "category": "updates", "important": False,
     "text": "Thanks for registering for TypeConf 2026! Your sessions are in the attached calendar file.\n\n"
             "See you there,\nThe TypeConf team", "ics": "typeconf"},
    {"key": "rsvp-sam", "acct": "work", "frm": SAM, "to": [WORK], "ago": "50m",
     "subject": "Accepted: Design critique", "itip": "critique-reply"},
    {"key": "counter-mei", "acct": "work", "frm": MEI, "to": [WORK], "ago": "35m",
     "subject": "New time proposed: Design critique", "itip": "critique-counter"},
    # ---------------- work: a muted reply-all thread ----------------
    *[{"key": f"lunch-{i}", "acct": "work", "parent": f"lunch-{i - 1}" if i else None,
       "frm": next(p for p in TEAM if p[0] == who), "to": ["team@northwind.example"], "ago": f"{26 - i}h",
       "subject": "Friday lunch" if i == 0 else "Re: Friday lunch", "text": body, "seen": i < 2,
       "muted": i == len(LUNCH) - 1, "important": False} for i, (who, body) in enumerate(LUNCH)],
    # ---------------- work: the code host ----------------
    {"key": "pr-142", "acct": "work", "frm": FORGEHUB, "to": [WORK], "ago": "2h40m", "list": FORGEHUB_LIST,
     "subject": "[northwind/website] Add pricing page (PR #142)", "text": PR_142,
     "msgid": "northwind/website/pull/142@forgehub.example"},
    {"key": "pr-142-c", "acct": "work", "parent": "pr-142", "frm": ("Sam Okafor via Forgehub", FORGEHUB[1]), "to": [WORK],
     "ago": "55m", "list": FORGEHUB_LIST, "subject": "Re: [northwind/website] Add pricing page (PR #142)",
     "text": PR_142_COMMENT},
    {"key": "run-failed", "acct": "work", "frm": FORGEHUB, "to": [WORK], "ago": "5h", "list": FORGEHUB_LIST,
     "subject": "[northwind/website] Run failed: deploy-preview on main", "text": RUN_FAILED},
    {"key": "issue-37", "acct": "work", "frm": FORGEHUB, "to": [WORK], "ago": "1d1h", "seen": True, "list": BRANDKIT_LIST,
     "subject": "[northwind/brand-kit] Icons render blurry at 1x (Issue #37)", "text": ISSUE_37},
    # ---------------- work: bills, receipts, codes, notices ----------------
    {"key": "energy-bill", "acct": "work", "frm": ("Brightline Energy", "billing@brightline-energy.example"),
     "to": [WORK], "ago": "9h", "subject": "Your studio energy bill is ready", "html": ENERGY_BILL,
     "category": "updates", "deadline": 2,
     "action": {"kind": "pay", "label": "Pay bill", "url": "https://pay.brightline-energy.example/bill/4471-209-88",
                "source": "rules", "fact": "$412.80 due {S:2}"}},
    {"key": "mobile-bill", "acct": "work", "frm": ("Cobalt Mobile", "statements@cobaltmobile.example"), "to": [WORK],
     "ago": "1d4h", "seen": True, "subject": "Your Cobalt Mobile statement is ready", "html": MOBILE_BILL,
     "category": "updates", "deadline": 12,
     "action": {"kind": "statement", "label": "View statement", "url": "https://my.cobaltmobile.example/statement",
                "source": "rules", "fact": "$86.40 due {S:12}"}},
    {"key": "fonts-receipt", "acct": "work", "frm": ("Pixelforge Fonts", "receipts@pixelforge.example"), "to": [WORK],
     "ago": "2d3h", "seen": True, "subject": "Your receipt from Pixelforge Fonts", "text": FONTS_RECEIPT,
     "important": False},
    {"key": "cloudnest-code", "acct": "work", "frm": ("Cloudnest", "security@cloudnest.example"), "to": [WORK],
     "ago": "12m", "subject": "Your Cloudnest verification code", "text": CLOUDNEST_CODE, "category": "updates",
     "important": False},
    {"key": "storage", "acct": "work", "frm": ("Cloudnest", "no-reply@cloudnest.example"), "to": [WORK], "ago": "1d8h",
     "seen": True, "subject": "Your storage is 85% full", "text": STORAGE, "category": "updates", "important": False},
    # ---------------- work: newsletters, social, forums, promotions ----------------
    {"key": "grid", "acct": "work", "frm": ("The Grid", "newsletter@thegrid.example"), "to": [WORK], "ago": "8h",
     "list": "weekly.thegrid.example", "unsubscribe": "<https://thegrid.example/unsubscribe>", "one_click": True,
     "subject": "The Grid #118: Variable fonts in production", "html": NEWSLETTER_HTML, "important": False,
     "remote_images": {"https://cdn.thegrid.example/issue-118/hero.png": ((15, 23, 42), 600, 200)}},
    {"key": "kerning", "acct": "work", "frm": ("Kerning Weekly", "hello@kerningweekly.example"), "to": [WORK],
     "ago": "2d7h", "seen": True, "unsubscribe": "<https://kerningweekly.example/u>, <mailto:leave@kerningweekly.example>",
     "subject": "Kerning Weekly #212: Grids that breathe",
     "text": "This week: modular grids for editorial layouts, and the case for wider gutters.\n\n"
             "https://kerningweekly.example/212", "important": False},
    {"key": "fieldnote", "acct": "work", "frm": ("Fieldnote Paper Co.", "hello@fieldnote-paper.example"), "to": [WORK],
     "ago": "6h", "unsubscribe": "<https://fieldnote-paper.example/unsubscribe>", "one_click": True,
     "subject": "25% off notebooks this week", "html": FIELDNOTE_PROMO, "category": "promotions", "important": False},
    {"key": "circlet-mention", "acct": "work", "frm": ("Circlet", "notifications@circlet.example"), "to": [WORK],
     "ago": "3h30m", "subject": "Mei Tanaka mentioned you in a comment", "text": CIRCLET_MENTION,
     "category": "social", "important": False},
    {"key": "forum-1", "acct": "work", "frm": ("Rosa Delgado", "rosa.delgado@example.com"),
     "to": ["design-systems@lists.example.org"], "ago": "1d3h", "seen": True, "list": "design-systems.lists.example.org",
     "subject": "[design-systems] Token naming for dark mode", "text": FORUM_1, "category": "forums", "important": False},
    {"key": "forum-2", "acct": "work", "parent": "forum-1", "frm": ("Kai Lindqvist", "kai@example.org"),
     "to": ["design-systems@lists.example.org"], "ago": "11h", "list": "design-systems.lists.example.org",
     "subject": "Re: [design-systems] Token naming for dark mode", "text": FORUM_2, "category": "forums",
     "important": False},
    # ---------------- work: spam ----------------
    {"key": "spam-1", "acct": "work", "frm": ("Reward Center", "rewards@prize-center.example"), "to": [WORK],
     "ago": "4h", "role": "junk", "subject": "Congratulations! Claim your $500 reward", "text": SPAM_1},
    {"key": "spam-2", "acct": "work", "frm": ("Mailbox Admin", "it-helpdesk@secure-mailbox.example"), "to": [WORK],
     "ago": "1d", "role": "junk", "subject": "Urgent: verify your mailbox", "text": SPAM_2},
    # ---------------- personal ----------------
    {"key": "theo", "acct": "personal", "frm": THEO, "to": [PERSONAL], "ago": "2h15m",
     "subject": "Climbing Saturday?", "text": THEO_CLIMB, "needs_reply": "Asks whether you can climb on Saturday",
     "replies": ["I'm in, see you at 10!", "Can we make it 11 instead?", "Can't this week, next time?"]},
    {"key": "book-1", "acct": "personal", "frm": NORA, "to": [PERSONAL, THEO[1], BEN[1]], "ago": "2d6h", "seen": True,
     "subject": "Book club: October pick", "text": BOOK_1},
    {"key": "book-2", "acct": "personal", "parent": "book-1", "frm": BEN, "to": [NORA[1], PERSONAL, THEO[1]],
     "ago": "1d9h", "seen": True, "subject": "Re: Book club: October pick", "text": BOOK_2},
    {"key": "book-3", "acct": "personal", "parent": "book-2", "frm": THEO, "to": [NORA[1], PERSONAL, BEN[1]],
     "ago": "20h", "subject": "Re: Book club: October pick", "text": BOOK_3},
    {"key": "mom", "acct": "personal", "frm": ELENA, "to": [PERSONAL], "ago": "1d1h", "subject": "Photos from Sunday",
     "text": MOM, "labels": ["Family"], "important": True, "starred": True,
     "attach": [("garden.png", "png", (101, 163, 13)), ("with-the-dog.png", "png", (217, 119, 6))]},
    {"key": "tandem-code", "acct": "personal", "frm": ("Tandem Bank", "no-reply@tandem-bank.example"),
     "to": [PERSONAL], "ago": "25m", "subject": "Your Tandem Bank sign-in code", "text": TANDEM_CODE,
     "category": "updates", "important": False},
    {"key": "water-bill", "acct": "personal", "frm": ("Riverbend Water", "billing@riverbendwater.example"),
     "to": [PERSONAL], "ago": "1d5h", "seen": True, "subject": "Your Riverbend Water bill", "text": WATER_BILL,
     "category": "updates", "deadline": 9,
     "action": {"kind": "pay", "label": "Pay", "url": "https://riverbendwater.example/pay", "source": "rules",
                "fact": "$38.15 due {S:9}"}},
    {"key": "parcel", "acct": "personal", "frm": ("Parcelpoint", "tracking@parcelpoint.example"), "to": [PERSONAL],
     "ago": "1h5m", "subject": "Your package arrives today", "html": PARCEL_HTML, "category": "updates",
     "important": False,
     "action": {"kind": "track", "label": "Track package", "url": "https://parcelpoint.example/track/PP7731940562",
                "source": "markup", "fact": "Arriving today 1:30 PM - 4:30 PM"}},
    {"key": "atlas", "acct": "personal", "frm": ("Atlas Outfitters", "deals@atlas-outfitters.example"),
     "to": [PERSONAL], "ago": "10h", "unsubscribe": "<https://atlas-outfitters.example/unsubscribe>",
     "subject": "The fall layering sale is here", "text": ATLAS_PROMO, "category": "promotions", "important": False},
    {"key": "trailhead", "acct": "personal", "frm": ("Trailhead Journal", "editors@trailheadjournal.example"),
     "to": [PERSONAL], "ago": "3d", "seen": True, "unsubscribe": "<https://trailheadjournal.example/unsubscribe>",
     "subject": "Trailhead Journal: the autumn issue", "text": TRAILHEAD, "category": "updates", "important": False},
    {"key": "circlet-photo", "acct": "personal", "frm": ("Circlet", "notifications@circlet.example"),
     "to": [PERSONAL], "ago": "5h", "subject": "Theo Martin shared a photo", "text": CIRCLET_PHOTO,
     "category": "social", "important": False},
    {"key": "spam-3", "acct": "personal", "frm": ("Jobs Team", "offers@quick-hire.example"), "to": [PERSONAL],
     "ago": "7h", "role": "junk", "subject": "Work from home: $900 a week", "text": SPAM_3},
    # ---------------- clinic (local-only) ----------------
    {"key": "clinic-packet", "acct": "clinic", "frm": AMARA, "to": [CLINIC], "ago": "1d7h", "seen": True,
     "subject": "Board packet: October meeting (confidential)", "text": CLINIC_PACKET, "important": True,
     "attach": [("Board-packet-October.pdf", "pdf", "Harbor Community Clinic: board packet, October")]},
    {"key": "clinic-intake", "acct": "clinic", "frm": RUTH, "to": [CLINIC], "ago": "3h50m",
     "subject": "Intake form: second draft feedback", "text": CLINIC_INTAKE,
     "needs_reply": "Asks for a revised intake form draft", "deadline": 5},
    {"key": "clinic-volunteers", "acct": "clinic", "frm": VOLUNTEERS, "to": [CLINIC], "ago": "2d2h", "seen": True,
     "subject": "Health fair volunteer shifts", "text": CLINIC_VOLUNTEER, "important": False},
]



def _backlog() -> list[dict]:
    """Older mail kept for the record, for Mail → Subscriptions and Mail → Clean up:
    earlier issues of the newsletters, old offers, and a year of shipping notices, all
    archived and without a category. Few were read, as with most newsletters."""
    out: list[dict] = []
    for i in range(1, 9):
        out.append({"key": f"grid-{118 - i}", "acct": "work", "frm": ("The Grid", "newsletter@thegrid.example"),
                    "to": [WORK], "ago": f"{i * 7}d8h", "role": "archive", "seen": i % 4 == 0,
                    "list": "weekly.thegrid.example", "unsubscribe": "<https://thegrid.example/unsubscribe>",
                    "one_click": True, "subject": f"The Grid #{118 - i}: notes on type and layout", "important": False,
                    "text": f"Issue {118 - i}. Links on type, grids and colour from the week.\n\nhttps://thegrid.example/{118 - i}"})
        out.append({"key": f"kerning-{212 - i}", "acct": "work", "frm": ("Kerning Weekly", "hello@kerningweekly.example"),
                    "to": [WORK], "ago": f"{2 + i * 7}d7h", "role": "archive", "seen": i % 2 == 0,
                    "unsubscribe": "<https://kerningweekly.example/u>, <mailto:leave@kerningweekly.example>", "important": False,
                    "subject": f"Kerning Weekly #{212 - i}", "text": "This week's reading on type and spacing."})
    for i in range(1, 7):
        out.append({"key": f"fieldnote-old-{i}", "acct": "work", "frm": ("Fieldnote Paper Co.", "hello@fieldnote-paper.example"),
                    "to": [WORK], "ago": f"{i * 23}d6h", "role": "archive", "seen": False, "one_click": True,
                    "unsubscribe": "<https://fieldnote-paper.example/unsubscribe>", "important": False,
                    "subject": ["New dot-grid colours", "Free shipping this weekend", "Back to school: 20% off",
                                "Last chance: summer notebooks", "Meet the Field Journal", "Your monthly notes"][i - 1],
                    "text": "Notebooks, pens and paper goods. Unsubscribe at any time."})
        out.append({"key": f"atlas-old-{i}", "acct": "personal", "frm": ("Atlas Outfitters", "deals@atlas-outfitters.example"),
                    "to": [PERSONAL], "ago": f"{40 + i * 31}d3h", "role": "archive", "important": False, "seen": i == 3,
                    "unsubscribe": "<https://atlas-outfitters.example/unsubscribe>",
                    "subject": ["Trail season sale", "New tents are in", "Members save 15%", "Rain gear, restocked",
                                "Winter layers preview", "Your gear checklist"][i - 1],
                    "text": "Outdoor gear for every season."})
    for i in range(1, 11):
        out.append({"key": f"parcel-old-{i}", "acct": "personal", "frm": ("Parcelpoint", "updates@parcelpoint.example"),
                    "to": [PERSONAL], "ago": f"{60 + i * 35}d5h", "role": "archive", "seen": True, "important": False,
                    "subject": f"Delivered: parcel PP{48210 + i}", "text": "Your parcel was delivered. Thank you for choosing Parcelpoint."})
    return out


MAILS += _backlog()

# Reply suggestions cached for the reader (mail.reply_suggestions), so they show without a model.
REPLY_SUGGESTIONS = {
    "lumen-3": ["Yes, Thursday at 2 PM works for us.", "Could we do 4 PM instead?",
                "Confirmed. I'll send the deck the day before."],
    "priya-review": ["Happy to present, 20 minutes is fine.", "Yes, I'll bring the two directions.",
                     "Can I present in the second half?"],
    "theo": ["I'm in, see you at 10!", "Can we make it 11 instead?", "Can't this week, next time?"],
}

DRAFTS = [
    {"key": "draft-priya", "acct": "work", "reply_to": "priya-review",
     "html": "<p>Hi Priya,</p><p>Yes, happy to present the Lumen concepts on Thursday. I will bring both "
             "directions and the packaging mockups.</p>"},
    {"key": "draft-workshop", "acct": "work", "to": [PRIYA, JORDAN, SAM, MEI], "subject": "Workshop outline: brand systems",
     "html": "<p>Hi all,</p><p>A first outline for the brand systems session at the offsite:</p>"
             "<ol><li>What a brand system is (and is not)</li><li>Three client examples</li>"
             "<li>Exercise: tokens for the Lumen range</li></ol><p>Thoughts before I build slides?</p>"},
]

SCHEDULED = {
    "acct": "work", "to": [DANA], "subject": "Lumen Coffee: weekly update",
    "html": "<p>Hi Dana,</p><p>Your weekly update: both directions are on track for Thursday, the packaging "
            "templates are in progress, and the van wrap is scheduled for the final phase.</p>"
            "<p>Have a good weekend,<br>Alex</p>",
    "send": "mon 9:00",
}


# ---------------- the sender screener (core/screener.py) ----------------
# First-time senders who wrote to the personal account. demo.seed_screener holds them in the
# Screener; the screener itself is left off, so no other screenshot changes (the screener shots
# turn it on). One sender's mail fails authentication, for the card's warning.
HANA = ("Hana Ito", "hana@kitestudio.example", "Kite Studio", "Producer")
SCREENED: list[dict] = [
    {"key": "scr-hana", "acct": "personal", "frm": HANA, "to": [PERSONAL], "ago": "3h40m",
     "subject": "Loved your talk at TypeConf: a collaboration?",
     "text": ("Hi Alex,\n\nI caught your talk on small-screen type at TypeConf last spring and have wanted to write "
              "ever since. Kite Studio is producing a short documentary on independent coffee roasters, and we are "
              "looking for a designer for the titles.\n\nWould you have twenty minutes next week?\n\nHana Ito\n"
              "Producer, Kite Studio\n")},
    {"key": "scr-hana-2", "acct": "personal", "frm": HANA, "to": [PERSONAL], "ago": "1h5m",
     "subject": "Re: Loved your talk at TypeConf: a collaboration?",
     "text": "Hi Alex, a quick follow-up with our schedule attached in the next note. No rush at all.\n\nHana\n"},
    {"key": "scr-growth", "acct": "personal", "frm": ("Peak Growth Partners", "team@peakgrowth.example"),
     "to": [PERSONAL], "ago": "5h20m", "auth": "fail",
     "subject": "Alex, your free brand audit is ready",
     "text": ("Hi Alex,\n\nWe ran a free audit of your studio's brand and found 14 quick wins. Book a call to see "
              "them.\n\nPeak Growth Partners\n")},
    {"key": "scr-letters", "acct": "personal", "frm": ("Type Tuesday", "letters@typetuesday.example"),
     "to": [PERSONAL], "ago": "20h", "list": "weekly.typetuesday.example",
     "subject": "Type Tuesday #112: variable fonts in the wild",
     "text": "This week: three studios on shipping variable fonts, a revival of a 1920s grotesque, and reader mail.\n"},
]
