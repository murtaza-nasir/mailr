"""Shared helpers, from test_security_send_path.py."""

from mailr.db import session_scope
from mailr.models import Connector, Identity, Person


async def _ident(user, known=("known@else.org",)):
    async with session_scope() as db:
        c = Connector(user_id=user.id, kind="imap", name="acct", mode="mirror", config={"smtp": {"host": "x"}},
                      state={}, enabled=False)
        db.add(c)
        await db.flush()
        db.add(Identity(user_id=user.id, email="me@example.com", name="Me", connector_id=c.id, is_default=True))
        for k in known:
            db.add(Person(user_id=user.id, email=k, sent_count=3, received_count=3))
