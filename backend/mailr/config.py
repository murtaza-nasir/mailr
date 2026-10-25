"""Instance configuration. Everything deployment-specific comes from the environment."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAILR_", env_file=".env", extra="ignore")

    # Public URL the browser uses, e.g. https://mail.example.com (no trailing slash).
    base_url: str = "http://localhost:8025"
    # Redirect page loads that arrive through another hostname to base_url.
    canonical_redirect: bool = True
    # Postgres DSN for mailr's own data.
    database_url: str = "postgresql+asyncpg://mailr:mailr@localhost:5432/mailr"
    # Connections each process keeps open to Postgres, and the extra ones it may open in a burst.
    # Tests set them low: many test workers share one development server.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # 32+ random bytes, base64. Wraps per-user data keys. Never commit it.
    secret_key: str = Field(default="", min_length=0)
    # Opaque public ids for conversations and messages (core/publicid.py). While the legacy
    # switch is on, plain internal ids are still accepted on input (old links, older apps).
    public_ids: bool = False
    public_ids_legacy: bool = True
    # HTTP proxy for the image proxy's fetches, e.g. a VPN container's proxy
    # (http://user:pass@vpn-host:8888). When set it wins over the admin setting.
    image_proxy_egress: str = ""
    # Fail closed (core/egress.py): every fetch of a URL taken from mail (remote images, sender
    # logos and pictures, one-click unsubscribe) needs a working egress proxy and never goes
    # out directly from the server. Off by default so an install without a VPN keeps working;
    # turn it on together with a VPN egress. The image proxy is then always on.
    require_egress: bool = False
    # Exit addresses the egress check (core/egress.py) treats as a leak, comma-separated (the
    # server's own public address). Empty = learn it with one direct request to the echo service.
    egress_forbidden_ips: str = ""
    # The egress proxy as a page-reading browser (an admin's browserless, websearch/readers.py)
    # reaches it, when that differs from the address mailr uses (a container name on mailr's own
    # stack network). Empty: the egress proxy URL itself.
    browser_egress_proxy: str = ""
    # A page reader set up by the server's environment (websearch/readers.py, "Browser (server)"):
    # a browserless at this URL (the stack's optional `browser` service: http://browser:3000), its
    # TOKEN, and the route its pages take at first (egress | direct; admins can change it later).
    # The token is used only from here, never stored in the database or shown.
    browser_url: str = ""
    browser_token: str = ""
    browser_route: str = "egress"
    # The browser gate (websearch/gate.py): the only way out of the stack's browser. On the app:
    # the gate's host name as the browser reaches it (the compose service `browser-gate`); the
    # server's reader then sends the browser's pages through it. Empty: no gate (a browserless
    # outside the stack). On the gate itself: the egress proxy its egress port forwards to, and the
    # browserless its control port relays to.
    browser_gate: str = ""
    browser_gate_upstream: str = ""
    browser_gate_target: str = "chrome:3000"
    # Optional separate key that signs the OIDC sign-in state (api/auth_routes.py); blank = secret_key.
    session_secret: str = ""
    session_days: int = 30

    # Stalwart (the mail store).
    stalwart_url: str = "http://localhost:8080"
    stalwart_admin_user: str = "mailr-svc@mailr.internal"
    stalwart_admin_password: str = ""
    # Domain under which mailr creates Stalwart principals (internal only, never routed).
    stalwart_domain: str = "mailr.internal"
    # Recursive resolver Stalwart should use for spam-check DNS lookups (blank = Stalwart default).
    stalwart_resolver_ip: str = ""
    # What users type into phone/desktop IMAP clients.
    imap_hostname: str = "localhost"
    imaps_port: int = 993
    imap_port: int = 143
    # Where phones download the Android app (the setup guide's QR code and /get-app page). Empty:
    # the APK published to this server (mailr app-publish; core/phone_apps.py), served at
    # /api/v1/app/mailr.apk, else the latest GitHub release.
    android_apk_url: str = ""
    # The package (applicationId) an APK must have to be published as each app (core/phone_apps.py).
    android_package_mailr: str = "io.github.murtazanasir.mailr"
    android_package_notes: str = "io.github.murtazanasir.mailr.notes"

    # Sign-in.
    local_login: bool = True
    allow_signup: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid email profile"
    # The provider's name on the sign-in buttons ("Continue with Pocket ID"). Empty = the buttons
    # say "single sign-on": a name guessed from the issuer's host read as "Id" or "Sso".
    oidc_name: str = ""
    oidc_admin_group: str = ""
    # Off by default: with it on, anyone the identity provider signs in gets an account (and a
    # mailbox). oidc_auto_create_group limits auto-creation to members of one IdP group.
    oidc_auto_create: bool = False
    oidc_auto_create_group: str = ""
    # Hosts besides the issuer's own that the sign-in picture (the `picture` claim) may be fetched
    # from, comma-separated (core/account_picture.py), e.g. lh3.googleusercontent.com for Google.
    # They must be public. Empty = only the issuer's host.
    oidc_picture_hosts: str = ""
    # First admin, created on startup when there are no users.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # Models. The registry of endpoints lives in the database; these seed it on first start.
    seed_model_endpoints: str = ""  # JSON list of endpoints

    # Push notifications (UnifiedPush / ntfy).
    ntfy_url: str = ""
    ntfy_token: str = ""

    # Storage for exports and attachment cache.
    data_dir: str = "/data"

    # Worker behaviour.
    worker_concurrency: int = 4
    # Writes to the users' mail servers at once per worker, never two for one mail account.
    upstream_concurrency: int = 4
    # Model calls at once per model endpoint, in the app and all workers together (intel/slots.py); 0: no cap.
    model_concurrency: int = 4
    default_retention_days: int = 14
    agent_send_hold_seconds: int = 60
    user_undo_send_seconds: int = 10

    # Inbox tab indicators (api/layout_routes.py): a deadline counts in a tab's "Due" indicator
    # until this many days past its date, then drops, so a forgotten one does not keep the tab
    # red. 0 = only today and later.
    overdue_days: int = Field(default=7, ge=0)

    # Ambrose's memory relevance gate (knowledge/relevance.py). A note found only by meaning
    # qualifies when its cosine distance to the request is under this; 0 = the calibrated value
    # for the embedding model (relevance.MAX_DISTANCE).
    memory_max_distance: float = Field(default=0.0, ge=0.0, le=2.0)
    # The decision model that checks candidate notes: an endpoint's name, "" = the first enabled
    # local Jev-protocol endpoint, "off" = no check. The check waits at most memory_check_ms.
    memory_check_endpoint: str = ""
    memory_check_ms: int = Field(default=1500, ge=100, le=10000)
    # The largest prompt the assistant sends to its model, in tokens (counted roughly): the
    # oldest turns of a long chat give way first, and a question that does not fit is answered
    # with the reason. Set it under the chat model's context window (a 32k local model: 28000).
    assistant_prompt_tokens: int = Field(default=100_000, ge=4_000)

    # The daily Maildir export of every user's mail (core/backup.py). Off: no mail export runs,
    # store mode is refused and upstream deletion stops (it needs the export) unless
    # store_backup_check is off; the calendar, contacts, notes and tables exports run as before.
    mail_export: bool = True
    # Store mode checks each message against that export (and the backup target) before it is
    # deleted at the provider. Off: store mode works without the export, and a message older
    # than the retention window is deleted at the provider once it is in the mail store
    # (connectors/tasks.py retention); backups are then the administrator's own.
    store_backup_check: bool = True
    # Optional off-host copy of the Maildir export (a mounted NAS path).
    backup_target: str = ""
    # The nightly database dump (core/dbdump.py): on or off, the hour (UTC) it starts, how many
    # nightly dumps stay, and the folder (empty: <backup_target>/postgres, or <data_dir>/backups/
    # postgres without a target). A dump before migrations at start when a target is set.
    db_dump: bool = True
    db_dump_hour: int = Field(default=9, ge=0, le=23)
    db_dump_keep: int = Field(default=14, ge=1)
    db_dump_dir: str = ""
    db_dump_before_migrate: bool = True
    # A dump this recent at the current revision (taken just before a deploy) stands in for the
    # dump before migrations, so a start with migrations does not wait for a second one.
    db_dump_fresh_minutes: int = 120
    # Folder of pg_dump and pg_restore when they are not on PATH (the image has them).
    pg_bin_dir: str = ""
    # Free-space check of the readiness endpoint (core/health.py): the data folder, the backup
    # target and these extra paths (comma-separated). Below disk_warn_percent free the check
    # warns; below disk_fail_percent it fails.
    disk_check_paths: str = ""
    disk_warn_percent: float = Field(default=15.0, ge=0, le=100)
    disk_fail_percent: float = Field(default=5.0, ge=0, le=100)
    # The daily calendar and address-book export (calendar/backup.py) keeps the newest snapshot
    # of each day for this many days, locally and on the backup target.
    calendar_export_keep_days: int = Field(default=30, ge=1)

    # Monitoring (core/health.py). Outbound heartbeats for push monitors (e.g. Uptime Kuma
    # push URLs); empty = off. The worker GETs heartbeat_url every 5 minutes with status up or
    # down, and backup_heartbeat_url after a completed daily export.
    heartbeat_url: str = ""
    backup_heartbeat_url: str = ""
    # Readiness checks (comma-separated names) reported in /health/ready but never failing it.
    health_optional: str = ""
    # Monitor token for the health endpoints' details (header X-Health-Token). Without it, or
    # without an admin session, /health/ready and /health/egress give only the verdict.
    health_token: str = ""
    # Read secrets written before they were bound to their record (crypto v1) where a context
    # is required. Off: the app's startup upgrade rewrites them, so such a value is refused.
    crypto_legacy_reads: bool = False

    # Error reporting to a Sentry-compatible server such as GlitchTip (core/errors.py); empty: off.
    # sentry_dsn is the server's project (API and worker); sentry_browser_dsn the web app's
    # (empty: the same project). The web app sends its reports through the app
    # (/api/v1/errors/envelope), so the server only needs to be reachable from the app.
    sentry_dsn: str = ""
    sentry_browser_dsn: str = ""
    sentry_environment: str = "production"
    # The release reports are tagged with (mailr@<release>); the image sets it at build time.
    release: str = ""

    # Agent approvals.
    approval_ttl_hours: int = 24

    # Outbound connection guard (core/netguard.py). Who may point mailr at private (LAN)
    # addresses (mail servers, CalDAV/CardDAV, push endpoints, Speakr): "admins" (default; what
    # an admin account sets up may use the LAN), "all" (also true) or "none" (also false).
    # MAILR_OUTBOUND_ALLOW (comma-separated hosts or CIDRs) opens listed targets to everyone.
    allow_private_hosts: str = "admins"
    outbound_allow: str = ""
    # Internal service names user URLs never reach, besides the store and the database
    # (the compose service names). Their addresses are refused too.
    internal_hosts: str = ("egress,resolver,app,worker,stalwart,postgres,db,browser,browser-gate,chrome,glitchtip,"
                            "glitchtip-postgres")

    # The oldest Android app the API answers (core/appversion.py): builds before 0.5.0
    # send phone shares without their origin marker. "" turns the check off.
    android_min_version: str = "0.5.0"

    # Authentication-Results headers are believed only from these authserv-ids (comma-separated:
    # the receiving server's hostname, plus an upstream provider's id for connector mail).
    # Empty = the store's own hostname (MAILR_IMAP_HOSTNAME) only.
    trusted_authserv_ids: str = ""

    # Request bodies larger than this are refused with 413 (bytes), except on the upload routes
    # that set their own, larger limits.
    max_request_bytes: int = 50 * 1024 * 1024
    # Per-user limits on uploads (api/mail_write.py, api/connector_routes.py). One attachment for
    # a draft; everything a user's attachments hold until they are sent or expire (30 days unused).
    max_attachment_bytes: int = Field(default=35 * 1024 * 1024, ge=1)
    uploads_quota_bytes: int = Field(default=1024**3, ge=1)
    # Mailbox files uploaded for imports in the last 24 hours (0 = no limit), and file imports
    # that have not finished at once. The size of one file is MAILR_MAX_IMPORT_BYTES.
    import_bytes_per_day: int = Field(default=40 * 1024**3, ge=0)
    max_concurrent_imports: int = Field(default=2, ge=1)
    # Live-update streams (/api/v1/events) one user may hold open at once.
    sse_max_per_user: int = 12
    # Requests an API token may make that call a model (core/throttle.py; the user is not limited).
    token_model_calls_per_minute: int = 20
    token_model_calls_per_hour: int = 300

    # Addresses allowed to set X-Forwarded-For / -Proto (the reverse proxy). Only these are
    # believed, and uvicorn takes the right-most untrusted address, so a client cannot forge
    # its IP by sending the header itself. Comma-separated IPs or networks.
    # Empty (the default): loopback and the gateway of the stack network (core/proxies.py), which
    # is where a reverse proxy on the Docker host reaches the app from. Behind a reverse proxy on
    # another host, set its address.
    trusted_proxies: str = ""
    # The stack network's subnet (the compose file passes it); its gateway is trusted by default.
    net_subnet: str = ""

    log_level: str = "INFO"
    # Logs (core/logsetup.py): "json" (one JSON object per line) or "text", on standard output; and
    # the same lines in rotating files under log_dir (empty: <data_dir>/logs; "off": no files),
    # log_files files of log_file_mb MB each per process, so the history survives a redeploy.
    log_format: str = "json"
    log_dir: str = ""
    log_file_mb: int = Field(default=10, ge=1)
    log_files: int = Field(default=10, ge=1)
    # One line per HTTP request (method, route template, status, time, user id; never the query
    # string), on the mailr.access logger. Off: only the slow-request lines.
    access_log: bool = True
    # An API request slower than this is logged as one warning line (core/slowlog.py); 0 = off.
    slow_request_ms: int = 2000
    dev: bool = False
    # Tests only: hash passwords with the cheapest Argon2 parameters (core/auth.py). Ignored
    # unless MAILR_DEV is on; the test suite sets both.
    test_cheap_password_hashes: bool = False

    # Dictation through the user's own speech server (agent/transcribe.py): an
    # OpenAI-compatible base URL on the local network, e.g. http://asr.local:9000. Empty = off.
    asr_url: str = ""
    asr_model: str = "whisper-1"
    asr_max_bytes: int = 8_000_000
    asr_max_seconds: int = 120

    # Native apps (Android): public OIDC client id(s) registered at the same issuer
    # (comma-separated) and the app's redirect URI.
    oidc_native_client_id: str = ""
    oidc_native_redirect: str = "io.github.murtazanasir.mailr:/oauth2redirect"
    # The Notes app (Android) signs in with the same client id(s) but
    # receives the redirect on its own scheme, so the two apps never answer each other's sign-in.
    oidc_native_notes_redirect: str = "io.github.murtazanasir.mailr.notes:/oauth2redirect"
    # Other addresses of this server that the apps may sign in through (comma-separated, such as
    # http://192.168.1.20:8025 on the local network). The app's sign-in is bound to the address it
    # used (api/native_auth.check_binding); MAILR_BASE_URL always counts.
    oidc_native_origins: str = ""
    # Accept native sign-ins from apps older than the server binding (no nonce seed). Off: they get
    # 426 and an update notice. Turn on only for a short changeover; it lets unbound sign-ins through again.
    oidc_native_unbound: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


def server_version() -> str:
    """The version this server reports (the web's help menu, the phone's Settings, GET /me
    `version`): MAILR_RELEASE, which the published image sets from its release tag, else the
    installed package's version (backend/pyproject.toml)."""
    if r := get_settings().release.strip():
        return r.removeprefix("v")
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("mailr")
    except PackageNotFoundError:
        return "dev"
