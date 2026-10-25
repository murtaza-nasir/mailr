"""Response model of GET /me (api/auth_routes.py)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from mailr.api.contract.base import IsoTime, Out, Untyped, absent


class MeUser(Out):
    """The signed-in user (api/schemas.user_out). A token without `read` gets id, email and name only."""

    id: int
    email: str
    name: str
    role: Literal["admin", "user"] = absent()
    created_at: IsoTime | None = absent()
    last_login_at: IsoTime | None = absent()
    disabled: bool = absent()
    oidc: bool = absent()
    has_password: bool = absent()
    email_verified: bool = absent()


class Identity(Out):
    """A send-as address (api/schemas.identity_out)."""

    id: int
    email: str
    name: str
    reply_to: str
    signature_html: str
    is_default: bool
    connector_id: int | None
    receives: list[str]
    has_avatar: bool = Field(description="A picture was uploaded (GET /identities/{id}/avatar answers 404 otherwise).")


class TokenInfo(Out):
    name: str
    local_only: bool


class AccountPicture(Out):
    has: bool
    source: Literal["upload", "idp"] | None
    version: int = Field(description="Changes when the picture changes (cache-busting).")
    idp_available: bool


class SetupInfo(Out):
    has_accounts: bool
    guide: str
    calendar: bool
    url: str


class Features(Out):
    transcribe: bool
    # Store mode can be chosen for a mail account: off while the daily mail export is off.
    store_mode: bool
    # False: deletion at the provider in store mode is not checked against the mail export
    # (MAILR_STORE_BACKUP_CHECK=false).
    store_backup_check: bool


class Me(Out):
    """GET /me: who is signed in, their settings, identities and what they may do."""

    user: MeUser
    csrf: str | None
    scopes: list[str]
    token: TokenInfo | None
    settings: dict[str, Any] = absent(description="Every user setting, defaults filled in "
                                                       "(tools/registry/settings).")
    identities: list[Identity] = absent()
    counts: dict[str, int] = absent(description="The older sidebar numbers: scheduled, approvals, "
                                                     "failed, snoozed, drafts, waiting.")
    features: Features = absent()
    picture: AccountPicture = absent()
    access: Untyped = absent(description="What the user's access policy allows.")
    setup: SetupInfo = absent()
    version: str = absent(description="The server's version: the release it was built from (MAILR_RELEASE), "
                                      "else the package version.")
