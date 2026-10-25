# The mail server

Every message is stored in a bundled [Stalwart](https://stalw.art) mail server, the `stalwart` service. Mailr's connection to it is JMAP, on the stack's internal network. Users reach it from mail apps over IMAP. It has no SMTP listener: mail comes in through Mailr's connectors and goes out through each account's provider.

## First start

On a blank volume, Stalwart is set up on the first start and restarted once. The internal mail domain (`MAILR_STALWART_DOMAIN`, `mailr.internal` by default, never routed) is created, only the listeners required by Mailr are kept, and Mailr's service account is created. Watch for the setup with:

```sh
docker compose logs stalwart | grep mailr-init
```

Two passwords from `.env` are involved:

| Variable | Description |
|---|---|
| `MAILR_SVC_PASSWORD` | The password of Mailr's service account. It is passed to Mailr in the compose file as `MAILR_STALWART_ADMIN_PASSWORD`, with `MAILR_STALWART_ADMIN_USER` and `MAILR_STALWART_URL` set to the service. |
| `STALWART_ADMIN_PASSWORD` | The recovery admin password. It works even when the directory is broken. Keep it for emergencies. |

Use only letters, digits and `.`, `_` and `-` in both. `MAILR_TRUSTED_NET` (`10.253.54.250/31`, the fixed addresses of the app and the worker) is the trusted network for Mailr's own connections to Stalwart. Only these two addresses are exempt from Stalwart's automatic ban after failed sign-ins; mail apps that connect through the published IMAP port are not. Stalwart's admin port stays internal.

Each Mailr user has a mailbox in Stalwart. These mailboxes are created, renamed and removed automatically.

## Stalwart settings checked on every start

On every start, a few Stalwart settings are checked and corrected. They are applied to a new store at the first start, and to an existing store at the next start after an upgrade.

- No meeting invitations and no reminder emails are sent from Stalwart. Invitations go out through Mailr, with the usual approval step and undo window. Reminders are sent by Mailr.
- Calendar and contact listings go up to 50,000 items. A higher limit set by an admin is kept.
- Users cannot look up other accounts on the server. Nobody can list the accounts, names or addresses on the instance from their mail or calendar app.
- New calendars and address books are named "Calendar" and "Contacts", unless the names were changed.

A line starting with `Stalwart groupware settings updated` is logged when something was changed.

## IMAP for mail apps

| Variable | Description |
|---|---|
| `MAILR_IMAP_HOSTNAME` | The server name users type into their mail apps, such as `mail.example.com`. It must resolve to the Mailr host. |
| `MAILR_IMAPS_PORT` | The published port for IMAP over TLS (993). |
| `MAILR_IMAPS_BIND` | The host address port 993 is published on (`0.0.0.0`, every address of the host). |
| `MAILR_IMAP_PORT` | The published port for IMAP with STARTTLS (143). TLS is required for sign-in on both ports. |
| `MAILR_IMAP_BIND` | The host address port 143 is published on (`127.0.0.1`, this host only). |

Port 993 is reachable from every network the host is on. When no mail app connects from another machine, set `MAILR_IMAPS_BIND=127.0.0.1`. When port 993 is taken on the host, set another `MAILR_IMAPS_PORT` and give that port to users.

Users find their IMAP details and create app passwords under **Settings → Mail → Mail accounts → Phone & desktop apps**.

## A real TLS certificate for IMAP

At first Stalwart has a self-signed certificate, which is not accepted in most mail apps. Install a real certificate for `MAILR_IMAP_HOSTNAME` with the admin TLS route:

```sh
curl -X PUT https://mail.example.com/api/v1/admin/tls \
  -H "Authorization: Bearer $TLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --rawfile c fullchain.pem --rawfile k privkey.pem '{certificate: $c, private_key: $k}')"
```

- `TLS_TOKEN` is a shell variable that holds an API token. The token must belong to an admin and carry the `tls` scope: `mailr create-token alex@northwind.example --name cert-push --scopes tls`.
- The certificate and key are checked for a match, handed to Stalwart, and used at once.
- Sending the same certificate again changes nothing, so the push can run daily from cron after the certificate is renewed.
- The installed certificate is returned by `GET /api/v1/admin/tls`.

A wildcard certificate from a DNS challenge works well: no open ports and no public DNS record for the IMAP name are required.

## Spam checks and the resolver

In Stalwart's spam filter, senders and links are checked against DNS blocklists such as Spamhaus. Queries that arrive at those services through public or ISP resolvers are refused, so the stack has its own recursive resolver, the `resolver` service, on a small private network:

| Variable | Description |
|---|---|
| `MAILR_DNS_SUBNET` | The resolver's network (`10.253.53.0/29`). |
| `MAILR_RESOLVER_IP` | The resolver's fixed address (`10.253.53.2`). |
| `MAILR_STALWART_RESOLVER_IP` | Stalwart's resolver. It is set to `MAILR_RESOLVER_IP` in the compose file. |

Change the subnet only if it clashes with a network on the host, and keep the resolver address inside it. Stalwart's resolver setting is checked on every start, and a line is logged when a restart of Stalwart is required to apply it.

Each user tunes spam handling under **Settings → Mail → Spam & Screener**. See [Triage signals and importance](../user-guide/triage.md).

## Upgrading Stalwart

The Stalwart image is pinned. Read Stalwart's release notes before you move to a new minor version, since its storage format can change between them. See [Upgrades](upgrades.md).
