"""mailr's own data. Mail bodies live in Stalwart; this schema holds the index and everything
Stalwart does not know about (connectors, conversations overlay, rules, tokens, audit, models).

Every user-owned table carries user_id, and every query filters on it (see core/scoping.py).
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import HALFVEC, Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    and_,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, defer, mapped_column

from mailr.db import Base

# Vector size for every embedding (requested from the endpoint via `dimensions`; Matryoshka
# models such as Qwen3-Embedding and OpenAI text-embedding-3 support it natively).
EMBED_DIM = 512


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Role(str, enum.Enum):
    admin = "admin"
    user = "user"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default=Role.user.value)
    password_hash: Mapped[str | None] = mapped_column(String(200))
    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    # Stalwart principal for this user; the secret is encrypted with the user's data key.
    stalwart_login: Mapped[str] = mapped_column(String(320), default="")
    # JMAP accountId of the user's Stalwart account (the service account acts on it).
    stalwart_account_id: Mapped[str | None] = mapped_column(String(64))
    stalwart_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Per-user data key, wrapped by the instance key.
    data_key_wrapped: Mapped[bytes | None] = mapped_column(LargeBinary)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # False only for self-service sign-up accounts: nobody proved they own the address, so
    # single sign-on never links to them by email (core/users.user_for_oidc_claims).
    email_verified: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # True when single sign-on created the account (MAILR_OIDC_AUTO_CREATE): every later
    # single sign-on then checks MAILR_OIDC_AUTO_CREATE_GROUP again.
    oidc_auto_created: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = _now()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Access policies (core/access.py). The identity provider's groups, as the
    # provider last reported them: NULL until a sign-in reported them (the OIDC `groups` claim
    # today; a SAML attribute or SCIM push can feed the same columns later, see idp_groups_source).
    idp_groups: Mapped[list[str] | None] = mapped_column(ARRAY(String(200)))
    idp_groups_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idp_groups_source: Mapped[str | None] = mapped_column(String(20))  # oidc | saml | scim
    # Groups an admin assigned (Admin → Users): used only while the provider has reported none
    # for this account (password-only accounts).
    assigned_groups: Mapped[list[str]] = mapped_column(ARRAY(String(200)), default=list, server_default="{}")
    # A hash of the user's mail accounts and their addresses when Conversation.account_ids was last
    # rebuilt for the whole mailbox (core/mailaccounts.py, the account index). NULL: never.
    account_index: Mapped[str | None] = mapped_column(String(64))
    # Server-owned sign-in state, never in `settings` (which PATCH /me/settings writes):
    # set while a disabled user's store password is replaced (core/users.lock_store_signin) ...
    store_signin_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # ... and the counter in the known-client cookie stamp (api/auth_routes.signin_stamp), raised by
    # Sign out everywhere and password resets.
    signin_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of cookie value
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(String(400), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    device: Mapped[str] = mapped_column(String(100), default="web")  # web | android | ...
    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Connector(Base):
    """A mail (or calendar) source: generic IMAP/SMTP, Gmail, M365, file import, CalDAV."""

    __tablename__ = "connectors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # imap | gmail | m365 | file | caldav
    name: Mapped[str] = mapped_column(String(200))
    preset: Mapped[str | None] = mapped_column(String(60))
    mode: Mapped[str] = mapped_column(String(10), default="mirror")  # store | mirror | import
    data_class: Mapped[str] = mapped_column(String(20), default="normal")  # normal | local_only
    # Non-secret settings (hosts, ports, folders, options) and encrypted secrets.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    secrets_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Sync cursors: per-folder UIDVALIDITY/UIDNEXT/HIGHESTMODSEQ, delta tokens, import progress.
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    retention_days: Mapped[int] = mapped_column(Integer, default=14)
    # Upstream deletion is armed only after an explicit confirmation + verified backup.
    delete_upstream: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # The user's built-in calendar on mailr's own Stalwart (calendar/builtin.py): kind "caldav",
    # at most one per user, set only by the server; never deleted or re-pointed through the API.
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Connected for the owner by an administrator (core/managed.py; the instance setting
    # accounts.admin_connect). The owner sees it, may disconnect it, and is told when it is added.
    managed_by_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    managed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="new")  # new|syncing|idle|error|paused
    status_detail: Mapped[str] = mapped_column(Text, default="")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class Identity(Base):
    """A send-as address. Sends go through the connector that owns it."""

    __tablename__ = "identities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[int | None] = mapped_column(ForeignKey("connectors.id", ondelete="SET NULL"))
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(200), default="")
    reply_to: Mapped[str] = mapped_column(String(320), default="")
    signature_html: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # Addresses (exact or *@domain) whose incoming mail this identity answers by default.
    receives: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (UniqueConstraint("user_id", "email"),)


class Label(Base):
    """Mirror of a Stalwart mailbox. System labels carry a role."""

    __tablename__ = "labels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    jmap_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))  # full path with '/' for nesting
    role: Mapped[str | None] = mapped_column(String(20))  # inbox|sent|drafts|junk|trash|archive
    color: Mapped[str | None] = mapped_column(String(20))
    show_in_list: Mapped[str] = mapped_column(String(10), default="show")  # show|hide|unread
    show_in_message_list: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    # The mail account a folder was made in ("New folder" in that account's group of the mail
    # navigation). The label is still one label for every account; the home only lists it under
    # that account while it holds none of the account's mail. NULL for other labels.
    home_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("connectors.id", ondelete="SET NULL", name="fk_labels_home_account_id"))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (UniqueConstraint("user_id", "name"),)


class Conversation(Base):
    """mailr's threading overlay. Aggregates are denormalised for fast list rendering."""

    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    subject: Mapped[str] = mapped_column(Text, default="")
    norm_subject: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    last_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    draft_count: Mapped[int] = mapped_column(Integer, default=0)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    important: Mapped[bool] = mapped_column(Boolean, default=False)
    importance_score: Mapped[float | None] = mapped_column(Float)
    category: Mapped[str | None] = mapped_column(String(20))  # primary|updates|promotions|social|forums|purchases
    # Union of label ids over non-deleted messages (label ids from labels table); a message in Spam
    # or Trash brings only its folder labels (core/threading.py).
    label_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    # The mail accounts (connector ids) the conversation belongs to (core/mailaccounts.py, the account
    # index); NULL until it is first computed.
    account_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    # [{"email","name"}] senders in order of appearance, for the list view.
    participants: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # When only the user wrote in it: [{"email","name"}] the people the user's messages went to (at
    # most 12, the user's own addresses left out), for "To: Dana" in the list. Empty otherwise.
    recipients: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # A note to self: the user wrote every message, and each went only to the user's own addresses.
    to_self: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    primary_action: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # the latest message's
    # mail.follow_up: "remind me if nobody replies". follow_up_since is when my message went out; a
    # reply after it cancels. When follow_up_at passes without one, the conversation comes back to
    # the inbox with nudged_at set ("No reply"), until a reply arrives or the user acts on it.
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_up_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nudged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Manual threading corrections pin a conversation so the threader will not re-merge it.
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (
        Index("ix_conv_user_last", "user_id", "last_date"),
        Index("ix_conv_labels", "label_ids", postgresql_using="gin"),
        Index("ix_conv_snooze", "user_id", "snoozed_until"),
        Index("ix_conv_follow_up", "follow_up_at", postgresql_where=text("follow_up_at IS NOT NULL")),
        Index("ix_conv_nudged", "user_id", postgresql_where=text("nudged_at IS NOT NULL")),
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    jmap_id: Mapped[str | None] = mapped_column(String(64))
    jmap_thread_id: Mapped[str | None] = mapped_column(String(64))
    blob_id: Mapped[str | None] = mapped_column(String(128))
    connector_id: Mapped[int | None] = mapped_column(ForeignKey("connectors.id", ondelete="SET NULL"))
    # Upstream location: {"folder": .., "uidvalidity": .., "uid": ..} or provider ids.
    upstream: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    upstream_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    message_id_hdr: Mapped[str | None] = mapped_column(String(998))
    in_reply_to: Mapped[str | None] = mapped_column(String(998))
    references: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    subject: Mapped[str] = mapped_column(Text, default="")
    norm_subject: Mapped[str] = mapped_column(Text, default="")
    from_email: Mapped[str] = mapped_column(String(320), default="")
    from_name: Mapped[str] = mapped_column(String(300), default="")
    to: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    cc: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # {kind, label, url, fact, source} of the email's main action, or None.
    primary_action: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    bcc: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    reply_to: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    delivered_to: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    list_id: Mapped[str | None] = mapped_column(String(400))
    list_unsubscribe: Mapped[str | None] = mapped_column(Text)
    # How the List-Unsubscribe can be used (core/unsubscribe.method_of): one_click | mailto | link |
    # none; null without the header. Indexed in ix_msg_groups for the subscriptions page.
    unsub_method: Mapped[str | None] = mapped_column(String(10))
    date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    size: Mapped[int] = mapped_column(Integer, default=0)
    snippet: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")  # plain text, quotes stripped, capped
    # The text of the emails attached to this one (message/rfc822 parts, core/mime.py): never in
    # body_text. Search reads it through attached_tsv at a lower rank; nothing else reads it
    # (summaries, embeddings, the assistant, own_words). NULL when the message has none.
    attached_text: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    # Language of the visible body text (core/langid.py, fastText lid.176): ISO 639-1 where one
    # exists, and the model's probability. Both null = not checked yet; lang null with
    # confidence 0 = checked, too short to tell.
    lang: Mapped[str | None] = mapped_column(String(16))
    lang_confidence: Mapped[float | None] = mapped_column(Float)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    auth_results: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # spf/dkim/dmarc

    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    answered: Mapped[bool] = mapped_column(Boolean, default=False)
    draft: Mapped[bool] = mapped_column(Boolean, default=False)
    important: Mapped[bool] = mapped_column(Boolean, default=False)
    label_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    # The user's own labels (given by the user or a filter) the message had when it was reported
    # as spam. In Spam it carries only the Spam label (in the store and upstream too, so other
    # mail apps never show it in those folders); "Not spam" puts these back. NULL otherwise.
    spam_kept_labels: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # expunged from the store

    category: Mapped[str | None] = mapped_column(String(20))
    importance_score: Mapped[float | None] = mapped_column(Float)
    spam_score: Mapped[float | None] = mapped_column(Float)
    sent_by_me: Mapped[bool] = mapped_column(Boolean, default=False)
    # Where the content came from when another app put it into mailr: "android-share" is the
    # phone's "Send to self" (X-Mailr-Origin, core/ingest.UNTRUSTED_ORIGINS). Null for everything
    # else. sent_by_me still files such mail (Sent, `from:me`, filters); the assistant treats its
    # text as other people's, never as the user's own words (own_words).
    origin: Mapped[str | None] = mapped_column(String(32))
    # Data class copied from the source connector at ingest. Kept on the message so the
    # local-only guard survives connector deletion and covers mail without a connector.
    local_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # When a user, rule or agent last changed the message in mailr (labels, placement, flags).
    # A mirror account's state is applied over mailr's only when it changed after this.
    local_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # sha256 of the normalised message (key headers + body); duplicates are merged only when
    # this matches, so a reused Message-ID never hides a different message.
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # The sender screener (core/screener.py): the key of the pending sender whose mail this is,
    # while it is held out of the Inbox. Allow, Block or a label clears it.
    screened_key: Mapped[str | None] = mapped_column(String(320))

    # Never read in Python (the trigger writes it, search reads it in SQL): deferred, so loading
    # a message does not pull it. Embedding and body_text are deferred per query where
    # the caller does not read them (light_message() below).
    tsv: Mapped[Any] = mapped_column(TSVECTOR, deferred=True)
    # attached_text as weight D, written by the same trigger; NULL without attached emails.
    attached_tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=True, deferred=True)
    embedding: Mapped[Any] = mapped_column(Vector(EMBED_DIM), nullable=True)
    # The embedding model key (intel/llm.embed_model_key) of `embedding`, as on passages.
    # Vector queries compare only vectors of the current model. NULL: no vector, or one stored
    # before the column (`mailr embed-model-backfill` fills those).
    embed_model: Mapped[str | None] = mapped_column(String(200))
    # pending|backfill|done|skipped|purged; backfill: triaged by the batched intel.backfill (an import)
    intel_state: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    @hybrid_property
    def own_words(self) -> bool:
        """The user wrote this text: sent by them and not shared in by another app. The test for
        anything that trusts content as the user's (assistant, memory, writing samples)."""
        return bool(self.sent_by_me) and self.origin is None

    @own_words.inplace.expression
    @classmethod
    def _own_words_expression(cls):
        return and_(cls.sent_by_me.is_(True), cls.origin.is_(None))

    __table_args__ = (
        UniqueConstraint("user_id", "jmap_id"),
        Index("ix_msg_user_msgid", "user_id", "message_id_hdr"),
        Index("ix_msg_user_date", "user_id", "date"),
        Index("ix_msg_tsv", "tsv", postgresql_using="gin"),
        Index("ix_msg_attached_tsv", "attached_tsv", postgresql_using="gin",
              postgresql_where=text("attached_tsv IS NOT NULL")),
        Index("ix_msg_labels", "label_ids", postgresql_using="gin"),
        Index("ix_msg_from", "user_id", "from_email"),
        # An account's messages (core/mailaccounts.py, the account: operator; migration a3d7c9e2f415).
        Index("ix_msg_user_connector", "user_id", "connector_id", "conversation_id"),
        Index("ix_msg_intel", "user_id", "intel_state"),
        Index("ix_msg_screened", "user_id", "screened_key", postgresql_where=text("screened_key IS NOT NULL")),
    )


def light_message() -> tuple:
    """Loader options for Message queries whose callers read neither the embedding nor the body
    text (lists, the reader, bulk actions, store sync): those columns stay in the database.
    Reading one of them on such a row raises instead of loading it."""
    return (defer(Message.embedding, raiseload=True), defer(Message.body_text, raiseload=True))


class MailGroupState(Base):
    """What the user decided about a group of mail, and what a model suggested for it
    (core/mailgroups.py). `kind` is sub (a subscription: a list or a bulk sender), sender, domain,
    list or category; `key` the group's key in that kind."""

    __tablename__ = "mail_group_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))
    key: Mapped[str] = mapped_column(String(400))
    # Keep: the user reviewed the group and keeps it as it is.
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribe_method: Mapped[str | None] = mapped_column(String(20))
    # The filter "Auto-archive future mail" made (rules engine).
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id", ondelete="SET NULL"))
    # A category the user's classify model suggested for an uncategorised group (applied only on
    # the user's confirmation).
    suggested_category: Mapped[str | None] = mapped_column(String(20))
    suggestion_confidence: Mapped[float | None] = mapped_column(Float)
    suggestion_model: Mapped[str | None] = mapped_column(String(200))
    suggestion_samples: Mapped[int | None] = mapped_column(Integer)
    suggested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (UniqueConstraint("user_id", "kind", "key", name="uq_mail_group_states"),)


class MailTally(Base):
    """The user's grouped mail, counted once and kept up to date (core/tally.py): one row per
    sender, list, category, place, age band, List-Unsubscribe, data class and protection, with the
    counts and dates the Subscriptions and Clean up pages show. It covers the mail those pages
    group: not deleted, not the user's own, not in Spam."""

    __tablename__ = "mail_tally"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    from_email: Mapped[str] = mapped_column(String(320))
    list_id: Mapped[str | None] = mapped_column(String(400))
    category: Mapped[str | None] = mapped_column(String(20))
    # 1: in the Inbox, 2: in the Trash (both can be set).
    loc: Mapped[int] = mapped_column(SmallInteger)
    # The age: 0 to 6 by tally.BANDS, counted from the user's anchor date; 7 without a date.
    band: Mapped[int] = mapped_column(SmallInteger)
    unsub: Mapped[bool] = mapped_column(Boolean)
    local_only: Mapped[bool] = mapped_column(Boolean)
    # The conversation's protection: 1 starred, 2 important, 4 the user wrote in it.
    prot: Mapped[int] = mapped_column(SmallInteger)
    messages: Mapped[int] = mapped_column(Integer, default=0)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    unread: Mapped[int] = mapped_column(Integer, default=0)
    # The conversations with mail in this row, and those with mail in no other row.
    units: Mapped[int] = mapped_column(Integer, default=0)
    solo: Mapped[int] = mapped_column(Integer, default=0)
    oldest: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest_read: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest_id: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        Index("uq_mail_tally", "user_id", "from_email", "list_id", "category", "loc", "band", "unsub",
              "local_only", "prot", unique=True, postgresql_nulls_not_distinct=True),
    )


class MailTallyUnit(Base):
    """One conversation's share of one mail_tally row (core/tally.py). `conv` is the conversation,
    or minus the message id for a message without one. `spread`: the conversation has mail in more
    than one row, so the pages count it here and not in mail_tally.solo."""

    __tablename__ = "mail_tally_units"
    conv: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cell_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    messages: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(BigInteger)
    unread: Mapped[int] = mapped_column(Integer)
    oldest: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest_read: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest_id: Mapped[int] = mapped_column(BigInteger)
    spread: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        Index("ix_mail_tally_units_cell", "cell_id"),
        Index("ix_mail_tally_units_user", "user_id"),
        Index("ix_mail_tally_units_spread", "user_id", postgresql_where=text("spread")),
    )


class MailTallyConv(Base):
    """A conversation (or a message without one, as in MailTallyUnit.conv) in the tally: the row its
    upkeep locks, so two changes to one conversation are counted one after the other."""

    __tablename__ = "mail_tally_convs"
    conv: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class MailTallyUser(Base):
    """The state of a user's tally (core/tally.py). state: building (the first count, a batch of
    conversations at a time from `cursor`), ready, or stale (to be counted again; the pages count
    live meanwhile). anchor: the date the age bands count from, moved forward every night."""

    __tablename__ = "mail_tally_users"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    state: Mapped[str] = mapped_column(String(10))
    anchor: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Senders the last nightly check found counted wrong (and corrected), and in all.
    drifted: Mapped[int] = mapped_column(Integer, default=0)
    drifted_total: Mapped[int] = mapped_column(Integer, default=0)


class Person(Base):
    """A correspondent, aggregated from headers. Feeds autocomplete, profiles, and the agent."""

    __tablename__ = "people"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(300), default="")
    organization: Mapped[str] = mapped_column(String(300), default="")
    received_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sent: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    # strength: recency-weighted correspondence on a fixed time base (core/people.py).
    strength: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    title: Mapped[str] = mapped_column(String(300), default="", server_default="")
    # [{"value", "type", "source"}] and [{"text", "street", "city", "region", "postcode", "country",
    # "type", "source"}]; source is carddav | signature | user.
    phones: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    addresses: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    birthday: Mapped[str] = mapped_column(String(10), default="", server_default="")  # YYYY-MM-DD or --MM-DD
    # {field: {"source": "carddav"|"signature"|"user", "connector_id"?, "href"?, "message_id"?,
    #  "local_only": bool}} for name, organization, title and birthday.
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    signature_checked_id: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("user_id", "email"),)


class PersonGroup(Base):
    """A contact group: made by the user (manual), accepted from a suggestion
    (suggested), or mirrored read-only from an address book (carddav). A suggestion is a row in
    state suggested; dismissed rows stay so the same set is never suggested again."""

    __tablename__ = "person_groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), default="")
    colour: Mapped[str | None] = mapped_column(String(9))
    source: Mapped[str] = mapped_column(String(12), default="manual")  # manual | suggested | carddav
    state: Mapped[str] = mapped_column(String(12), default="active")  # active | suggested | dismissed
    connector_id: Mapped[int | None] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    remote_key: Mapped[str] = mapped_column(String(500), default="")
    name_hint: Mapped[str] = mapped_column(String(200), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class PersonGroupMember(Base):
    __tablename__ = "person_group_members"
    group_id: Mapped[int] = mapped_column(ForeignKey("person_groups.id", ondelete="CASCADE"), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), default="")


class PersonSuggestion(Base):
    """A detail found for a person (a phone, title, organization or postal address from a
    signature), waiting for the user's Accept or Dismiss. A dismissed one never returns."""

    __tablename__ = "person_suggestions"
    __table_args__ = (UniqueConstraint("user_id", "email", "field", "norm", name="uq_person_suggestions"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    field: Mapped[str] = mapped_column(String(20))  # phone | title | organization | address
    value: Mapped[str] = mapped_column(Text)
    norm: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="signature")
    method: Mapped[str] = mapped_column(String(10), default="rules")  # rules | model
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    local_only: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(12), default="suggested")  # suggested | accepted | dismissed
    created_at: Mapped[datetime] = _now()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AddressBook(Base):
    """A CardDAV address book read through a CalDAV connector's credentials (read-only)."""

    __tablename__ = "address_books"
    __table_args__ = (UniqueConstraint("connector_id", "href", name="uq_address_books"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    href: Mapped[str] = mapped_column(Text)
    displayname: Mapped[str] = mapped_column(Text, default="")
    sync_token: Mapped[str | None] = mapped_column(Text)
    ctag: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, default="")


class ContactCard(Base):
    """One vCard of an address book, parsed (contacts/vcard.py). People are merged from these."""

    __tablename__ = "contact_cards"
    __table_args__ = (UniqueConstraint("connector_id", "href", name="uq_contact_cards"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    book_id: Mapped[int] = mapped_column(ForeignKey("address_books.id", ondelete="CASCADE"))
    href: Mapped[str] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    uid: Mapped[str] = mapped_column(String(500), default="")
    kind: Mapped[str] = mapped_column(String(12), default="individual")  # individual | group
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # The card as the server sent it, for the daily export (calendar/backup.py). NULL for cards
    # read before it was kept; the next address-book sync fetches those again.
    vcard: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime] = _now()


class Rule(Base):
    """Deterministic filter. `query` uses Gmail search syntax; actions are a JSON object."""

    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    query: Mapped[str] = mapped_column(Text)
    # {"add_labels":[ids],"remove_labels":[..],"archive":bool,"mark_read":bool,"star":bool,
    #  "important":true|false|null,"category":str|null,"spam":bool,"never_spam":bool,
    #  "trash":bool,"forward_to":str|null}
    actions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stop: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(30), default="user")  # user|gmail-import|agent
    hits: Mapped[int] = mapped_column(Integer, default=0)
    # Set when a token limited to some accounts (or not local_only) created the rule: the rule
    # only ever matches mail that token could see. {"connector_ids": [..], "local_only": bool}
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _now()


class LabelDecision(Base):
    """Why a label/category/importance was applied or suggested. Corrections become training data."""

    __tablename__ = "label_decisions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # label|category|importance|spam
    target: Mapped[str] = mapped_column(String(100))  # label id, category name, 'important', 'spam'
    value: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(20))  # rule|classifier|llm|user
    source_ref: Mapped[str] = mapped_column(String(200), default="")  # rule id / model name
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="applied")  # applied|suggested|accepted|rejected|withdrawn (moved to Spam)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (Index("ix_decision_user_kind", "user_id", "kind", "target"),)


class ClassifierModel(Base):
    __tablename__ = "classifier_models"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    target: Mapped[str] = mapped_column(String(100))  # 'label:<id>' | 'category' | 'importance'
    blob: Mapped[bytes] = mapped_column(LargeBinary)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    trained_at: Mapped[datetime] = _now()
    __table_args__ = (UniqueConstraint("user_id", "target"),)


class Outbox(Base):
    """Every outgoing message waits here: undo window, scheduled send, approval queue."""

    __tablename__ = "outbox"
    __table_args__ = (
        Index("uq_outbox_idem_key", "user_id", "idem_key", unique=True, postgresql_where=text("idem_key IS NOT NULL")),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    identity_id: Mapped[int] = mapped_column(ForeignKey("identities.id", ondelete="CASCADE"))
    token_id: Mapped[int | None] = mapped_column(ForeignKey("api_tokens.id", ondelete="SET NULL"))
    # Who asked for the send: user | token:<id> | assistant | session:<id> (rate limits, audit).
    subject: Mapped[str] = mapped_column(String(80), default="user", server_default="user")
    draft_jmap_id: Mapped[str | None] = mapped_column(String(64))
    conversation_id: Mapped[int | None] = mapped_column(BigInteger)
    # The composed message as structured JSON (to/cc/bcc/subject/html/text/attachments/headers).
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    recipients: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # queued|awaiting_approval|sending|sent|cancelled|failed|rejected|uncertain (may have been sent)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    approval_reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(50), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_message_id: Mapped[str | None] = mapped_column(String(998))
    # Message-ID fixed at first build, so a retry after an ambiguous SMTP failure resends the
    # same message (recipients' clients dedupe it) instead of a new one.
    mime_message_id: Mapped[str | None] = mapped_column(String(998))
    created_at: Mapped[datetime] = _now()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the send worker claimed it (queued → sending): the stuck-send sweep keys on this.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Sends the server makes on its own (a filter's forward): one Outbox row per key and user,
    # so a job that runs again after a crash never queues a second copy.
    idem_key: Mapped[str | None] = mapped_column(String(200))


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(16))  # shown in UI, e.g. mlr_ab12
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # read | label | draft | send | delete | rules | calendar | admin
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    # Empty = all of the user's connectors that the token's local_only flag allows.
    connector_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    local_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # {"mode":"approve_all"|"policy","replies_only":bool,"known_only":bool,"domains":[..],
    #  "max_per_hour":int,"max_per_day":int,"identities":[ids]}
    send_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_id: Mapped[int | None] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(50))  # user | token:<name> | agent | system
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(200), default="")
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (Index("ix_audit_user_time", "user_id", "created_at"),
                      # Settings → Activity (core/activity.py; migration d7a3f9c1e2b4)
                      Index("ix_audit_activity", "user_id", "id",
                            postgresql_where=text("(result ? 'activity' OR action LIKE 'admin_notice.%')")))


class ModelUsage(Base):
    """How often a user's work went to each model: one row per user, day, feature, endpoint and
    model (intel/usage.py; Settings → Ambrose → Model use). `local` is the endpoint's locality at
    the time. Rows older than KEEP_DAYS are pruned."""

    __tablename__ = "model_usage"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # no foreign key: see the migration c3a9e6f1b2d4
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    feature: Mapped[str] = mapped_column(String(40), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(200), primary_key=True)
    model: Mapped[str] = mapped_column(String(200), primary_key=True)
    local: Mapped[bool] = mapped_column(Boolean)
    calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ModelEndpoint(Base):
    """An OpenAI-compatible endpoint registered by an admin."""

    __tablename__ = "model_endpoints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base_url: Mapped[str] = mapped_column(String(500))
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    locality: Mapped[str] = mapped_column(String(10), default="local")  # local | hosted
    adapter: Mapped[str] = mapped_column(String(20), default="openai")  # openai | jev
    models: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    # task -> model name, e.g. {"chat": "qwen3.8-27b", "embed": "..."}
    defaults: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set up by the server's environment (MAILR_ASR_URL, agent/transcribe.sync_env_asr): its URL
    # comes from there; an admin changes its models, defaults and whether it is on.
    from_env: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = _now()


class WebSearchProvider(Base):
    """A web search service registered by an admin (websearch/; site/docs/admin/web-search.md)."""

    __tablename__ = "web_search_providers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # search: a search service (websearch/adapters.py ADAPTERS); reader: a page reader
    # (websearch/readers.py READERS), tried after the built-in reader.
    purpose: Mapped[str] = mapped_column(String(10), default="search", server_default="search")
    kind: Mapped[str] = mapped_column(String(20))  # ADAPTERS or READERS, by purpose
    base_url: Mapped[str] = mapped_column(String(500))
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    # internet: a public service (through the egress proxy when one is set); local: a service on
    # the admin's own network (reached directly; never through the egress, which has no LAN route).
    network: Mapped[str] = mapped_column(String(10), default="internet")
    # internet: the requests to the service go through the egress proxy. A local browserless page
    # reader: its page fetches go through the egress proxy (True) or directly from this network
    # (False, the admin's explicit choice; allowed even under MAILR_REQUIRE_EGRESS).
    use_egress: Mapped[bool] = mapped_column(Boolean, default=True)
    # The page reader set up by the server's environment (MAILR_BROWSER_URL): its URL and token
    # come from there; an admin changes only its route, order, limits and whether it is on.
    from_env: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower is tried first
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_results: Mapped[int] = mapped_column(Integer, default=10)
    timeout_s: Mapped[int] = mapped_column(Integer, default=10)
    per_minute: Mapped[int] = mapped_column(Integer, default=0)  # 0: no limit
    per_day: Mapped[int] = mapped_column(Integer, default=0)  # 0: no limit
    usage_day: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD (UTC) of usage_count
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    # Today's use per person ("u<id>") and per API token ("t<id>"), for their shares of per_day
    # (websearch/service.shares). Reset with usage_day.
    usage_users: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _now()


class Job(Base):
    """Postgres-backed job queue (SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    # The worker lane that runs it (core/jobs.py LANE_KINDS), set by enqueue.
    lane: Mapped[str] = mapped_column(String(20), default="main", server_default="main")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    run_at: Mapped[datetime] = _now()
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|running|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, default="")
    # Items done, for jobs that report progress (bulk.conversations). A column of its own, so
    # a progress update does not rewrite the payload.
    progress: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        Index("ix_jobs_pick", "status", "run_at"),
        # The lanes' claims (core/jobs.py _claim): the main lane in run_at order, the others by kind.
        Index("ix_jobs_queued", "run_at", "id", postgresql_where=text("status = 'queued'")),
        Index("ix_jobs_queued_kind", "kind", "run_at", "id", postgresql_where=text("status = 'queued'")),
        # Per-user turns in each lane (_claim): the users with due jobs, then one user's earliest.
        Index("ix_jobs_turns", "lane", "user_id", "run_at", "id", postgresql_where=text("status = 'queued'")),
        # The writes to the mail servers by account: one running write per account.
        Index("ix_jobs_upstream_account", text("(payload->>'connector_id')"), "run_at", "id",
              postgresql_where=text("status = 'queued' AND lane = 'upstream'")),
        # Pruning finished jobs by age (reap_stale).
        Index("ix_jobs_status_created", "status", "created_at"),
        Index(
            "uq_jobs_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where="status in ('queued','running')",
        ),
    )


class JobUndoPart(Base):
    """One chunk's undo record of a bulk job (core/tasks.bulk_conversations): kind "reverse" holds
    {message id: labels before}, kind "importance" an importance toggle's record. Written once per
    chunk, read once by the Undo."""

    __tablename__ = "job_undo_parts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (Index("ix_job_undo_parts_job", "job_id", "kind"),)


class PushTarget(Base):
    """UnifiedPush / ntfy endpoints for a user's devices."""

    __tablename__ = "push_targets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="unifiedpush")  # unifiedpush | ntfy | webpush
    endpoint: Mapped[str] = mapped_column(Text)
    device: Mapped[str] = mapped_column(String(200), default="")
    events: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)  # empty = all
    # Web Push (kind "webpush"): the browser subscription's keys (base64url).
    p256dh: Mapped[str | None] = mapped_column(Text)
    auth: Mapped[str | None] = mapped_column(Text)
    # UnifiedPush: the phone registered with mailr's VAPID key, so every message is signed with it
    # (core/push.py) and the push server refuses unsigned ones.
    vapid: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # The sign-in session that registered it: the target ends with the session (sign-out, revoke,
    # Sign out everywhere, password change, admin reset). None for targets older than the column.
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = _now()


class EventLink(Base):
    """Links from a conversation to a CalDAV event or task created from it."""

    __tablename__ = "event_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, index=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))  # event | task
    href: Mapped[str] = mapped_column(Text)
    uid: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _now()


class ChangeLog(Base):
    """Monotonic per-user change stream for sync clients (web, Android) and SSE."""

    __tablename__ = "change_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # No index of its own: ix_changes_user_id (user_id, id) serves every lookup by user.
    user_id: Mapped[int] = mapped_column(Integer)
    entity: Mapped[str] = mapped_column(String(20))  # conversation|message|label|outbox|...
    entity_id: Mapped[int] = mapped_column(BigInteger)
    op: Mapped[str] = mapped_column(String(10))  # upsert | delete
    created_at: Mapped[datetime] = _now()
    __table_args__ = (Index("ix_changes_user_id", "user_id", "id"),)


class IdempotencyKey(Base):
    """Key = sha256(user, caller, method, path, client key). Inserted before the request
    runs (status pending), completed with the response; replays get the stored response."""

    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="done", server_default="done")
    status_code: Mapped[int] = mapped_column(Integer, default=200, server_default="200")
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _now()


class RateEvent(Base):
    """One counted event for a rate limit shared by every API worker (core/throttle.py): a failed
    sign-in, a sign-up, a token's model-backed call. `key` is a hash of the bucket name; rows
    older than the longest window are pruned as new ones are written."""

    __tablename__ = "rate_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (Index("ix_rate_events_key_created", "key", "created_at"),
                      Index("ix_rate_events_created", "created_at"))


class PermissionRule(Base):
    """Allow / ask / deny rule for agent actions."""

    __tablename__ = "permission_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(80))  # token:<id> | session:<id> | assistant | *
    action: Mapped[str] = mapped_column(String(40))  # dotted class, may end in .* or be *
    effect: Mapped[str] = mapped_column(String(10))  # allow | ask | deny
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source: Mapped[str] = mapped_column(String(20), default="user")  # user | grant | migrated
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (Index("ix_perm_user_subject", "user_id", "subject"),)


class PendingAction(Base):
    """An agent action waiting for the user's decision."""

    __tablename__ = "pending_actions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_id: Mapped[int | None] = mapped_column(ForeignKey("api_tokens.id", ondelete="SET NULL"))
    chat_session_id: Mapped[str | None] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(200), default="")
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(Text, default="")
    ctx: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # values constraints are derived from
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # what the executor runs
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # human-readable
    # pending | approved | denied | expired | executed | failed
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(50), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ForwardAddress(Base):
    """An address the user proved they control; only these can be targets of forwarding rules."""

    __tablename__ = "forward_addresses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    code_hash: Mapped[str] = mapped_column(String(64), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (UniqueConstraint("user_id", "email"),)


class TrustedAddress(Base):
    """An address of the user's own that mailr is not connected to ("Addresses that speak for
    you", core/trusted.py). Mail the user forwards from a confirmed address, verified by its
    domain's authentication, carries their note as an instruction for the assistant. Confirmed
    only by a one-time code mailr mails to it (code_hash, expires at code_expires_at)."""

    __tablename__ = "trusted_addresses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    code_hash: Mapped[str] = mapped_column(String(64), default="")
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Wrong codes since the address was added (a new code keeps the count: guessing stays bounded).
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (UniqueConstraint("user_id", "email", name="uq_trusted_addresses"),)


class StoreSyncState(Base):
    """Persisted JMAP change cursors per user (core/storesync.py)."""

    __tablename__ = "store_sync_state"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    email_state: Mapped[str | None] = mapped_column(String(100))
    mailbox_state: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StoreSyncPending(Base):
    """A store message seen but not yet indexed (core/storesync.py): one row each, so a lost cursor
    on a big mailbox no longer rewrites one JSON document of every id on each tick. `next_at` is
    when it is due (unix time: first seen plus the grace period, or the retry after a failure);
    `n` counts failed attempts. It goes with its user's sync state (`mailr store resync`)."""

    __tablename__ = "store_sync_pending"
    user_id: Mapped[int] = mapped_column(ForeignKey("store_sync_state.user_id", ondelete="CASCADE"), primary_key=True)
    jmap_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    next_at: Mapped[float] = mapped_column(Float)
    n: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (Index("ix_store_sync_pending_due", "user_id", "next_at"),)


class InstanceSetting(Base):
    """Instance-wide settings managed by admins (e.g. local_only_domains)."""

    __tablename__ = "instance_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScheduleRun(Base):
    """When a periodic task of the worker's scheduler last ran (core/tasks.py scheduler)."""

    __tablename__ = "schedule_runs"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccessPolicy(Base):
    """What members of one external (identity-provider) group may use (core/access.py).

    `models`: {task: {"allowed": [{"endpoint_id": int, "model": str | None}], "default":
    {"endpoint_id": int, "model": str | None} | None, "any": bool}} for the policy tasks (chat,
    classify). `model` None means any model the endpoint lists. `any` allows every endpoint.
    `features`: {"assistant", "choose_models", "lab", "api_tokens", "local_only_tokens", "hosted_models"}
    booleans (core/access.FEATURES; a missing key is off).
    `needs`: {task: what the seed wanted} while an admin still has to pick the endpoints."""

    __tablename__ = "access_policies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    models: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    needs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CalendarGroup(Base):
    """A mailr group that has group calendars, and the store accounts that stand for it
    (calendar/groups.py): a Stalwart Group account (members get read-write calendars it owns) and
    an "owner" account for the group's read-only calendars. Made on the first group calendar,
    removed with the last."""

    __tablename__ = "calendar_groups"
    group_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    store_group_id: Mapped[str | None] = mapped_column(String(64))
    store_group_login: Mapped[str | None] = mapped_column(String(320))
    owner_id: Mapped[str | None] = mapped_column(String(64))
    owner_login: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = _now()


class GroupCalendar(Base):
    """A calendar an admin attached to a group (calendar/groups.py). `access` is what members may
    do: "write" (a calendar of the group's own account) or "read" (a calendar of the group's owner
    account, shared read-only with the group; `editors`, user ids, may write). `href` is the
    collection's path on the store (/dav/cal/<login>/<slug>/), set once it is made."""

    __tablename__ = "group_calendars"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_name: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(200))
    color: Mapped[str | None] = mapped_column(String(9))
    access: Mapped[str] = mapped_column(String(8), default="write", server_default="write")
    editors: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list, server_default="{}")
    href: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class IdpGroupSeen(Base):
    """Every group name an identity provider has reported at a sign-in (Admin → Access)."""

    __tablename__ = "idp_groups_seen"
    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), default="oidc")
    first_seen_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()


class MessagePassage(Base):
    """A searchable slice of a message body (quoted history excluded).

    `start`/`end` are character offsets into Message.body_text, so a citation can point at
    the exact place. Embeddings come from the instance's embedding endpoint in the worker;
    `embed_model` records which model produced the vector (a model change re-embeds).
    """

    __tablename__ = "message_passages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    conversation_id: Mapped[int | None] = mapped_column(BigInteger)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    start: Mapped[int] = mapped_column(Integer, default=0)
    end: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(40))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True))
    embedding: Mapped[Any] = mapped_column(HALFVEC(EMBED_DIM), nullable=True)
    embed_model: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        Index("ix_passage_user_msg", "user_id", "message_id"),
        # A message's passages by its id alone (building, re-embedding, the cascade from messages).
        Index("ix_passage_message", "message_id"),
        Index("ix_passage_tsv", "tsv", postgresql_using="gin"),
        Index("ix_passage_hash", "content_hash", "embed_model"),
        Index("ix_passage_pending", "user_id", postgresql_where="embedding IS NULL"),
    )


class ChatSession(Base):
    """An assistant conversation. History lives here, not in the client, so its local-only
    sensitivity is sticky: once any tool result touched local-only mail, every later model
    call in the session is treated as sensitive."""

    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), default="")
    # The user renamed the chat (PATCH /agent/sessions/{id}): later turns keep this title.
    title_custom: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    tool_call_id: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str | None] = mapped_column(String(100))
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    steps: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # On a user row: the turn's state, running | done | stopped | error | interrupted.
    status: Mapped[str | None] = mapped_column(String(12))
    # User row: {"skills", "stop_reason", "error"}; assistant rows: {"segment": n, "final": bool}.
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- assistant knowledge
#
# Skills, the user's profile for the assistant, and a small knowledge graph of entities and
# memories. Everything is per user; rows that were learned from
# local-only mail carry `sensitive` and follow the same rules as that mail.


class AssistantProfile(Base):
    """What the assistant knows about the user (typed by the user) and the assistant options."""

    __tablename__ = "assistant_profiles"
    # Fetch server-side defaults (timestamps, tsv) on insert/update: async code cannot lazy-load them.
    __mapper_args__ = {"eager_defaults": True}
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # {"about", "preferred_name", "signoff_name", "role", "timezone", "languages", "writing"}
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # {"learn_from_mail": bool, "auto_recall": bool}
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Bookkeeping: {"skills_seeded": [names], "extract_since": iso send date, "extract_at": iso}
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Skill(Base):
    """A named prompt the user (or the assistant, with approval) wrote. Invoked as //name."""

    __tablename__ = "assistant_skills"
    # Fetch server-side defaults (timestamps, tsv) on insert/update: async code cannot lazy-load them.
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(48))  # slug: a-z 0-9 - _
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")  # when to use it
    instructions: Mapped[str] = mapped_column(Text, default="")  # the prompt body (markdown)
    # Where it applies: compose, reply, chat, summarize. Empty = everywhere it is invoked.
    applies_to: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    # Applied without being invoked (writing defaults), in the contexts of applies_to.
    auto: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source: Mapped[str] = mapped_column(String(20), default="user", server_default="user")  # user|builtin|agent
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (UniqueConstraint("user_id", "name"),)


class Entity(Base):
    """Someone or something the user deals with: a person, organization, project, topic or place
    (the UI calls all but people "topics"). People link to the correspondents table through their
    addresses and `person_id`; one person per user is `is_self`, the user (knowledge/memory.py)."""

    __tablename__ = "kb_entities"
    # Fetch server-side defaults (timestamps, tsv) on insert/update: async code cannot lazy-load them.
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="person")
    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    emails: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    domains: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    conversation_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default=list)
    summary: Mapped[str] = mapped_column(Text, default="")  # one line: who / what it is
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source: Mapped[str] = mapped_column(String(20), default="user", server_default="user")  # user|agent|extracted
    # The user: notes about them show under "About you" (at most one per user).
    is_self: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # The contact (correspondent) this person is, matched by address: one person card shows both.
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_kb_entity_emails", "emails", postgresql_using="gin"),
        Index("ix_kb_entity_convs", "conversation_ids", postgresql_using="gin"),
        Index("uq_kb_entity_self", "user_id", unique=True, postgresql_where=text("is_self")),
    )


class Memory(Base):
    """A note the assistant can recall: a fact, preference, routine or commitment, optionally about
    an entity. `status` suggested = proposed by the background learner (or from a chat) and not
    used until the user accepts it."""

    __tablename__ = "kb_memories"
    # Fetch server-side defaults (timestamps, tsv) on insert/update: async code cannot lazy-load them.
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("kb_entities.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="fact")
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="user")  # user | agent | extracted | chat
    status: Mapped[str] = mapped_column(String(12), default="active", server_default="active")  # active|suggested
    confidence: Mapped[float | None] = mapped_column(Float)
    # Provenance: [{"message_id", "conversation_id"}] or [{"chat_session_id", "chat_message_id"}].
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    use_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True))
    embedding: Mapped[Any] = mapped_column(HALFVEC(EMBED_DIM), nullable=True)
    embed_model: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (
        Index("ix_kb_memory_tsv", "tsv", postgresql_using="gin"),
        Index("ix_kb_memory_user_status", "user_id", "status"),
        Index("ix_kb_memory_refs", "refs", postgresql_using="gin"),
    )


class AssistantOrder(Base):
    """A standing order or routine for the assistant. trigger="message":
    evaluated on each new message after triage; trigger="schedule": a routine run at the cron
    `schedule` in `timezone`."""

    __tablename__ = "assistant_orders"
    __table_args__ = (Index("uq_assistant_orders_builtin", "user_id", "builtin", unique=True,
                            postgresql_where=text("builtin <> ''")),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    trigger: Mapped[str] = mapped_column(String(20), default="message")  # message | schedule
    schedule: Mapped[str] = mapped_column(String(120), default="")  # cron, for trigger=schedule
    timezone: Mapped[str] = mapped_column(String(64), default="")
    query: Mapped[str] = mapped_column(Text, default="")
    question: Mapped[str] = mapped_column(Text, default="")  # yes/no condition for the decision model
    # A choice order: the decision model picks one of these (or none) for the question, and the
    # picked option's actions run. [{"key","name","description","actions"}].
    choices: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    threshold: Mapped[float] = mapped_column(Float, default=0.7, server_default="0.7")
    actions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    errand: Mapped[str] = mapped_column(Text, default="")  # instructions for a background run
    # Speakr recordings (integrations/speakr/orders.py): the recording filter of a `recording`
    # order, or a routine's recording source ({"enabled", "q", "tag", "participant", "days"}).
    recordings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # A message order's condition: only mail the user forwarded from one of their trusted
    # addresses, verified (core/trusted.py).
    from_trusted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # A message order's condition: only mail from the user (one of their own addresses) or from one
    # of their trusted addresses, verified by its domain (core/trusted.from_user). Off by default;
    # the editor offers it for orders that match on a recipient address.
    from_me: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # A built-in order mailr seeds per user ("forwarded_notes": Follow my forwarded notes,
    # assistant/forwards.py); "" for the user's own. A built-in order can be turned off and moved,
    # never deleted or edited.
    builtin: Mapped[str] = mapped_column(String(40), default="", server_default="")
    deliver: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)  # brief | notify | digest
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source: Mapped[str] = mapped_column(String(20), default="user", server_default="user")  # user | assistant
    runs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AssistantRun(Base):
    """One run of an order (or a manual/test run): the audit record and, for errands, the result.
    `slot` makes runs idempotent: "m<message id>" for message orders, the scheduled time for
    routines; (order_id, slot) is unique."""

    __tablename__ = "assistant_runs"
    __table_args__ = (UniqueConstraint("order_id", "slot", name="uq_assistant_runs_order_slot"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("assistant_orders.id", ondelete="SET NULL"), index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="message")  # message | schedule | manual | test
    slot: Mapped[str | None] = mapped_column(String(64))
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    conversation_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | skipped | failed
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    session_id: Mapped[str | None] = mapped_column(String(40))  # the errand's (hidden) chat session
    output: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    delivered: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailReview(Base):
    """A bulk review of existing mail (agent/review.py): the
    conversations frozen at the start, those set aside without a model (`kept`, id -> reason), the
    model's decisions so far (`decisions`, id -> {"a": action, "r": reason}) and how far it got, so a
    stopped or interrupted run continues. It ends in one approval card (`approval_id`)."""

    __tablename__ = "mail_reviews"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_session_id: Mapped[str | None] = mapped_column(String(40), index=True)
    errand: Mapped[bool] = mapped_column(Boolean, default=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    instructions: Mapped[str] = mapped_column(Text, default="")
    labels: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default=list)
    kept: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    decisions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    done: Mapped[int] = mapped_column(Integer, default=0)
    # running | stopping | stopped | done | failed
    status: Mapped[str] = mapped_column(String(16), default="running")
    approval_id: Mapped[int | None] = mapped_column(BigInteger)
    model: Mapped[str] = mapped_column(String(200), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ReviewSenderChoice(Base):
    """The move the user chose on a review card for one sender's mail, where it differed from the
    review's proposal (agent/review.py learn). Later reviews give that sender's conversations this
    move without the model; the latest explicit choice replaces an earlier one. `action` is trash,
    archive, read, keep or label (with `label_id`); `count` is how many conversations the choice
    was made for. Settings → Ambrose → Sweep lists and removes them."""

    __tablename__ = "review_sender_choices"
    __table_args__ = (UniqueConstraint("user_id", "sender", name="uq_review_sender_choices_user_sender"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    sender: Mapped[str] = mapped_column(String(320))  # the lower-case address
    action: Mapped[str] = mapped_column(String(16))
    label_id: Mapped[int | None] = mapped_column(ForeignKey("labels.id", ondelete="CASCADE"))
    count: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = _now()


class ReminderState(Base):
    """What happened to one reminder, shared by every device.

    `key` is the reminder key devices plan with: "e|<uid>|<occurrence>|<minutes>" for events,
    "t|<uid>|<due>|<minutes>" for tasks. `state`: pending (a one-off reminder added with "remind
    before", not fired yet) | snoozed (fires again at snoozed_until) | handled (done with; final)."""

    __tablename__ = "reminder_states"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_reminder_states_user_key"),
                      Index("ix_reminder_states_user_updated", "user_id", "updated_at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(400))
    kind: Mapped[str] = mapped_column(String(1))  # e | t
    uid: Mapped[str] = mapped_column(String(300))
    occurrence: Mapped[str] = mapped_column(String(40), default="")  # event occurrence / task due, as in the key
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(10))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oneoff: Mapped[bool] = mapped_column(Boolean, default=False)  # added from a notification, not a VALARM
    device: Mapped[str] = mapped_column(String(60), default="")  # which client changed it last
    updated_at: Mapped[datetime] = _now()
    anchor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # the occurrence or due, parsed
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # last time the server fired it


class Template(Base):
    """Fixed text the user inserts in compose: typed as
    `;shortcut` or picked from the compose menu. `body_html` is sanitized like a signature;
    {first_name}, {name}, {email}, {my_name} and {date} are filled on insert (core/templates.py)."""

    __tablename__ = "templates"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (Index("uq_templates_user_shortcut", "user_id", "shortcut", unique=True,
                            postgresql_where=text("shortcut <> ''")),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    shortcut: Mapped[str] = mapped_column(String(48), default="", server_default="")  # a-z 0-9 - _; "" = none
    subject: Mapped[str] = mapped_column(Text, default="", server_default="")  # set when the draft has none
    body_html: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------- calendar mirror ----------------
# A copy of the CalDAV collections and resources each caldav connector can reach. CalDAV stays
# the source of truth: rows change only after the server accepted a write, or when sync saw a
# change. `rev` comes from one global sequence (cal_rev_seq), the cursor of /calendar/delta.


class CalCollection(Base):
    __tablename__ = "cal_collections"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (UniqueConstraint("connector_id", "href", name="uq_cal_collections_connector_href"),
                      Index("ix_cal_collections_user_rev", "user_id", "rev"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    href: Mapped[str] = mapped_column(Text)
    displayname: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str | None] = mapped_column(String(9))
    components: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    server_order: Mapped[int | None] = mapped_column(Integer)
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    calendar_tz: Mapped[str | None] = mapped_column(String(64))
    ctag: Mapped[str | None] = mapped_column(Text)
    sync_token: Mapped[str | None] = mapped_column(Text)
    sync_supported: Mapped[bool | None] = mapped_column(Boolean)
    sync_status: Mapped[str] = mapped_column(String(12), default="idle")  # idle|syncing|error|backoff
    sync_error: Mapped[str] = mapped_column(Text, default="")
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    busy: Mapped[bool] = mapped_column(Boolean, default=True)
    notify: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    rev: Mapped[int] = mapped_column(BigInteger, server_default=text("nextval('cal_rev_seq')"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class CalObject(Base):
    """One CalDAV resource (an event series with its overrides, or a task), raw and parsed."""

    __tablename__ = "cal_objects"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("collection_id", "href", name="uq_cal_objects_collection_href"),
        Index("ix_cal_objects_user_uid", "user_id", "uid"),
        Index("ix_cal_objects_user_rev", "user_id", "rev"),
        Index("ix_cal_objects_tsv", "tsv", postgresql_using="gin"),
        Index("ix_cal_objects_categories", "categories", postgresql_using="gin"),
        Index("ix_cal_objects_open_tasks", "user_id", "due_at", "due_date",
              postgresql_where=text("kind = 'task' AND deleted_at IS NULL AND status <> 'COMPLETED'")),
        Index("ix_cal_objects_parent", "user_id", "parent_uid", postgresql_where=text("parent_uid IS NOT NULL")),
        Index("ix_cal_objects_user_kind", "user_id", "kind", postgresql_where=text("deleted_at IS NULL")),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    collection_id: Mapped[int] = mapped_column(ForeignKey("cal_collections.id", ondelete="CASCADE"))
    href: Mapped[str] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    uid: Mapped[str] = mapped_column(String(300), default="")
    kind: Mapped[str] = mapped_column(String(7))  # event | task | journal
    ics: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="")
    class_: Mapped[str] = mapped_column("class", String(12), default="")
    transp: Mapped[str] = mapped_column(String(12), default="")
    color: Mapped[str | None] = mapped_column(String(40))
    url: Mapped[str | None] = mapped_column(Text)
    tzid: Mapped[str | None] = mapped_column(String(64))
    end_tzid: Mapped[str | None] = mapped_column(String(64))
    floating: Mapped[bool] = mapped_column(Boolean, default=False)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_date: Mapped[Any] = mapped_column(Date)
    end_date: Mapped[Any] = mapped_column(Date)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[Any] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    percent: Mapped[int | None] = mapped_column(SmallInteger)
    priority: Mapped[int | None] = mapped_column(SmallInteger)
    sort_order: Mapped[int | None] = mapped_column(BigInteger)  # X-APPLE-SORT-ORDER
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    parent_uid: Mapped[str | None] = mapped_column(String(300))
    repeat_from: Mapped[str | None] = mapped_column(String(10))  # due | completion (tasks)
    rrule: Mapped[str | None] = mapped_column(Text)
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    has_overrides: Mapped[bool] = mapped_column(Boolean, default=False)
    range_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # null = open-ended
    expanded_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expanded_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    organizer: Mapped[str | None] = mapped_column(String(320))
    organizer_name: Mapped[str] = mapped_column(Text, default="")
    is_organizer: Mapped[bool] = mapped_column(Boolean, default=False)
    my_partstat: Mapped[str | None] = mapped_column(String(20))
    attendees: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    attendee_count: Mapped[int] = mapped_column(Integer, default=0)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    dtstamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # CREATED property
    alarms: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    # The event carries X-MAILR-NO-REMINDER:TRUE (ical.NO_REMINDER): no fallback reminder either.
    no_reminder: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    tags_text: Mapped[str] = mapped_column(Text, default="")
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(
        "setweight(to_tsvector('simple', coalesce(summary, '')), 'A') || "
        "setweight(to_tsvector('simple', coalesce(location, '')), 'B') || "
        "setweight(to_tsvector('simple', coalesce(tags_text, '')), 'B') || "
        "setweight(to_tsvector('simple', left(coalesce(description, ''), 20000)), 'C')", persisted=True))
    rev: Mapped[int] = mapped_column(BigInteger, server_default=text("nextval('cal_rev_seq')"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class CalOccurrence(Base):
    """Event instances inside the materialized window (expand.py)."""

    __tablename__ = "cal_occurrences"
    object_id: Mapped[int] = mapped_column(ForeignKey("cal_objects.id", ondelete="CASCADE"), primary_key=True)
    occurrence: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    collection_id: Mapped[int] = mapped_column(BigInteger)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    is_override: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    busy: Mapped[bool] = mapped_column(Boolean, default=True)
    # Only on overrides, and only where they differ from the master (null = the master's value).
    summary: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(20))


class ReminderSchedule(Base):
    """The server's reminder plan (calendar/planner.py)."""

    __tablename__ = "reminder_schedule"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_reminder_schedule_user_key"),
                      Index("ix_reminder_schedule_due", "fire_at", postgresql_where=text("fired_at IS NULL")))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(400))
    kind: Mapped[str] = mapped_column(String(1))  # e | t
    object_id: Mapped[int | None] = mapped_column(ForeignKey("cal_objects.id", ondelete="CASCADE"))
    uid: Mapped[str] = mapped_column(String(300), default="")
    occurrence: Mapped[str] = mapped_column(String(40), default="")
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    anchor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(8), default="valarm")  # valarm|default|oneoff|snooze
    local_only: Mapped[bool] = mapped_column(Boolean, default=False)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_at: Mapped[datetime] = _now()


# Speakr recordings. The mirror holds metadata
# only: title, dates, duration, participants, tags, folder and status. Summaries, transcripts and
# notes are fetched from Speakr when someone reads them and are never stored here.


class SpeakrRecording(Base):
    """One recording of a Speakr connector, as the last sync saw it."""

    __tablename__ = "speakr_recordings"
    __table_args__ = (UniqueConstraint("connector_id", "recording_id", name="uq_speakr_recordings_connector_rec"),
                      Index("ix_speakr_recordings_user_date", "user_id", "meeting_date"),
                      Index("ix_speakr_recordings_tsv", "tsv", postgresql_using="gin"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    recording_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text, default="")
    meeting_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_upstream: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_upstream: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float | None] = mapped_column(Float)
    participants: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    tags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    folder: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="")
    has_transcript: Mapped[bool] = mapped_column(Boolean, default=False)
    has_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    audio_available: Mapped[bool] = mapped_column(Boolean, default=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # A recording another Speakr user shared with this account (the card's "Include recordings
    # shared with me"): whose it is and whether the share allows edits.
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    owner_name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    can_edit: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # mailr's own external references on the recording in Speakr: [{kind, ref}], so a link
    # mailr wrote there (an event, a conversation) is found before the matcher scores by time.
    mailr_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    # The summary (Markdown) as Speakr last gave it, kept for search.
    # summary_at is when it was read; NULL means it is read again on the next sync. Transcripts
    # are never stored.
    summary_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    summary_hash: Mapped[str] = mapped_column(String(64), default="", server_default="")
    summary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(
        "setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
        "setweight(to_tsvector('simple', left(coalesce(summary_text, ''), 100000)), 'B')", persisted=True))
    synced_at: Mapped[datetime] = _now()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecordingLink(Base):
    """A recording linked to an event occurrence, a conversation or a task. Suggested links come
    from the matcher; the user confirms or dismisses them, and a dismissed one never returns."""

    __tablename__ = "recording_links"
    __table_args__ = (UniqueConstraint("connector_id", "recording_id", "kind", "target", "occurrence",
                                       name="uq_recording_links_target"),
                      Index("ix_recording_links_user_target", "user_id", "kind", "target"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    recording_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12))  # event | conversation | task
    target: Mapped[str] = mapped_column(String(300))  # event uid, conversation id, task uid
    occurrence: Mapped[str] = mapped_column(String(40), default="")  # events only
    state: Mapped[str] = mapped_column(String(12), default="suggested")  # suggested | confirmed | dismissed
    score: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(12), default="auto")  # auto | user | assistant
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class SpeakrWebhookDelivery(Base):
    """A Speakr webhook delivery already accepted (replay protection; integrations/speakr/webhook.py)."""

    __tablename__ = "speakr_webhook_deliveries"
    __table_args__ = (UniqueConstraint("connector_id", "delivery_id", name="uq_speakr_webhook_delivery"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    delivery_id: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = _now()


class SpeakrSpeakerPerson(Base):
    """A Speakr speaker mapped to a mailr person by email (integrations/speakr/people.py). The
    speaker key is "id:<speaker id>" when Speakr gives one, else "name:<lower-case name>"."""

    __tablename__ = "speakr_speaker_people"
    __table_args__ = (UniqueConstraint("connector_id", "speaker_key", "email", name="uq_speakr_speaker_people"),
                      Index("ix_speakr_speaker_people_email", "user_id", "email"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    speaker_key: Mapped[str] = mapped_column(String(200))
    speaker_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(320))
    person_name: Mapped[str] = mapped_column(String(300), default="")
    state: Mapped[str] = mapped_column(String(12), default="confirmed")  # confirmed | dismissed
    source: Mapped[str] = mapped_column(String(12), default="user")  # user | assistant
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class SpeakrParticipantAlias(Base):
    """A recording participant name that is the same person as another identity on one Speakr
    connector (integrations/speakr/participants.py). `target` is "me", "email:<address>" or
    "name:<name>"; state alias | dismissed (a suggested merge the user turned down) | separate (the
    name stands on its own; target empty)."""

    __tablename__ = "speakr_participant_aliases"
    __table_args__ = (UniqueConstraint("connector_id", "name", "state", "target", name="uq_speakr_participant_aliases"),
                      Index("ix_speakr_participant_aliases_conn", "connector_id", "name"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    target: Mapped[str] = mapped_column(String(330), default="")
    target_label: Mapped[str] = mapped_column(String(300), default="")
    state: Mapped[str] = mapped_column(String(12), default="alias")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ScreenerSender(Base):
    """A sender the screener has met (core/screener.py): the key is an address, or the domain of a
    corporate domain (shared mail providers and institutions are always by address). status:
    pending (waiting in the Screener) | allowed | blocked | label. A blocked sender's mail goes to
    Spam, or to Trash with block_to "trash"; a label sender's mail gets label_id and skips the
    Inbox."""

    __tablename__ = "screener_senders"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_screener_senders"),
                      Index("ix_screener_senders_status", "user_id", "status"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(320))
    email: Mapped[str] = mapped_column(String(320), default="")
    name: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(10), default="pending")
    label_id: Mapped[int | None] = mapped_column(ForeignKey("labels.id", ondelete="SET NULL"))
    block_to: Mapped[str] = mapped_column(String(10), default="spam")  # spam | trash
    decided_by: Mapped[str] = mapped_column(String(60), default="")  # user | assistant | token:<id>
    created_at: Mapped[datetime] = _now()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ItemVersion(Base):
    """A version of an item that people and agents edit live (core/live.py): a draft, an event,
    a task, a memory or a filter rule. `version` counts up per item from 1; `content` is the
    item's editable fields at that version, kept for a while so a write based on an older version
    can be merged three ways. `by`: user | device | assistant | agent:<token name> | sync | seed.
    `origin`: the client (browser tab or device) that wrote it, when it said."""

    __tablename__ = "item_versions"
    __table_args__ = (UniqueConstraint("user_id", "kind", "item_id", "version", name="uq_item_versions"),
                      Index("ix_item_versions_created", "created_at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(12))
    item_id: Mapped[str] = mapped_column(String(300))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    by: Mapped[str] = mapped_column(String(80), default="user", server_default="user")
    origin: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()


# Notes and checklists keep their tables in their own module.
from mailr.notes.models import (  # noqa: E402, F401
    CheckitemCheck,
    CheckitemRank,
    CheckitemUsualDismissed,
    Note,
    NoteItem,
    NoteLabel,
    NoteLink,
    NoteRevision,
    NoteSection,
    NoteSectionViewer,
    NoteViewerState,
)

# Tables keep theirs in their own module too.
from mailr.tables.models import (  # noqa: E402, F401
    DataTable,
    TableColumn,
    TableRow,
    TableRowChange,
    TableRowRef,
    TableSchemaChange,
    TableSnapshot,
    TableView,
)


class HouseholdInvite(Base):
    """A one-time invite link for a household member (core/invites.py). Only a hash of the token
    is kept; the link is shown once, when the admin makes it. `defaults` are setup defaults in
    the shape of an onboarding preset (core/onboarding.py), applied when the member accepts."""

    __tablename__ = "household_invites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    defaults: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportBatch(Base):
    """One import a user ran (core/imports.py): Google Keep notes, an .ics file, a .vcf file.
    `undo` lists what the import made or changed, so one step takes it back."""

    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # keep | ics | vcf
    source: Mapped[str] = mapped_column(String(300), default="", server_default="")
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    undo: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = _now()
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
