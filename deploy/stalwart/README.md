# Stalwart for mailr

These files were tested against `stalwartlabs/stalwart:v0.16.23` on 2026-09-27, against `v0.16.27` on 2026-11-01 and against `v1.0.1` on 2026-11-02. The server booted from an empty volume, provisioned itself and served JMAP, IMAP 143 and IMAP 993. A 0.16.23 volume starts on 0.16.27 unchanged. A 1.0 server does not open a 0.16 volume: the store must be copied with `stalwart-migrate` first (below).

| File | Purpose |
|---|---|
| `config.json` | The only on-disk config in v0.16. It describes the data store only (RocksDB at `/var/lib/stalwart/data`, with 64 MiB cache and buffer). Mount it read-only. |
| `config.postgres.json` | Alternative: PostgreSQL store. The password comes from the `STALWART_PG_PASSWORD` env var. This was verified against Postgres 17. |
| `provision.json`, `provision.v1.json` | The 0.16 and the 1.x version of a single JMAP management request (1.x renamed `tlsImplicit` to `implicitTls` and the application's `enabled` to `enable`; nothing else differs). It creates the domain, sets the hostname, keeps only the http:8080, imap:143 and imaps:993 listeners, logs to stdout, allows plaintext IMAP auth, turns off the web UI download and ASN/Geo downloads, whitelists the docker network from auto-ban, and creates the `mailr-svc` Admin account. |
| `provision-groupware.json`, `provision-groupware.v1.json` | A second request, because one request takes at most 16 calls: calendar and contacts settings. Scheduling and email alarms off, `maxResults` 50000, assisted discovery on, default names "Calendar" and "Contacts", `maxAttendees` 100, `maxShares` 50, no directory queries. The mail search index is on for 0.16 (its date filters and sorts need it) and off for 1.x (Mailr searches its own index). The backend applies the same settings to existing stores at every start (`backend/mailr/core/store_groupware.py`). |
| `entrypoint.sh` | Wrapper entrypoint (uses only `sh` and `curl`, which are both in the image). On a blank store it boots Stalwart, applies the two provisioning requests for the server's version (`stalwart --version`; the `*.v1.json` files on 1.x) as `STALWART_RECOVERY_ADMIN`, and restarts the server so the listener changes take effect. On later boots it does nothing extra. |
| `Dockerfile` | The image with these files. The base is the build argument `STALWART_IMAGE`, pinned by digest; the default is `v0.16.27`. |
| `docker-compose.example.yml` | The service definition to merge into the main compose file. |
| `stalwart_example.py` | A `requests`-only client: create, rotate and delete accounts, import a message, and query threads plus full-text search. |

## First boot

```sh
export STALWART_ADMIN_PASSWORD=... MAILR_SVC_PASSWORD=...   # use only [A-Za-z0-9._-] (substituted with sed)
docker compose up -d stalwart
docker logs stalwart | grep mailr-init    # "provisioned; restarting to apply listeners"
```

There is no wizard. Mounting a `config.json` skips bootstrap mode entirely. Bootstrap mode only happens when `config.json` is missing.

The backend talks to `http://stalwart:8080` as `mailr-svc@<domain>` using HTTP Basic auth. That account has the Admin role, so it can:

- use any user's JMAP `accountId` directly;
- manage accounts through the `x:Account` methods;
- bypass the per-user concurrency and upload limits.

`STALWART_RECOVERY_ADMIN` stays set because it is the break-glass credential. It authenticates even when the directory is broken.

## Before production

- **IMAP TLS certificate.** 993 and STARTTLS on 143 currently use an auto-generated `rcgen self signed cert`. Add an `x:Certificate` object that reads PEM files from a mount, `{"@type":"File","filePath":"/certs/fullchain.pem"}`. Then either set `x:SystemSettings.defaultCertificateId` or run the `ReloadTlsCertificates` action. This step is not yet tested.
- **Plaintext IMAP.** Port 143 allows plaintext auth (`x:Imap.allowPlainTextAuth=true`). Drop that setting if phones should be forced to 993 or STARTTLS.
- **Image tag.** The Dockerfile pins `v0.16.27` by digest. `deploy/tools/check-image-updates.sh` reports newer images; read the release notes before moving.

## Stalwart 1.x

Mailr works with 0.16.27 and 1.0.1. An image on 1.0.1:

```sh
docker build --build-arg STALWART_IMAGE=stalwartlabs/stalwart:v1.0.1@sha256:b9da4df8b4efc06bc926dd6e990bdeb0ad7b9261a7c3893d74b0461a00bb389b deploy/stalwart
```

A new installation can start on it directly. An existing 0.16 store is copied into a new, empty volume with `stalwartlabs/stalwart-migrate:1.0.0` (https://stalw.art/docs/migration/upgrade-0-16/): `prepare` with the 0.16 `config.json`, one or more `run` passes while 0.16 serves, then stop the stack, `run --final --verify-full`, and start the 1.x image on the new volume. Keep the old volume until the new store has been checked: the tool opens it read-only, so 0.16.27 starts on it again unchanged. Phones fetch their calendars and contacts once after the move. For development, `deploy/dev/stalwart-1.0.yml` runs a 1.0.1 store on port 28080.
