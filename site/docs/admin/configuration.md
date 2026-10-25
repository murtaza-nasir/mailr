# Configuration

Mailr is configured in `deploy/.env`. On this page the settings are grouped by topic. Every variable is listed with its default in one table in [Environment variables](../reference/environment.md).

After you change `.env`, check it and apply it:

```sh
docker compose run --rm cli mailr check-config
docker compose up -d
```

With the `cli` service, one command is run with the app's image and settings, also while the stack is up.
`mailr check-config` prints one line per problem, such as
`error   MAILR_OIDC_CLIENT_ID: empty while MAILR_OIDC_ISSUER is set.` It never prints a value. The same
checks are run when the app and the worker start. On an error they stop with these lines in the
log, which can be read with `docker compose logs app`. Warnings are logged, and the app starts.

## Required

| Variable | Description |
|---|---|
| `MAILR_BASE_URL` | The public URL of the web app, such as `https://mail.example.com`, with no trailing slash. It is used for sign-in redirects, cookies and links in notifications. |
| `MAILR_SECRET_KEY` | 32 or more random bytes in base64. Stored account passwords and keys are protected with it. Generate it with `mailr gen-secret`. |
| `MAILR_SESSION_SECRET` | Optional, though recommended: a second random key. Short-lived sign-in data, such as the single sign-on state, is signed with it. When it is empty, `MAILR_SECRET_KEY` is used. |
| `POSTGRES_PASSWORD` | The Postgres password. `MAILR_DATABASE_URL` is built from it in the compose file. |
| `STALWART_ADMIN_PASSWORD` | The recovery admin password of the bundled mail server. |
| `MAILR_SVC_PASSWORD` | The password of Mailr's service account in the mail server. |

## The first admin

| Variable | Description |
|---|---|
| `MAILR_BOOTSTRAP_ADMIN_EMAIL` | The first admin account. It is created when the app starts with no users in the database, so only on the first start. Later changes to these two values have no effect. |
| `MAILR_BOOTSTRAP_ADMIN_PASSWORD` | That admin's password. Leave it empty when the admin signs in with single sign-on only. |

## Sign-in

| Variable | Description |
|---|---|
| `MAILR_LOCAL_LOGIN` | Allow sign-in with email and password. On by default. |
| `MAILR_ALLOW_SIGNUP` | Let people create their own accounts. Off by default. |
| `MAILR_SESSION_DAYS` | How many days a sign-in lasts (30). |
| `MAILR_OIDC_ISSUER` | Your OIDC provider's issuer URL. Single sign-on is on when it is set together with a client id. |
| `MAILR_OIDC_CLIENT_ID` | The OIDC client id. |
| `MAILR_OIDC_CLIENT_SECRET` | The client secret. None is required for public clients with PKCE. |
| `MAILR_OIDC_SCOPES` | The requested scopes (`openid email profile`). |
| `MAILR_OIDC_ADMIN_GROUP` | Members of this group become Mailr admins. |
| `MAILR_OIDC_AUTO_CREATE` | Create a Mailr account on a person's first single sign-on. Off by default. |
| `MAILR_OIDC_AUTO_CREATE_GROUP` | With auto-create on, only members of this group get an account. |
| `MAILR_OIDC_NATIVE_CLIENT_ID` | Public client id (or ids, comma separated) for the Android app. Without it, only email and password are offered in the app. |
| `MAILR_OIDC_NATIVE_REDIRECT` | The Android app's redirect URI. The default matches the published app. |
| `MAILR_OIDC_NATIVE_NOTES_REDIRECT` | The Notes app's redirect URI. The default matches the published app. Add it to the same OIDC client. |

Details: [Sign-in and OIDC](sign-in.md).

## Network and proxy

| Variable | Description |
|---|---|
| `MAILR_HTTP_PORT` | The host port of the web app (8025). |
| `MAILR_HTTP_BIND` | The host address that port is published on (`127.0.0.1` by default). |
| `MAILR_TRUSTED_PROXIES` | Addresses allowed to set `X-Forwarded-For` and `X-Forwarded-Proto`. Empty (the default): loopback and the gateway of the stack network (`MAILR_NET_SUBNET`), the source of connections from a proxy on the same host. See [Reverse proxy](reverse-proxy.md). |
| `MAILR_MAX_REQUEST_BYTES` | The largest request body accepted (50 MB). Imports and recording uploads have their own limits. |
| `MAILR_CANONICAL_REDIRECT` | Redirect page loads that arrive on another host name to `MAILR_BASE_URL`. On by default. |
| `MAILR_ALLOW_PRIVATE_HOSTS` | Who may connect Mailr to private network addresses, such as a self-hosted mail, calendar or push server: `admins` (default), `all` or `none`. |
| `MAILR_OUTBOUND_ALLOW` | Hosts or networks open to every user's outbound connections, comma separated. |
| `MAILR_INTERNAL_HOSTS` | The stack's own service names, which are closed to outbound connections. |
| `MAILR_PUBLIC_IDS` | Use opaque public ids for conversations, messages and approvals in URLs and the API. |
| `MAILR_PUBLIC_IDS_LEGACY` | While public ids are on, still accept plain internal ids from old links and older apps. |

Details: [Reverse proxy](reverse-proxy.md) and [Security and permissions](security.md).

## Mail server and IMAP

| Variable | Description |
|---|---|
| `MAILR_IMAP_HOSTNAME` | The IMAP server name users type into mail apps. |
| `MAILR_IMAPS_PORT` | The published IMAP over TLS port (993). |
| `MAILR_IMAPS_BIND` | The host address that port is published on (`0.0.0.0`: every address of the host, so mail apps on other machines can connect). |
| `MAILR_IMAP_PORT` | The published IMAP with STARTTLS port (143). |
| `MAILR_IMAP_BIND` | The host address that port is published on (`127.0.0.1`). |
| `MAILR_STALWART_DOMAIN` | The internal domain of mailboxes in the mail server (`mailr.internal`). It is never routed. |
| `MAILR_STALWART_URL` | The URL of the mail server. It is set in the compose file. |
| `MAILR_STALWART_ADMIN_USER` | Mailr's service account. It is set in the compose file. |
| `MAILR_STALWART_ADMIN_PASSWORD` | That account's password. It is copied from `MAILR_SVC_PASSWORD` in the compose file. |
| `MAILR_STALWART_RESOLVER_IP` | The DNS resolver for the mail server's spam checks. It is set to the bundled resolver in the compose file. |
| `MAILR_TRUSTED_NET` | The trusted network for Mailr's own connections to the mail server (`10.253.54.250/31`: the fixed addresses of the app and the worker). Change it only together with `MAILR_APP_IP` and `MAILR_WORKER_IP`. |
| `MAILR_DNS_SUBNET` | The subnet of the resolver's private network (`10.253.53.0/29`). |
| `MAILR_RESOLVER_IP` | The fixed address of the bundled resolver (`10.253.53.2`). |

Details: [The mail server](mail-server.md).

## Mail handling

| Variable | Description |
|---|---|
| `MAILR_DEFAULT_RETENTION_DAYS` | Default days mail stays at the provider after it reaches Mailr, for accounts where Mailr keeps the mail (`store`) (14). |
| `MAILR_MAX_IMPORT_BYTES` | The largest mailbox file accepted for import (20 GB). |
| `MAILR_IMPORT_BYTES_PER_DAY` | How much one person may upload for imports in 24 hours (40 GB; `0`: no limit). |
| `MAILR_MAX_CONCURRENT_IMPORTS` | How many file imports of one person may run at once (2). |
| `MAILR_MAX_ATTACHMENT_BYTES` | The largest attachment uploaded for a draft (35 MB). |
| `MAILR_UPLOADS_QUOTA_BYTES` | What the attachments of one person's drafts and unsent mail may take in all (1 GB). |
| `MAILR_USER_UNDO_SEND_SECONDS` | The default undo-send window for new users (10 seconds). Each user can change their own. |
| `MAILR_OVERDUE_DAYS` | Days a passed deadline still counts in an inbox tab's Due indicator (7). With `0`, only today and later count. |
| `MAILR_TRUSTED_AUTHSERV_IDS` | The `Authentication-Results` headers that are believed, by their authserv-id, comma separated: such as `mx.google.com` for the provider of a connected account. The store's own host name (`MAILR_IMAP_HOSTNAME`) counts too, unless it is still `localhost`. |

## Storage and backups

| Variable | Description |
|---|---|
| `MAILR_DATA_DIR` | Where exports, uploads and the attachment cache are stored inside the container (`/data`). |
| `MAILR_MAIL_EXPORT` | The [daily mail export](backups.md#the-daily-export). On by default. With `false`, no mail is exported and the mode where Mailr keeps the mail (`store`) is unavailable, unless `MAILR_STORE_BACKUP_CHECK=false`. |
| `MAILR_STORE_BACKUP_CHECK` | Whether mail of accounts where Mailr keeps the mail is deleted at the provider only after it is in the daily mail export (and on the backup target). On by default. With `false`, that mode works without the export, and mail older than the delay is deleted at the provider once it is in Mailr's mail store, with [no backup check](store-and-mirror.md#deletion-without-a-backup-check). |
| `MAILR_BACKUP_DIR` | The host folder for a second copy of the export, such as a NAS share mounted on the host. |
| `MAILR_BACKUP_TARGET` | Where that folder appears inside the container. Set it to `/backup`. |
| `MAILR_CALENDAR_EXPORT_KEEP_DAYS` | How many days of [calendar and contact export](backups.md#calendars-and-contacts) snapshots to keep, locally and on the target. The default is 30. The newest snapshot always stays. |
| `MAILR_DB_DUMP` | The nightly [database dump](backups.md#database-dumps). On by default. |
| `MAILR_DB_DUMP_HOUR` | The hour, in UTC, at which the nightly dump starts (9). |
| `MAILR_DB_DUMP_KEEP` | How many nightly dumps stay (14). |
| `MAILR_DB_DUMP_DIR` | The folder of the dumps. By default, `postgres/` on the backup target, or `/data/backups/postgres` without one. |
| `MAILR_DB_DUMP_BEFORE_MIGRATE` | A dump is written before migrations run at start, on the backup target or in the data volume (on by default). |

Details: [Backups](backups.md).

## Database

| Variable | Description |
|---|---|
| `MAILR_DATABASE_URL` | The Postgres connection string. It is built from `POSTGRES_PASSWORD` in the compose file; set it yourself only for an external database. |
| `MAILR_PG_SHARED_BUFFERS`, `MAILR_PG_EFFECTIVE_CACHE_SIZE`, `MAILR_PG_WORK_MEM`, `MAILR_PG_MAINTENANCE_WORK_MEM`, `MAILR_PG_MAX_CONNECTIONS` | The memory and connection settings of the bundled Postgres (1GB, 3GB, 16MB, 256MB, 150), sized for a host of about 8 GB shared with other services. See [Deployment](deployment.md#postgres-settings). |

## Models and search

| Variable | Description |
|---|---|
| `MAILR_SEED_MODEL_ENDPOINTS` | A JSON list of model endpoints. They are added to the model registry on the first start. |

Details: [Models](models.md).

## Agents and approvals

| Variable | Description |
|---|---|
| `MAILR_AGENT_SEND_HOLD_SECONDS` | How long an email that an agent sends is held before it goes out (60 seconds). It can be cancelled during the hold. |
| `MAILR_APPROVAL_TTL_HOURS` | How long an approval request stays open before it expires (24 hours). |
| `MAILR_URL` | For the MCP server: the Mailr instance for its connection. |
| `MAILR_TOKEN` | For the MCP server: its API token. |

Details: [MCP for Claude Code and other agents](../agents/mcp.md) and [Approvals for agents](../agents/approvals.md).

## Ambrose's memory

A memory is used only when it fits the request: a person or topic named, a shared word, or a close meaning. When a local decision model is registered under **Admin → Models** with the task **Sorting and decisions**, each memory found by words or meaning is checked with that model before it is added to the prompt.

| Variable | Description |
|---|---|
| `MAILR_MEMORY_MAX_DISTANCE` | How close in meaning a memory must be (cosine distance). With 0, the default, the value measured for the embedding model is used: 0.46 for Qwen3-Embedding, 0.42 for others. |
| `MAILR_MEMORY_CHECK_ENDPOINT` | The local decision model used to check the memories: an endpoint's name, empty for the first local one, or `off`. Hosted endpoints are never used for this check. |
| `MAILR_MEMORY_CHECK_MS` | How long the check is awaited (1500 ms). Memories without an answer in time are used as found. |

The prompt of one question has a size limit, `MAILR_ASSISTANT_PROMPT_TOKENS` (100000 tokens, counted roughly). The oldest turns of a long chat are left out first, and the results read in the current question are shortened when the answer is written. A question that still does not fit is answered with the reason instead of a model error. For a local model with a small context window, set the limit below that window, for example `28000` for 32k.

Details: [Memory selection](../user-guide/assistant.md#memory-selection).

## Dictation

| Variable | Description |
|---|---|
| `MAILR_ASR_URL` | A self-hosted OpenAI-compatible speech-to-text server. When it is empty, server dictation is off. |
| `MAILR_ASR_MODEL` | The model name sent to that server (`whisper-1`). |
| `MAILR_ASR_MAX_BYTES` | The largest clip accepted, in bytes (8,000,000). |
| `MAILR_ASR_MAX_SECONDS` | The longest clip accepted, in seconds (120). |

Details: [Dictation server](dictation.md).

## Recordings

| Variable | Description |
|---|---|
| `MAILR_SPEAKR_MAX_UPLOAD_MB` | The largest email attachment sent to Speakr, in MB (1024). |

## Push notifications

| Variable | Description |
|---|---|
| `MAILR_NTFY_URL` | The ntfy server, for push notifications to phones through UnifiedPush. |
| `MAILR_NTFY_TOKEN` | The access token for that ntfy server. |

Details: [Push notifications](push.md).

## Monitoring

| Variable | Description |
|---|---|
| `MAILR_HEARTBEAT_URL` | A push monitor URL that is called every 5 minutes with `status=up` or `status=down`. |
| `MAILR_BACKUP_HEARTBEAT_URL` | A push monitor URL that is called after a completed export. |
| `MAILR_HEALTH_OPTIONAL` | Readiness checks that are reported but never counted as a failure of `/api/v1/health/ready`, such as `export,backup_target`. |
| `MAILR_HEALTH_TOKEN` | Monitor token: with this value in the `X-Health-Token` header, the health endpoints' details are shown. See [Monitoring](monitoring.md). |
| `MAILR_DISK_WARN_PERCENT`, `MAILR_DISK_FAIL_PERCENT` | Free space, in percent, below which the `disk` check warns (15) and fails (5). |
| `MAILR_DISK_CHECK_PATHS` | More paths, comma-separated, for the `disk` check. |

Details: [Monitoring](monitoring.md).

## Error reports

| Variable | Description |
|---|---|
| `MAILR_SENTRY_DSN` | The DSN of the error tracker's project for the server. Empty: no reports. |
| `MAILR_SENTRY_BROWSER_DSN` | The DSN of the project for the web app (default: the server's project). |
| `MAILR_SENTRY_ENVIRONMENT` | The environment name on each report (`production`). |

Details: [Error reports](errors.md).

## Image proxy and VPN

| Variable | Description |
|---|---|
| `MAILR_IMAGE_PROXY_EGRESS` | An HTTP proxy for remote image fetches, such as the optional VPN container (`http://mailr:<MAILR_EGRESS_PASSWORD>@egress:8888`). |
| `MAILR_REQUIRE_EGRESS` | With `true`, every fetch caused by a sender (images, sender logos, one-click unsubscribe) is paused unless the egress proxy works; nothing goes out directly. Off by default; turn it on with a VPN egress. |
| `MAILR_EGRESS_FORBIDDEN_IPS` | The server's own public address(es), comma-separated. The egress check fails when the VPN exit is one of them. Empty: the address is learned with one direct request. |
| `MAILR_BROWSER_EGRESS_PROXY` | The egress proxy's address as seen from a [browserless page reader](web-search.md#page-readers), when it differs from the egress proxy URL (inside Mailr, the proxy is reached by a container name). Empty: the egress proxy URL. |
| `COMPOSE_PROFILES` | Optional services to start, comma separated. `vpn` is for the VPN container; `phones` is for the calendar gateway for phones and desktop apps ([Calendars](calendars.md#phones-and-desktop-apps)); `browser` is for a headless browser that Ambrose uses to read pages ([Web search](web-search.md#a-browser-in-mailrs-stack)); `errors` is for the GlitchTip error tracker ([Error reports](errors.md)). |
| `MAILR_VPN_PROVIDER` | The VPN provider of that container (`protonvpn`). |
| `MAILR_VPN_WIREGUARD_KEY` | The WireGuard private key from your VPN provider. |
| `MAILR_VPN_COUNTRIES` | The countries the VPN may exit from. |

Details: [Image proxy and VPN egress](image-proxy.md).

## Image and logging

| Variable | Description |
|---|---|
| `MAILR_IMAGE` | A published Mailr image to run instead of building one. See [Use a published image](../getting-started/install.md#use-a-published-image). |
| `MAILR_PULL_POLICY` | Set to `missing` together with `MAILR_IMAGE`, so the image is pulled and never built. |
| `MAILR_LOG_LEVEL` | The log level: `INFO` (default), `DEBUG`, `WARNING`. |
| `MAILR_LOG_FORMAT` | `json` (default, one object per line) or `text`. |
| `MAILR_LOG_DIR`, `MAILR_LOG_FILE_MB`, `MAILR_LOG_FILES` | The rotating log files: `/data/logs`, 10 MB each, 10 files per process. `MAILR_LOG_DIR=off` writes none. |
| `MAILR_ACCESS_LOG` | One line per request (on by default). See [Monitoring](monitoring.md#logs). |
| `MAILR_WORKER_CONCURRENCY` | How many jobs run at once in the worker's main lane (4). Writes to providers, CalDAV sync, image fetches, scheduled upkeep, bulk reviews and the sorting of imported mail have lanes of their own; see [Deployment](deployment.md#scaling-the-work). |
| `MAILR_UPSTREAM_CONCURRENCY` | How many writes to the users' mail servers each worker runs at once (4), never two for one mail account. |
| `MAILR_MODEL_CONCURRENCY` | How many model calls run at once against one model endpoint, over the app and all workers (4; `0` for no limit). See [Scaling](scaling.md#model-calls). |
