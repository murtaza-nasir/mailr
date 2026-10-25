# Scaling

One server runs Mailr for a household or for an organisation of about a hundred people. This page describes how the background work is shared between the users, how model calls are limited, how more workers are added, and what size of server to plan for.

## How the work is shared

The worker runs background jobs in lanes ([Deployment](deployment.md#scaling-the-work)): the main lane for triage, sends, reminders and most other jobs, and lanes of their own for writes to the mail servers, CalDAV sync, image fetches, scheduled upkeep, bulk reviews and the sorting of imported mail.

- **Users take turns.** In every lane, the next job is the earliest due job of the user whose turn it is: the user served longest ago. A user with thousands of jobs, for example after adding a large account, does not hold up the new mail of the others.
- **Half of the slots per user.** While another user has a job waiting, one user holds at most half of a lane's slots (at least one). When nobody else has a job waiting, one user may use all slots but one, so the lane keeps working for them and the next user's job still finds a free slot.
- **Instance upkeep takes its own turn.** Jobs that belong to no user, such as the database dump, take turns as one more user.
- **Sends come first.** Sends, reminders, snooze wake-ups, follow-up checks and push notifications are taken before any turn and are never held back by the limit per user.
- **One write per mail account.** Writes to the users' mail servers (moves, flags, deletions, copies in Sent) run `MAILR_UPSTREAM_CONCURRENCY` at a time in each worker (4), and never two at once for one mail account. An account with a write running is passed over, so the slots go to other accounts.
- **Daily work is spread over the day.** Each user's exports, retraining, contact scores, learning pass and purge of deleted mail run at a time of day of the user's own, the same every day. After an upgrade, the first run of each user comes at most a day after the last one.

## Model calls

`MAILR_MODEL_CONCURRENCY` (default 4) is the number of model calls that run at once against one model endpoint, counted over the app and all workers. Set it to the number of requests the model server handles at once without slowing down. For one local GPU this is usually 4 to 8. For a hosted endpoint, set it to the provider's limit, or `0` for no limit.

- Calls from the app come first: chat, Help me write and the query vector of a search. Someone is waiting for them.
- Background calls from the workers (triage, summaries, the embeddings of imported mail) leave the last slot free for the app's calls.
- While another user's call waits, one user's calls hold at most half of the slots.
- A call that waits gets the next free slot in this order: calls from the app, then the user with the fewest calls running, then the call that waited longest.

Each process holds one database connection for the model call slots. When a process stops, its slots are free again.

## More workers

Extra workers take jobs from the same queue as the `worker` service. They are started with:

```sh
docker compose up -d --scale worker-extra=2
```

`docker compose up -d --scale worker-extra=0` stops them again. Running jobs are handed back to the queue when a worker stops.

- **Nothing runs twice.** Each job is claimed by one worker. Scheduled tasks, such as the daily exports, are claimed in the database, so only one worker queues them.
- **Mail accounts sync in one worker.** Each mail account is synced by the worker that holds its lease. When that worker stops, another worker takes the account within about half a minute. **Sync now** reaches the worker that holds the account.
- **Same host and volume.** All workers use the `mailr-data` volume. The exports are guarded by file locks on it, so every worker must run on the same host.
- **Addresses.** The `worker` service has a fixed address on the internal network; the extra workers get theirs from Docker. They sign in to the mail store with the service account only.
- **The app stays one process.** Live chat runs and the event stream of the web app are kept in the app's process, so add workers, not app processes.

### Database connections

Each extra worker holds up to 25 connections to Postgres. Raise `MAILR_PG_MAX_CONNECTIONS` by 25 for each extra worker, and restart the `postgres` container ([Postgres settings](deployment.md#postgres-settings)).

| Process | Connections at most |
|---|---|
| `app` | 44 |
| `worker` | 38 |
| `worker-extra`, each | 25 |
| one `cli` command | 17 |

## Sizes of server

The sizes below are for Mailr's own containers (app, workers, Postgres and the mail store) with mailboxes of tens of thousands of messages per user. The model server is not included: it runs on its own machine or GPU.

| Users | CPU cores | Memory | Workers | Postgres memory limit and settings in `.env` |
|---|---|---|---|---|
| up to 10 | 4 | 8 GB | `worker` with the defaults | the defaults (`MAILR_PG_MEMORY=4g`, `MAILR_PG_SHARED_BUFFERS=1GB`, `MAILR_PG_EFFECTIVE_CACHE_SIZE=3GB`) |
| up to 50 | 8 | 16 GB | `worker` with `MAILR_WORKER_CONCURRENCY=8` | `MAILR_PG_MEMORY=8g`, `MAILR_PG_SHARED_BUFFERS=2GB`, `MAILR_PG_EFFECTIVE_CACHE_SIZE=6GB` |
| up to 100 | 12 to 16 | 32 GB | `worker` and one `worker-extra`, `MAILR_WORKER_CONCURRENCY=8` | `MAILR_PG_MEMORY=16g`, `MAILR_PG_SHARED_BUFFERS=4GB`, `MAILR_PG_EFFECTIVE_CACHE_SIZE=12GB`, `MAILR_PG_MAX_CONNECTIONS=175` |

`MAILR_PG_MEMORY` is the memory limit of the Postgres container, and the shared buffers count against it, together with the memory of the running queries and the shared memory ([Hardening](deployment.md#hardening)). Raise the two together: the shared buffers are about a quarter of the limit. With shared buffers as large as the limit, Postgres is stopped by the kernel under load.

### Settings for 50 users

Add these lines to `.env`:

```sh
MAILR_WORKER_CONCURRENCY=8
MAILR_PG_MEMORY=8g
MAILR_PG_SHARED_BUFFERS=2GB
MAILR_PG_EFFECTIVE_CACHE_SIZE=6GB
```

### Settings for 100 users

Add these lines to `.env`:

```sh
MAILR_WORKER_CONCURRENCY=8
MAILR_PG_MEMORY=16g
MAILR_PG_SHARED_BUFFERS=4GB
MAILR_PG_EFFECTIVE_CACHE_SIZE=12GB
MAILR_PG_MAX_CONNECTIONS=175
```

The extra worker is started with `--scale worker-extra=1` in the last command below.

### Apply the settings

The Postgres settings take effect when the `postgres` container is restarted ([Upgrades](upgrades.md#postgres-settings)). Write a dump first, then restart Postgres, then the rest:

```sh
docker compose run --rm cli mailr check-config
docker compose run --rm cli mailr db-dump --reason before-restart
docker compose up -d postgres
docker compose up -d
```

For 100 users, run the last command as `docker compose up -d --scale worker-extra=1`. Within about a minute, every container is listed as healthy in `docker compose ps`.

- **Disk**: roughly 1.3 times the mail size for the store, the index and the search vectors, plus the export copy. Use SSD storage.
- **Model calls**: set `MAILR_MODEL_CONCURRENCY` one above the number of requests the model server runs at once. The last slot is kept for live calls, so background triage then still uses the whole model server.
- **More users or more mail per user**: the database grows with the mail, not with the number of users. Raise `MAILR_PG_MEMORY` and, with it, `MAILR_PG_SHARED_BUFFERS` to about a quarter of that limit; set `MAILR_PG_EFFECTIVE_CACHE_SIZE` to about three quarters of it ([Postgres settings](deployment.md#postgres-settings)).
- **Workers**: an extra worker adds job slots; it does not add model capacity. Add one when jobs wait while the model server is idle. The `worker` check of [Monitoring](monitoring.md#the-checks) fails when jobs wait too long.

## Measurements

These figures were measured on a test server with fictional mailboxes of 2,000 messages per user and a stand-in model server that answers each chat request in 0.3 seconds and each embedding request in 0.05 seconds, four requests at a time. During each two-minute run, every user received a message every 30 seconds on average, sent one every two minutes and opened the inbox or searched every 10 seconds. One more user had 100,000 imported messages waiting for triage and received 2,000 new messages at once at the start.

| Users | Workers | New mail sorted, median / 95th percentile | Send queued to done | Inbox page | Search | Imported mail sorted per minute |
|---|---|---|---|---|---|---|
| 10 | 1 | 0.5 s / 1.1 s | 0.06 s / 0.4 s | 7 ms / 20 ms | 22 ms / 59 ms | 1,460 |
| 10 | 2 | 1.1 s / 2.5 s | 0.2 s / 1.1 s | 8 ms / 31 ms | 23 ms / 44 ms | 1,230 |
| 50 | 1 | 0.7 s / 1.5 s | 0.07 s / 0.4 s | 5 ms / 9 ms | 21 ms / 56 ms | 1,680 |
| 50 | 2 | 0.9 s / 2.8 s | 0.06 s / 0.3 s | 5 ms / 32 ms | 18 ms / 146 ms | 1,450 |
| 100 | 1 | 0.8 s / 1.8 s | 0.08 s / 0.3 s | 6 ms / 10 ms | 25 ms / 65 ms | 1,490 |
| 100 | 2 | 1.4 s / 2.7 s | 0.08 s / 0.3 s | 5 ms / 11 ms | 26 ms / 65 ms | 1,350 |

"New mail sorted" is the time from a message's arrival in the index to the end of its triage, with its model calls. With the default settings, a model server that runs four requests at once was busy about two thirds of the time. With `MAILR_MODEL_CONCURRENCY=5` and `MAILR_WORKER_CONCURRENCY=8` at 100 users, it was busy all the time: 60 % more of the busy user's 2,000 new messages were sorted during the run, and the other users' new mail took 0.8 s / 3.3 s.
