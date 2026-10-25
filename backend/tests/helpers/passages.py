"""Shared helpers, from test_passages.py."""

from mailr.db import sessionmaker
from mailr.models import Message, User


async def _process(mid):
    from mailr.core import outbound
    from mailr.intel.tasks import process_message

    with outbound.scope():  # as in a job (core/jobs.run_one)
        async with sessionmaker()() as db:
            m = await db.get(Message, mid)
            u = await db.get(User, m.user_id)
            await process_message(db, m, u, allow_llm=False)
            await db.commit()


LONG = ("The committee met on Tuesday to review the budget. " * 8 + "\n\n"
        "Nominations for the OEA and DSA awards close on September 30, while paper submissions are due "
        "October 4. " + "Please share this with your department. " * 6 + "\n\n"
        + ("Travel grants are available for doctoral students presenting at the meeting. " * 12))
