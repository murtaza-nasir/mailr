"""The constraints of approval rules (core/permissions.py `constraints_match`): each kind of
constraint, with a request it lets through and one it refuses, for `allow` rules (every value
must be listed) and `deny`/`ask` rules (any listed value matches). A constraint that never refused
would fail the refusal rows here."""

from datetime import UTC, datetime, timedelta

import pytest

from mailr.core import permissions
from mailr.db import sessionmaker
from mailr.models import Identity, Outbox, User

pytestmark = pytest.mark.db_user

ME, ALICE, BOB = "me@example.com", "alice@acme.example", "bob@other.example"

# (constraint, value, effect, a request that matches, a request that does not). The action is
# "mail.send" unless the row names another.
CASES = [
    ("replies_only", True, "allow",
     {"reply": True, "recipients": [ALICE], "participants": [ALICE.upper(), ME]},
     {"reply": True, "recipients": [ALICE, BOB], "participants": [ALICE, ME]}),
    ("replies_only (not a reply)", True, "allow",
     {"reply": True, "recipients": [ALICE], "participants": [ALICE]},
     {"reply": False, "recipients": [ALICE], "participants": [ALICE]}),
    ("replies_only (no recipients)", True, "allow",
     {"reply": True, "recipients": [ALICE], "participants": [ALICE]},
     {"reply": True, "recipients": [], "participants": [ALICE]}),
    ("known_only", True, "allow", {"unknown_recipients": []}, {"unknown_recipients": [BOB]}),
    ("domains", ["acme.example"], "allow", {"recipients": [ALICE, "c@sub.acme.example"]}, {"recipients": [ALICE, BOB]}),
    ("domains (deny: any)", ["acme.example"], "deny", {"recipients": [BOB, ALICE]}, {"recipients": [BOB]}),
    ("domains (no recipients)", ["acme.example"], "allow", {"recipients": [ALICE]}, {"recipients": []}),
    ("domains (look-alike)", ["acme.example"], "allow", {"recipients": [ALICE]}, {"recipients": ["x@evilacme.example"]}),
    ("recipients", [ALICE.upper()], "allow", {"recipients": [ALICE]}, {"recipients": [ALICE, BOB]}),
    ("recipients (ask: any)", [ALICE], "ask", {"recipients": [BOB, ALICE]}, {"recipients": [BOB]}),
    ("recipients (none)", [ALICE], "deny", {"recipients": [ALICE]}, {"recipients": []}),
    ("identities", [7], "allow", {"identity_id": 7}, {"identity_id": 8}),
    ("identities (none given)", [7], "allow", {"identity_id": 7}, {}),
    ("connectors", [1, 2], "allow", {"connectors": [1, 2]}, {"connectors": [1, 3]}),
    ("connectors (deny: any)", [1], "deny", {"connectors": [3, 1]}, {"connectors": [3]}),
    ("labels", [10, 11], "allow", {"labels": [10]}, {"labels": [10, 12]}),
    ("labels (ask: any)", [10], "ask", {"labels": [12, 10]}, {"labels": [12]}),
    ("labels (none)", [10], "deny", {"labels": [10]}, {"labels": []}),
    ("labels (notes, by name)", ["Work"], "allow", {"labels": ["work"]}, {"labels": ["work", "home"]}, "notes.write"),
    ("calendars", ["/cal/a/"], "allow", {"calendar": "/cal/a/"}, {"calendar": "/cal/a/", "calendars": ["/cal/b/"]}),
    ("calendars (none)", ["/cal/a/"], "deny", {"calendars": ["/cal/a/"]}, {}),
    ("lists", ["L1"], "allow", {"list": "L1"}, {"list": "L2"}),
    ("lists (ask: any)", ["L1"], "ask", {"lists": ["L2", "L1"]}, {"lists": ["L2"]}),
    ("notes", ["n_1"], "allow", {"notes": ["n_1"]}, {"notes": ["n_1", "n_2"]}),
    ("tags", ["Advising"], "allow", {"tags": ["advising"]}, {"tags": ["advising", "work"]}, "speakr.write"),
]


async def _actor(user_id):
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        return permissions.Actor.assistant(u, None)


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
async def test_each_constraint_matches_and_refuses(user, case):
    name, val, effect, hit, miss, *rest = case
    key = name.split(" ")[0]
    action = rest[0] if rest else "mail.send"
    actor = await _actor(user.id)
    async with sessionmaker()() as db:
        assert await permissions.constraints_match(db, actor, action, {key: val}, hit, effect=effect), (name, hit)
        assert not await permissions.constraints_match(db, actor, action, {key: val}, miss, effect=effect), (name, miss)


async def test_unset_constraints_are_skipped_and_zero_is_a_limit(user):
    actor = await _actor(user.id)
    async with sessionmaker()() as db:
        for val in (None, False, "", []):
            assert await permissions.constraints_match(db, actor, "mail.send", {"recipients": val}, {"recipients": [BOB]})
        assert await permissions.constraints_match(db, actor, "mail.send", {}, {"recipients": [BOB]})
        # 0 is a real limit: no sends at all.
        assert not await permissions.constraints_match(db, actor, "mail.send", {"max_per_hour": 0}, {})
        assert not await permissions.constraints_match(db, actor, "mail.send", {"max_per_day": 0}, {})


async def test_every_constraint_must_hold(user):
    """One rule with several constraints: a request that meets all but one is refused."""
    actor = await _actor(user.id)
    cons = {"domains": ["acme.example"], "identities": [7], "labels": [10]}
    ok = {"recipients": [ALICE], "identity_id": 7, "labels": [10]}
    async with sessionmaker()() as db:
        assert await permissions.constraints_match(db, actor, "mail.send", cons, ok)
        for k, v in (("recipients", [BOB]), ("identity_id", 8), ("labels", [11])):
            assert not await permissions.constraints_match(db, actor, "mail.send", cons, {**ok, k: v}), k


@pytest.mark.parametrize("key, window", [("max_per_hour", timedelta(hours=1)), ("max_per_day", timedelta(days=1))])
async def test_send_limits_count_this_levels_recent_sends(user, key, window):
    """The limit counts the sends of the rule's level inside the window: queued, sending and sent;
    not older ones, not failed or cancelled ones, not the user's own."""
    actor = await _actor(user.id)
    now = datetime.now(UTC)
    async with sessionmaker()() as db:
        ident = Identity(user_id=user.id, email=ME)
        db.add(ident)
        await db.flush()

        def out(subject="assistant", status="sent", age=timedelta(0)):
            return Outbox(user_id=user.id, identity_id=ident.id, subject=subject, payload={}, recipients=[ALICE],
                          send_at=now, status=status, created_at=now - age)

        db.add_all([out(), out(status="queued"),
                    out(status="failed"), out(status="cancelled"),     # not counted
                    out(age=window + timedelta(minutes=5)),             # outside the window
                    out(subject="user")])                               # the user's own send
        await db.commit()
    async with sessionmaker()() as db:
        assert await permissions.constraints_match(db, actor, "mail.send", {key: 3}, {}, level="assistant")
        assert not await permissions.constraints_match(db, actor, "mail.send", {key: 2}, {}, level="assistant")
        # Another level's limit counts only its own sends: a token never sent anything.
        assert await permissions.constraints_match(db, actor, "mail.send", {key: 1}, {}, level="token:999999")
        # "*" counts every agent send, never the user's.
        assert not await permissions.constraints_match(db, actor, "mail.send", {key: 2}, {}, level="*")
        assert await permissions.constraints_match(db, actor, "mail.send", {key: 3}, {}, level="*")
