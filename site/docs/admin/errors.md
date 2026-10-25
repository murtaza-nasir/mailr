# Error reports

Unhandled errors of the app, the worker and the web app can be reported to an error tracker that speaks the Sentry protocol. The stack includes one, [GlitchTip](https://glitchtip.com), in the optional `errors` profile. It runs on the same host, so reports never leave it. Without a DSN, nothing is reported and the reporting code in the web app is never loaded.

## What is reported

| From | Reported |
|---|---|
| The app (`mailr serve`) | Unhandled errors in requests, and every log line at `ERROR` or above. |
| The worker | Background jobs that failed for good (after their last attempt), errors in the periodic tasks, and log lines at `ERROR` or above, such as a failed database dump. |
| The web app | Errors that no part of the page handled (uncaught exceptions and rejected promises). |

Each report carries the release (`mailr@<version>`, set when the image is built), the environment (`MAILR_SENTRY_ENVIRONMENT`, `production` by default) and the process (`app`, `worker` or `web`).

Every report is cleaned in the process that sends it, and reports from browsers are cleaned again by the app before they are passed on:

- request bodies, cookies and headers (the sign-in cookie, `Authorization` and the CSRF token among them) are removed;
- URLs keep only their path: query strings and fragments, such as a search, are removed; addresses and tokens in the path and in the transaction name are replaced as in other texts;
- the values of variables in the stack trace are never collected;
- breadcrumbs (the steps before an error: clicks, log lines, requests) are never collected;
- email addresses in error messages are replaced by `<address>`;
- the values of a failed database query or a response check (a subject, a note) are left out, here and in the logs;
- of the user, only the numeric id is kept;
- an error raised while a request or job had read local-only content keeps only the type of the error, without its text, and a log line keeps only its template;
- when the tracker's host is not on the local network (every address of the DSN host is private or loopback), every report keeps only the error type, and `mailr check-config` warns about it.

No performance data is sent.

## Set up GlitchTip

1. Add to `.env`, with two new random values (for example from `openssl rand -hex 32`):

    ```ini
    COMPOSE_PROFILES=errors                  # with the other profiles, comma-separated
    GLITCHTIP_SECRET_KEY=<random value>
    GLITCHTIP_POSTGRES_PASSWORD=<random value, letters and digits>
    GLITCHTIP_DOMAIN=https://errors.example.com
    ```

    For alert mail, also set `GLITCHTIP_EMAIL_URL` (such as `smtp://user:password@smtp.example.com:587`) and `GLITCHTIP_FROM_EMAIL`. Without them, each alert mail is written to GlitchTip's log instead of being sent.

2. Start it: `docker compose up -d`. Two containers start: `glitchtip` (the web interface and its background worker in one process) and `glitchtip-postgres` (its own database). Its port is published on `127.0.0.1:8027` (`GLITCHTIP_BIND`, `GLITCHTIP_PORT`).
3. Point a host name of the reverse proxy, such as `errors.example.com`, at that port. The address must match `GLITCHTIP_DOMAIN`.
4. Create the first account; sign-up from the web page is off:

    ```sh
    docker compose exec glitchtip python manage.py createsuperuser
    ```

5. Sign in at `GLITCHTIP_DOMAIN`, create an organisation, and in it two projects: one for the server (platform Python) and one for the web app (platform JavaScript). Each project shows its DSN under **Settings → Client Keys (DSN)**, such as `https://3f2a…@errors.example.com/1`.
6. Put both DSNs in `.env`, with `http://` and the host `glitchtip:8000` in place of the public address. The app and the worker reach GlitchTip directly on the stack's own network, and the web app sends its reports through the app:

    ```ini
    MAILR_SENTRY_DSN=http://3f2a…@glitchtip:8000/1
    MAILR_SENTRY_BROWSER_DSN=http://9c41…@glitchtip:8000/2
    ```

7. Apply it: `docker compose up -d`. The app and the worker log `error reporting on (mailr@<version>)` at start.
8. Send a test report:

    ```sh
    docker compose run --rm cli python -c "from mailr.core import errors; errors.init('cli'); \
      import sentry_sdk; sentry_sdk.capture_message('mailr test report'); sentry_sdk.flush()"
    ```

    It appears in the server project within a few seconds.

Alerts are set per project in GlitchTip, under **Settings → Alerts**: for example, one mail for each new kind of error.

## How it runs

- GlitchTip and the app and worker share one network, `errors`. GlitchTip cannot reach Mailr's database or the mail store. Its database is on an internal network with nothing else.
- GlitchTip keeps its queue and its cache in its own Postgres; no Redis or Valkey container is needed. Uptime checks and log collection in GlitchTip are off.
- Events are kept for 90 days (`GLITCHTIP_RETENTION_DAYS`).
- Memory: about 300 MB for both containers; GlitchTip is limited to 1 GB (`GLITCHTIP_MEMORY`) and its database to 512 MB (`GLITCHTIP_POSTGRES_MEMORY`). Both run with every capability dropped that their images do not need, and with a read-only root filesystem ([Hardening](deployment.md#hardening)).
- GlitchTip never fetches source files or source maps from the addresses in a report: it uses only the source maps uploaded to it. A report sent through Mailr therefore cannot make GlitchTip request a page on the `errors` network.
- GlitchTip's database is not in Mailr's nightly dump. Its volumes are `glitchtip-postgres-data` and `glitchtip-uploads`; a snapshot of the host covers them.

## Another Sentry-compatible server

Any server that takes Sentry's envelope format works, such as a GlitchTip or Sentry elsewhere: set the two DSNs to its projects. The app must reach that server; the browsers never contact it.

## The web app's reports

The web app asks `GET /api/v1/errors/config` for its DSN at start. With none, the reporting code is never loaded. With one, reports are sent to `POST /api/v1/errors/envelope` on Mailr's own address, since the page's content security policy allows no other. A report is accepted only from a signed-in browser, or with the key that a page on the sign-in screen gets from `GET /api/v1/errors/config`. Each page load gets a key of its own, valid only from the same network address (an IPv6 address counts by its /64) for 6 hours, and each key may send 20 reports in 10 minutes. Only reports for the configured DSN are accepted, at most 64 KB each, with at most 5 events, 10 exceptions of 100 frames and 20 tags, and not nested more than 24 levels deep. Each network address may send 30 reports in 10 minutes and 60 in an hour, and each user 60 in 10 minutes. Signed-in browsers share 600 reports an hour for the whole server, and signed-out pages a separate 200, so reports from outside cannot use up those of the people signed in. More are refused with 429. A report of another shape is refused with 400. Only error events and the fields a report needs are kept. They are cleaned, given the user id of the session (none for a signed-out page) and the server's release, and passed to the tracker.

The app passes reports only to the host of `MAILR_SENTRY_BROWSER_DSN` (or `MAILR_SENTRY_DSN`); nothing in a report or a request chooses where they go.

- The DSN must have the usual form, `http(s)://<key>@<host>[:<port>][/<path>]/<project>`. A DSN of another form, for example with a second `@`, a backslash or a query string, turns the web app's reports off: `GET /api/v1/errors/config` answers without a DSN.
- The host name is resolved once for each connection, and the connection goes to the checked address. It never goes through a proxy from the environment, and a redirect from the tracker is not followed.
- A link-local address (such as the cloud metadata address `169.254.169.254`), a multicast address or `0.0.0.0` is never contacted.
- A loopback or private address is contacted only when the DSN names it: as the address itself (`http://<key>@10.0.0.5:8000/2`), as `localhost`, or by a local name: one without a dot, such as the compose service `glitchtip`, or one ending in `.internal`, `.lan`, `.local` or `.home.arpa`. The DSN of a public name, such as `errors.example.com`, reaches public addresses only, whatever its DNS answers later. Where the local DNS answers a public name with a private address, put that address or a local name in `MAILR_SENTRY_BROWSER_DSN`.
- A report that cannot be passed on is dropped, and the app logs one warning line.
