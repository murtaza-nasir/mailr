# Backups

Back up three things. With all three, everything can be restored.

| What | Where | How |
|---|---|---|
| **Postgres** | the `postgres-data` volume | the nightly [database dump](#database-dumps), written by the worker |
| **The mail store** | the `stalwart-data` volume | a nightly snapshot of the volume (below); it holds mail, the Mailr calendars and their contacts |
| **Mailr's data** | the `mailr-data` volume (`/data`) | copy or snapshot it: uploads, caches and the exports |

The daily exports of mail, of calendars and contacts, and of notes and tables (below) are a second, plain-file copy. They are no substitute for the three backups.

Mail is stored unencrypted in the volumes, the exports and the dumps. [Data at rest](security.md#data-at-rest) lists every copy and how to protect them.

Keep a copy of `.env` too. Without `MAILR_SECRET_KEY` the stored account passwords cannot be read, and users must enter them again.

## Database dumps

Notes and their history, tables, task details, settings, layouts, Ambrose's memory, chats and approvals are stored only in Postgres. A snapshot of the host holds the database only as it was on disk at that moment. A database dump is a second, consistent copy that can be loaded into any Postgres 17 server.

A dump is written by the worker every night, at `MAILR_DB_DUMP_HOUR` (UTC, 9 by default). When the newest good dump is more than 26 hours old, for example after the server was off at that hour, a dump is written at once, and again every 6 hours while it fails.

### Where the dumps are

| Backup target | Folder |
|---|---|
| set (`MAILR_BACKUP_TARGET`) | `postgres/` on the target, next to the mail export |
| not set | `/data/backups/postgres` in the data volume |

`MAILR_DB_DUMP_DIR` names another folder. With a target set but not mounted (no marker file), no dump is written and the failure is reported: a dump on the local disk under an empty mount point is no backup.

Each dump is two files:

```text
postgres/
  mailr-20261014T090012Z-nightly.dump        pg_dump, custom format, compressed
  mailr-20261014T090012Z-nightly.dump.json   the manifest
```

The manifest holds the time, the schema revision, the size, the SHA-256 checksum of the dump and the number of rows in every table. The counts and the dump come from one database snapshot, so a restore of the dump has exactly these counts. A dump is written under a temporary name and renamed when it is complete and read back with `pg_restore --list`, so a file with the final name is never a partial dump. A dump is refused when the folder has less than one and a half times the size of the last dump free.

### How long dumps stay

| Kind | Kept |
|---|---|
| `nightly` | the newest `MAILR_DB_DUMP_KEEP` (14) |
| `pre-migrate`, `pre-deploy` | as many days as `MAILR_DB_DUMP_KEEP`; the newest three always |
| `manual` (`mailr db-dump`) | until removed by hand |

The production database of a household with about 100,000 messages makes a dump of roughly a gigabyte, mostly search vectors, which compress poorly. Fourteen nightly dumps then need about 14 GB on the target.

### Monitoring

The `db_dump` check of [Monitoring](monitoring.md) fails when the last dump failed or the newest good dump is more than 26 hours old. The backup heartbeat (`MAILR_BACKUP_HEARTBEAT_URL`) is sent only while it passes, and a completed dump sends it too.

### A dump by hand

```sh
docker compose run --rm cli mailr db-dump              # into the dump folder, kept until removed
docker compose run --rm cli mailr db-dump --list       # the dumps and the last nightly result
```

### Before migrations

A dump (`pre-migrate`) is written when a new version starts and the database schema is behind, before any migration runs. It goes to the same folder as the nightly dumps: the backup target, `MAILR_DB_DUMP_DIR`, or `/data/backups/postgres` in the data volume. When that dump fails, nothing is migrated and the app does not start; the reason is in its log (`docker compose logs app`). After the target is fixed, the next start writes the dump. `MAILR_DB_DUMP_BEFORE_MIGRATE=false` lets a start go on without it.

A dump is refused when it would leave less than `MAILR_DISK_FAIL_PERCENT` (5 %) of the disk free. In the data volume, the start then goes on without the dump and a warning is logged, because a full disk would also stop Postgres and the mail store. On the backup target or in `MAILR_DB_DUMP_DIR`, the start stops as for any other failed dump.

That dump keeps the app down until it is written (about two minutes for a database of 750 MB). To avoid the wait, take the dump while the running version still serves, just before deploying a version with migrations:

```sh
docker compose run --rm cli mailr db-dump --reason pre-deploy
```

A dump at the current schema revision that is at most `MAILR_DB_DUMP_FRESH_MINUTES` old (default 120) stands in for the dump at start, so the new version migrates at once. Without such a dump, the start writes its own as before.

The dump adds about a minute to the first start of a new version on a database of 2 GB. The app's healthcheck allows five minutes for the start.

## The daily export

A Maildir copy of every user's mail is written once a day, under `/data/exports/user-<id>/`. Only new messages are added in each run. The export is a plain copy in a standard format readable by any mail tool, independent of Mailr and of the mail server. Each file starts with an `X-Mailr-Labels:` header listing its labels and an `X-Keywords:` header readable by Thunderbird and Dovecot, followed by the original message.

The exports run every 24 hours, counted from when the worker started. A new user's first export, and the first export after their first account, note, table or calendar, is queued within about 5 minutes, so a new server does not wait a day for it; a first export that fails is tried again at most once an hour. To write the exports at once:

```sh
docker compose exec app mailr export                                  # everyone
docker compose exec app mailr export --user alex@example.com --mail   # one user, mail only
```

The time of each run that catches up is recorded in `.last_run` in the export folder (and on the backup target). It is read in the `export` and `backup_target` checks of [Monitoring](monitoring.md).

For accounts where Mailr keeps the mail, once deletion at the provider is turned on, mail is deleted at the provider only after a message is in this export and older than the delay, unless `MAILR_STORE_BACKUP_CHECK=false`. See [Account modes](store-and-mirror.md).

A message deleted with **Delete forever** (also through **Empty Trash** and **Empty Spam**) leaves the export: its file is removed, here and on the backup target, and its line leaves the `.exported` list. When the backup target is not mounted at that moment, its copy is removed at the next export run after the share is back. Removing a user removes their export folders.

A message removed in the mail store by other means, such as another mail app or the store's own Trash expiry, keeps its export file. The same happens when the store loses mail or is restored from an older snapshot, and the export is then the only copy left (see [The mail store on the same server](#the-mail-store-on-the-same-server)). The daily cleanup removes the text of such messages from the database, and holds back when most of a user's mail looks deleted (see [Data at rest](security.md#data-at-rest)). The database dumps are not edited; the nightly ones age out after `MAILR_DB_DUMP_KEEP` dumps, and the others as listed above. To run the cleanup at once, or to remove the export files of all deleted mail when you know the mail store is complete:

```sh
docker compose exec app mailr purge-deleted --dry-run             # count what would go
docker compose exec app mailr purge-deleted                       # remove the text of deleted mail
docker compose exec app mailr purge-deleted --exports --dry-run   # also count the export files
docker compose exec app mailr purge-deleted --exports             # also remove the export files
```

When the database is restored from an older dump, the next export run notices that its position (`.cursor`) is ahead of the restored rows. The run then checks every message against the export again, writes only what is missing, and logs a warning.

### Turn the mail export off

The mail export is on by default. To turn it off, set in `.env`:

```ini
MAILR_MAIL_EXPORT=false
```

Then run `docker compose up -d`.

Turn it off only when the whole server, or its Docker volumes, is backed up another way (a VM backup or volume snapshots, as in [Snapshot the Stalwart volume](#snapshot-the-stalwart-volume)). The mail is then kept at the provider and in the mail server's volume, and the export would be a third full copy on the same disk.

Where deletion at the provider is on, mail is deleted at the provider, and by default only mail that is in this export. To use that with the export off, set `MAILR_STORE_BACKUP_CHECK=false` as well:

```ini
MAILR_MAIL_EXPORT=false
MAILR_STORE_BACKUP_CHECK=false
```

Mail older than the delay is then deleted at the provider once it is in Mailr's mail store, with no backup check, so your own backups of the mail store's volume are the only copy outside it. See [Deletion without a backup check](store-and-mirror.md#deletion-without-a-backup-check).

While it is off:

- No mail export runs, daily or for a new user. `mailr export` skips the mail; `mailr export --mail` ends with an error.
- The calendar and contacts export, the notes and tables export and the nightly database dumps run as before, and are copied to the backup target when one is set.
- "Mailr keeps the mail" (`store`) is unavailable, unless `MAILR_STORE_BACKUP_CHECK=false`. A new account cannot be added in that mode, and an account cannot be switched to it. In the web app, the mode is shown as unavailable with the reason. An account added through the API without a mode is added as `mirror` (Keep in sync).
- For an account where Mailr keeps the mail, nothing is deleted at the provider, and deletion at the provider cannot be turned on, unless `MAILR_STORE_BACKUP_CHECK=false`. The account shows "Deletion at the provider paused: the mail export is off" under **Settings → Mail → Mail accounts** and in **Settings → Admin → Diagnostics**. Deletion resumes when the export is on again.
- The `export` check of [Monitoring](monitoring.md) reports `off` and passes.

The mail exported before stays in `/data/exports/user-<id>/` and on the backup target. While the export is off, it is not updated and does not count for deletion at the provider. Remove the folders by hand when they are no longer needed. When the export is turned on again, the next run adds every message that arrived in the meantime.

## Notes, lists and tables

Notes, lists and tables are stored only in Postgres, so once a day each user's notes and tables are also exported as plain files, next to the mail export. Each user's export holds what they own: their own notes and tables, and the group lists and tables they made. A note shared with several people is in its owner's export only. Items in Trash are left out.

| File | Contents |
|---|---|
| `notes/<title>.md` | one Markdown file per note or list. A list is written as `- [ ]` and `- [x]` lines, with `## Section` headings. These lines are read back to the same items. |
| `notes.json` | every note in full: items, sections, labels, links and reminders |
| `tables/<name>.csv` | one CSV file per table, with every row and column |
| `tables.xlsx` | every table in one workbook, one sheet each, with typed cells (numbers, dates, currency) |
| `tables.json` | every table in full: columns, views and every row with its raw values |
| `manifest.json` | the counts and the list of files |

```text
/data/exports/notes/
  user-<id>/
    2026-10-07T031500Z/
      manifest.json
      notes/Groceries.md
      notes.json
      tables/Trip costs.csv
      tables.xlsx
      tables.json
    LATEST
    .last_run
```

Snapshots, `LATEST`, `.last_run`, the copy to `notes/` on the [backup target](#the-backup-target) and how long snapshots stay work as for [calendars](#calendars-and-contacts) (the days for both are set with `MAILR_CALENDAR_EXPORT_KEEP_DAYS`). Local-only notes and tables are exported too. The export stays on the server and on the backup target.

The `notes_export` check of [Monitoring](monitoring.md) fails when a user who owns notes or tables has no completed export in the last 26 hours, or, with a mounted target, no copy there. The backup heartbeat is also held until it passes.

## Calendars and contacts

Once a day each user's calendars, task lists and address books are exported as plain files, next to the mail export. The export has:

- the user's Mailr calendar, with every calendar and task list in it;
- the calendar accounts the user connected (Nextcloud, Fastmail, iCloud, any CalDAV server);
- the address books of all those accounts;
- every [group calendar](calendars.md#group-calendars), once for the whole server. A group calendar is not repeated in each member's export.

Accounts that users marked as local-only are exported too, as their mail is. The export stays on the server and on the backup target.

The files are standard formats readable by any calendar or contacts app:

- one `.ics` file per calendar or task list, with every event, task and journal entry and the time zones they use;
- one `.vcf` file per address book;
- a `manifest.json` with each calendar's name, colour, kind (Mailr calendar, connected account or group calendar), the group and what its members may do, and the name of each item on the server. It is used in `mailr calendars restore` to make the calendars again.

The export is written from Mailr's copy of the calendars in Postgres, so it does not depend on the mail server being up.

### Export location

```text
/data/exports/calendars/
  user-<id>/
    2026-10-07T031500Z/       one snapshot
      manifest.json
      calendars/01-Calendar.ics
      calendars/02-Tasks.ics
      contacts/01-Contacts.vcf
    LATEST                    the name of the newest snapshot
    .last_run                 when the last run finished
  groups/                     the group calendars, the same way
```

Each snapshot is complete or absent: it is written to a temporary folder first and renamed when every file is on disk. When a run's content equals the newest snapshot, no new one is written, and only the run is recorded in `.last_run`.

With a [backup target](#the-backup-target), the newest snapshot, `LATEST` and `.last_run` are copied to `calendars/` on the target. Nothing is copied to a target without its marker file; the snapshots are copied once it is mounted again.

### Snapshot retention

The newest snapshot of each day stays for 30 days, on the server and on the target. Set `MAILR_CALENDAR_EXPORT_KEEP_DAYS` for another number of days. The newest snapshot always stays, however old.

### Monitoring

The `calendar_export` check of [Monitoring](monitoring.md) fails when a user's calendar export, or the group calendars' export, is more than 26 hours old. With a mounted target, the copy there counts too. The backup heartbeat (`MAILR_BACKUP_HEARTBEAT_URL`) is a single heartbeat, sent only while the mail, calendar and notes exports and the database dump checks all pass.

To write the export at once, for example before maintenance:

```sh
docker compose exec app mailr calendars export                  # every user and the group calendars
docker compose exec app mailr calendars export --user alex@northwind.example
```

## Snapshot the Stalwart volume

The `stalwart-data` volume holds the mail and the Mailr calendars and contacts. Back it up every night, in one of two ways.

**A filesystem or VM snapshot.** On ZFS, btrfs or LVM, or with a hypervisor backup of the whole VM (Proxmox Backup Server, for example), snapshot the volume while Stalwart runs. Such a snapshot is safe to restore: Stalwart's database is built to survive a power cut, and restoring a snapshot is the same as recovering from one. If your host is already backed up this way, check that the volume's folder is included and keep the job as it is.

**A short stop and a copy.** Stalwart is stopped for a few seconds. Mail sent in that time is retried by the sending servers, so none is lost. For example, as a nightly job on the host:

```sh
#!/bin/sh
# /usr/local/bin/mailr-stalwart-snapshot, run by cron at 02:30:
#   30 2 * * * /usr/local/bin/mailr-stalwart-snapshot
set -e
umask 077                   # the copies hold everyone's mail: readable by root only
out=/mnt/mailr-backup/stalwart
mkdir -p "$out" && chmod 700 "$out"
cd /home/you/mailr/deploy   # the deploy folder of your clone
docker compose stop stalwart
docker run --rm -v mailr_stalwart-data:/data:ro -v "$out":/out alpine \
  sh -c 'umask 077 && tar czf /out/stalwart-$(date +%F).tar.gz -C /data .'
docker compose start stalwart
find "$out" -name 'stalwart-*.tar.gz' -mtime +14 -delete
```

The volume's name is `mailr_stalwart-data` with the bundled compose file; it is listed by `docker volume ls`. Run the snapshot at another time than the daily export.

## The backup target

Keep a second copy of the export off the host before you turn on deletion at the provider. Mount a share on the host, such as a NAS share over NFS, then set in `.env`:

```ini
MAILR_BACKUP_DIR=/mnt/mailr-backup   # the host path, mounted at /backup in the app and worker
MAILR_BACKUP_TARGET=/backup
```

Then create the marker file `.mailr-backup-target` at the top of the share itself, while it is mounted (on the host: `touch /mnt/mailr-backup/.mailr-backup-target`). This file and the target folder are never created automatically. Without the marker the target counts as not mounted: nothing is copied there, no message counts as backed up, upstream deletion is held, and the `backup_target` health check fails. With the marker, an unmounted share (an empty mount point on the local disk) never passes for a backup.

Run `docker compose up -d`. With a target set:

- every new export file is also copied to the target;
- a message counts as backed up only when its copy exists on the target;
- files exported before you set the target are copied on the next export run;
- the sample for the restore check, which is run when deletion at the provider is turned on, is read from the target.

!!! tip
    Give the share its own snapshots, so a mistake on the Mailr host cannot remove older copies.

## The restore check

When a user turns on deletion at the provider for an account, a random sample of exported files is read back, and each one is compared byte for byte with the mail store. If any file is missing or differs, deletion stays off.

## The restore drill

A dump is tested by loading it:

```sh
docker compose run --rm cli mailr db-restore --check
```

The newest dump that this server wrote is checked against its checksum, loaded into a scratch database (`mailr_restore_check`) next to the live one, and the row count of every table is compared with the manifest.

The checksum of every dump is also kept in the database when the dump is written, because anyone who can write to the backup target could put a dump there with a manifest that matches it. The drill loads only dumps whose checksum is on that record. A newer file without a record is listed as `NOT CHECKED` and is not loaded. A dump that differs from its recorded checksum is refused, by the drill and by a restore by hand. The drill loads the dump as a role without superuser rights (`mailr_restore`, created on first use), so the dump's contents cannot run programs on the database server. The scratch database is dropped again. The command ends with "every table has the row count of the dump", or lists each table that differs and exits with 1. A database of 2 GB takes a few minutes and needs as much free space in the Postgres volume while it runs. Run the drill once a month, and after any change to the backup setup.

## Restore

Pick the procedure for what you restore. Each one is written for the bundled compose file, run in the deploy folder of your clone.

| You restore | Section |
|---|---|
| Postgres from a dump | [The database on the same server](#the-database-on-the-same-server) |
| the `stalwart-data` volume from a snapshot | [The mail store on the same server](#the-mail-store-on-the-same-server) |
| both, for example after a failed disk | [The database and the mail store](#the-database-and-the-mail-store) |
| everything, on another machine | [The whole server on a new host](#the-whole-server-on-a-new-host) |

### The database on the same server

A dump is never loaded over the live database. It is loaded into a new database, checked, and then renamed into place while the app and the worker are stopped:

1. Load the dump into a new database and compare the counts:

    ```sh
    docker compose run --rm cli mailr db-restore /backup/postgres/mailr-20261014T090012Z-nightly.dump --into mailr_restored
    ```

    A dump that this server wrote is checked against the checksum recorded for it. A dump from another server, or one older than the record, is loaded as it is: check where it came from first. The path is where your dumps are: `/backup/postgres/…` with a backup target, `/data/backups/postgres/…` without one (see [Where the dumps are](#where-the-dumps-are)). `docker compose run --rm cli mailr db-dump --list` lists their file names, and they are in that folder.

2. Stop the app and the worker: `docker compose stop app worker`.
3. Swap the databases:

    ```sh
    docker compose exec postgres psql -U mailr -d postgres \
      -c 'ALTER DATABASE mailr RENAME TO mailr_old' \
      -c 'ALTER DATABASE mailr_restored RENAME TO mailr'
    ```

4. Make the worker compare every mailbox with the mail store at its start: `docker compose run --rm cli mailr store resync`.
5. Start them again: `docker compose up -d`. Migrations newer than the dump are applied at start.
6. When everything works, remove the old database: `docker compose exec postgres dropdb -U mailr mailr_old`.

What happens at the start:

- Mail that arrived after the dump is still in the mail store and in the daily export. It is added to the index again, with the mail account it came through. Mail of a local-only account stays local-only.
- The mail export notices that the restored database is older than the export, and checks every message against the export again. Messages already exported are not written twice.
- Messages that the dump holds as queued or scheduled but that went out after the dump are not sent again: each message's Message-ID is noted in `/data/outbox/sent` when it goes out, and such a message is marked sent. A scheduled message that was made after the dump is lost with the rest of the database changes.

Notes, settings and other database-only changes made after the dump are lost.

### The mail store on the same server

Use this when the `stalwart-data` volume is restored from a snapshot, as in [Snapshot the Stalwart volume](#snapshot-the-stalwart-volume), while Postgres stays as it is. Mail that arrived after the snapshot is not in the restored volume. It is still in the daily export, and at the provider unless the provider deleted it.

1. Stop the app and the worker, so that nothing is written to the mail store and the index is not compared with it too early: `docker compose stop app worker`.
2. With calendars or contacts in the Mailr calendars, write a fresh calendar export from Mailr's copy: `docker compose run --rm cli mailr calendars export`.
3. Restore the volume. With a snapshot from the script above:

    ```sh
    docker compose stop stalwart
    docker run --rm -v mailr_stalwart-data:/data -v /mnt/mailr-backup/stalwart:/in:ro alpine \
      sh -c 'find /data -mindepth 1 -delete && tar xzf /in/stalwart-2026-10-14.tar.gz -C /data'
    docker compose start stalwart
    ```

    With a filesystem or VM snapshot, roll the volume back with your snapshot tool while `stalwart` is stopped.

4. Forget the store sync state, and scan every mail account again from the start:

    ```sh
    docker compose run --rm cli mailr store resync --accounts
    ```

5. With calendars, put back what changed since the snapshot, as in steps 4 and 5 of [After restoring the Stalwart volume](#after-restoring-the-stalwart-volume) (with `docker compose run --rm cli` in place of `docker compose exec app`).
6. Start the app and the worker: `docker compose up -d`.

    At its first store sync, the worker compares each user's mailbox with the index:

    - Messages the snapshot lacks are marked deleted. Their export files stay, here and on the backup target.
    - A message whose store id the restored server gave to other mail is marked deleted too, and the other mail is added to the index.
    - Mail that is in the snapshot but was deleted after it is in the store again, and comes back in the mailbox. Delete it again.

    With `--accounts`, the mail accounts are scanned again: mail the snapshot lacks that the provider still has comes back. Messages already in Mailr are skipped.

7. Wait for the first store sync (a few minutes; `docker compose logs worker | grep 'store reconcile'` shows one line per user), then import the rest from the daily export:

    ```sh
    docker compose run --rm cli mailr store import-export
    ```

    Each user gets a file import account named "Mail export" with their whole export. Messages already in Mailr are skipped, so only the missing ones are imported. For a user whose mailbox has not been compared yet, nothing is imported and the command says so; run it again later. Messages that were deleted in another mail app before the restore come back this way too, because their export files stay. Mail that arrived after the last export run and that the provider no longer has cannot be restored.

### The database and the mail store

Restore both from the same night when you can. Then:

1. Stop the app and the worker: `docker compose stop app worker`.
2. With calendars, write a fresh calendar export from the current database before you swap it: `docker compose run --rm cli mailr calendars export`.
3. Load the dump and swap the databases, as in steps 1 and 3 of [The database on the same server](#the-database-on-the-same-server).
4. Restore the volume, as in step 3 of [The mail store on the same server](#the-mail-store-on-the-same-server).
5. Continue with steps 4 to 7 of [The mail store on the same server](#the-mail-store-on-the-same-server).

What [The database on the same server](#the-database-on-the-same-server) says about the export and the outbox applies here too. With the data volume kept, a message that went out after the dump is not sent again.

### The whole server on a new host

1. Install Mailr as in [Install with Docker Compose](../getting-started/install.md), with the same `.env`. Do not start it yet.
2. Restore the `stalwart-data` and `mailr-data` volumes from your copies.
3. Start Postgres alone and load the dump into the empty database:

    ```sh
    docker compose up -d postgres
    docker compose exec -T postgres pg_restore -U mailr -d mailr --no-owner --exit-on-error < mailr-20261014T090012Z-nightly.dump
    ```

4. Start the mail store and forget the store sync state: `docker compose up -d stalwart`, then `docker compose run --rm cli mailr store resync --accounts`.
5. Start everything with `docker compose up -d`.
6. When the Stalwart snapshot is older than the dump, or older than the last export run, import the rest as in step 7 of [The mail store on the same server](#the-mail-store-on-the-same-server).

To recover one user's mail without the rest, run `docker compose run --rm cli mailr store import-export --user alex@northwind.example`. Messages already present are skipped.

## Restore calendars and contacts

An export is put back into the Mailr calendars with `mailr calendars restore`:

```sh
docker compose exec app mailr calendars restore --from /data/exports/calendars --dry-run
docker compose exec app mailr calendars restore --from /data/exports/calendars
```

For `--from`, give the whole export folder, one user's folder (`/data/exports/calendars/user-7`), the group calendars' folder, one snapshot, or the backup target (`/backup`). For a folder above the snapshots, the newest snapshot of each user is used. With `--user alex@northwind.example` you restore one user, with `--groups` only the group calendars.

Restore behaviour:

- Calendars, task lists and address books that no longer exist are made again, with their names and colours. Group calendars are made again with the same access and the editors who are still members.
- Each item keeps its UID. An item already on the server with the same content is skipped. An item already there with other content is a conflict: it is listed and left as it is. Add `--overwrite` to put the export's version back instead.
- New items are created only where nothing exists yet, so nothing is created twice when you run the command twice.
- Afterwards every affected user's calendar is resynced in full.
- Calendars of accounts that users connected themselves are not restored: their data is stored on that server. Their `.ics` and `.vcf` files are in the export to import there by hand.

With `--dry-run`, the server is read and the counts are printed. Nothing is changed.

The user must have a Mailr calendar on the server ([Built-in calendars](calendars.md) turned on for them).

### After restoring the Stalwart volume

A restored volume holds the calendars as they were at the snapshot. Mailr's copy in Postgres is newer. The mail in the volume needs steps of its own: [The mail store on the same server](#the-mail-store-on-the-same-server) has both in one order. For the calendars alone, follow these steps, in this order:

1. Stop the worker, so that Mailr's copy is not synced to the old state: `docker compose stop worker`.
2. Write a fresh export from Mailr's copy: `docker compose exec app mailr calendars export`.
3. Restore the volume (stop `stalwart`, put the copy back, start `stalwart`).
4. Put back what changed since the snapshot. Check the counts first:

    ```sh
    docker compose exec app mailr calendars restore --from /data/exports/calendars --overwrite --dry-run
    docker compose exec app mailr calendars restore --from /data/exports/calendars --overwrite
    ```

5. Resync every Mailr calendar in full: `docker compose exec app mailr calendars resync --all`.
6. Start the worker: `docker compose start worker`.

Step 5 is needed even when you skip the others. After a restore, the sync tokens stored in Mailr are still accepted by the server, although they come from its future, and no changes are reported. With `resync`, the stored tokens are cleared, so every item is compared at the next sync. With `--user`, one user is resynced, with `--include-external` also the accounts users connected themselves, and with `--dry-run` items are only counted.

Items deleted after the snapshot come back in step 4, because a deleted item cannot be told apart from a lost one in a restore.

!!! warning "Test a restore"
    Restore to a spare host once before you rely on deletion at the provider.
