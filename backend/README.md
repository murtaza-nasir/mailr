# Mailr backend

Python 3.13, FastAPI, SQLAlchemy (async) on Postgres with pgvector, Alembic. The mail itself is
stored in Stalwart (JMAP); Postgres holds the index, the settings and everything else. One image
runs as two processes: the API (`mailr serve`, which also serves the built web app) and the worker
(`mailr worker`: background jobs, mail connectors, calendar sync).

## Layout

```
mailr/
  main.py      the FastAPI app: routers, middleware, error handlers
  worker.py    the background worker (jobs from the `jobs` table, connector runners, schedules)
  cli.py       the `mailr` command line (serve, worker, migrate, create-user, backups, …)
  config.py    settings, all from MAILR_* environment variables
  db.py        engine, sessions (`get_db` for routes, `session_scope` for jobs)
  models.py    the ORM models of the mail side; other packages keep their own models.py
  api/         HTTP routes of the mail side, accounts and administration; api/contract holds the
               response models (below)
  core/        the mail engine: ingest and threading, the store (Stalwart), actions, search,
               labels and layouts, the send gate and outbox, permissions and approvals, jobs,
               public ids, backups, health
  connectors/  upstream accounts: IMAP/SMTP, Gmail, mbox and Maildir import, the runners
  calendar/    calendars and tasks: the CalDAV mirror, the writer, invitations (iTIP), sharing,
               subscriptions, reminders
  contacts/    people, groups, signatures, CardDAV
  intel/       local models: spam, categories, importance, labels, summaries, deadlines, embeddings
  agent/       the assistant's chat loop and its tools
  assistant/   standing orders, routines and errands
  knowledge/   the user's profile, skills and memory
  notes/       notes and checklists
  tables/      tables (typed columns, views, history)
  websearch/   web search and page reading for the assistant, behind the browser gate
  integrations/  outside services (Speakr recordings)
  mcp/         the MCP server, a thin proxy to the REST tool API (`python -m mailr.mcp`)
  i18n/        texts the server writes, in the reader's language
  dev/         development only: test-database helpers, the e2e and demo seeds, a demo model server
migrations/    Alembic revisions
tests/         pytest suite; tests/helpers holds what several test modules share
```

A request goes `api/*` route → `core` (or the area's package) → the store and the database. Work
that can wait (sending, write-back to upstream accounts, training, sync) is queued as a job
(`core/jobs.py` `enqueue`) and run by the worker; a job type is a function decorated with
`@handler("<name>")`.

## The development services

The tests and a local server need Postgres and three services from `deploy/dev/docker-compose.yml`:
a Stalwart mail store (`:18080`, IMAP `:11143`/`:11993`), a second Stalwart that plays the upstream
mail provider (`:18091`), and Radicale for CalDAV (`:15232`). They use development-only accounts
defined in that file.

```sh
docker compose -f deploy/dev/docker-compose.yml up -d
docker run -d --name mailr-dev-pg -e POSTGRES_USER=mailr -e POSTGRES_PASSWORD=mailr \
  -e POSTGRES_DB=mailr -p 15432:5432 pgvector/pgvector:pg17
```

Settings come from the environment (`deploy/.env.example` lists every variable). For development,
a `backend/.env` along these lines is enough:

```sh
MAILR_DEV=true
MAILR_DATABASE_URL=postgresql+asyncpg://mailr:mailr@localhost:15432/mailr
MAILR_SECRET_KEY=<the output of: uv run mailr gen-secret>
MAILR_STALWART_URL=http://localhost:18080
MAILR_STALWART_ADMIN_USER=mailr-svc@mailr.internal
MAILR_STALWART_ADMIN_PASSWORD=dev-svc-pass
MAILR_STALWART_DOMAIN=mailr.internal
```

```sh
uv sync --all-extras          # .venv with the dev tools
uv run mailr migrate
uv run mailr serve --port 8025
uv run mailr worker           # in another terminal
```

The web app's dev server (`frontend/`, `npm run dev`) proxies `/api` to port 8025.

## Tests

```sh
uv run pytest -n 4 --dist worksteal          # the suite, in parallel (pytest-xdist)
uv run pytest -p no:xdist -m serial          # then the tests marked `serial`, in one process
uv run pytest tests/test_core_flow.py -k tokens   # one module or test
```

Each test process creates its own database on the Postgres server (`mailr/dev/testdb.py`), so the
user in `MAILR_DATABASE_URL` must be allowed to create databases. Tests marked `serial` use fixed
ports or state outside the database and are skipped under xdist; CI runs them in a second pass.
Tests marked `scale` build production-sized data and run only with `-m scale`. The calendar tests
need Radicale; `MAILR_TEST_WITHOUT_RADICALE=1` skips them on a machine without it.

## Static checks

```sh
uv run ruff check mailr tests      # lint; the rule set is in pyproject.toml
uv run basedpyright                # types; only errors not in .basedpyright/baseline.json fail
```

CI runs both (`.github/workflows/test.yml`). Older findings carry a `# noqa: <code>` (ruff) or an
entry in the baseline (basedpyright). After fixing type errors, `uv run basedpyright --writebaseline`
records the smaller baseline. A deliberate broad `except Exception` takes `# noqa: BLE001` with the
reason after it.

## Typed API contract

The most-used routes declare their answer as a Pydantic model (`mailr/api/contract`). The models
give the OpenAPI document its response schemas, the web app its TypeScript types, and the tests a
check of every real response.

| File | Role |
|---|---|
| `mailr/api/contract/{mail,calendar,notes,account}.py` | the response models |
| `mailr/api/contract/base.py` | `Out` (the base class), `PublicId`, `IsoTime`, `absent()` |
| `frontend/src/api/openapi.json`, `frontend/src/api/schema.gen.ts` | generated by `tools/api-types` |
| `tests/test_api_contract.py` | each typed route's real response against its model |

To type a route:

1. Write its model from the serialiser the route uses. A field the route always sends is
   required (`name: T`, or `name: T | None` when it can be null); a key the route sometimes
   leaves out is `name: T = absent()`. Times stay strings (`IsoTime`). The `id` of a conversation,
   message or approval object is `PublicId`, which keeps its kind for the public-id encoding.
2. Name it on the route: `@router.get("/x", response_model=C.mail.X, **C.TYPED)` with
   `from mailr.api import contract as C`. `TYPED` keeps absent keys absent, so the answer does not
   change.
3. Add the route to `MODELS` and `_requests` in `tests/test_api_contract.py`. The test fails when a
   response carries a field the model does not list, and checks that removing or renaming a
   required field breaks validation.
4. Run `tools/api-types` (from the repository root), then `npx tsc -b` in `frontend/`, and point
   the web's type at the generated one in `src/api/types.ts`.

A response that does not match its model is a 500 with the reason while `MAILR_DEV` is on (the
tests and the browser tests run that way). In production it goes out as the route built it, and
the mismatch is logged and reported.
