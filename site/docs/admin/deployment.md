# Deployment

Mailr is deployed with one Docker Compose file, `deploy/docker-compose.yml`. Every host, port, path and secret is set in `deploy/.env`. The first start is described step by step in [Install with Docker Compose](../getting-started/install.md).

## Services

| Service | Image | Profile | Description | Ports |
|---|---|---|---|---|
| `app` | Mailr | always | The API and the web app (`mailr serve`). | 8025, published as `MAILR_HTTP_PORT` on `MAILR_HTTP_BIND` (`127.0.0.1`) |
| `worker` | Mailr | always | Account and calendar sync, background jobs, reminders, the daily export (`mailr worker`). | none |
| `worker-extra` | Mailr | none started | More workers for a larger server, started with `docker compose up -d --scale worker-extra=N`. See [Scaling](scaling.md#more-workers). | none |
| `postgres` | `pgvector/pgvector:pg17` | always | Mailr's index, settings, search vectors and job queue. | internal only |
| `stalwart` | built from `deploy/stalwart` | always | The mail store. JMAP stays internal; IMAP is published for mail apps. | 993 (`MAILR_IMAPS_PORT`) on `MAILR_IMAPS_BIND` (`0.0.0.0`, every address of the host); 143 (`MAILR_IMAP_PORT`) on `MAILR_IMAP_BIND` (`127.0.0.1`) |
| `resolver` | Unbound | always | Recursive DNS for spam blocklist lookups. | internal only |
| `egress` | gluetun | `vpn` | Optional VPN exit for image fetches and page reading. See [Image proxy and VPN egress](image-proxy.md). | internal only |
| `browser` | browserless (headless Chrome) | `browser` | Optional browser for page readers that need JavaScript. Its network has no route out. See [Web search](web-search.md#a-browser-in-mailrs-stack). | internal only |
| `browser-gate` | Mailr (`mailr browser-gate`) | `browser` | The browser's only way out. Every connection of the browser is checked against Mailr's address rules here. | internal only |
| `dav-gateway` | nginx | `phones` | Optional gateway for calendar and contacts apps on phones and computers.  See [Phones and desktop apps](calendars.md#phones-and-desktop-apps). | 8026 (`MAILR_DAV_PORT`) on `MAILR_DAV_BIND` (`127.0.0.1`) |
| `glitchtip` | GlitchTip | `errors` | Optional error tracker for the app, the worker and the web app. See [Error reports](errors.md). | 8027 (`GLITCHTIP_PORT`) on `GLITCHTIP_BIND` (`127.0.0.1`) |
| `glitchtip-postgres` | `postgres:17` (Alpine) | `errors` | GlitchTip's own database. | internal only |
| `cli` | Mailr | `tools` | One-off maintenance commands while the stack is up: `docker compose run --rm cli mailr <command>`. Never started by `docker compose up`. | none |

A profile is turned on in `.env`, for example `COMPOSE_PROFILES=vpn,browser`. Services without a profile always run.

### Ports on the host

Two ports are published on the host by default: the web app on `127.0.0.1:8025` and IMAP over TLS on port 993 of every address of the host. Port 993 is open to your network, and to the internet if the host is reachable from it, so that phones and desktop mail apps can connect. To keep IMAP on one address only, set `MAILR_IMAPS_BIND`, for example `MAILR_IMAPS_BIND=127.0.0.1` when no mail app connects from another machine.

When a port is already taken on the host, choose another one in `.env`: `MAILR_HTTP_PORT` for the web app, `MAILR_IMAPS_PORT` and `MAILR_IMAP_PORT` for IMAP, `MAILR_DAV_PORT` for the DAV gateway. Only the port on the host changes; mail apps then connect to the new port.

## The app image

The image of the `app` and `worker` services is obtained in one of two ways, chosen in `.env`:

| Source | `.env` | Commands |
|---|---|---|
| Built from the repository (default) | `MAILR_IMAGE` and `MAILR_PULL_POLICY` unset | `docker compose up -d --build` |
| A published image | `MAILR_IMAGE=ghcr.io/murtaza-nasir/mailr:latest` and `MAILR_PULL_POLICY=missing` | `docker compose pull && docker compose up -d` |

With the defaults, the repository is built and the result is tagged `mailr:local`. With a published image and `MAILR_PULL_POLICY=missing`, the image is pulled and never built. The mail server image is always built from `deploy/stalwart`. [Use a published image](../getting-started/install.md#use-a-published-image) has the steps.

## Help pages

This guide is part of the app image. Users open it from **Help** in the ? menu or in their
account menu, and it is served at `/help/` on the Mailr address. Only people signed in to the
server can read it. Anyone else is sent to the sign-in page first. Search engines are told not to
index it, and nothing in it is loaded from other sites.

## Hardening

The app and the worker are the same image with different commands. Both run with every Linux capability dropped, with `no-new-privileges` and with a read-only root filesystem; they write only to `/data`, `/backup` and a small `/tmp`. The mail server's admin port, Postgres and the resolver are never published.

Every other container runs the same way: every capability is dropped, `no-new-privileges` is set, and each container gets back only the capabilities its image cannot start without. Each root filesystem is read-only, apart from the VPN container's, and the containers write only to their volumes and to small in-memory folders.

| Service | Capabilities kept | Writes to | Memory, processes |
|---|---|---|---|
| `app`, `cli` | none | `/data`, `/backup`, `/tmp` | 2 GB, 4096 |
| `worker`, `worker-extra` | none | `/data`, `/backup`, `/tmp` | 3 GB, 4096 |
| `postgres` | `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID`: its start script sets the owner of the data folder as root, then runs Postgres as the `postgres` user | its volume, `/var/run/postgresql`, `/tmp` | 4 GB, 1024 |
| `stalwart` | `NET_BIND_SERVICE`: the mail server's program does not start without the right to listen on ports below 1024 (143, 993) | its volumes, `/tmp` | 2 GB, 1024 |
| `resolver` | `SETGID`, `SETUID`: Unbound starts as root and switches to its own user | its volume `resolver-data` (the root zone's trust anchor), `/tmp` | 512 MB, 64 |
| `egress` | `NET_ADMIN` (the tunnel, its routes and firewall), `CHOWN`, `DAC_OVERRIDE` (its state files) | its root filesystem: gluetun adds its own user to `/etc/passwd` at every start | 256 MB, 256 |
| `browser` | none | `/tmp`, its home folder | 3 GB, 1024 |
| `browser-gate` | none | `/tmp` | 256 MB, 256 |
| `dav-gateway` | none | `/tmp`, its generated configuration | 128 MB, 256 |
| `glitchtip` | none | its uploads volume, `/tmp` | 1 GB, 256 |
| `glitchtip-postgres` | as `postgres` | its volume, `/var/run/postgresql`, `/tmp` | 512 MB, 128 |

The memory limits are well above what the services use on a small server (see [Resources](#resources)). Postgres's limit includes its shared buffers, the memory of the queries that run and its shared memory; the browser's includes its 1 GB of shared memory. A container that reaches its memory limit is stopped by the kernel and started again by Docker, so one service that runs out of memory cannot take the memory of the others. The process limits count threads too, and stop a runaway process from filling the host's process table. Each limit has a variable in `.env` ([Container limits](../reference/environment.md#container-limits)), for example `MAILR_PG_MEMORY=8g` with `MAILR_PG_SHARED_BUFFERS=2GB` on a larger host. Raise `MAILR_PG_PIDS` with `MAILR_PG_MAX_CONNECTIONS`, and `MAILR_APP_PIDS` and `MAILR_WORKER_PIDS` when many users connect many mail accounts (one thread for each open IMAP connection).

Every service has a healthcheck, and `docker compose ps` shows its result. The app's is in its image; the worker's is `mailr worker-alive`; the resolver's is a query for a name from its own zone data; the DAV gateway's and the browser's are a connection to their ports; the VPN container's is gluetun's own check of the tunnel.

## Volumes

| Volume | Holds |
|---|---|
| `postgres-data` | The Postgres database. |
| `stalwart-data` | Every message, in the mail server's store. |
| `resolver-data` | The DNS resolver's copy of the root zone's trust anchor. |
| `mailr-data` (mounted at `/data`, `MAILR_DATA_DIR`) | Uploads, caches and the per-user Maildir exports. |
| `${MAILR_BACKUP_DIR}` (mounted at `/backup`) | The optional second copy of the export, and the nightly database dumps. See [Backups](backups.md). |
| `glitchtip-postgres-data`, `glitchtip-uploads` | The optional error tracker's database and files. |

The logs of the app and the worker are kept in `mailr-data`, under `/data/logs`. See [Monitoring](monitoring.md#logs).

## Postgres settings

The bundled Postgres is started with settings for a host of about 8 GB shared with other services. Each can be changed in `.env`.

| Setting | Value | Reason |
|---|---|---|
| `shared_buffers` | 1GB (`MAILR_PG_SHARED_BUFFERS`) | Postgres's own cache. The hot part of the index, with the two search vector indexes of about 450 MB, stays in it. |
| `effective_cache_size` | 3GB (`MAILR_PG_EFFECTIVE_CACHE_SIZE`) | What the planner may assume the operating system caches on top. Nothing is allocated. |
| `work_mem` | 16MB (`MAILR_PG_WORK_MEM`) | Memory for each sort or hash step of a query, before it spills to disk. |
| `maintenance_work_mem` | 256MB (`MAILR_PG_MAINTENANCE_WORK_MEM`) | Vacuum and index builds, such as the vector indexes, in fewer passes. |
| `max_connections` | 150 (`MAILR_PG_MAX_CONNECTIONS`) | The pools of the app (at most 44 connections), the worker (38, with one connection for the locks of all mail accounts, however many there are, and one for each write to a mail server that runs) and one `cli` command (17), with room for a second app during a deploy. Each [extra worker](scaling.md#more-workers) holds up to 25 more. |
| `random_page_cost`, `jit` | 1.1, off | The data is on SSD. Compiling queries cost more time than it saved. |
| `pg_stat_statements` | loaded | Statement statistics ([Monitoring](monitoring.md#postgres)). |
| `log_min_duration_statement`, `log_lock_waits` | 500 ms, on | Slow statements and lock waits are logged, without their parameters. |

The container also gets 256 MB of shared memory (`shm_size`) for parallel queries. A change to any of these settings takes effect when the `postgres` container is restarted; write a dump first ([Upgrades](upgrades.md#postgres-settings)). On a host with more memory, raise the container's memory limit `MAILR_PG_MEMORY` with them: `shared_buffers` about a quarter of that limit, and `effective_cache_size` about three quarters ([Sizes of server](scaling.md#sizes-of-server) has the values for 50 and 100 users).

## Resources

For a small group of people with tens of thousands of messages, plan for:

- **Memory**: about 2.5 GB in total (Stalwart about 350 MB, Postgres up to about 1.5 GB with the settings above, app and worker about 600 MB), and about 300 MB more for the optional error tracker. The containers' memory limits ([Hardening](#hardening)) are upper bounds, not reservations.
- **Disk**: roughly 1.3 times the mail size for the store, index and vectors, plus the export copy.
- **CPU**: any modern machine. No model is ever run in Mailr's own containers, so no GPU is needed.

## Scaling the work

- `MAILR_WORKER_CONCURRENCY` (default 4) is the number of jobs run at once in the worker's main lane: sends, reminders, model work and most other jobs. Raise it on a larger host.
- Some kinds of jobs run in lanes of their own, so a slow job of one kind never holds up sends and reminders: writes to the mail providers (`MAILR_UPSTREAM_CONCURRENCY` at a time, default 4, and one at a time per mail account), CalDAV sync (two at a time), image proxy fetches (two at a time), scheduled upkeep such as exports, retraining and the database dump (two at a time), Ambrose's bulk reviews (one at a time), and the sorting and indexing of imported mail with the embedding catch-up after a model change (two at a time). A big import therefore no longer delays sends, reminders or snooze wake-ups.
- In every lane the users take turns: the next job is the earliest one of the user whose turn it is, and one user holds at most half of a lane's slots. Sends, reminders, snooze wake-ups and push notifications go first.
- Each user's daily work (the exports, retraining, contact scores and the purge of deleted mail) runs at a time of day of its own, so the jobs of many users are not all queued at once.
- `MAILR_MODEL_CONCURRENCY` (default 4) limits the model calls that run at once against one model endpoint.
- More workers can run side by side: `docker compose up -d --scale worker-extra=N`.
- Pass `--host` and `--port` to `mailr serve` to change its address. Run the app as one process: live chat runs and the event stream are kept in it.

[Scaling](scaling.md) has the details and the sizes of server for 10, 50 and 100 users.

## Running on a private network

Mailr can run on a LAN only or behind a public reverse proxy. On a LAN, set `MAILR_BASE_URL` to the address used on the devices. Before you expose Mailr to the internet, turn on single sign-on and set `MAILR_LOCAL_LOGIN=false`, and read [Security and permissions](security.md).

## Commands inside the stack

Run any `mailr` command in the app container:

```sh
docker compose exec app mailr <command>
```

| Command | Description |
|---|---|
| `mailr serve` | Run the API and the web app. It is run in the `app` service. |
| `mailr worker` | Run the background worker. It is run in the `worker` service. |
| `mailr migrate` | Apply database migrations. They are also applied when the app starts. |
| `mailr db-dump` | Write a database dump now. See [Backups](backups.md#database-dumps). |
| `mailr db-restore` | Load a dump into a new database and compare it, or run the restore drill (`--check`). |
| `mailr gen-secret` | Print a random secret for `MAILR_SECRET_KEY` or `MAILR_SESSION_SECRET`. |
| `mailr create-user` | Add a user. See [Users](users.md). |
| `mailr set-password` | Set a user's password. |
| `mailr create-token` | Create an API token. See [Tokens and scopes](../agents/tokens.md). |
| `mailr reembed` | Rebuild the search vectors. See [Models](models.md). |
| `mailr rethread` | Rebuild a user's conversations. See [Upgrades](upgrades.md). |

Every option is listed in the [command line reference](../reference/cli.md).
