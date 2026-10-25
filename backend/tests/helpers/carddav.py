"""Shared helpers, from test_carddav.py."""

import httpx
from dev_services import RADICALE

ALICE = """BEGIN:VCARD\r
VERSION:4.0\r
UID:alice-uid\r
FN:Alice Smith\r
N:Smith;Alice;;Dr.;\r
EMAIL;TYPE=work:alice@ab.example\r
TEL;TYPE=cell:+1 202 555 0101\r
ADR;TYPE=work:;;100 Main St;Anytown;ST;00000;USA\r
ORG:Northwind Traders;Harbor Logistics\r
TITLE:Operations Manager\r
BDAY:19800214\r
CATEGORIES:Board\r
END:VCARD\r
"""


BOB = """BEGIN:VCARD\r
VERSION:3.0\r
UID:bob-uid\r
FN:Bob Jones\r
item1.EMAIL;type=INTERNET;type=pref:Bob@AB.example\r
item1.X-ABLabel:_$!<Other>!$_\r
TEL;TYPE=WORK,VOICE:(202) 555-0102\r
CATEGORIES:Board,Friends\r
NOTE:a long note that \r
 is folded\r
END:VCARD\r
"""


def _mkbook(username: str, slug: str = "contacts") -> str:
    url = f"{RADICALE}{username}/{slug}/"
    body = ('<?xml version="1.0"?><d:mkcol xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav"><d:set><d:prop>'
            '<d:resourcetype><d:collection/><c:addressbook/></d:resourcetype><d:displayname>Contacts</d:displayname>'
            '</d:prop></d:set></d:mkcol>')
    r = httpx.request("MKCOL", url, content=body, auth=(username, "x"), headers={"Content-Type": "application/xml"})
    assert r.status_code == 201, r.text
    return url


def _put(book: str, username: str, name: str, card: str) -> None:
    r = httpx.put(f"{book}{name}.vcf", content=card.encode(), auth=(username, "x"),
                  headers={"Content-Type": "text/vcard; charset=utf-8"})
    assert r.status_code in (201, 204), r.text
