"""Response models of the mail read side: conversation lists and threads, message bodies, labels,
sidebar counts, layouts and the change feed (api/mail_read.py, api/layout_routes.py,
api/schemas.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, RootModel

from mailr.api.contract.base import IsoDate, IsoTime, Out, PublicId, absent

# ---------------------------------------------------------------- labels


class Label(Out):
    """A label with its list's numbers (GET /labels)."""

    id: int
    name: str
    role: Literal["inbox", "sent", "drafts", "junk", "trash", "archive"] | None
    color: str | None
    show_in_list: Literal["show", "hide", "unread"]
    show_in_message_list: bool
    sort: int
    home_account_id: int | None = Field(
        description="The mail account the label was made in: it is listed under that account even while it "
                    "holds none of its mail.")
    unread: int
    total: int


class LabelList(RootModel[list[Label]]):
    pass


# ---------------------------------------------------------------- conversations


class Participant(Out):
    email: str
    name: str | None = None
    me: bool = absent()


class PrimaryAction(Out):
    """The main action an email offers (Track package, Pay, Join…)."""

    kind: str
    label: str
    url: str = absent()
    fact: str | None = absent()
    source: str = absent()
    code: str = absent(description='kind "code": the one-time code the chip copies.')


class Signal(Out):
    """A triage signal on the newest message (Reply needed, Waiting, Due)."""

    name: str = Field(description="needs_reply, waiting_on or deadline")
    reason: str
    date: IsoDate | None = absent(description="deadline only")


class AttachmentChip(Out):
    filename: str
    type: str
    n: int = Field(description="how often the same file was sent in the thread")


class ConversationSummary(Out):
    """A conversation as a list row and as a thread's header (api/schemas.conversation_out)."""

    id: PublicId
    subject: str
    snippet: str
    last_date: IsoTime | None
    message_count: int
    unread_count: int
    draft_count: int
    has_attachments: bool
    starred: bool
    important: bool
    importance_score: float | None
    category: str | None
    label_ids: list[int]
    participants: list[Participant]
    to_self: bool = Field(description="A note to self: the user wrote every message, each only to their own "
                                      "addresses.")
    recipients: list[Participant] = Field(description="When only the user wrote in it: whom it went to.")
    snoozed_until: IsoTime | None
    muted: bool
    follow_up_at: IsoTime | None
    nudged_at: IsoTime | None
    primary_action: PrimaryAction | None
    signals: list[Signal] = absent(description="Absent where the route does not compute them.")
    attachments_preview: list[AttachmentChip] = absent(description="list rows only")
    accounts: list[int] = absent(description="The mail accounts it belongs to; only with two or more "
                                                         "accounts.")
    match_in_attached: bool = absent(description="A search matched it only inside an attached email.")


class ConversationPage(Out):
    """GET /conversations: one page of a list."""

    items: list[ConversationSummary]
    next_cursor: str | None
    total: int = absent(description="Left out with count=false.")
    total_capped: bool = absent(description="Counting stopped at `total` (shown as 200,000+).")


class Address(Out):
    email: str
    name: str = absent()


class AttachedEmailSummary(Out):
    subject: str
    from_name: str
    from_email: str
    date: IsoTime | None
    excerpt: str
    attachments: int
    invite: bool = absent()


class Attachment(Out):
    part: int
    filename: str
    type: str
    size: int
    inline: bool
    cid: str | None
    email: AttachedEmailSummary = absent(description="An attached email the server could read.")


class EventChange(Out):
    """A move of one of the user's events that an email announces in words."""

    uid: str
    summary: str
    old_start: str
    old_end: str | None
    new_start: str
    new_end: str
    sentence: str


class Message(Out):
    """A message in a thread (api/schemas.message_out)."""

    id: PublicId
    conversation_id: int
    from_: Address = Field(alias="from")
    to: list[Address]
    cc: list[Address]
    bcc: list[Address]
    reply_to: list[Address]
    subject: str
    date: IsoTime | None
    snippet: str
    seen: bool
    flagged: bool
    important: bool
    draft: bool
    answered: bool
    label_ids: list[int]
    has_attachments: bool
    attachments: list[Attachment]
    list_unsubscribe: str | None
    list_id: str | None
    auth_results: dict[str, str] = Field(description="spf, dkim, dmarc, from_problem…")
    sent_by_me: bool
    origin: str | None = Field(description='"android-share": shared in by another app, not the user\'s words.')
    category: str | None
    delivered_to: list[str]
    message_id: str | None = Field(description="The Message-ID header.")
    connector_id: int | None
    primary_action: PrimaryAction | None
    event_change: EventChange = absent(description="Only when the email announces a move.")
    lang: str | None
    offer_translate: bool
    untrusted: bool = absent(description="Read with an API token: the content is untrusted.")


class ConversationDetail(Out):
    """GET /conversations/{id}."""

    conversation: ConversationSummary
    messages: list[Message]


class Trackers(Out):
    count: int
    domains: list[str]


class MessageBody(Out):
    """GET /messages/{id}/body: the sanitised body for the reader."""

    html: str | None
    text: str | None
    source: str | None
    remote_images: bool
    has_quote: bool
    proxied: bool
    trackers: Trackers
    links_unwrapped: int
    csp: str | None
    junk: bool = Field(description="The message is in Spam: images load only after the reader confirms.")
    quoted_html_start: int | None = absent()
    images_paused: str = absent(description="Remote images are paused on the server: why.")
    untrusted: bool = absent()


# ---------------------------------------------------------------- counts


class ViewCount(Out):
    unread: int
    total: int


class InboxCount(Out):
    """GET /inbox/count: the number by Inbox in the sidebar."""

    count: int
    mode: Literal["first_tab", "all"]
    layout: str | None
    tab: str | None


class MailAccount(Out):
    """A mail account as the mailbox shows it."""

    id: int
    address: str
    name: str
    color: str
    addresses: list[str]
    identity_id: int | None
    local_only: bool


class SidebarCounts(Out):
    """GET /counts: every sidebar number. Each view counts what its list shows."""

    cursor: int
    views: dict[str, ViewCount] = Field(description="By view: inbox, starred, label:<id>, acct:<id>:<view>, …")
    inbox_count: InboxCount
    waiting: int
    approvals: int
    failed: int
    accounts: list[MailAccount]


class TabIndicators(Out):
    """The tab's indicators; each is there only when the tab shows it."""

    unread: int = absent()
    all: int = absent()
    due_count: int = absent()
    earliest_due: IsoDate | None = absent()
    earliest_due_cid: int | None = absent()
    earliest_due_from: str | None = absent()
    earliest_due_subject: str | None = absent()


class TabCountsDetail(Out):
    tabs: dict[str, TabIndicators]


class TabCounts(RootModel[TabCountsDetail | dict[str, int]]):
    """GET /layouts/{id}/counts: {tab id: n} (the tab's badge), or with detail=true {"tabs": {tab id:
    indicators}}."""


# ---------------------------------------------------------------- layouts


class LayoutSection(Out):
    id: str
    title: str
    base: str = Field(description="A list view: inbox, all, starred, important, snoozed, sent, drafts, archive, "
                                  "label:<id>.")
    category: str | None
    filter: Literal["important", "important_unread", "unread", "starred"] | None
    q: str
    limit: int | None
    exclude_above: bool
    inbox_only: bool
    view_all_q: str = absent(description="The search that lists the whole section.")


class LayoutTab(Out):
    id: str
    name: str
    icon: str | None
    badge: Literal["unread", "all", "none"]
    indicators: list[Literal["unread", "all", "due"]]
    claims: bool = absent()
    sections: list[LayoutSection]


class InboxLayout(Out):
    id: str
    name: str
    preset: bool = absent()
    tab_detail: bool = absent()
    hide_empty: bool = absent()
    tabs: list[LayoutTab]


class LayoutLimits(Out):
    tabs: int
    sections: int
    layouts: int


class LayoutOptions(Out):
    bases: list[str]
    filters: list[Literal["important", "important_unread", "unread", "starred"]]
    icons: list[str]
    limits: LayoutLimits


class LayoutsResponse(Out):
    """GET /layouts: the presets and the user's own layouts, and which is active."""

    active: str
    layouts: list[InboxLayout]
    options: LayoutOptions


# ---------------------------------------------------------------- changes


class ChangeEntry(Out):
    entity: str = Field(description="conversation, message, label, calendar, event, task, …")
    id: PublicId
    op: Literal["upsert", "delete"]


class Changes(Out):
    """GET /changes: what changed since a cursor, the latest operation per object."""

    cursor: int
    more: bool
    changes: list[ChangeEntry]
    server_time: IsoTime
    reset: bool = absent(description="The log was pruned past the cursor: resync fully.")

