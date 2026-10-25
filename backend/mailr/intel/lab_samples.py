"""Sample emails for the decisions lab (intel/lab.py run_samples): realistic cases with what a
good answer is, built in memory for the user and never stored. An expected category may be a list
(either is right). Each tests one or two decisions:
category, needs reply, deadline (and which date), the main action, a one-time code."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

SAMPLES: list[dict[str, Any]] = [
    {"id": "review-by-thursday", "tests": "A colleague asks you to do something by a date",
     "from": "Dana Whitfield <dana.whitfield@example.edu>", "subject": "Draft for the grant section",
     "text": "Hi,\n\nI've attached the draft of the methods section. Could you review it and send me your comments "
             "by Thursday, October 8? I want to send the full proposal to Sam on Friday, October 9.\n\nThanks,\nDana",
     "expected": {"category": "primary", "needs_reply": True, "deadline": "October 8"}},
    {"id": "fyi-notes", "tests": "A colleague's FYI: nothing asked",
     "from": "Sam Ortega <sam.ortega@example.edu>", "subject": "Notes from today's curriculum meeting",
     "text": "Hi all,\n\nNotes from today's meeting are below for the record. We agreed to keep DATA 520 in the fall "
             "rotation and revisit the elective list in the spring. No action needed from anyone right now.\n\nSam",
     "expected": {"category": "primary", "needs_reply": False, "deadline": None}},
    {"id": "meeting-times", "tests": "A direct question to you (scheduling)",
     "from": "Priya Raman <priya@acme-analytics.example>", "subject": "Quick call next week?",
     "text": "Hello,\n\nWould you have 30 minutes next week to talk about the analytics workshop? Are you free "
             "Tuesday at 2pm or Wednesday at 10am?\n\nBest,\nPriya",
     "expected": {"category": "primary", "needs_reply": True, "deadline": None}},
    {"id": "student-extension", "tests": "A student request (a reply is expected; the date is theirs, not yours)",
     "from": "Jordan Lee <jlee42@students.example.edu>", "subject": "Extension request for Assignment 3",
     "text": "Dear Professor,\n\nI was sick most of this week. Could I have an extension on Assignment 3 until "
             "Monday, October 12? I can share a doctor's note if needed.\n\nThank you,\nJordan",
     "expected": {"category": "primary", "needs_reply": True, "deadline": None}},
    {"id": "card-bill", "tests": "A bill: deadline yes, reply no; pay is the main action",
     "from": "Card Services <alerts@notify.examplebank.example>", "subject": "Your Visa payment is due October 12, 2026",
     "list": True, "noreply": True,
     "text": "Your statement closed on September 15, 2026.\nMinimum payment due: $35.00\nPayment due date: October 12, 2026",
     "html": '<p>Your statement closed on September 15, 2026.</p><p>Minimum payment due: $35.00<br>Payment due date: '
             'October 12, 2026</p><a href="https://examplebank.example/pay">Make a Payment</a> '
             '<a href="https://examplebank.example/statement">View Statement</a> '
             '<a href="https://examplebank.example/login">Sign On</a>',
     "expected": {"category": "updates", "needs_reply": False, "deadline": "October 12, 2026", "action": "pay"}},
    {"id": "sale-ends", "tests": "Marketing with an end date: no deadline for you",
     "from": "Outdoor Co <deals@outdoorco.example>", "subject": "48-hour sale: 30% off everything",
     "list": True,
     "text": "Our biggest sale of the season ends Sunday, October 4. Use code FALL30 at checkout. Don't miss out!",
     "expected": {"category": "promotions", "needs_reply": False, "deadline": None}},
    {"id": "call-for-papers", "tests": "A call for papers: the submission date is the deadline, not the conference",
     "from": "NAI Conference <news@nai.example.org>", "subject": "Call for papers: 2027 Annual Conference",
     "list": True,
     "text": "The 2027 Annual Conference will be held April 3-5, 2027 in Honolulu. Paper submissions are due "
             "November 1, 2026. Reviews will be returned by January 15, 2027.",
     "expected": {"category": ["updates", "forums"], "needs_reply": False, "deadline": "November 1, 2026"}},
    {"id": "package", "tests": "A delivery: track is the main action, not view order",
     "from": "Shop <ship-confirm@shop.example>", "subject": "Your package is out for delivery", "noreply": True,
     "text": "Your package is out for delivery. Arriving today by 8 PM.",
     "html": '<p>Your package is out for delivery. Arriving today by 8 PM.</p>'
             '<a href="https://shop.example/orders/123">View order</a> <a href="https://track.shop.example/123">Track package</a>',
     "expected": {"category": "purchases", "needs_reply": False, "deadline": None, "action": "track"}},
    {"id": "issue-question", "tests": "A notification quoting someone else's question: not a question to you",
     "from": "Gil <notifications@github.com>", "subject": "Re: [org/project] Crash on startup (Issue #412)",
     "list": True, "noreply": True,
     "text": "@pierre does this still happen on v0.10? Can you share the log from the last run?\n\n— Reply to this "
             "email directly or view it on GitHub.",
     "expected": {"category": ["updates", "forums"], "needs_reply": False, "deadline": None}},
    {"id": "verification-code", "tests": "A sign-in code: the copy-code chip",
     "from": "Example Airlines <no-reply@info.examplair.example>", "subject": "Your login verification code",
     "noreply": True,
     "text": "Your verification code is: 480204\nEnter this code to finish signing in. It expires in 10 minutes.",
     "expected": {"category": "updates", "needs_reply": False, "deadline": None, "action": "code"}},
    {"id": "social", "tests": "A social notification",
     "from": "LinkedIn <notifications-noreply@linkedin.example>", "subject": "Ana Ruiz commented on your post",
     "list": True, "noreply": True,
     "text": "Ana Ruiz commented on your post: \"Great talk, would love the slides!\" See the comment.",
     "expected": {"category": "social", "needs_reply": False, "deadline": None}},
    {"id": "newsletter-question", "tests": "A newsletter that asks readers a question: not a question to you",
     "from": "The Analytics Weekly <hello@analyticsweekly.example>", "subject": "Issue 112: forecasting with small data",
     "list": True,
     "text": "This week: three ways to forecast with small data. What do you think? Hit reply and tell us your "
             "favorite method!",
     "expected": {"category": "promotions", "needs_reply": False, "deadline": None}},
    # Deadlines are the reader's own obligations (intel/deadlines.py); marketing dates are not.
    {"id": "points-sale", "tests": "A points promotion with an end date (and a stylesheet in its plain part): no deadline",
     "from": "Harborview Rewards <rewards_us@members.harborview.example>",
     "subject": "Account Summary: Discounts, points and experiences await", "list": True,
     "text": ".sup {\nvertical-align: 6px !important;\nfont-size: 60% !important;\n}\n\n96\n\nHarborview Hotels\n\n"
             "Get 30% off points. Purchase now through October 22, 2026. Transactions must be completed between "
             "12 AM EDT Sep 7, 2026 and 11:59 PM EDT Oct 22, 2026 to be eligible for this discount offer.",
     "html": '<!--[if mso | ie]><style>.sup { vertical-align: 6px !important; }</style><![endif]-->'
             '<p>Get 30% off points. Purchase now through October 22, 2026.</p><p>Transactions must be completed '
             'between 12 AM EDT Sep 7, 2026 and 11:59 PM EDT Oct 22, 2026 to be eligible for this discount offer.</p>'
             '<a href="https://harborview.example/buy-points">Buy points</a>',
     "expected": {"category": "promotions", "needs_reply": False, "deadline": None}},
    # Points expiring: the account is the reader's, but nothing is owed and no one asked for
    # anything. It is the programme's nudge to spend, so it is not a deadline.
    {"id": "points-expire", "tests": "Loyalty points about to expire: a retention nudge, not a deadline",
     "from": "SkyWays Miles <no-reply@miles.skyways.example>", "subject": "Your 12,450 miles expire November 30, 2026",
     "list": True, "noreply": True,
     "text": "Your 12,450 SkyWays miles expire on November 30, 2026. Earn or redeem miles before then to keep them "
             "active: book a flight, shop with our partners or donate your miles.",
     "expected": {"category": ["promotions", "updates"], "needs_reply": False, "deadline": None}},
    {"id": "card-statement", "tests": "A card statement with an offer inside it: the payment date is the deadline",
     "from": "Card Services <statements@mail.examplecard.example>", "subject": "Your Mastercard statement is ready",
     "list": True, "noreply": True,
     "text": "Your statement for the account ending 4417 is ready.\nNew balance: $412.88\nMinimum payment due: $25.00\n"
             "Payment due date: November 3, 2026\n\nThis month only: 5% back on dining. Offer ends October 31, 2026.",
     "expected": {"category": "updates", "needs_reply": False, "deadline": "November 3, 2026"}},
    # An earning period: the activation is done and nothing is due by Dec. 31. The sender also
    # sends real bills, so only the wording can tell.
    {"id": "cashback-period", "tests": "A cash-back period ('now-Dec. 31') after activation: no deadline",
     "from": "Card Rewards <rewards@card-e.em.examplecard.example>", "esp": True,
     "subject": "5% cash back activation confirmed, Alex: You're earning at Restaurants, Entertainment and Utilities",
     "text": "You're earning 5% cash back at Restaurants, Entertainment and Utilities\n\nNow-Dec. 31, on up to $1,500 in "
             "purchases\n\nYour activation is confirmed. There is nothing else you need to do.",
     "expected": {"category": ["promotions", "updates"], "needs_reply": False, "deadline": None}},
    {"id": "conference-register", "tests": "A conference you have not signed up for: 'register by' is no deadline",
     "from": "AI in Business Summit <events@aibizsummit.example>", "subject": "Early-bird registration closes October 15",
     "list": True,
     "text": "Join 2,000 leaders at the AI in Business Summit, November 12-13, 2026 in Chicago. Register by October 15, "
             "2026 to save $300 with the early-bird rate. Sessions on agents, governance and analytics.",
     "expected": {"category": ["promotions", "updates"], "needs_reply": False, "deadline": None}},
    {"id": "form-by-friday", "tests": "A colleague asks you to return a form by a date: a deadline and a reply",
     "from": "Marcus Bell <marcus.bell@example.edu>", "subject": "Travel reimbursement form",
     "text": "Hi,\n\nCould you fill in the attached travel reimbursement form for the Denver trip and return it to me "
             "by Friday, October 9? Finance closes the quarter the week after.\n\nThanks,\nMarcus",
     "expected": {"category": "primary", "needs_reply": True, "deadline": "October 9"}},
]


def raw_for(s: dict[str, Any], to: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = s["from"]
    msg["To"] = to
    msg["Subject"] = s["subject"]
    msg["Date"] = format_datetime(datetime.now(UTC))
    msg["Message-ID"] = f"<lab-{s['id']}-{uuid.uuid4().hex[:8]}@lab.mailr.invalid>"
    if s.get("esp"):  # sent through a bulk-mail service, with no unsubscribe header
        msg["Feedback-ID"] = "80372:40099525:esp"
    if s.get("list"):
        msg["List-Id"] = f"<{s['id']}.lists.example>"
        msg["List-Unsubscribe"] = "<https://lists.example/unsubscribe>"
    msg.set_content(s["text"])
    if s.get("html"):
        msg.add_alternative(f"<html><body>{s['html']}</body></html>", subtype="html")
    return msg.as_bytes()
