# Monitoring

Mailr has health endpoints for an uptime monitor and for Docker's container healthcheck, and two heartbeats can be sent to push monitors. The examples are for Uptime Kuma; any monitor for HTTP status codes or push URLs works the same way.

## Health endpoints

No sign-in is required for any of the endpoints. No answer has secrets, account names, addresses or mail.

Without credentials, only the verdict is returned: the answer of `/health/live` and `/health/ready` is `{"ok":true,"checked_at":"…"}` (or `"ok":false` with `503`), and the answer of `/health/egress` is `"state":"ok"` or `"state":"failing"`. The per-check details below, and which way the egress fails, are shown only with the monitor token or to a signed-in admin. Set `MAILR_HEALTH_TOKEN` to a long random value and send it in the `X-Health-Token` header. In Uptime Kuma, put `{"X-Health-Token": "<token>"}` under the monitor's *Headers*. For a monitor on the status code or the `"state":"ok"` keyword only, no token is needed.

| Route | Description |
|---|---|
| `GET /api/v1/health` | Answers `{"ok":true}` while the web app runs. |
| `GET /api/v1/health/live` | Liveness, the app container's healthcheck. Only the `db` and `store` checks below are run: `200` while the database and the mail store answer, `503` otherwise. |
| `GET /api/v1/health/ready` | Readiness, for uptime monitors. The checks below are run, and the answer is `200` when every required check passes, `503` when one fails. |
| `GET /api/v1/health/egress` | The VPN egress for remote images. Answers `200` with `"state":"ok"` while the [egress check](image-proxy.md#the-egress-check) passes, `503` otherwise. Without an egress proxy configured, it answers `200`: `"state":"ok"` without the token, and `"state":"off"` with the token or to a signed-in admin, meaning there is nothing to check. |

The result of `/health/ready` is cached for 20 seconds, so it can be polled often from a monitor.

An example answer with the monitor token:

```json
{
  "ok": false,
  "checks": {
    "db": {"ok": true, "detail": "reachable", "required": true},
    "store": {"ok": true, "detail": "reachable", "required": true},
    "worker": {"ok": true, "detail": "heartbeat 12 s ago", "required": true},
    "sync": {"ok": false, "detail": "1 account not syncing", "required": true},
    "export": {"ok": true, "detail": "3 accounts exported in the last 26 h", "required": true},
    "backup_target": {"ok": true, "detail": "3 accounts copied in the last 26 h", "required": true},
    "calendar_export": {"ok": true, "detail": "4 calendar exports in the last 26 h and copied", "required": true},
    "notes_export": {"ok": true, "detail": "2 notes exports in the last 26 h and copied", "required": true},
    "db_dump": {"ok": true, "detail": "last dump 5 h ago (1180 MB)", "required": true},
    "disk": {"ok": true, "detail": "backup: 12 % free (low)", "required": true, "warn": true},
    "push": {"ok": true, "detail": "push server reachable", "required": false},
    "egress": {"ok": true, "detail": "via egress proxy", "required": false},
    "jobs_failed": {"ok": true, "detail": "no job failed in the last hour", "required": false},
    "store_sync": {"ok": true, "detail": "no message failed to be indexed", "required": false}
  },
  "checked_at": "2026-10-03T14:05:09+00:00"
}
```

## The checks

| Check | Passes when | Required |
|---|---|---|
| `db` | A trivial query on Postgres succeeds. | yes |
| `store` | A health probe of the bundled Stalwart mail store is answered. | yes |
| `worker` | A heartbeat from the worker's scheduler was recorded in the last 3 minutes, and no queued job is more than 15 minutes overdue. Jobs scheduled for later do not count. Scheduled upkeep ([Deployment](deployment.md#scaling-the-work)) gets the users' daily exports and retraining and the nightly dump and runs two of them at a time, so these jobs count as overdue only after 12 hours. Bulk reviews and changes sent to the users' mail servers count after an hour. | yes |
| `sync` | No enabled mail account in `store` or `mirror` mode (Mailr keeps the mail, or Keep in sync) is in error, and each one was synced in the last 30 minutes (or within twice its poll interval, when that is longer). An account on its first sync passes. An account in error still passes for 15 minutes (plus its poll interval) after its last good sync, while the connection is retried after a short outage at the provider. Accounts that are copied once (`import`) are not checked. | yes |
| `export` | Every active user with mail has a completed [daily export](backups.md#the-daily-export) in the last 26 hours. A user or account that has never been exported counts as "first export pending" (the check stays ok) for 26 hours after its data appeared, for example `0 of 1 account exported in the last 26 h, 1 new (first export pending)`; after that, a missing export fails. The same applies to `calendar_export`, `notes_export` and the copies on the backup target. With the [mail export turned off](backups.md#turn-the-mail-export-off) (`MAILR_MAIL_EXPORT=false`), the check is off: it passes with `"off": true` and the detail `off (MAILR_MAIL_EXPORT=false)`. | yes |
| `backup_target` | Without `MAILR_BACKUP_TARGET`: always. With it: the target folder exists, holds the marker file `.mailr-backup-target` (see [Backups](backups.md#the-backup-target)), can be written to in a test, and holds a completed export for every user with mail from the last 26 hours. With the mail export off, only the target itself is checked; the calendar and notes copies are in their own checks. | yes |
| `calendar_export` | Every active user with calendars or address books, and the group calendars when there are any, has a completed [calendar export](backups.md#calendars-and-contacts) in the last 26 hours. With a mounted backup target, the copy there counts too. A target that is not mounted counts as a failure of `backup_target`, not of this check. | yes |
| `notes_export` | Every active user who owns a note, a list or a table has a completed [notes export](backups.md#notes-lists-and-tables) in the last 26 hours. With a mounted backup target, the copy there counts too. | yes |
| `db_dump` | With nightly dumps on (`MAILR_DB_DUMP`): the last [database dump](backups.md#database-dumps) succeeded, and the newest good one is less than 26 hours old. | yes |
| `disk` | The data volume, the backup target and the paths in `MAILR_DISK_CHECK_PATHS` have at least `MAILR_DISK_FAIL_PERCENT` (5 %) free. Below `MAILR_DISK_WARN_PERCENT` (15 %) the check passes with `"warn": true`. The detail names the fullest of them. With the bundled compose file, the Postgres and mail store volumes are on the same file system as the data volume, so this check covers them too. | yes |
| `push` | Without `MAILR_NTFY_URL`: always. With it: an answer comes from the ntfy server. | no |
| `proxy` | No request in the last hour had `X-Forwarded-For` from an address outside `MAILR_TRUSTED_PROXIES` (see [Reverse proxy](reverse-proxy.md)). Its result is for the app process that answers. | no |
| `egress` | Without an egress proxy (and without `MAILR_REQUIRE_EGRESS`): always. With one: in the worker's last [egress check](image-proxy.md#the-egress-check), less than 15 minutes old, the proxy answered through an address other than the server's own. Details such as "egress down: images paused" or "LEAK: exit address is the server's own" have no address. | no |
| `jobs_failed` | No background job failed for good in the last hour. A failure here is a warning (`"warn": true`); it never makes the endpoint fail. Failed jobs are listed in **Settings → Admin → Diagnostics**. | no |
| `store_sync` | No message in the mail store has failed to be added to the index eight times. A message that fails, for example on a download timeout, is tried again after 1, 2, 4 minutes and so on, and after the eighth failure once a day. A failure here is a warning (`"warn": true`). The worker's log names each message and its error (`could not index stored message`). | no |

A check that is off has nothing to watch because its feature is turned off. It counts as passing, so `/health/ready` keeps answering `200` and a monitor on it does not go down. **Settings → Admin → Diagnostics** shows it as information.

Details have counts only, such as "1 account not syncing". **Settings → Admin → Diagnostics** lists every check with a plain name and the account's name; see [Troubleshooting](troubleshooting.md#diagnostics).

To report a check without counting it as a failure of the endpoint, list it in `MAILR_HEALTH_OPTIONAL`, for example `MAILR_HEALTH_OPTIONAL=export,backup_target,calendar_export,notes_export` on a server without exports to watch.

## Heartbeats

A push URL can be called from the worker, so that a stopped worker or a missing backup is noticed in a monitor. Each heartbeat is off while its variable is empty.

| Variable | Schedule |
|---|---|
| `MAILR_HEARTBEAT_URL` | Every 5 minutes. With `status=up&msg=OK` when the readiness checks pass, otherwise `status=down` and a message naming the failing checks. |
| `MAILR_BACKUP_HEARTBEAT_URL` | After an export run (mail, calendars, or notes and tables) or a database dump, when the `export`, `backup_target`, `calendar_export`, `notes_export` and `db_dump` checks pass (at most once an hour). |

`status` and `msg` are set on the URL and its other parameters are kept, so a push URL copied from Uptime Kuma works as it is. The calls are plain `GET` requests with a 5-second timeout and no redirects. A failed call is logged at most once an hour. The same outbound check applies to heartbeat URLs as to every other connection from Mailr: a monitor on `localhost` or `127.0.0.1` must be listed in `MAILR_OUTBOUND_ALLOW`.

## Example monitors

The stack is covered with three monitors in Uptime Kuma.

1. **HTTP monitor on readiness.** Type *HTTP(s)*, URL `https://mail.example.com/api/v1/health/ready`, interval 60 seconds, accepted status codes `200-299`. On a `503`, the monitor is down. With the `X-Health-Token` header set, the failed check is named in the response body.
2. **Push monitor for the worker.** Type *Push*, heartbeat interval 600 seconds. Copy its push URL into `.env`:

    ```ini
    MAILR_HEARTBEAT_URL=https://kuma.example.com/api/push/AbCdEf123?status=up&msg=OK&ping=
    ```

    The monitor goes down when a failing check is reported, or when no heartbeat arrives for 10 minutes.

3. **Push monitor for the backup.** Type *Push*, heartbeat interval 93600 seconds (26 hours). Copy its push URL into `.env`:

    ```ini
    MAILR_BACKUP_HEARTBEAT_URL=https://kuma.example.com/api/push/GhIjKl456?status=up&msg=OK&ping=
    ```

    The monitor goes down when no completed export arrives for 26 hours. One heartbeat covers mail, calendars, notes and the database dump: it is sent only while all of them are current.

Run `docker compose up -d` after changing `.env`.

## Watch the VPN egress

With a VPN egress for remote images, add a fourth monitor: type *HTTP(s) - Keyword*, URL `https://mail.example.com/api/v1/health/egress`, keyword `"state":"ok"`, interval 300 seconds. The monitor goes down when there is no answer through the VPN, when the exit address is the server's own, or when the checks stop. Mail itself is not affected; images are paused (with `MAILR_REQUIRE_EGRESS`) or fail to load until the egress works again.

## Container healthchecks

Docker marks each container healthy or unhealthy with its own check. A container manager such as Portainer shows the state.

| Service | Check | Start period |
|---|---|---|
| `app` | `GET /api/v1/health/live` answers `200`: the database and the mail store answer | 5 minutes (a dump before migrations, then the migrations) |
| `worker` | `mailr worker-alive`: the scheduler ticked in the last 3 minutes, and the mail store answers | 5 minutes |
| `postgres` | `pg_isready` | |
| `stalwart` | its `/healthz/live` answers | 3 minutes (the first start provisions the server and restarts it once) |

The app waits for Postgres and Stalwart to be healthy before it starts. With plain Docker, a container that is unhealthy keeps running and is not restarted. A reverse proxy such as Traefik stops routing to an unhealthy container, and some container managers restart it. For this reason the container's healthcheck is liveness only: a failing readiness check, such as one account's expired password, a backup share that is not mounted or a failed nightly dump, never takes the web app down. Watch readiness with a monitor (above).

## Logs

The app and the worker log one JSON object per line on standard output (`MAILR_LOG_FORMAT=json`, the default; `text` gives the older one-line format). Each line has `ts`, `level`, `logger` and `msg`, and a traceback in `exc`.

The same lines are written to rotating files in the data volume, `/data/logs/app.log` and `/data/logs/worker.log`: 10 files of 10 MB each per process (`MAILR_LOG_FILE_MB`, `MAILR_LOG_FILES`). Docker's own log of a container is removed with the container at every redeploy; these files stay. Docker's logs are rotated too, at 10 MB with five files per container (the `logging` options of every service in the compose file).

```sh
docker compose exec app sh -c 'tail -n 50 /data/logs/app.log'
docker compose logs --since 1h worker
```

### Requests

Every request gives one line on the `mailr.access` logger:

```json
{"ts": "2026-10-14T09:12:03.418+00:00", "level": "INFO", "logger": "mailr.access",
 "msg": "GET /api/v1/conversations/{cid} 200 41 ms", "method": "GET",
 "route": "/api/v1/conversations/{cid}", "status": 200, "ms": 41.2, "user": 7,
 "db_ms": 18.5, "queries": 6, "stream": false}
```

The line names the route's template, never the path as requested or its query string, so no search text, address or id from a URL is logged. `user` is the numeric id of the signed-in user. Requests to the health endpoints are logged at `DEBUG` only. `MAILR_ACCESS_LOG=false` turns the lines off; the slow-request warnings stay.

### Postgres

Postgres logs every statement that takes longer than 500 ms, and every wait for a lock longer than its deadlock timeout (1 s), without the statement's parameters. Statement statistics are collected by `pg_stat_statements`:

```sh
docker compose exec postgres psql -U mailr -c "SELECT calls, round(mean_exec_time) AS ms, left(query, 80) FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 15"
```

## Error reports

Unhandled errors of the app, the worker and the web app can be sent to a GlitchTip server in the stack. See [Error reports](errors.md).
