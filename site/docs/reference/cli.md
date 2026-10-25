# Command line

The `mailr` command is included in the image. With Docker Compose, run it in the app container:

```sh
docker compose exec app mailr create-user alex@northwind.example --name "Alex Rivera" --admin
```

For a one-off command in a container of its own, use the `cli` service. The service has the app's image and settings, without the app's fixed address and port, so it can run while the app and worker are up:

```sh
docker compose run --rm cli mailr reparse-attached-emails --dry-run
```

`docker compose run --rm app …` fails while the app runs ("Address already in use"). In a container manager that runs Compose stacks, run the service `cli` with the command. The `cli` service is in the `tools` profile, so it is never started by `docker compose up`.

| Command | Description | Arguments |
|---|---|---|
| `mailr serve` | Run the API and the web app. | `--host` (default `0.0.0.0`), `--port` (default `8025`), `--workers` (default `1`) |
| `mailr worker` | Run the background worker: connectors, sorting, reminders, exports and other jobs. |  |
| `mailr browser-gate` | Run the [browser gate](../admin/web-search.md#the-browser-gate), the only way out for the page-reading browser. It is the Compose service `browser-gate`. |  |
| `mailr check-config` | Check the configuration and print one line per problem, each naming its environment variable. Values are never printed. Also check that the database and the mail server are reachable, and that Mailr's service password is accepted by the mail server. With `MAILR_STORE_BACKUP_CHECK=false`, a warning is printed while an account where Mailr keeps the mail has deletion at the provider turned on, as a reminder that no backup is checked. The exit code is 1 when there is an error. The same checks, without the network ones, are run at the start of `mailr serve` and `mailr worker`, and on an error, neither is started. | `--offline` (skip the database and mail server checks) |
| `mailr migrate` | Apply database migrations. They are also applied when the app starts. A database dump is written first when the schema is behind ([Backups](../admin/backups.md#before-migrations)). |  |
| `mailr db-dump` | Write a database dump now (`pg_dump`, custom format) with its manifest. The worker writes one every night. Dumps made with this command are never removed automatically. | `--dir` (default: `postgres/` on the backup target, else `/data/backups/postgres`), `--reason` (the word in the file name, default `manual`), `--list` (list the dumps and the last nightly result) |
| `mailr db-restore` | Load a dump into a new database and compare the row count of every table with the dump's manifest. The live database is never a target. With `--check`, the newest dump is loaded into a scratch database, compared and dropped again (the restore drill). The exit code is 1 when a count differs. | `file`, `--into` (the new database), `--check`, `--keep` (with `--check`: keep the scratch database) |
| `mailr worker-alive` | Exit with 0 when the worker in this container recorded a scheduler tick in the last 3 minutes and the mail store answers, else 1. It is the worker's Docker healthcheck. |  |
| `mailr gen-secret` | Print a random secret for `MAILR_SECRET_KEY` or `MAILR_SESSION_SECRET`. |  |
| `mailr create-user` | Create a user. A password (at least 10 characters) is requested. | `email`, `--name`, `--admin`, `--no-password` (an account for sign-in through OIDC only) |
| `mailr set-password` | Set a user's password, as account recovery. The new password is requested. The user's sessions are signed out and every API token of the user is revoked. The IMAP password is replaced and the calendar app passwords are deleted, so mail and calendar apps must sign in again. | `email` |
| `mailr create-token` | Create an API token and print it once. | `email`, `--name` (default `cli`), `--scopes` (default `read,draft`), `--local-only` |
| `mailr reembed` | Build missing search passages and embed mail with the current embedding model. Without an email, every user. | `email` (optional), `--inline` (run here, with progress, instead of queueing worker jobs) |
| `mailr rethread` | Rebuild a user's conversations from the message headers. | `email` |
| `mailr rethread-automated` | Split conversations in which separate automated emails (alerts, newsletters) are grouped only by subject. Each email gets its own conversation and keeps its labels. | `user` (email or user id), `--dry-run` (list them without changing anything), `--sample` (default `10`, how many to list) |
| `mailr langid-backfill` | Identify the language of recent mail that arrived before languages were identified in Mailr, so the **Translate** bar can be shown on it. It is run on the server with no model service, in batches; mail that already has a language is skipped, and the command can be stopped and run again. Without a user, every user. | `user` (optional, email or user id), `--days` (default `90`), `--batch` (default `200`), `--redo` (check mail that already has a language too) |
| `mailr langid-model` | Download the language identification model and check its checksum. The Docker image already contains the model; the command is for running Mailr from a source checkout. |  |
| `mailr resnippet` | Rebuild the list previews of recent mail from the stored messages, for example after an upgrade with a change to how previews are made. The new previews are shown in conversations too. It is run in batches and can be stopped and run again. Without a user, every user. | `user` (optional, email or user id), `--days` (default `30`), `--batch` (default `200`, messages per transaction), `--dry-run` (count and show examples without changing anything) |
| `mailr web-search-key` | Store the API key of a [web search provider](../admin/web-search.md) or, with `--reader`, a [page reader](../admin/web-search.md#page-readers), read from standard input (or asked for without echo on a terminal). The key is never printed. With `--kind`, one of that name is created when none exists. | `name`, `--kind` (`exa`, `brave`, `tavily`, `degoog`, `searxng`, `jina`, `firecrawl`; with `--reader`: `browserless`, `firecrawl`, `jina`), `--reader`, `--base-url`, `--network` (`internet` or `local`), `--priority`, `--per-day` |
| `mailr export` | Write the daily exports now: the mail export (Maildir, batch after batch until it has caught up), notes and tables, and calendars and contacts. The worker writes them every day. The exit code is 1 when a mail export could not catch up (an unreadable message; the log names it). See [Backups](../admin/backups.md#the-daily-export). | `--user` (email or user id; default: every enabled user and the group calendars), `--mail`, `--notes`, `--calendars` (only those exports; they can be combined) |
| `mailr purge-deleted` | Remove what deleted mail left behind: the text of deleted messages in the database. The worker does this every day, but holds back when the deleted messages are more than a tenth of a user's messages (and more than 1,000), because a mail store that lost its data looks the same; **Admin → Diagnostics** then shows the hold. Run it when the mail store is known to be healthy. The export files of mail that vanished from the mail store stay, unless you add `--exports`. Only counts are printed. The exit code is 1 when an export was busy (a copy to the backup target); run it again later. See [Data at rest](../admin/security.md#data-at-rest). | `user` (optional, email or user id; default: every user), `--dry-run` (count only), `--exports` (also remove the files of all deleted mail from the mail export and the backup target; only with a complete mail store) |
| `mailr store resync` | Forget the store sync state, so that the worker compares each whole mailbox with the index at its next store sync: messages the mail store no longer has are marked deleted (their export files stay), and messages only the store has are added. Run it after restoring the Stalwart volume or the database. See [Restore](../admin/backups.md#restore). | `--user` (email or user id; default: every enabled user), `--accounts` (also scan the users' mail accounts again from the start, as **Resync from scratch** does) |
| `mailr store import-export` | Import each user's mail export back, as a file import account named "Mail export", to bring back mail that a restore of the mail store lost. Messages already in Mailr are skipped. A user whose mailbox the worker has not compared with the mail store since `mailr store resync` is skipped, with exit code 1; run it again later. See [The mail store on the same server](../admin/backups.md#the-mail-store-on-the-same-server). | `--user` (email or user id; default: every enabled user) |
| `mailr tally rebuild` | Count the mail behind the **Subscriptions** and **Clean up** pages again from scratch. Mailr keeps this count up to date as mail changes, and the worker checks it every night and corrects any difference it finds (the log names the user and the number of senders). After an upgrade, the worker makes the first count in the background; until it is ready, the pages count the mail on each visit, as before. While the command runs, the pages do the same. | `--user` (email or user id; default: every enabled user) |
| `mailr calendars export` | Write the calendar and contact export now. The worker writes one every day. See [Backups](../admin/backups.md#calendars-and-contacts). | `--user` (email or user id; default: every enabled user and the group calendars), `--groups` (only the group calendars) |
| `mailr calendars restore` | Put a calendar export back into the built-in calendars. Where the server holds other content under the same UID, the server's version is kept and a conflict is reported. See [Restore calendars and contacts](../admin/backups.md#restore-calendars-and-contacts). | `--from` (an export folder), `--user`, `--groups`, `--overwrite` (put back the export's version), `--dry-run` (print counts only) |
| `mailr calendars resync` | Forget the sync state of calendar accounts and sync them in full, for example after the Stalwart volume was restored. | `--all` (every user's built-in calendar), `--user`, `--include-external` (the users' own CalDAV accounts too), `--dry-run` (count only) |
| `mailr calendars migrate` | Copy a user's connected CalDAV/CardDAV account into their Mailr calendar. The account stays connected and unchanged. Without `--from-connector`, the accounts are listed. See [Calendars](../admin/calendars.md). | `--user` (required), `--from-connector` (the account's id), `--collections` (names or slugs; default: every calendar, task list and address book the user owns there), `--dry-run`, `--overwrite`, `--make-default` (make the copies the default calendar and task list), `--hide-copied` (hide the account's copied calendars in Mailr) |
| `mailr sent-backfill` | Copy the past sent mail of an account that is kept in sync (`mirror`) to the provider's Sent folder: the messages sent from Mailr through that account (from one of its sending addresses) that are missing there. Messages already in that folder are found by their `Message-ID` and skipped, so a stopped run is simply run again; nothing is deleted. Without `--account`, the user's IMAP accounts are listed with their **Save sent mail at the provider** setting. The exit code is 1 when a message could not be copied. See [Sent mail at the provider](../admin/store-and-mirror.md#sent-mail-at-the-provider). | `--user` (required, email or user id), `--account` (the account's id), `--since` (`YYYY-MM-DD`; default: all), `--dry-run` (read the Sent folder and print the counts only) |
| `mailr app-publish` | Publish a release APK of an Android app, read from standard input. It is then downloaded to phones from this server (`/get-app`). The version is read from the APK; an APK of another app, an unsigned APK or a lower version is refused. The version it replaces is kept as the previous one. See [Phone apps](../admin/phone-apps.md). | `--app` (`mailr` or `notes`), `--allow-older` (accept a lower `versionCode`), `--rollback` (instead, make the previous version current again) |
| `mailr read-page` | Read one web page through the [page readers](../admin/web-search.md#page-readers), as Ambrose reads it, to check a reader. It prints the reader, the route (through the egress proxy, directly from this network, or by the reader service), the HTTP status, the title and the length, or the error. The page's text and keys are never printed. Exit code 1 when the page could not be read. | `url` (`https://` only), `--reader` (one reader by its name, or `Built-in`; default: the whole chain) |
| `python -m mailr.mcp` | Run the MCP server for agents, over stdio (the default) or HTTP. | `--http`, `--host` (default `127.0.0.1`), `--port` (default `8765`), `--url` (default `$MAILR_URL`) |

## Repairs after an upgrade from an early build

These commands are not needed on a new install. They repair data that early builds of Mailr stored in an older form. Each one can be stopped and run again, and a second run changes nothing. Without a user, each one runs for every user.

| Command | Description | Arguments |
|---|---|---|
| `mailr embed-model-backfill` | Record the current embedding model on message vectors stored before the model of each one was recorded. Ambrose compares a message's vector only with vectors of the same model, so until this command has run, Ambrose finds related mail through people and keywords only. Those vectors are taken to come from the embedding model now in use on the instance. A message whose search passages carry another model's vectors is skipped: it is in the middle of a re-embedding, and its model is recorded when the re-embedding finishes. The run takes about two minutes for 90,000 messages, while Mailr keeps running. | `user` (optional, email or user id), `--batch` (default `1000`, messages per transaction) |
| `mailr reparse-attached-emails` | Read stored mail again for emails attached to it. Early builds stored the text of an attached email, such as a **Forward as attachment** or a digest, as the text of the message that carries it. That text then showed in the list preview, summaries and Ambrose's view, and in your writing samples. With `--dry-run`, only the messages that would change are counted, per user and per mail account; no mail text is printed. Without it, those messages are parsed again. Their own text, preview and attachment list are stored, and each attached email becomes an attachment of its own. The search index and the passages are rebuilt, and the conversation summaries are made again when next asked for. Memories that Ambrose only suggested from your own messages among them are withdrawn; memories you accepted stay. Marks derived by triage from the old text are removed too: the deadline, **Needs reply** and **Waiting on**, the action chip and an open suggestion to move an event. A mark you turned down stays turned down. Every message with a stored copy is read, at most `--rate` a second, and only those with an attached email are parsed. A stopped run resumes after the last finished batch. | `user` (optional, email or user id), `--batch` (default `200`, messages per transaction), `--rate` (default `50`, messages read per second), `--dry-run` (count only), `--restart` (start from the first message) |
| `mailr share-origin-backfill` | Repair notes shared to yourself from the phone (**Send to self**) that one early build stored without their mark. Those notes no longer counted as your own mail, so `from:me` filters, such as a notes-to-self filter, skipped them. Each recent message from your own address is read. A phone share is marked as shared from another app, filed as sent by you again, and run through your filters as on arrival. | `user` (optional, email or user id), `--since` (ISO date or time; the default covers every affected build), `--batch` (default `200`), `--dry-run` (count and list them without changing anything) |
| `mailr address-clean-backfill` | Remove direction controls (such as right-to-left marks), invisible characters and control characters from stored addresses and names. With these characters, one address can read as another. Current builds store addresses without them. The command cleans the senders and recipients of stored mail, the senders in the conversation list, and the organisers and guests in Mailr's calendar copy. Your calendar server's own copy is not changed. Only counts are printed. | `user` (optional, email or user id), `--batch` (default `500`, rows per transaction), `--dry-run` (count only) |
| `mailr auth-results-backfill` | Read the stored authentication results of recent mail again with the current parser. A message from one of your own addresses that counted as yours only because of a forged result is no longer counted as yours. It is left out of your writing samples, `from:me` filters and Ambrose's view of your own words. Each such message is listed. | `user` (optional, email or user id), `--days` (default `365`), `--batch` (default `200`), `--dry-run` (count and list them without changing anything) |
| `mailr calendar-fix-zones` | Repair calendar items stored with a Windows time zone name, such as "Central Standard Time". Outlook and Exchange invitations name zones this way. In early builds, an event added from such an invitation kept that name on your calendar server, and Calendar in the browser could not open it. Each affected event or task is written again with the standard name (for example `America/Chicago`) and a matching time zone definition, so phones and other calendar apps can read it too. Everything else stays as it is, and no invitation update is sent. Items in read-only calendars and subscriptions are counted and left alone. Only counts are printed. | `user` (optional, email or user id), `--dry-run` (count only) |

## Examples

```sh
# Check the configuration after you change .env
docker compose run --rm cli mailr check-config

# A second user, who will sign in with your OIDC provider
docker compose exec app mailr create-user sam@northwind.example --name "Sam Okafor" --no-password

# A read-and-draft token for an agent that runs on a hosted model
docker compose exec app mailr create-token alex@northwind.example --name claude --scopes read,draft

# Publish the Notes app's release APK (phones download it from /get-app)
docker compose exec -T app mailr app-publish --app notes < notes-release.apk

# Re-embed everything after you change the embedding model
docker compose exec app mailr reembed

# Identify the language of the last 90 days of mail, for every user
docker compose exec app mailr langid-backfill --days 90

# Rebuild the previews of the last 90 days of mail, after a look at what would change
docker compose exec app mailr resnippet alex@northwind.example --days 90 --dry-run
docker compose exec app mailr resnippet alex@northwind.example --days 90

# Check that the browser reads pages (after enabling the stack's browser)
docker compose run --rm cli mailr read-page https://www.reddit.com/r/selfhosted/ --reader "Browser (server)"

# Keep attached emails out of the text of the mail that carries them, after counting what would change
docker compose exec app mailr reparse-attached-emails --dry-run
docker compose exec app mailr reparse-attached-emails

# Copy the sent mail of an account that is kept in sync of this year to the provider's Sent folder, after a count
docker compose exec app mailr sent-backfill --user alex@northwind.example
docker compose exec app mailr sent-backfill --user alex@northwind.example --account 3 --since 2026-01-01 --dry-run
docker compose exec app mailr sent-backfill --user alex@northwind.example --account 3 --since 2026-01-01

# Repair phone shares stored by an early build, after a look at what would change
docker compose exec app mailr share-origin-backfill alex@northwind.example --dry-run
docker compose exec app mailr share-origin-backfill alex@northwind.example
```

See [Tokens and scopes](../agents/tokens.md) for the scopes a token can carry and
[MCP](../agents/mcp.md) for running the MCP server.
