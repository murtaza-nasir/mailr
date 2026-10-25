# Stalwart for mailr

These files were tested against `stalwartlabs/stalwart:v0.16.23` on 2026-09-27. The server booted from an empty volume, provisioned itself and served JMAP, IMAP 143 and IMAP 993.

| File | Purpose |
|---|---|
| `config.json` | The only on-disk config in v0.16. It describes the data store only (RocksDB at `/var/lib/stalwart/data`, with 64 MiB cache and buffer). Mount it read-only. |
| `config.postgres.json` | Alternative: PostgreSQL store. The password comes from the `STALWART_PG_PASSWORD` env var. This was verified against Postgres 17. |
| `provision.json` | A single JMAP management request. It creates the domain, sets the hostname, keeps only the http:8080, imap:143 and imaps:993 listeners, logs to stdout, allows plaintext IMAP auth, turns off the web UI download and ASN/Geo downloads, whitelists the docker network from auto-ban, and creates the `mailr-svc` Admin account. |
| `provision-groupware.json` | A second request, because one request takes at most 16 calls: calendar and contacts settings. Scheduling and email alarms off, `maxResults` 50000, assisted discovery on, default names "Calendar" and "Contacts", `maxAttendees` 100, `maxShares` 50, no directory queries. The backend applies the same settings to existing stores at every start (`backend/mailr/core/store_groupware.py`). |
| `entrypoint.sh` | Wrapper entrypoint (uses only `sh` and `curl`, which are both in the image). On a blank store it boots Stalwart, applies `provision.json` and `provision-groupware.json` as `STALWART_RECOVERY_ADMIN`, and restarts the server so the listener changes take effect. On later boots it does nothing extra. |
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
- **Image tag.** Pin the image tag. v0.16.24 fixes `Email/import` with a `#creationId` blobId.
