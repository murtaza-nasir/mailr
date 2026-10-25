# Contributing to Mailr

Thank you for your interest in contributing to Mailr.

## Ways to contribute

- **Report bugs:** [open an issue](https://github.com/murtaza-nasir/mailr/issues) describing the problem.
  Security vulnerabilities are not reported in issues: they go through GitHub's private
  vulnerability reporting, as described in [SECURITY.md](SECURITY.md).
- **Suggest features:** open an issue with your idea and the use case behind it.
- **Improve the documentation:** the user and admin guides are in `site/docs/`.
- **Translate:** the web app's texts are in `frontend/src/locales/`, the Android apps' in `android/*/src/main/res/values-*/`.

## Contributor License Agreement (CLA)

Mailr is dual-licensed:

1. **AGPLv3**, the open-source licence;
2. **a commercial licence**, for organisations that cannot comply with the AGPLv3.

All code contributions are subject to the [Contributor License Agreement](CLA.md). Under it, the
maintainers may include a contribution in both the open-source and the commercial versions of Mailr.
Contributors keep the copyright of their contributions; the CLA grants permission to use them.

**By submitting a pull request to this repository, you agree to the terms of the [Contributor License Agreement](CLA.md).**

Please read the [CLA](CLA.md) before you submit a contribution. A reminder of its terms is posted
on each new pull request.

## Pull requests

1. Fork the repository and create a branch for your change.
2. Keep each pull request to one feature or fix.
3. Run the checks for the parts you changed (below). The same checks in
   `.github/workflows/test.yml` run on every pull request.
4. Describe what changed and why, and reference related issues (`Fixes #123`).
5. Include screenshots for changes to the interface.

## Running the checks

Each part has its own README with the setup: [backend/README.md](backend/README.md) (Python, the
development services, the typed API contract), [frontend/README.md](frontend/README.md) and
[android/README.md](android/README.md). Everything below runs from a clone of this repository with
Docker, Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 22 and JDK 21.

**Development services:** the backend tests need Postgres with pgvector, two Stalwart mail servers
(the store and a stand-in provider) and Radicale. These commands start them, with
development-only accounts:

```sh
docker compose -f deploy/dev/docker-compose.yml up -d
docker run -d --name mailr-dev-pg -e POSTGRES_USER=mailr -e POSTGRES_PASSWORD=mailr -e POSTGRES_DB=mailr \
  -p 15432:5432 pgvector/pgvector:pg17
export MAILR_DATABASE_URL=postgresql+asyncpg://mailr:mailr@localhost:15432/mailr
```

The tests create their own databases (one per checkout and per pytest worker), so the Postgres user
must be allowed to create databases. Without a `backend/.env`, the tests use the development
accounts of that compose file.

**Backend:**

```sh
cd backend
uv sync --all-extras
uv run ruff check mailr tests                  # lint
uv run basedpyright                            # types: only errors not in the baseline fail
uv run pytest -n 4 --dist worksteal            # the suite, in parallel
uv run pytest -p no:xdist -m serial            # the tests that cannot run in parallel
```

**Web app:**

```sh
cd frontend
npm ci
npx tsc -b                # strict TypeScript
npm run lint              # eslint (no warnings allowed), the i18n and right-to-left checks
npx vitest run            # unit tests
npm run build
```

**Browser tests** (Playwright, `frontend/e2e/`): start the backend (`uv run mailr serve --port 8025`
with `MAILR_PUBLIC_IDS=true` and `MAILR_TRUSTED_AUTHSERV_IDS=mx.e2e.example`) and the web dev server
(`npx vite --port 5175`), then run `npx playwright test` in `frontend/`. Each test seeds its own
user. The header of `frontend/playwright.config.ts` lists the options.

**Generated files:** after changing a response model in `backend/mailr/api/contract`, run
`tools/api-types` from the repository root and commit the two files it writes. After changing the
server's English texts, the web styles or the server's time zone table, run `tools/generate`. CI
runs both with `--check`.

**Android:** `cd android && ./gradlew testDebugUnitTest` runs both apps' unit tests (or
`./build.sh testDebugUnitTest`, which needs only Docker). The screenshot tests render every screen
and compare nothing, because the reference images are not in this repository; to check a visual
change, record references before it and compare after it (android/README.md, "Build").

**Documentation:** `cd site && mkdocs build --strict`.

## ACT tags in comments

Some comments carry a tag such as `ACT task.snooze @web.tasks`. An `ACT` tag names a user action
(`task.snooze`) and the surface that offers it (`@web.tasks`: the web app's Tasks view); the
maintainers' coverage tools read them to check that every action is offered, tested and documented
where it should be. A pull request does not need to add or update them, and the comment around each
one says what the code does.

## License

By contributing to Mailr, you agree that your contributions are licensed under the project's
dual-licence model (AGPLv3 and commercial), as set out in the [CLA](CLA.md).
