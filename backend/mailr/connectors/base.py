"""The connector contract.

A connector moves mail between an upstream service and the user's mailr store. The core
never talks to a provider directly: it calls these methods, and connectors deliver mail
through `ingest.ingest_raw`, which dedupes, stores in Stalwart, indexes, threads, and runs rules.

Modes:
  store   mailr is the store of record. New upstream mail is ingested, then deleted upstream
          after `retention_days`, but only when `delete_upstream` has been armed.
  mirror  upstream stays authoritative. Local actions (labels, read, move, delete) are
          written back via `write_back`.
  import  one-time copy of history, then the connector goes idle.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, ClassVar

from mailr.models import Connector as ConnectorRow


@dataclass
class Capabilities:
    push: bool = False           # IDLE / streaming notifications
    send: bool = False
    write_back: bool = False     # can mirror flags / labels / moves upstream
    delete: bool = False
    labels: bool = False         # upstream supports multi-label (Gmail, M365 categories)
    calendar: bool = False
    modes: tuple[str, ...] = ("store", "mirror", "import")


@dataclass
class TestResult:
    ok: bool
    detail: str = ""
    folders: list[dict[str, Any]] = field(default_factory=list)
    identities: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)


@dataclass
class WriteBack:
    """A local change to reflect upstream (mirror mode)."""

    upstream: dict[str, Any]           # Message.upstream of the affected message
    add_labels: list[str] = field(default_factory=list)      # label names / roles
    remove_labels: list[str] = field(default_factory=list)
    seen: bool | None = None
    flagged: bool | None = None
    delete: bool = False
    message_id: str | None = None  # Message-ID header: makes COPY retries idempotent
    # The stored message's content hash (core/mime.content_hash) and its mailr id: the only
    # identity an untracked label-folder copy may be expunged by (ImapConnector._untracked_label_copy).
    content_hash: str | None = None
    mailr_id: int | None = None
    # The message reached the user as mail (anything not sent by them, and mail they sent to one
    # of their own addresses): its provider copy outside Sent and Drafts is the delivered one, and
    # a copy in Sent never stands in for it (ImapConnector.write_back). False only for mail the
    # user sent to others, whose Sent copy is where it lives.
    received: bool = True


class MailConnector(abc.ABC):
    kind: ClassVar[str]
    capabilities: ClassVar[Capabilities]

    def __init__(self, row: ConnectorRow, secrets: dict[str, Any]):
        self.row = row
        self.secrets = secrets or {}
        # Whether the account's servers may be private (LAN) addresses (core/netguard.py); the
        # loader sets it from the owner. None = the instance default.
        self.private_ok: bool | None = None

    @abc.abstractmethod
    async def test(self) -> TestResult:
        """Check that sign-in works; list folders and discovered identities."""

    @abc.abstractmethod
    async def run(self, ctx: SyncContext) -> None:
        """Long-running sync loop. Must return promptly when ctx.stopping is set.

        Initial sync imports history (respecting config['since'] / folder selection),
        then waits for new mail (IDLE or polling) and ingests it.
        """

    async def send(self, raw: bytes, mail_from: str, rcpt_to: list[str]) -> str:
        """Submit a message upstream. Returns the provider's queue id / response text."""
        raise NotImplementedError

    async def write_back(self, changes: list[WriteBack]) -> None:
        """Mirror mode: apply local changes upstream."""
        return

    async def delete_upstream(self, upstream_refs: list[dict[str, Any]]) -> int:
        """Store mode: delete already-stored messages upstream. Returns count deleted."""
        raise NotImplementedError


class SyncContext:
    """Passed to MailConnector.run. Implemented in connectors/runner.py."""

    stopping: Any  # asyncio.Event

    async def ingest(self, raw: bytes, **kw: Any) -> Any: ...  # noqa: E704
    async def save_state(self, state: dict[str, Any]) -> None: ...  # noqa: E704
    async def set_status(self, status: str, detail: str = "") -> None: ...  # noqa: E704
    async def known_upstream(self, folder: str, uidvalidity: int) -> set[int]: ...  # noqa: E704


REGISTRY: dict[str, type[MailConnector]] = {}


def register(cls: type[MailConnector]) -> type[MailConnector]:
    REGISTRY[cls.kind] = cls
    return cls
