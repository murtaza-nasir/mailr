"""Calendars and tasks (CalDAV mirror, writer, iTIP, subscriptions).

Importing the package puts mailr's time-zone rules into icalendar's parser first
(calendar/zones.py): every parse in this package is covered.
"""

from mailr.calendar.zones import install as _install_zone_rules

_install_zone_rules()
