"""Read-only CardDAV mirror: address books served by the user's CalDAV server.

The CalDAV connector's URL and credentials are reused; there is no CardDAV connector of its own.
Discovery: the principal's `addressbook-home-set`, else `/.well-known/carddav` on the same
origin. Each address book is synced like a calendar collection: RFC 6578 sync-collection with the
stored token when the server supports it, else an ETag listing; changed cards come in with
addressbook-multiget. Cards are kept parsed in `contact_cards`, and People are merged from them
(`merge`). mailr sends PROPFIND and REPORT only: nothing is ever written to the address book.
One exception, on mailr's own store only: when the user's address-book home there holds no address
book, mailr makes one named "Contacts" (`ensure_own_book`): while phone access is on,
Stalwart no longer makes it by itself (core/store_groupware.ensure_phone_settings).

Merge rules: People are matched by email address. A value set in the address book (name,
organization, title, birthday) wins over a value from mail, and its provenance names the card.
Phones and postal addresses from the card replace the earlier card values and sit next to the
ones the user accepted from signatures. When a card goes away, its values go with it. Groups come
from group cards (vCard 4 KIND:group and MEMBER, Apple's X-ADDRESSBOOKSERVER-KIND and -MEMBER) and
from CATEGORIES; they are read-only in mailr.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import dav
from mailr.contacts import vcard
from mailr.core.crypto import connector_context, decrypt_for_user
from mailr.core.netguard import UnsafeTarget
from mailr.core.people import org_from_email
from mailr.db import session_scope
from mailr.models import (
    AddressBook,
    Connector,
    ContactCard,
    Person,
    PersonGroup,
    PersonGroupMember,
    User,
)

log = logging.getLogger("mailr.contacts.carddav")

D, CARD, CS = "DAV:", "urn:ietf:params:xml:ns:carddav", "http://calendarserver.org/ns/"
ET.register_namespace("card", CARD)
SINGLE_FIELDS = ("name", "organization", "title", "birthday")


def _t(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def _propfind(*props: tuple[str, str]) -> bytes:
    root = ET.Element(_t(D, "propfind"))
    prop = ET.SubElement(root, _t(D, "prop"))
    for ns, local in props:
        ET.SubElement(prop, _t(ns, local))
    return ET.tostring(root, xml_declaration=True, encoding="utf-8")


def _ok(resp: ET.Element) -> ET.Element:
    merged = ET.Element(_t(D, "prop"))
    for ps in resp.findall(_t(D, "propstat")):
        st = ps.find(_t(D, "status"))
        if st is None or " 200" in (st.text or ""):
            for p in ps.findall(_t(D, "prop")):
                merged.extend(list(p))
    return merged


def _txt(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


class CardDavClient(dav.DavClient):
    """The CalDAV client's transport (outbound guard on every hop, manual redirects) with the
    CardDAV reads on top. Only PROPFIND, REPORT and OPTIONS are sent."""

    async def _href(self, url: str, ns: str, local: str) -> str | None:
        ms = await self._multistatus("PROPFIND", url, _propfind((ns, local)), "0")
        for resp in ms.findall(_t(D, "response")):
            el = _ok(resp).find(f"{_t(ns, local)}/{_t(D, 'href')}")
            if _txt(el):
                return self.abs(_txt(el))
        return None

    async def addressbook_home(self) -> str | None:
        """The address-book home set: from the principal of the base URL, else through
        /.well-known/carddav. None when the server serves no address books."""
        for start in (self.base, urljoin(self.base, "/.well-known/carddav")):
            try:
                principal = await self._href(start, D, "current-user-principal") or start
                home = await self._href(principal, CARD, "addressbook-home-set")
            except dav.Unauthorized:
                raise
            except dav.DavError:
                continue
            if home:
                return home
        return None

    async def books(self, home: str) -> list[dav.CollectionInfo]:
        ms = await self._multistatus("PROPFIND", home, _propfind(
            (D, "resourcetype"), (D, "displayname"), (CS, "getctag"), (D, "sync-token")), "1")
        out = []
        for resp in ms.findall(_t(D, "response")):
            p = _ok(resp)
            rt = p.find(_t(D, "resourcetype"))
            if rt is None or rt.find(_t(CARD, "addressbook")) is None:
                continue
            out.append(dav.CollectionInfo(href=self.abs(_txt(resp.find(_t(D, "href")))),
                                          displayname=_txt(p.find(_t(D, "displayname"))),
                                          ctag=_txt(p.find(_t(CS, "getctag"))) or None,
                                          sync_token=_txt(p.find(_t(D, "sync-token"))) or None))
        return out

    async def multiget_cards(self, href: str, hrefs: list[str], version: str | None = None
                             ) -> list[tuple[str, str | None, str]]:
        """addressbook-multiget in batches: [(href, etag, card)]. `version` asks for that vCard
        version (RFC 6352 address-data; Sabre/Davis converts, other servers may ignore it)."""
        out = []
        for i in range(0, len(hrefs), dav.MULTIGET_BATCH):
            root = ET.Element(_t(CARD, "addressbook-multiget"))
            prop = ET.SubElement(root, _t(D, "prop"))
            ET.SubElement(prop, _t(D, "getetag"))
            ET.SubElement(prop, _t(CARD, "address-data"),
                          {"content-type": "text/vcard", "version": version} if version else {})
            for h in hrefs[i:i + dav.MULTIGET_BATCH]:
                ET.SubElement(root, _t(D, "href")).text = urlsplit(self.abs(h)).path
            ms = await self._multistatus("REPORT", href, ET.tostring(root, xml_declaration=True, encoding="utf-8"), "1")
            for resp in ms.findall(_t(D, "response")):
                p = _ok(resp)
                data = p.find(_t(CARD, "address-data"))
                if data is None or not (data.text or "").strip():
                    continue
                out.append((self.abs(_txt(resp.find(_t(D, "href")))), _txt(p.find(_t(D, "getetag"))) or None, data.text))
        return out


OWN_BOOK_SLUG = "default"
OWN_BOOK_NAME = "Contacts"


async def ensure_own_book(client: CardDavClient, home: str, infos: list[dav.CollectionInfo]) -> bool:
    """A built-in connector's user has no address book of their own: make "Contacts" (extended
    MKCOL, RFC 5689) in their home. Books under other homes (shared ones) do not count. Returns
    True when it made one."""
    if any(client.abs(i.href).startswith(client.abs(home)) for i in infos):
        return False
    root = ET.Element(_t(D, "mkcol"))
    prop = ET.SubElement(ET.SubElement(root, _t(D, "set")), _t(D, "prop"))
    rt = ET.SubElement(prop, _t(D, "resourcetype"))
    ET.SubElement(rt, _t(D, "collection"))
    ET.SubElement(rt, _t(CARD, "addressbook"))
    ET.SubElement(prop, _t(D, "displayname")).text = OWN_BOOK_NAME
    href = client.abs(urljoin(home if home.endswith("/") else home + "/", OWN_BOOK_SLUG + "/"))
    resp = await client.request("MKCOL", href, body=ET.tostring(root, xml_declaration=True, encoding="utf-8"),
                                headers={"Content-Type": "application/xml; charset=utf-8"})
    if resp.status_code == 405:  # made meanwhile
        return False
    client._raise(resp, f"MKCOL {href}")
    log.info("address book %s made", href)
    return True


def client_for(conn: Connector, user: User) -> CardDavClient:
    if conn.builtin:  # mailr's own store, signed in like the built-in calendar (calendar/builtin.py)
        from mailr.calendar import builtin
        from mailr.config import get_settings

        s = get_settings()
        account = (conn.config or {}).get("account") or ""
        return CardDavClient(builtin.dav_url(), f"{account}%{s.stalwart_admin_user}", s.stalwart_admin_password,
                             internal=True, limiter=builtin.limiter(conn.id))
    secrets = decrypt_for_user(user.id, user.data_key_wrapped, conn.secrets_enc,
                               context=connector_context(conn.id)) or {}
    from mailr.core.netguard import private_ok_for

    return CardDavClient(conn.config.get("url") or "", conn.config.get("username") or "", secrets.get("password", ""),
                         private_ok=private_ok_for(user))


async def _set_state(db: AsyncSession, conn_id: int, state: dict[str, Any]) -> None:
    # Only the carddav key: the calendar sync owns the rest of the connector state.
    await db.execute(text("UPDATE connectors SET state = jsonb_set(coalesce(state, '{}'), '{carddav}', CAST(:v AS jsonb)) "
                          "WHERE id=:c"), {"c": conn_id, "v": json.dumps(state, default=str)})


# ---------------- sync ----------------


async def sync_connector(connector_id: int, *, full: bool = False) -> dict[str, Any]:
    """Sync every address book of one CalDAV connector, then merge People. Returns the state
    written to connectors.state.carddav (available, books, cards, error)."""
    async with session_scope() as db:
        conn = await db.get(Connector, connector_id)
        if conn is None or conn.kind != "caldav" or not conn.enabled:
            return {"available": False, "error": "no calendar account"}
        user = await db.get(User, conn.user_id)
        client = client_for(conn, user)
        prev = dict((conn.state or {}).get("carddav") or {})
        user_id = conn.user_id
        builtin_conn = bool(conn.builtin)
    now = datetime.now(UTC).isoformat()
    changed = False
    async with client:
        try:
            home = prev.get("home") if not full else None
            infos = None
            if home:
                try:
                    infos = await client.books(home)
                except dav.DavError:
                    infos = None
            if not infos:
                home = await client.addressbook_home()
                infos = await client.books(home) if home else []
            if builtin_conn and home:
                try:
                    if await ensure_own_book(client, home, infos):
                        infos = await client.books(home)
                except dav.DavError as e:  # the sync of the other books goes on; the next run tries again
                    log.warning("could not make the address book of connector %s: %s", connector_id, e)
        except dav.Unauthorized:
            st = {**prev, "available": False, "error": "Sign-in failed.", "checked_at": now}
            async with session_scope() as db:
                await _set_state(db, connector_id, st)
            return st
        except (dav.DavError, UnsafeTarget) as e:
            st = {**prev, "available": False, "error": str(e)[:300], "checked_at": now}
            async with session_scope() as db:
                await _set_state(db, connector_id, st)
            return st
        async with session_scope() as db:
            rows = {b.href: b for b in (await db.execute(select(AddressBook).where(
                AddressBook.connector_id == connector_id))).scalars()}
            for href, b in rows.items():
                if href not in {i.href for i in infos}:
                    await db.delete(b)
                    changed = True
            ids: dict[str, int] = {}
            for info in infos:
                b = rows.get(info.href)
                if b is None:
                    b = AddressBook(user_id=user_id, connector_id=connector_id, href=info.href)
                    db.add(b)
                b.displayname = info.displayname or ""
                await db.flush()
                ids[info.href] = b.id
        errors = []
        for info in infos:
            try:
                changed = await _sync_book(client, ids[info.href], info, full, user_id, connector_id) or changed
            except Exception as e:  # noqa: BLE001  one book failing must not stop the others
                log.warning("address book sync of %s failed: %s", info.href, e)
                errors.append(str(e)[:200])
                async with session_scope() as db:
                    b = await db.get(AddressBook, ids[info.href])
                    if b is not None:
                        b.error = str(e)[:500]
    async with session_scope() as db:
        if changed or not prev.get("merged"):
            await merge(db, user_id)
        cards = (await db.execute(text("SELECT count(*) FROM contact_cards WHERE connector_id=:c AND kind='individual'"),
                                  {"c": connector_id})).scalar() or 0
        groups = (await db.execute(text("SELECT count(*) FROM person_groups WHERE connector_id=:c"),
                                   {"c": connector_id})).scalar() or 0
        st = {"available": bool(home), "home": home, "books": len(infos), "cards": cards, "groups": groups,
              "error": "; ".join(errors), "checked_at": now, "merged": True}
        await _set_state(db, connector_id, st)
    return st


async def _sync_book(client: CardDavClient, book_id: int, info: dav.CollectionInfo, full: bool,
                     user_id: int, connector_id: int) -> bool:
    async with session_scope() as db:
        book = await db.get(AddressBook, book_id)
        token, ctag = book.sync_token, book.ctag
        rows_ = (await db.execute(select(ContactCard.href, ContactCard.etag, ContactCard.vcard.is_(None)).where(
            ContactCard.book_id == book_id))).all()
        known = {h: e for h, e, _ in rows_}
        # Cards read before the mirror kept the card itself: fetched again for the export.
        no_raw = [h for h, _, missing in rows_ if missing]
    if not full and known and not no_raw and (
            (info.sync_token and info.sync_token == token) or (info.ctag and info.ctag == ctag)):
        return False
    changed_hrefs: list[str] = []
    removed: list[str] = []
    new_token = None
    use_listing = full
    if not use_listing:
        try:
            tok = token if known else None
            ch, rm, new_token, truncated = await client.sync_collection(info.href, tok)
            changed_hrefs = [h for h, etag in ch if known.get(h) != etag or etag is None]
            removed = list(rm)
            if truncated:
                # Never complete: Stalwart sends its current token with it, so the cards past its
                # limit would never come in a later delta. The ETag listing decides.
                use_listing = True
            elif tok is None:
                # A sync from no token lists everything: what is not listed is gone.
                listed = {h for h, _ in ch}
                removed += [h for h in known if h not in listed]
            elif not ch and not rm and ctag and info.ctag and info.ctag != ctag:
                use_listing = True  # the token was taken, yet the book changed with no delta
        except dav.InvalidSyncToken:
            use_listing = True
        except dav.DavError:
            use_listing = True
    if use_listing:
        try:
            etags = await client.etags(info.href)
        except dav.Truncated:
            # Nothing is removed and the old token stays; the next sync tries again.
            raise dav.Truncated("The address book server listed only part of this address book; "
                                "nothing was removed", 507) from None
        changed_hrefs = [h for h, e in etags.items() if known.get(h) != e or e is None]
        removed = [h for h in known if h not in etags]
        new_token = info.sync_token
    changed_hrefs = list(dict.fromkeys(changed_hrefs + [h for h in no_raw if h not in removed]))
    got = await client.multiget_cards(info.href, changed_hrefs) if changed_hrefs else []
    async with session_scope() as db:
        if removed:
            await db.execute(delete(ContactCard).where(ContactCard.book_id == book_id, ContactCard.href.in_(removed)))
        rows = {c.href: c for c in (await db.execute(select(ContactCard).where(
            ContactCard.book_id == book_id, ContactCard.href.in_([h for h, _, _ in got])))).scalars()}
        for href, etag, data in got:
            card = vcard.parse(data)
            row = rows.get(href)
            if row is None:
                row = ContactCard(user_id=user_id, connector_id=connector_id, book_id=book_id, href=href)
                db.add(row)
            row.etag, row.uid, row.kind, row.data, row.vcard = etag, card["uid"][:500], card["kind"], card, data
            row.synced_at = datetime.now(UTC)
        book = await db.get(AddressBook, book_id)
        book.sync_token, book.ctag, book.synced_at, book.error = new_token or info.sync_token, info.ctag, datetime.now(UTC), ""
    return bool(got or removed)


# ---------------- merge into People ----------------


def _norm_phone(v: str) -> str:
    return "".join(ch for ch in v if ch.isdigit() or ch == "+")


def _norm_text(v: str) -> str:
    return " ".join(v.lower().replace(",", " ").split())


def merge_list(existing: list[dict[str, Any]], card_items: list[dict[str, Any]], key: str, prov: dict[str, Any]) -> list[dict[str, Any]]:
    """Card items replace earlier card items; items from other sources stay unless the card has the same value."""
    norm = _norm_phone if key == "value" else _norm_text
    out = [dict(x, source="carddav", connector_id=prov["connector_id"], local_only=prov["local_only"]) for x in card_items]
    seen = {norm(x.get(key, "")) for x in out}
    for x in existing or []:
        if x.get("source") == "carddav" or norm(x.get(key, "")) in seen:
            continue
        out.append(x)
        seen.add(norm(x.get(key, "")))
    return out


async def merge(db: AsyncSession, user_id: int) -> None:
    """Recompute every address-book value of a user's People and the address-book groups."""
    local = {c.id: c.data_class == "local_only" for c in (await db.execute(select(Connector).where(
        Connector.user_id == user_id, Connector.kind == "caldav"))).scalars()}
    cards = (await db.execute(select(ContactCard).where(ContactCard.user_id == user_id)
                              .order_by(ContactCard.connector_id, ContactCard.href))).scalars().all()
    by_email: dict[str, tuple[ContactCard, dict]] = {}
    for c in cards:
        if c.kind != "individual":
            continue
        for e in c.data.get("emails") or []:
            by_email.setdefault(e["email"], (c, c.data))
    ids = (await db.execute(text(
        "SELECT id FROM people WHERE user_id=:u AND (email = ANY(:e) OR provenance::text LIKE '%\"carddav\"%' "
        "OR phones::text LIKE '%\"carddav\"%' OR addresses::text LIKE '%\"carddav\"%')"),
        {"u": user_id, "e": list(by_email)})).scalars().all()
    people = {p.email: p for p in (await db.execute(select(Person).where(Person.id.in_(ids)))).scalars()}
    for email, (c, d) in by_email.items():
        p = people.get(email)
        if p is None:
            p = Person(user_id=user_id, email=email, name="", organization=org_from_email(email), received_count=0,
                       sent_count=0, notes="", blocked=False, strength=0.0, phones=[], addresses=[], provenance={},
                       title="", birthday="")
            db.add(p)
            people[email] = p
        prov = {"source": "carddav", "connector_id": c.connector_id, "href": c.href,
                "local_only": local.get(c.connector_id, False)}
        pv = dict(p.provenance or {})
        values = {"name": d.get("fn") or "", "organization": d.get("organization") or "", "title": d.get("title") or "",
                  "birthday": d.get("birthday") or ""}
        for f in SINGLE_FIELDS:
            if values[f]:
                setattr(p, f, values[f][:300] if f != "birthday" else values[f][:10])
                pv[f] = prov
            elif (pv.get(f) or {}).get("source") == "carddav":
                _clear(p, f, pv)
        p.provenance = pv
        p.phones = merge_list(p.phones, [{"value": x["value"], "type": x.get("type", "")} for x in d.get("phones") or []],
                              "value", prov)
        p.addresses = merge_list(p.addresses, [{k: x.get(k, "") for k in ("text", "street", "city", "region", "postcode",
                                                                            "country", "type")}
                                               for x in d.get("addresses") or []], "text", prov)
    for email, p in people.items():
        if email in by_email:
            continue
        pv = dict(p.provenance or {})
        for f in SINGLE_FIELDS:
            if (pv.get(f) or {}).get("source") == "carddav":
                _clear(p, f, pv)
        p.provenance = pv
        p.phones = [x for x in p.phones or [] if x.get("source") != "carddav"]
        p.addresses = [x for x in p.addresses or [] if x.get("source") != "carddav"]
    await db.flush()
    await _merge_groups(db, user_id, cards)


def _clear(p: Person, f: str, pv: dict[str, Any]) -> None:
    pv.pop(f, None)
    if f == "organization":
        p.organization = org_from_email(p.email)
    elif f != "name":  # the name stays until mail brings another one
        setattr(p, f, "")


async def _merge_groups(db: AsyncSession, user_id: int, cards: list[ContactCard]) -> None:
    primary: dict[str, str] = {}  # card uid → its first email
    for c in cards:
        if c.kind == "individual" and c.data.get("emails"):
            primary[c.uid or c.href] = c.data["emails"][0]["email"]
    wanted: dict[tuple[int, str], tuple[str, set[str]]] = {}
    for c in cards:
        if c.kind == "group":
            members = set()
            for ref in c.data.get("members") or []:
                if "@" in ref:
                    members.add(ref.lower())
                elif ref in primary:
                    members.add(primary[ref])
            wanted[(c.connector_id, f"uid:{c.uid or c.href}"[:500])] = (c.data.get("fn") or "Group", members)
        elif c.data.get("emails"):
            for cat in c.data.get("categories") or []:
                key = (c.connector_id, f"cat:{cat}"[:500])
                wanted.setdefault(key, (cat, set()))[1].add(c.data["emails"][0]["email"])
    existing = {(g.connector_id, g.remote_key): g for g in (await db.execute(select(PersonGroup).where(
        PersonGroup.user_id == user_id, PersonGroup.source == "carddav"))).scalars()}
    for key, g in existing.items():
        if key not in wanted:
            await db.delete(g)
    for key, (name, members) in wanted.items():
        g = existing.get(key)
        if g is None:
            g = PersonGroup(user_id=user_id, source="carddav", state="active", connector_id=key[0], remote_key=key[1],
                            name=name[:200], evidence={})
            db.add(g)
            await db.flush()
        g.name = name[:200]
        g.updated_at = datetime.now(UTC)
        await db.execute(delete(PersonGroupMember).where(PersonGroupMember.group_id == g.id))
        for e in sorted(members):
            db.add(PersonGroupMember(group_id=g.id, email=e))
    await db.flush()


async def due_syncs() -> None:
    """Periodic: queue an address-book sync for every enabled CalDAV connector (every 30 minutes)."""
    from mailr.core.jobs import enqueue

    async with session_scope() as db:
        rows = (await db.execute(select(Connector).where(Connector.kind == "caldav", Connector.enabled.is_(True)))).scalars().all()
        for c in rows:
            st = (c.state or {}).get("caldav") or {}
            if st.get("auth_failed"):
                continue
            if c.builtin:
                from mailr.calendar import builtin

                if not await builtin.active(db, c):  # off, or the group policy leaves this user out
                    continue
            await enqueue(db, "contacts.sync", {"connector_id": c.id}, user_id=c.user_id, dedupe_key=f"contacts.sync:{c.id}")
