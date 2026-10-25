"""Calendar background jobs. The handlers live in calendar/sync.py (`calendar.sync`,
`calendar.check`, `calendar.rematerialize`), calendar/planner.py (`reminders.fire`) and
calendar/inbound.py (`calendar.itip_incoming`) and
calendar/migrate.py (`calendar.migrate`); importing this module registers them."""

from __future__ import annotations

import mailr.calendar.groups  # calendar.groups_reconcile
import mailr.calendar.inbound
import mailr.calendar.migrate  # calendar.migrate
import mailr.calendar.phones  # calendar.phone_reconcile
import mailr.calendar.planner
import mailr.calendar.sharing  # calendar.shares_cleanup
import mailr.calendar.sync
import mailr.calendar.todo  # noqa: F401  (calendar.reorder, calendar.retag)
