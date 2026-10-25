"""List previews and notifications show the body's first words as plain text (make_snippet)."""

from mailr.core.mime import html_to_text, make_snippet

AMEX = """<table><tr><td></td><td></td></tr><tr><td>See the details about this purchase</td><td></td></tr>
<tr><td><p><b>ROWAN HALE</b></p><p>Account Ending: 912005</p></td></tr><tr><td></td><td></td><td></td></tr>
<tr><td>A purchase was made on your Additional account, but it appears the Card wasn't present</td></tr></table>"""


def test_tables_become_words():
    s = make_snippet(html_to_text(AMEX))
    assert "|" not in s
    assert s.startswith("See the details about this purchase ROWAN HALE Account Ending: 912005 A purchase")


def test_headings_links_rules_and_padding_go():
    s = make_snippet("͏ ‌͏ # Your payment is due\n\nPay [now](https://x.example/abc) or see "
                     "<https://wf.example/a>.\n\n* * *\n\n* item one\n> quoted")
    assert s == "Your payment is due Pay now or see. item one quoted"


def test_plain_text_is_left_alone():
    assert make_snippet("Meet at 3-4pm in room 5. Cost: 5 - 3 = 2, ok?") == "Meet at 3-4pm in room 5. Cost: 5 - 3 = 2, ok?"
    assert len(make_snippet("word " * 100)) == 200


def test_dot_leaders_go():
    assert make_snippet("You have 2 new messages ...................... Abdullah") == "You have 2 new messages Abdullah"
    assert make_snippet("Wait... what?") == "Wait... what?"


# The shape of a hotel-loyalty promotion: its plain part opens with the sender's stylesheet and
# HTML attribute residue, and its HTML hides a <style> in an Outlook conditional comment.
PROMO_PLAIN = ('.sup {\nvertical-align: 6px !important;\nfont-size: 60% !important;\n}\n\n96\n\nChoice Hotels\n*\n\n#\n\n'
               '(https://www.choicehotels.example/choice-privileges/account)" style="color: #5e5745; font-size: 10px" '
               'target="_blank" data-link-name="AccountAccount\n\n. Terms&nbsp;apply.\n\nTransactions must be completed '
               'between 12 AM EDT Sep 7, 2026 and 11:59 PM EDT Oct 22, 2026 to be eligible for this discount offer.')
PROMO_HTML = ('<html><head></head><!--Empty Head for Yahoo--><head><meta name="viewport" content="width=device-width" />'
              '<!--[if mso | ie]><style>.sup { vertical-align: 6px !important; font-size: 60% !important; }</style><![endif]-->'
              '<style type="text/css">@media screen and (max-width: 600px) { .col { width: 100% !important; } }</style>'
              '<title>Choice Privileges</title></head><body><script>var t = 1;</script>'
              '<p>Get 30% off points. Purchase now through October 22, 2026.</p></body></html>')


def _promo_raw(with_html=True):
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "Rewards <r@hotel.example>", "me@example.com", "Account Summary"
    msg.set_content(PROMO_PLAIN)
    if with_html:
        msg.add_alternative(PROMO_HTML, subtype="html")
    return msg.as_bytes()


def test_css_never_reaches_a_snippet():
    from mailr.core.mime import parse

    p = parse(_promo_raw())
    assert p.snippet == "Get 30% off points. Purchase now through October 22, 2026."
    assert "{" not in p.body_text and "vertical-align" not in p.body_text
    # Without an HTML part the plain text loses its stylesheet and attribute residue.
    s = parse(_promo_raw(with_html=False)).snippet
    assert s.startswith("96 Choice Hotels. Terms apply. Transactions must be completed")
    assert "{" not in s and "style=" not in s and "&nbsp;" not in s
    # A stored snippet that already starts with CSS is cleaned too.
    assert make_snippet(".sup { vertical-align: 6px !important; font-size: 60% !important; } 96 Choice Hotels") \
        == "96 Choice Hotels"


def test_style_script_and_head_stay_out_of_text():
    t = html_to_text(PROMO_HTML)
    assert t.strip() == "Get 30% off points. Purchase now through October 22, 2026."
    assert html_to_text("<p>@media screen { .x { color: red; } }</p><p>Hello there</p>").strip() == "Hello there"
    # Braces in a sentence are not CSS.
    assert make_snippet("Use the dict {a: 1} here. Meet {at noon}.") == "Use the dict {a: 1} here. Meet {at noon}."
