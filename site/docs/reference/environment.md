# Environment variables

Mailr's configuration is read from the environment. With Docker Compose you put these in
`deploy/.env` (start from `deploy/.env.example`); they are passed to the app and the worker
through `docker-compose.yml`. Restart the stack after a change: `docker compose up -d`.

They are explained by topic on the [Configuration](../admin/configuration.md) page.

## Required

| Variable | Default | Description |
|---|---|---|
| `MAILR_BASE_URL` | `http://localhost:8025` | The public URL of the web app, e.g. https://mail.example.com (no trailing slash). |
| `MAILR_SECRET_KEY` | required | 32+ random bytes, base64, used to protect stored account passwords and keys. Generate with `mailr gen-secret`. |
| `MAILR_ALLOW_WEAK_SECRET_KEY` | `false` | Start even though `MAILR_SECRET_KEY` is shorter than 32 random bytes, with a warning instead of a refusal. Only for an existing install, until a new key is set. |
| `MAILR_CRYPTO_LEGACY_READS` | `false` | Read stored secrets in the old format that is not bound to its record. Such secrets are rewritten when the app starts, so leave this off; turn it on only for a database restored from an early build, until the app has started once. |
| `MAILR_SESSION_SECRET` | empty | A separate random key for signing sign-in state during OIDC sign-in. Without it, `MAILR_SECRET_KEY` is used. |
| `POSTGRES_PASSWORD` | required | The Postgres password. |
| `STALWART_ADMIN_PASSWORD` | required | The Stalwart recovery admin password. |
| `MAILR_SVC_PASSWORD` | required | The password of Mailr's service account in Stalwart. |

## First start

| Variable | Default | Description |
|---|---|---|
| `MAILR_BOOTSTRAP_ADMIN_EMAIL` | empty | The first admin account, created on first start when there are no users. |
| `MAILR_BOOTSTRAP_ADMIN_PASSWORD` | empty | The first admin's password. |
| `MAILR_SEED_MODEL_ENDPOINTS` | empty | A JSON list of model endpoints added to the admin model registry on first start. |

## Sign-in

| Variable | Default | Description |
|---|---|---|
| `MAILR_LOCAL_LOGIN` | `true` | Allow sign-in with email and password. |
| `MAILR_ALLOW_SIGNUP` | `false` | Let people create their own accounts. |
| `MAILR_SESSION_DAYS` | `30` | How many days a sign-in lasts. |
| `MAILR_OIDC_ISSUER` | empty | The OIDC provider's issuer URL (single sign-on). |
| `MAILR_OIDC_CLIENT_ID` | empty | The OIDC client id. |
| `MAILR_OIDC_CLIENT_SECRET` | empty | The OIDC client secret (optional for public clients with PKCE). |
| `MAILR_OIDC_SCOPES` | `openid email profile` | The OIDC scopes requested at sign-in. |
| `MAILR_OIDC_NAME` | empty | The provider's name on the sign-in buttons of the web and the phone apps, for example `Pocket ID` (**Continue with Pocket ID**). Empty: the buttons say **single sign-on**. |
| `MAILR_OIDC_ADMIN_GROUP` | empty | Members of this OIDC group are Mailr admins. |
| `MAILR_OIDC_AUTO_CREATE` | `false` | Create a Mailr account on a person's first OIDC sign-in. |
| `MAILR_OIDC_AUTO_CREATE_GROUP` | empty | With auto-create on, only members of this OIDC group get an account. An account made this way can be used to sign in only while its user stays in the group. |
| `MAILR_OIDC_PICTURE_HOSTS` | empty | Hosts besides the issuer's own that the sign-in picture may be fetched from (comma-separated, public hosts only). See [Sign-in](../admin/sign-in.md#profile-pictures-from-the-provider). |
| `MAILR_TOKEN_MODEL_CALLS_PER_MINUTE` | `20` | Requests with a model call one API token may make per minute. |
| `MAILR_TOKEN_MODEL_CALLS_PER_HOUR` | `300` | Requests with a model call one API token may make per hour. |
| `MAILR_OIDC_NATIVE_CLIENT_ID` | empty | Public OIDC client id(s) for the Android app; without it, only email and password are offered in the app. |
| `MAILR_OIDC_NATIVE_REDIRECT` | `io.github.murtazanasir.mailr:/oauth2redirect` | The Android app's OIDC redirect URI. Change it only when you build the app with your own application id. |
| `MAILR_OIDC_NATIVE_NOTES_REDIRECT` | `io.github.murtazanasir.mailr.notes:/oauth2redirect` | The Notes app's OIDC redirect URI. Add it to the same OIDC client as the Mailr app's. |
| `MAILR_OIDC_NATIVE_ORIGINS` | empty | Other addresses of this server the phone apps may sign in through, comma separated (for example a local network address). The app's sign-in is bound to the address it used; `MAILR_BASE_URL` always counts. See [Sign-in](../admin/sign-in.md#the-sign-in-is-bound-to-your-server). |
| `MAILR_OIDC_NATIVE_UNBOUND` | `false` | Let older app versions without the server-bound sign-in sign in. Off: they are asked to update. Turn it on only while phones are updated. |

## Web and network

| Variable | Default | Description |
|---|---|---|
| `MAILR_HTTP_PORT` | `8025` | The host port of the web app. |
| `MAILR_HTTP_BIND` | `127.0.0.1` | The host address the web port is published on. When the reverse proxy runs on another machine, set this host's address on that network. |
| `MAILR_TRUSTED_PROXIES` | empty | Reverse proxy addresses allowed to set X-Forwarded-For and X-Forwarded-Proto. Empty: loopback and the gateway of the stack network (`MAILR_NET_SUBNET`), the source of connections from a proxy on the same host. Set your proxy's address when it runs on another machine. |
| `MAILR_MAX_REQUEST_BYTES` | `52428800` (50 MB) | The largest request body accepted. Mailbox imports (`MAILR_MAX_IMPORT_BYTES`) and recording uploads (`MAILR_SPEAKR_MAX_UPLOAD_MB`) have their own limits. |
| `MAILR_SSE_MAX_PER_USER` | `12` | How many live-update connections (open tabs and apps) one user may hold; past it the oldest one is closed. |
| `MAILR_CANONICAL_REDIRECT` | `true` | Redirect page loads that arrive on another hostname to MAILR_BASE_URL. |
| `MAILR_ALLOW_PRIVATE_HOSTS` | `admins` | Who may point Mailr at private network addresses (a mail, calendar, push or Speakr server on your LAN): `admins` (accounts, push targets and integrations of admin users), `all`, or `none`. `true` and `false` mean `all` and `none`. |
| `MAILR_OUTBOUND_ALLOW` | empty | Hosts or networks (comma-separated, e.g. `192.0.2.20,calendar.example.internal`) allowed for every user's outbound connections, private or not. |
| `MAILR_INTERNAL_HOSTS` | `egress,resolver,app,worker,stalwart,postgres,db,browser,browser-gate,chrome,glitchtip,glitchtip-postgres` | Names of the stack's own services. They, their addresses and the container's gateway are never reachable by outbound connections. |
| `MAILR_PUBLIC_IDS` | `false` | Use opaque public ids for conversations, messages and approvals in URLs and the API. |
| `MAILR_PUBLIC_IDS_LEGACY` | `true` | While public ids are on, still accept plain internal ids (old links, older apps). |

## Mail server (Stalwart)

| Variable | Default | Description |
|---|---|---|
| `MAILR_IMAP_HOSTNAME` | `localhost` | The IMAP server name shown to users for phone and desktop mail apps. |
| `MAILR_TRUSTED_AUTHSERV_IDS` | empty | Receivers whose `Authentication-Results` headers are trusted (comma-separated authserv-ids; `.example.com` also matches its subdomains). Your own mail server's name (`MAILR_IMAP_HOSTNAME`) counts too, unless it is still `localhost` (any sender can write that name; list it here if the setup requires it); add the provider of each connected account, such as `mx.google.com`. |
| `MAILR_ANDROID_MIN_VERSION` | `0.5.0` | The oldest Android app that is served. An older app is given an "update the app" error instead of mail. Leave it empty to turn the check off. |
| `MAILR_IMAPS_PORT` | `993` | The IMAP over TLS port published for mail apps. |
| `MAILR_IMAP_PORT` | `143` | The IMAP (STARTTLS) port published for mail apps. |
| `MAILR_IMAPS_BIND` | `0.0.0.0` | The host address the IMAP over TLS port is published on. |
| `MAILR_IMAP_BIND` | `127.0.0.1` | The host address the IMAP (STARTTLS) port is published on. Set `0.0.0.0` if your mail apps are set to port 143. |
| `MAILR_STALWART_DOMAIN` | `mailr.internal` | The internal domain of mailboxes in Stalwart; never routed. |
| `MAILR_STALWART_URL` | set by `docker-compose.yml` | The URL of the bundled Stalwart mail store. |
| `MAILR_STALWART_ADMIN_USER` | set by `docker-compose.yml` | Mailr's service account in Stalwart. |
| `MAILR_STALWART_ADMIN_PASSWORD` | set by `docker-compose.yml` | The password of Mailr's service account in Stalwart. It is set from `MAILR_SVC_PASSWORD`. |
| `MAILR_STALWART_RESOLVER_IP` | set by `docker-compose.yml` | The DNS resolver for Stalwart's spam checks (the bundled resolver). |
| `MAILR_TRUSTED_NET` | `10.253.54.250/31` | The addresses never banned in Stalwart after failed sign-ins: the app and worker containers. |
| `MAILR_NET_SUBNET` | `10.253.54.0/24` | The subnet of the stack's own network. It is passed to the app in the compose file; its gateway is trusted for forwarded headers while `MAILR_TRUSTED_PROXIES` is empty. |
| `MAILR_APP_IP` | `10.253.54.250` | The app container's fixed address on that network. |
| `MAILR_WORKER_IP` | `10.253.54.251` | The worker container's fixed address on that network. |
| `MAILR_RESOLVER_IP` | `10.253.53.2` | The fixed address of the bundled DNS resolver. |
| `MAILR_DNS_SUBNET` | `10.253.53.0/29` | The subnet of the resolver's network. |

## Storage and backups

| Variable | Default | Description |
|---|---|---|
| `MAILR_DATABASE_URL` | set by `docker-compose.yml` | The Postgres connection string (built from POSTGRES_PASSWORD). |
| `MAILR_DB_POOL_SIZE` | `10` | Connections each process keeps open to Postgres. |
| `MAILR_DB_MAX_OVERFLOW` | `20` | Extra connections each process may open in a burst. |
| `MAILR_CALENDAR_EXPORT_KEEP_DAYS` | `30` | How many days of the daily calendar and contact export are kept, locally and on the backup target. |
| `MAILR_MAIL_EXPORT` | `true` | The daily mail export, a Maildir copy of every user's mail. With `false`, the mode where Mailr keeps the mail (`store`) is unavailable (unless `MAILR_STORE_BACKUP_CHECK=false`); the calendar, contacts, notes and tables exports still run. |
| `MAILR_STORE_BACKUP_CHECK` | `true` | For accounts where Mailr keeps the mail, mail is deleted at the provider only once it is in the daily mail export (and on the backup target, when one is set). With `false`, that mode works without the export, and mail older than the delay is deleted at the provider once it is in Mailr's mail store, with no backup check. See [Account modes](../admin/store-and-mirror.md#deletion-without-a-backup-check). |
| `MAILR_DATA_DIR` | `/data` | Where exports, uploads and the attachment cache are stored. |
| `MAILR_BACKUP_DIR` | `./backup` | The host folder (e.g. a mounted share) that holds the second copy of the export. |
| `MAILR_BACKUP_TARGET` | empty | Where a second copy of the mail export is written inside the container (e.g. /backup). |
| `MAILR_MAX_IMPORT_BYTES` | `21474836480` (20 GiB) | The largest mailbox file accepted for import. |
| `MAILR_IMPORT_BYTES_PER_DAY` | `42949672960` (40 GiB) | How much one person may upload for imports in 24 hours, refused uploads included. `0`: no limit. |
| `MAILR_MAX_CONCURRENT_IMPORTS` | `2` | How many file imports of one person may run at once. Another one can be started when one has finished. |
| `MAILR_MAX_ATTACHMENT_BYTES` | `36700160` (35 MiB) | The largest attachment uploaded for a draft. The web app refuses files over 25 MB itself. |
| `MAILR_UPLOADS_QUOTA_BYTES` | `1073741824` (1 GiB) | What the attachments of one person's drafts and unsent mail may take in all. When it is full, attachments that no draft uses and that are older than an hour are removed first; otherwise the upload is refused. |
| `MAILR_DEFAULT_RETENTION_DAYS` | `14` | Default days to keep mail at the provider after it is in Mailr, when deletion at the provider is turned on. |

## Sending and agents

| Variable | Default | Description |
|---|---|---|
| `MAILR_USER_UNDO_SEND_SECONDS` | `10` | The server's default undo-send window. |
| `MAILR_AGENT_SEND_HOLD_SECONDS` | `60` | How long an email an agent sends is held before it is sent. |
| `MAILR_APPROVAL_TTL_HOURS` | `24` | How long an approval request stays open. |

## Inbox tabs

| Variable | Default | Description |
|---|---|---|
| `MAILR_OVERDUE_DAYS` | `7` | Days a passed deadline still counts in an inbox tab's Due indicator. |
| `MAILR_ASSISTANT_PROMPT_TOKENS` | `100000` | The largest prompt Ambrose sends to its chat model, in tokens (a rough count). The oldest turns of a long chat are left out first; a question that still does not fit is answered with the reason. Set it below the chat model's context window, for example `28000` for a 32k local model. |

## Ambrose's memory

| Variable | Default | Description |
|---|---|---|
| `MAILR_MEMORY_MAX_DISTANCE` | `0` | How close in meaning a note must be to a request before Ambrose uses it (cosine distance). With 0, the value measured for your embedding model is used. |
| `MAILR_MEMORY_CHECK_ENDPOINT` | empty | The local decision model that checks whether each candidate note helps with a request: an endpoint's name, empty for the first local one, or `off`. |
| `MAILR_MEMORY_CHECK_MS` | `1500` | How long Ambrose waits for that check, in milliseconds. Notes not checked in time are used as they are. |
| `MAILR_ACCOUNT_COUNTS` | `1` | With `0`, the numbers on the folders inside each account are hidden (Show each account separately). |

## Dictation

| Variable | Default | Description |
|---|---|---|
| `MAILR_ASR_URL` | empty | A self-hosted speech-to-text server (OpenAI-compatible) on the local network. It is added as the built-in local endpoint of the Speech to text task; its address is changed only here. |
| `MAILR_ASR_MODEL` | `whisper-1` | The model of that built-in endpoint. |
| `MAILR_ASR_MAX_BYTES` | `8000000` | The largest dictation clip accepted, in bytes. |
| `MAILR_ASR_MAX_SECONDS` | `120` | The longest dictation clip accepted, in seconds. |

## Image proxy and VPN egress

| Variable | Default | Description |
|---|---|---|
| `MAILR_IMAGE_PROXY_EGRESS` | empty | An HTTP proxy for the image proxy's fetches, such as the optional VPN container. |
| `MAILR_REQUIRE_EGRESS` | `false` | Fail closed: fetches caused by a sender (remote images, sender logos and pictures, one-click unsubscribe) are made only through a working egress proxy, never directly, and the image proxy stays on. Recommended with a VPN egress. |
| `MAILR_EGRESS_FORBIDDEN_IPS` | empty | The server's own public addresses (comma-separated). The egress check is marked failed when the egress exits from one of them. Empty: learned with one direct request to the address-echo service. |
| `MAILR_BROWSER_EGRESS_PROXY` | empty | The egress proxy's address as seen from a browserless [page reader](../admin/web-search.md#page-readers), when it differs from the egress proxy URL. Empty: the egress proxy URL. |
| `MAILR_BROWSER_URL` | empty | A browserless [page reader](../admin/web-search.md#a-browser-in-mailrs-stack) set up by the server, listed as **Browser (server)**. The stack's optional `browser` service is `http://browser:3000`. Empty: none. |
| `MAILR_BROWSER_TOKEN` | empty | The browserless `TOKEN`. The optional `browser` service starts only with it set, and it is sent with the server's page reader requests. It is used from the environment only: never stored in the database or shown. |
| `MAILR_BROWSER_ROUTE` | `egress` | The first route of the server's page reader: `egress` (through the egress proxy) or `direct` (from this network). Admins can change it later in **Page readers**. |
| `MAILR_BROWSER_GATE` | `browser-gate` (compose) | The [browser gate](../admin/web-search.md#the-browser-gate)'s host name as the stack's browser reaches it. All of the browser's connections are then sent through the gate. Set it empty when `MAILR_BROWSER_URL` points at a browserless outside the stack. |
| `MAILR_BROWSER_GATE_UPSTREAM` | `MAILR_IMAGE_PROXY_EGRESS` | Read by the browser gate: the egress proxy, with its password, that the gate's egress port forwards to. Empty: every page is refused on the route **Through the egress proxy**. |
| `MAILR_BROWSER_GATE_TARGET` | `chrome:3000` | Read by the browser gate: the browser (`host:port`) that its control port relays to. |
| `COMPOSE_PROFILES` | empty | Optional services to start, comma separated: `vpn` the VPN container for the image proxy and the page readers, `phones` the calendar gateway, `browser` a headless browser for the page readers. The `cli` service (profile `tools`) for one-off commands is started with `docker compose run --rm cli mailr …`; no profile is needed for it here. |
| `MAILR_VPN_PROVIDER` | `protonvpn` | The VPN provider of the optional VPN container. |
| `MAILR_VPN_WIREGUARD_KEY` | empty | The WireGuard private key for the VPN container. |
| `MAILR_VPN_COUNTRIES` | empty | The countries the VPN container may exit from. |
| `MAILR_EGRESS_PASSWORD` | empty | The password of the VPN container's HTTP proxy (user `mailr`). Put it in `MAILR_IMAGE_PROXY_EGRESS` as well: `http://mailr:<password>@egress:8888`. |

## Push notifications

| Variable | Default | Description |
|---|---|---|
| `MAILR_NTFY_URL` | empty | An ntfy server for push notifications to phones (UnifiedPush). |
| `MAILR_ANDROID_APK_URL` | empty | Where the Mailr Android app is downloaded from: it is linked from the setup guide's QR code and the Get the app page (`/get-app`). Empty: the APK published to the server with `mailr app-publish` (served at `/api/v1/app/mailr.apk`), else the latest GitHub release. See [Phone apps](../admin/phone-apps.md). |
| `MAILR_ANDROID_PACKAGE_MAILR` | the published app's package name | The package name (applicationId) an APK must have to be published as the Mailr app. Change it only for a build with another applicationId. |
| `MAILR_ANDROID_PACKAGE_NOTES` | the published Notes app's package name | The package name (applicationId) an APK must have to be published as the Notes app. Change it only for a build with another applicationId. |
| `MAILR_NTFY_TOKEN` | empty | The access token for that ntfy server. |

## Monitoring

| Variable | Default | Description |
|---|---|---|
| `MAILR_HEARTBEAT_URL` | empty | A push monitor URL that is called every 5 minutes with status up or down. Leave it empty to turn this heartbeat off. |
| `MAILR_BACKUP_HEARTBEAT_URL` | empty | A push monitor URL that is called after an export run when the export and backup target checks pass. Leave it empty to turn this heartbeat off. |
| `MAILR_HEALTH_OPTIONAL` | empty | Readiness checks (comma-separated) that are reported by `/api/v1/health/ready` but never count as a failure. |
| `MAILR_HEALTH_TOKEN` | empty | Monitor token for the health endpoints. For a request with this value in the `X-Health-Token` header (or from a signed-in admin), each check's details are returned; anyone else is given only the verdict. |
| `MAILR_DISK_WARN_PERCENT` | `15` | Below this much free space, in percent, the `disk` readiness check warns. |
| `MAILR_DISK_FAIL_PERCENT` | `5` | Below this much free space, in percent, the `disk` readiness check fails, and a database dump that would leave less is refused. |
| `MAILR_DISK_CHECK_PATHS` | empty | More paths (comma-separated) whose free space the `disk` check reads, besides `/data` and the backup target. |

## Database dumps

| Variable | Default | Description |
|---|---|---|
| `MAILR_DB_DUMP` | `true` | Write a database dump every night. |
| `MAILR_DB_DUMP_HOUR` | `9` | The hour, in UTC, at which the nightly dump starts. |
| `MAILR_DB_DUMP_KEEP` | `14` | How many nightly dumps stay. Dumps written before migrations stay for as many days (the newest three always). |
| `MAILR_DB_DUMP_DIR` | empty | The folder of the dumps. Empty: `postgres/` on the backup target, or `/data/backups/postgres` without a target. |
| `MAILR_DB_DUMP_BEFORE_MIGRATE` | `true` | Write a dump before migrations run at start, into the dump folder (the backup target, `MAILR_DB_DUMP_DIR`, or the data volume). When that dump fails, nothing is migrated and the start stops; in the data volume, a dump that does not fit is skipped with a warning. |
| `MAILR_DB_DUMP_FRESH_MINUTES` | `120` | A dump at the current schema this recent, such as one taken just before a deploy, counts as the dump before migrations. |
| `MAILR_PG_BIN_DIR` | empty | The folder of `pg_dump` and `pg_restore` when they are not on the path. The image has them. |

## Error reports

| Variable | Default | Description |
|---|---|---|
| `MAILR_SENTRY_DSN` | empty | The DSN of the error tracker's project for the server: GlitchTip or another Sentry-compatible server. Empty: no error reports. |
| `MAILR_SENTRY_BROWSER_DSN` | empty | The DSN of the project for the web app. Empty: the server's project. Reports from browsers are passed on by the app. |
| `MAILR_SENTRY_ENVIRONMENT` | `production` | The environment name on every report. |
| `MAILR_RELEASE` | set by the image | The release that reports are tagged with. |
| `GLITCHTIP_SECRET_KEY` | empty | The secret key of the bundled GlitchTip (the `errors` profile). |
| `GLITCHTIP_POSTGRES_PASSWORD` | empty | The password of GlitchTip's own database. |
| `GLITCHTIP_DOMAIN` | `http://localhost:8027` | GlitchTip's public address with its scheme, such as `https://errors.example.com`. |
| `GLITCHTIP_FROM_EMAIL` | `glitchtip@localhost` | The sender address of GlitchTip's alert mail. |
| `GLITCHTIP_EMAIL_URL` | `consolemail://` | How GlitchTip sends alert mail, such as `smtp://user:password@smtp.example.com:587`. The default writes each mail to its log. |
| `GLITCHTIP_RETENTION_DAYS` | `90` | How many days GlitchTip keeps events. |
| `GLITCHTIP_BIND` | `127.0.0.1` | The host address GlitchTip's web port is published on. |
| `GLITCHTIP_PORT` | `8027` | The host port of GlitchTip. |

## Speakr

| Variable | Default | Description |
|---|---|---|
| `MAILR_SPEAKR_MAX_UPLOAD_MB` | `1024` | The largest attachment sent to Speakr, in MB. |

## Operation

| Variable | Default | Description |
|---|---|---|
| `MAILR_IMAGE` | `mailr:local` (built from source) | A published image to run instead of building locally, such as `ghcr.io/murtaza-nasir/mailr:latest`. |
| `MAILR_PULL_POLICY` | `never` | How the app image is obtained in Compose. With `never`, it is built from source only when it is missing (the first `docker compose up -d`) or when you build (`docker compose build`, or `up -d --build`), so a plain `up -d` never rebuilds. Set `missing` (or `always`) with a published `MAILR_IMAGE`. |
| `MAILR_WORKER_CONCURRENCY` | `4` | How many background jobs are run at once in the worker's main lane. |
| `MAILR_UPSTREAM_CONCURRENCY` | `4` | How many writes to the users' mail servers (moves, flags, deletions, copies in Sent) each worker runs at once. Two writes for one mail account never run at once. |
| `MAILR_MODEL_CONCURRENCY` | `4` | How many model calls run at once against one model endpoint, counted over the app and all workers. `0` turns the limit off. See [Scaling](../admin/scaling.md#model-calls). |
| `MAILR_LOG_LEVEL` | `INFO` | Log level (INFO, DEBUG, ...). |
| `MAILR_LOG_FORMAT` | `json` | The log format on standard output and in the log files: `json` (one object per line) or `text`. |
| `MAILR_LOG_DIR` | `/data/logs` | The folder of the rotating log files. With `off`, no files are written. |
| `MAILR_LOG_FILE_MB` | `10` | The size in MB at which a log file is rotated. |
| `MAILR_LOG_FILES` | `10` | How many rotated files each process keeps. |
| `MAILR_ACCESS_LOG` | `true` | One log line per request: method, route template, status, time and user id. The query string is never logged. |
| `MAILR_PG_SHARED_BUFFERS` | `1GB` | Postgres `shared_buffers`, set in the compose file. |
| `MAILR_PG_EFFECTIVE_CACHE_SIZE` | `3GB` | Postgres `effective_cache_size`. |
| `MAILR_PG_WORK_MEM` | `16MB` | Postgres `work_mem`. |
| `MAILR_PG_MAINTENANCE_WORK_MEM` | `256MB` | Postgres `maintenance_work_mem`. |
| `MAILR_PG_MAX_CONNECTIONS` | `150` | Postgres `max_connections`. |
| `MAILR_SLOW_REQUEST_MS` | `2000` | An API request slower than this many milliseconds is logged as one warning line, with its route, status, time, database time and query count, and the wait for a database connection. With `0`, the line is never logged. |

## Container limits

Each container of the compose file has a memory limit and a limit on its processes and threads.
A container that reaches its memory limit is stopped by the kernel and started again by Docker.
The limits are read by Docker Compose when a container is created: run `docker compose up -d`
after a change. [Deployment](../admin/deployment.md#hardening) explains the defaults.

| Variable | Default | Description |
|---|---|---|
| `MAILR_APP_MEMORY` | `2g` | The memory limit of the app (and of the `cli` service). |
| `MAILR_APP_PIDS` | `4096` | The process and thread limit of the app (and of the `cli` service). One thread is held for each open IMAP connection. |
| `MAILR_WORKER_MEMORY` | `3g` | The memory limit of the worker and of each extra worker. |
| `MAILR_WORKER_PIDS` | `4096` | The process and thread limit of the worker and of each extra worker. |
| `MAILR_PG_MEMORY` | `4g` | The memory limit of Postgres. Raise it with `MAILR_PG_SHARED_BUFFERS`. |
| `MAILR_PG_PIDS` | `1024` | The process limit of Postgres: one process per connection. Raise it with `MAILR_PG_MAX_CONNECTIONS`. |
| `MAILR_STALWART_MEMORY` | `2g` | The memory limit of the mail server. |
| `MAILR_STALWART_PIDS` | `1024` | The thread limit of the mail server. |
| `MAILR_RESOLVER_MEMORY` | `512m` | The memory limit of the DNS resolver. |
| `MAILR_RESOLVER_PIDS` | `64` | The thread limit of the DNS resolver. |
| `MAILR_EGRESS_MEMORY` | `256m` | The memory limit of the VPN container (the `vpn` profile). |
| `MAILR_EGRESS_PIDS` | `256` | The thread limit of the VPN container. |
| `MAILR_BROWSER_MEMORY` | `3g` | The memory limit of the headless browser (the `browser` profile), with its 1 GB of shared memory. |
| `MAILR_BROWSER_PIDS` | `1024` | The process and thread limit of the headless browser. |
| `MAILR_BROWSER_GATE_MEMORY` | `256m` | The memory limit of the browser gate. |
| `MAILR_BROWSER_GATE_PIDS` | `256` | The thread limit of the browser gate. |
| `MAILR_DAV_MEMORY` | `128m` | The memory limit of the DAV gateway (the `phones` profile). |
| `MAILR_DAV_PIDS` | `256` | The process limit of the DAV gateway: nginx starts one worker per CPU. |
| `GLITCHTIP_MEMORY` | `1g` | The memory limit of GlitchTip (the `errors` profile). |
| `GLITCHTIP_PIDS` | `256` | The process and thread limit of GlitchTip. |
| `GLITCHTIP_POSTGRES_MEMORY` | `512m` | The memory limit of GlitchTip's database. |
| `GLITCHTIP_POSTGRES_PIDS` | `128` | The process limit of GlitchTip's database. |

## MCP server

| Variable | Default | Description |
|---|---|---|
| `MAILR_URL` | `http://localhost:8025` | MCP server: the Mailr instance to connect to. |
| `MAILR_TOKEN` | empty | MCP server: the API token for its requests. |

!!! tip "Generating secrets"

    Run `openssl rand -base64 32` once for `MAILR_SECRET_KEY` and once more for
    `MAILR_SESSION_SECRET`. `docker compose run --rm cli mailr gen-secret` prints the same kind of value. Keep `MAILR_SECRET_KEY` safe: stored account passwords cannot be read
    without it.
