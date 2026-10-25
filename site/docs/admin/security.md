# Security and permissions

## Secrets and encryption

- **`MAILR_SECRET_KEY`** is the instance key. Each user's stored secrets (mail account passwords, OAuth tokens, model API keys, Speakr tokens) are encrypted with a key of their own, and those keys are wrapped with the instance key. The instance key is never in the repository or the image. Generate it with `mailr gen-secret` and keep a copy with the backups. Mailr cannot be started with a key shorter than 32 random bytes. A stored account password can be decrypted only for the account it belongs to. Instance-wide secrets (the API keys of model endpoints, the egress proxy address and the web push key) are encrypted with the instance key, and each one can be decrypted only for the setting it was stored for.
- **`MAILR_SESSION_SECRET`** is a separate key for signing short-lived sign-in data, such as the single sign-on state. Without it, the instance key is used.
- Session and API tokens are stored only as hashes.

## Data at rest

Mailr does not encrypt mail at rest. Only the secrets listed above (account passwords, OAuth tokens, model API keys, Speakr tokens) are encrypted with `MAILR_SECRET_KEY`. Mail, the search index and the exports are stored as plain data on the server's disk.

| Where | What | Volume or path |
|---|---|---|
| The mail server's store | the full original messages with their attachments, read by Mailr and by IMAP apps | Stalwart's RocksDB store (`deploy/stalwart/config.json`) in the `stalwart-data` volume |
| Postgres | headers, a plain-text copy of each body without quoted history (at most 60,000 characters), previews, the text of attached emails, attachment names and sizes, the search index and embeddings | the `postgres-data` volume |
| The [daily mail export](backups.md#the-daily-export) | a full copy of every message, unless `MAILR_MAIL_EXPORT=false` | `/data/exports` in the `mailr-data` volume |
| The nightly [database dumps](backups.md#database-dumps) | everything Postgres holds | `/data/backups/postgres`, or `postgres/` on the backup target |
| The [backup target](backups.md#the-backup-target), when one is set | the copies of the exports and the dumps | the share mounted at `MAILR_BACKUP_DIR` |

The `mailr-data` volume also holds caches of summaries and remote images, and files attached to drafts.

Anyone with root on the host, or with access to the Docker volumes, the dumps, the backup target or the `.env` file (it holds the database and mail server passwords), can read this mail. Other users of Mailr cannot: every query is scoped to the signed-in user (below).

To protect it:

- Use full-disk encryption on the host, such as LUKS, and an encrypted backup target.
- When the whole VM or server is backed up, for example with Proxmox Backup Server, encrypt those backups on the client and keep the key off the backup server. The backed-up copies of everything above are then encrypted at once.
- When no account is set to `store` (Mailr keeps the mail) and the server is backed up as a whole, the mail export can be turned off (see [Turn the mail export off](backups.md#turn-the-mail-export-off)). That removes one full copy.

With the mail export on, the full mail is on disk twice, in the mail server's store and in the export, plus the index in Postgres.

**Delete forever** removes a message from the mail server's store at once. In the background, its text is removed from Postgres (headers, body, previews, attached emails, attachment names, the search index, embeddings and what the assistant derived from it), and its file is removed from the daily mail export and from the copy on the backup target. Only a small record stays in Postgres: the Message-ID and reply references, the place at the provider, a content fingerprint, dates, size and flags (see [Delete forever](../user-guide/inbox.md#delete-forever)). The database dumps are not edited: the nightly dumps keep the message until they age out (`MAILR_DB_DUMP_KEEP`), the newest three dumps taken before migrations or deploys keep it until newer ones replace them, and manual dumps keep it until you remove them. Backups you make outside Mailr, such as VM snapshots, keep it until they expire. When a user is removed with all their mail, their mail, notes and calendar export folders are removed too, here and on the backup target (on a target that cannot be reached at the time, as soon as it can).

A message removed in the mail store by other means (another mail app, the store's Trash cleanup, a store that lost its data or was restored from an older snapshot) is only marked as deleted. A daily cleanup then removes its text from Postgres the same way. Its export file stays, here and on the backup target: after a restore of the mail store, the export is the only copy left of the mail the snapshot lacks. The cleanup holds back when the deleted messages are more than a tenth of a user's messages (and more than 1,000), because a mail store that lost its data looks the same, and the text is then all that is left in Postgres. A hold is logged and shown in **Settings → Admin → Diagnostics** under **Backup**. When the mail store is known to be healthy, `mailr purge-deleted` removes the text, and `mailr purge-deleted --exports` also removes the export files of all deleted mail (see [Command line](../reference/cli.md)).

## Isolation between users

Every query is scoped to the signed-in user in the data layer, not only in the interface. One user can never read or change another user's mail through any route, tool or job. Sorting models and Ambrose learn from each user's own mail only.

## Sign-in

- Single sign-on is OIDC with state, nonce and PKCE, and the token's signature, issuer and audience are checked.
- The phone apps' sign-in is bound to the server's address, so an ID token obtained for another server is refused (see [Server-bound sign-in](sign-in.md#the-sign-in-is-bound-to-your-server)).
- The phone link of an invite carries only a phone form of the invite. It works only with a sign-in to the invited address, never to set a password, so another app on the phone that reads the link cannot claim the invite.
- Before you expose Mailr to the internet, turn on single sign-on and set `MAILR_LOCAL_LOGIN=false`. Leave `MAILR_ALLOW_SIGNUP` off.
- Each user can see and sign out their sessions under **Settings → You → Password & devices**.
- After a password change or an admin's password reset, every API token of the account is revoked, as well as its other sessions. The IMAP password and every app password of the account stop working too, including ones made outside Mailr (a user may keep them on a routine change). If the mail server refuses to delete one, the user is told that the apps were not signed out. Push notification targets of the other devices are removed too (an admin's reset and disabling an account remove all of them), and open live-update streams of the ended sessions stop within a minute. **Sign out everywhere** does the same without a password change, for single sign-on users. All three also end the sign-in cookie of browsers the account used before; with that cookie, sign-in is possible while strangers fill the account's count.
- Recovery also takes back the standing channels someone with a session could have set up to keep acting for the user, for everything set up since the oldest ended session signed in (at least the last 14 days): "Follow my forwarded notes" on Allow goes back to Ask me, Allow rules and "Allow always" grants for Ambrose ask first again, and addresses that speak for the user and forwarding addresses confirmed in that time need a new code (nothing is forwarded to them meanwhile). Older choices stay. The user sees the list under **Settings → You → Password & devices** ("Review after signing out"), also after an admin's reset or `mailr set-password`. See [Password & devices](../user-guide/settings.md#review-after-signing-out).
- Disabling an account always gives the mail server a new random secret for it, so its IMAP password and phone app passwords stop working whatever state the account was in; enabling it restores the password its own devices know. Which accounts are locked this way is kept by the server, never in the user's settings.
- The stack's page-reading browser can reach only the [browser gate](web-search.md#the-browser-gate), through which only public `https://` connections pass.
- When you sign out, the browser is told to clear its cache for Mailr (attachments, images and pictures), so nothing stays behind on a shared computer.

See [Sign-in and OIDC](sign-in.md).

## Agents: scopes, rules and approvals

A person using the web app or the phone app with their own session is never gated. Everything an agent does, with an API token or through the built-in assistant, is subject to one permission engine:

- **Scopes** on each token are the coarse limits: `read`, `label`, `draft`, `send`, `delete`, `rules`, `calendar`, `memory`, `recordings`, `notes`, `profile`, `web`, `admin` and `tls`.
- **Account limits**: a token can be restricted to some mail accounts.
- **Rules** are `allow`, `ask` or `deny` per kind of action, with constraints such as "replies only" or "known recipients only" and rate limits.
- **Approvals**: an action set to ask is held in the approval queue until the user approves or denies it, on the web or from a phone notification. A request expires after `MAILR_APPROVAL_TTL_HOURS` (24).
- **The send hold**: an email an agent sends is held for `MAILR_AGENT_SEND_HOLD_SECONDS` (60) before it goes out, and can be cancelled.
- **First contact**: the first message ever sent to an address is always held for approval, whatever the rules.
- **Audit log**: every call made with an agent token is recorded, and the user can review it.

See [Tokens and scopes](../agents/tokens.md) and [Approvals for agents](../agents/approvals.md).

## Local-only mail

The content of a mail account marked local-only is never sent to a hosted model, and only a local-only token can read it. When a request or a background job has read local-only mail, notes, tables, memories, recordings or calendar items, its later model and service calls are treated as local-only too. A model endpoint marked local is used only while every address of its host is private or loopback; this is checked again on every connection. Error reports raised while local-only content was read keep only the type of the error ([Error reports](errors.md)). Notifications for it have only the text "New message". An approval card that shows local-only mail anywhere in it (a review of a list, a filter's match against existing mail, a cleanup plan, a brief, a send) is pushed to phones with no text of the mail, and a question to Ambrose about such a card is answered by a local model. Admins can require local-only for whole domains under **Settings → Admin → Data & privacy → Data policies**. See [Models](models.md#local-only-mail).

## Mail is untrusted

Every message is treated as untrusted content:

- Message bodies are rendered in a sandboxed frame with no scripts and a strict content policy.
- Instructions found in mail are not followed by Ambrose. In API and tool responses, mail content is marked as untrusted.
- Images in Ambrose's answers are shown as links, so Ambrose cannot be made to load a URL from an email.
- Remote images go through the [image proxy](image-proxy.md).
- Links from mail that are opened on the server (one-click unsubscribe, brand logos) must be public HTTPS addresses. When an egress proxy is set for the [image proxy](image-proxy.md), these requests go through it too, and their host names are not looked up on the Mailr server. With `MAILR_REQUIRE_EGRESS=true`, they never go out directly ([Require the VPN](image-proxy.md#require-the-vpn-fail-closed)).

## Attachments in the browser

Images, audio, video, plain text and PDFs are opened in the browser. Every other file type is downloaded. Images, media and text are opened in a locked-down page where no scripts run and nothing else is loaded. A PDF is opened in the browser's own PDF viewer, on the same address as the mailbox, with a policy under which nothing is loaded from the internet and the PDF cannot be shown inside another site. (Browsers cannot show a PDF in the fully locked-down page, so PDFs get this slightly looser policy.)

### Optional: a separate address for attachments

In some mail setups, attachments are served from a second address, for example `files.example.com` beside `mail.example.com`. A file opened there is fully separated from the mailbox. A flaw in a browser's PDF viewer, exploited with a crafted PDF, would then give no access to the mail or the session and no way to act as the user, because the two addresses are unrelated sites to the browser.

A separate address would be an extra layer for the rare case of a browser flaw, not a fix for a known problem. The protections above apply either way.

!!! note "Not yet available"
    A separate attachment address is not supported yet: attachments are always served from `MAILR_BASE_URL`. Until it is, users who prefer not to open PDFs in the browser can download them and open them in another program.

## Uploads

- An upload is checked for a signed-in session (with its CSRF token) or an API token before any of it is read. A request from a signed-out client is refused at once, so it cannot fill the disk. Phone app uploads need a signed-in admin at that point too.
- Mailbox imports, recordings and phone apps are written straight to their place on the disk, not first to a temporary copy. The person's import space, the daily import limit (`MAILR_IMPORT_BYTES_PER_DAY`), the size limit and the free disk (`MAILR_DISK_FAIL_PERCENT`) are checked while the file arrives, and an upload that goes over one stops there and leaves no file behind.
- At most 4 uploads of one person and 24 in all are read at the same time. A further upload waits up to a minute for its turn, then is refused with "too many uploads at once". Smaller uploads (attachments, pictures, dictation) are held in `/tmp` and on the data disk only while they are read, so these limits also bound that space.

## Outbound connections

Users can enter server addresses in Mailr: IMAP and SMTP hosts, CalDAV and CardDAV servers, push endpoints, Speakr, model endpoints. Every such address is checked before a connection is made, and connections go only to the checked address.

- Always refused: loopback, link-local (including cloud metadata addresses), multicast and reserved addresses, Mailr's own services (`MAILR_INTERNAL_HOSTS`) and the host the containers run on.
- Private network addresses (including shared `100.64.0.0/10` addresses) are allowed for what admin users set up: their accounts, calendars, push targets and integrations. With `MAILR_ALLOW_PRIVATE_HOSTS=all`, they are allowed for every user, and with `none`, they are refused for everyone.
- Hosts or networks that every user may reach are listed in `MAILR_OUTBOUND_ALLOW`, comma separated, for example `127.0.0.1` for a mail bridge running beside Mailr. For model endpoints marked local, only the IP addresses and networks in this list count as local; a host name in it counts as local only when it resolves to private or loopback addresses.
- Model, speech, web search and page reader connections never use a proxy from the environment (`HTTP_PROXY`, `HTTPS_PROXY`), and they connect only to the address that was checked.
- A calendar or address-book password is sent only to the server it was entered for, or to an HTTPS server in the same domain. It is never sent over plain HTTP to an internet address, and a redirect from HTTPS to HTTP is refused.
- Answers from these servers are read up to a size limit.

For the Android app's Agenda widget, the weather is requested from [Open-Meteo](https://open-meteo.com) by the phone itself (`api.open-meteo.com`, and `geocoding-api.open-meteo.com` for a city search). Nothing about it passes through the Mailr server, so no setting or firewall rule on the server is needed, and a person's location never reaches the server. Only the location rounded to about 10 km, or the city name, is sent. Weather lookups are turned off with **Weather** in the widget's settings.

## Public ids

With `MAILR_PUBLIC_IDS=true`, conversations, messages and approvals get opaque ids in URLs and in the API, so nobody can learn from the ids how much mail the server holds or how many actions agents asked for. While `MAILR_PUBLIC_IDS_LEGACY=true`, the plain internal ids from old links and older apps are still accepted. Turn `MAILR_PUBLIC_IDS_LEGACY` off once every client is on the new ids.

Approval links and notifications sent before the switch have the internal id. With `MAILR_PUBLIC_IDS_LEGACY=false`, such an id is still accepted for an approval that is pending, so a notification's **Allow once** and **Deny** keep working until the approval expires (`MAILR_APPROVAL_TTL_HOURS`, 24 hours by default). A decided or expired approval is then found only by its public id. An old approval link opened in a signed-in browser is redirected to the public form. Ownership is checked on every lookup.

## Containers and network exposure

- The app and the worker run with every Linux capability dropped, `no-new-privileges` and a read-only root filesystem. Only `/data`, `/backup` and a small `/tmp` are writable for them, and their files are not readable by other accounts on the host.
- The app and the worker get only their own settings. The other containers' passwords in `.env` (Postgres, the Stalwart recovery admin, the VPN key) are blanked for them.
- Addresses are banned from Stalwart after repeated failed sign-ins. Only the app and the worker (`MAILR_TRUSTED_NET`) are exempt.
- Only the web port and the IMAP ports are published, the web port and port 143 on `127.0.0.1` by default. With their profiles on, the DAV gateway and the error tracker publish one port each, on `127.0.0.1` by default. Postgres, the resolver, the VPN container and the mail server's admin port stay on the internal network.
- The stack has no SMTP listener. Mail is never sent from Mailr directly to the internet.
- Only the addresses in `MAILR_TRUSTED_PROXIES` may set forwarded headers, so a client cannot fake its address. See [Reverse proxy](reverse-proxy.md).
