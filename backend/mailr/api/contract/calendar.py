"""Response models of the calendar and task reads (calendar/routes.py, calendar/routes_tasks.py).

Events and tasks come from several sources (CalDAV accounts, the built-in calendar, subscribed
links, local-only lists), so most of their fields are optional: a field is required only where
every source writes it."""

from __future__ import annotations

from pydantic import Field, RootModel

from mailr.api.contract.base import IsoTime, Out, Untyped, absent


class SyncState(Out):
    state: str | None = None
    last_sync_at: IsoTime | None = None
    error: str | None = None


class Attendee(Out):
    email: str
    name: str | None = None
    partstat: str | None = None
    role: str | None = None
    rsvp: bool | None = None
    comment: str | None = None


class Event(Out):
    """An occurrence in the agenda, with my_status and is_organizer."""

    id: int | None = None
    uid: str
    occurrence: str | None = None
    recurrence_id: str | None = None
    summary: str
    location: str | None = None
    description: str | None = None
    start: str
    end: str | None
    all_day: bool | None = None
    tz: str | None = None
    end_tz: str | None = None
    status: str | None = None
    transparent: bool | None = None
    class_: str | None = absent(alias="class")
    color: str | None = None
    calendar_color: str | None = None
    categories: list[str] | None = None
    url: str | None = None
    sequence: int | None = None
    etag: str | None = None
    recurring: bool | None = None
    is_override: bool | None = None
    rrule: str | None = None
    organizer: str | None = None
    organizer_name: str | None = None
    attendees: int | None = None
    attendee_list: list[Attendee] | None = None
    reminders: list[int] | None = None
    no_reminders: bool | None = None
    calendar: str | None = None
    calendar_id: int | None = None
    connector_id: int | None = None
    href: str | None = None
    local_only: bool | None = None
    my_status: str | None = absent(description="My answer as a guest (ACCEPTED, NEEDS-ACTION…), else null.")
    is_organizer: bool | None = None
    conversation_id: int | None = absent(description="The email the event came from, when it has one.")


class Agenda(Out):
    """GET /calendar/agenda: the occurrences between two times."""

    from_: IsoTime = Field(alias="from")
    to: IsoTime
    connected: bool
    events: list[Event]
    sync: SyncState


class CollectionSharing(Out):
    people: int
    others: int


class Collection(Out):
    """A calendar or task list."""

    id: str
    url: str | None = None
    calendar_id: int | None = None
    connector_id: int
    name: str
    description: str | None = None
    color: str | None = None
    color_local: bool | None = None
    default_reminders: list[int] | dict[str, list[int]] | None = None
    events: bool | None = None
    tasks: bool | None = None
    components: list[str] | None = None
    read_only: bool | None = None
    tz: str | None = None
    hidden: bool | None = None
    busy: bool | None = None
    notify: bool | None = None
    sort_order: int | None = None
    local_only: bool | None = None
    shared: Untyped | None = None
    sharing: CollectionSharing | None = None
    shareable: bool | None = None
    subscription: Untyped | None = None
    sync: SyncState | None = None
    error: str | None = None


class CollectionList(RootModel[list[Collection]]):
    """GET /calendar/calendars."""


class Task(Out):
    """A task."""

    id: int | None = None
    uid: str
    summary: str
    notes: str | None = None
    status: str
    completed: bool | None = None
    completed_at: IsoTime | None = None
    due: str | None
    due_all_day: bool | None = None
    start: str | None = None
    start_all_day: bool | None = None
    tz: str | None = None
    priority: int | None = absent(description="iCalendar: 0 none, 1-4 high, 5 medium, 6-9 low.")
    percent: int | None = None
    recurring: bool | None = None
    rrule: str | None = None
    repeat_from: str | None = None
    categories: list[str] | None = None
    sort_order: int | None = None
    parent: str | None = None
    subtask_count: int | None = None
    open_subtask_count: int | None = None
    reminders: list[int] | None = None
    reminders_at: list[str] | None = None
    list_: str | None = absent(alias="list", description="The task list's URL.")
    list_id: int | None = None
    connector_id: int | None = None
    href: str | None = None
    etag: str | None = None
    version: int | None = None
    created: IsoTime | None = None
    last_modified: IsoTime | None = None
    local_only: bool | None = None
    section: str | None = absent(description="Today and Upcoming: the section the row is in.")
    conversation_id: int | None = None


class TaskPage(Out):
    """GET /tasks."""

    connected: bool
    items: list[Task]
    next_cursor: str | None
    sort: str | None
    sync: SyncState
    sections: dict[str, int] | None = absent(description="smart=today|upcoming: rows per section.")


class TaskCounts(Out):
    """GET /tasks/counts: open tasks per smart list, per list (by URL, and by calendar id) and per tag."""

    smart: dict[str, int]
    lists: dict[str, int]
    lists_by_id: dict[str, int]
    tags: dict[str, int]
    overdue: int
