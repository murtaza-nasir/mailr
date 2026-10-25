"""Work counters for tests that bound cost: they count what the code does (periods a repeat rule
scans, cells a diff fills, bytes read) instead of timing it, so other workers' load cannot fail
them. A counter with a `limit` stops the work at once when it passes the limit, so a test of a
removed guard fails in a moment instead of running for minutes."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class TooMuchWork(BaseException):
    """Raised inside the code under test when a counter passes its limit. A BaseException, so the
    code's own `except Exception` cannot swallow it."""


class Counter:
    def __init__(self, limit: int | None = None) -> None:
        self.n = 0
        self.limit = limit
        self._lock = threading.Lock()

    def add(self, k: int = 1) -> None:
        with self._lock:
            self.n += k
            n = self.n
        if self.limit is not None and n > self.limit:
            raise TooMuchWork(f"more than {self.limit} units of work")


@contextmanager
def rrule_periods(limit: int | None = None) -> Iterator[Counter]:
    """Counts the periods dateutil's rrule scans (one per pass of its main loop: a year, a month,
    a day, ... depending on FREQ), in every thread. Zone lookups, recurrence expansion and the
    zone checks all walk rules this way, and a rule's cost is the number of periods it scans, not
    the occurrences it yields: a rule that never matches scans to the year 9999 and yields
    nothing."""
    from dateutil import rrule

    counter = Counter(limit)
    names = ("ydayset", "mdayset", "wdayset", "ddayset")
    saved = {n: getattr(rrule._iterinfo, n) for n in names}

    def wrap(fn):
        def counted(self, *a, **kw):
            counter.add()
            return fn(self, *a, **kw)

        return counted

    for n, fn in saved.items():
        setattr(rrule._iterinfo, n, wrap(fn))
    try:
        yield counter
    finally:
        for n, fn in saved.items():
            setattr(rrule._iterinfo, n, fn)
