"""Spam and Trash hide triage signals.

A message in Spam or Trash shows no Reply needed / Waiting / Due chip, counts in no tab's Due
indicator, joins no smart list (Needs reply, Waiting on, Deadline), and is left out of the
assistant's signals and routines. Its decisions stay stored: moving it back out of Spam or Trash
brings its chips back. Searches that ask for Spam or Trash (`in:spam`, `in:trash`, `in:anywhere`)
still find its signals.

"Newest message" in the signal rules means the newest message that is in neither Spam nor Trash.
"""

from __future__ import annotations

HIDDEN_ROLES = ("junk", "trash")


def live_sql(alias: str = "m") -> str:
    """SQL condition: the message `alias` (a messages row) is in neither Spam nor Trash."""
    return (f"NOT EXISTS (SELECT 1 FROM labels hl WHERE hl.id = ANY({alias}.label_ids) "
            "AND hl.role IN ('junk', 'trash'))")
