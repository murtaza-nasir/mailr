# API routes

This page lists every route of the REST API, by area. Paths start with `/api/v1`. Send a token as
`Authorization: Bearer <token>`; see [REST API](../agents/rest-api.md) for sign-in, errors and
conventions, and [Tokens and scopes](../agents/tokens.md) for what each scope allows.

The same routes, with their parameters and response shapes, are in the interactive reference at
`/api/v1/docs` (the OpenAPI document is `/api/v1/openapi.json`), for people signed in to the web
app who may make API tokens; see [Interactive reference](../agents/rest-api.md#interactive-reference).

## Sign-in, account and sessions

Scope: a signed-in session.

| Route | Description |
|---|---|
| `GET /api/v1/auth/config` | Which sign-in methods are offered on the server. |
| `POST /api/v1/auth/login` | Sign in with email and password; starts a cookie session. |
| `POST /api/v1/auth/logout` | Sign out of this session. |
| `GET /api/v1/auth/session` | Like `GET /me`, but answers `{"authenticated": false}` with 200 when you are signed out. |
| `GET /api/v1/auth/oidc/start` | Start sign-in at your OIDC provider; `next` is the page to open afterwards. |
| `GET /api/v1/auth/native-config` | The sign-in settings for the Android apps (`app=notes` for the Notes app's redirect address). No sign-in needed. |
| `POST /api/v1/auth/oidc/native` | Sign in from an Android app with an ID token from your OIDC provider; starts a device session. |
| `POST /api/v1/auth/native/logout` | Sign out of an Android app's device session. |
| `POST /api/v1/auth/handover` | A one-time hand-over token, so another Mailr app on the same phone can sign in without a second sign-in. |
| `POST /api/v1/auth/handover/redeem` | Exchange a hand-over token for a device session of the Notes app. |
| `GET /api/v1/auth/oidc/callback` | The return address after sign-in at your OIDC provider. Register `${MAILR_BASE_URL}/api/v1/auth/oidc/callback` as the redirect URI. |
| `POST /api/v1/auth/signup` | Create an account (only when `MAILR_ALLOW_SIGNUP` is on). |
| `GET /api/v1/app/releases` | The Android apps offered on this server, with version, size and SHA-256. No sign-in needed. |
| `GET /api/v1/app/{app}.apk` | Download the APK of `mailr` or `notes`. No sign-in needed. |
| `GET /api/v1/me` | Your account: name, email, role and settings, and `setup` (whether a mail account is connected, the setup guide's status, whether a calendar is there, and the setup address). |
| `PATCH /api/v1/me` | Change your name. |
| `GET /api/v1/me/imap` | The IMAP server name, ports and user name for other mail apps. |
| `POST /api/v1/me/imap/app-password` | Create a new IMAP app password (shown once). |
| `GET /api/v1/me/dav` | The server address and user name for calendar and contacts apps, and your DAV app passwords. |
| `POST /api/v1/me/dav/app-passwords` | Create a DAV-only app password (shown once), with an iOS/macOS profile that holds it. |
| `DELETE /api/v1/me/dav/app-passwords/{pid}` | Delete a DAV app password. |
| `POST /api/v1/me/password` | Change your password. Other sessions end and API tokens are revoked; with `sign_out_mail_apps` (default true), the IMAP password is replaced and the DAV app passwords are deleted. |
| `POST /api/v1/me/sign-out-everywhere` | End every other session, revoke every API token, replace the IMAP password and delete the DAV app passwords. This session stays. |
| `GET /api/v1/me/sessions` | Your signed-in browsers and devices. |
| `DELETE /api/v1/me/sessions/{sid}` | Sign a browser or device out. |
| `DELETE /api/v1/me/recovery-review` | Dismiss "Review after signing out" (what a sign-out everywhere, password change or reset took back). |
| `PATCH /api/v1/me/settings` | Change settings (send only the keys you change). |

## Setup guide

Scope: a signed-in session (API tokens are refused with 403). See [Your first sign-in: the setup guide](../getting-started/your-first-sign-in.md).

| Route | Description |
|---|---|
| `GET /api/v1/onboarding` | The guide's progress (status, step, steps done or skipped), whether Ambrose can chat, and the phone links. A new account is given its prepared defaults here. |
| `PUT /api/v1/onboarding` | Move to a step, mark steps done or skipped, finish (`status: done`) or close (`dismissed`). |
| `POST /api/v1/onboarding/restart` | Open the guide again at the start. |
| `POST /api/v1/onboarding/chat` | The next turn of the quick chat (`{messages}` → `{reply, done}`); your access policy's chat model. Nothing is saved. |
| `POST /api/v1/onboarding/chat/summary` | What was learned from the chat, as a proposal to review. Nothing is saved. |
| `POST /api/v1/onboarding/about` | Save what you approved: About-you fields, notes to remember and your signature. |

## Mail accounts connected by an administrator

See [Users → Connect a member's mail account](../admin/users.md#connect-a-members-mail-account). A signed-in admin session only, never a token; for every change but the setting, `accounts.admin_connect` must be on.

| Route | Description |
|---|---|
| `GET /api/v1/admin/managed-accounts` | The setting (`enabled`) and every account an administrator connected (no passwords). |
| `PUT /api/v1/admin/managed-accounts` | Turn the setting on or off (`{enabled}`). |
| `GET /api/v1/admin/users/{uid}/accounts` | The accounts an administrator connected for this person, and the ones they disconnected. |
| `POST /api/v1/admin/users/{uid}/accounts/test` | Sign in to the servers as the person's account would. |
| `POST /api/v1/admin/users/{uid}/accounts` | Connect an IMAP/SMTP account for the person (`reconnect: true` for one they disconnected). |
| `DELETE /api/v1/admin/users/{uid}/accounts/{cid}` | Remove an account an administrator connected; its mail stays. |

## Invites

See [Users → Invite someone](../admin/users.md#invite-someone). An admin session is required for the admin routes; no sign-in is required for the other two.

| Route | Description |
|---|---|
| `POST /api/v1/admin/invites` | Make an invite (`{email, name?, days?, defaults?}`): the link and the phone link, shown once. |
| `GET /api/v1/admin/invites` | The invites, newest first, without their links. |
| `DELETE /api/v1/admin/invites/{iid}` | Withdraw an invite. |
| `GET /api/v1/invites/{token}` | The data for the invite page: the address, the name, whether it is still open, how to sign in. |
| `POST /api/v1/invites/{token}/accept` | Use the invite: signed in as its address, or (with password sign-in) with `{password}` for a new or unused account. |

## Imports

Scope: a signed-in session (API tokens are refused with 403). See [Notes](../user-guide/notes.md#import-from-google-keep), [Calendar](../user-guide/calendar-sharing.md#import-and-calendar-links) and [People](../user-guide/people.md#import-contacts).

| Route | Description |
|---|---|
| `POST /api/v1/imports/preview` | Read uploaded files (`{kind: keep \| ics \| vcf, blobs, calendar?}`) and count what would come in. Nothing is written. |
| `POST /api/v1/imports` | Run the import; its counts and its id are returned. |
| `GET /api/v1/imports` | Your recent imports (`kind=` to filter). |
| `POST /api/v1/imports/{bid}/undo` | Take a whole import back. |

## Mail

Scope: `read`; `label`, `delete` or `send` for actions; `draft` for drafts.

| Route | Description |
|---|---|
| `GET /api/v1/attachments` | Attachments of the messages a search matches, newest first. |
| `GET /api/v1/bulk/{job_id}` | Progress of a bulk action on many conversations. |
| `GET /api/v1/cleanup/groups` | Mail grouped for a cleanup (`by`: sender, domain, list or category), in a scope (`where`, `older_than_days`, `uncategorised`, `bulk_only`, `include_*`). |
| `GET /api/v1/cleanup/preview` | Exactly what an action on one group changes: conversations, messages, 20 samples, what is left alone. |
| `POST /api/v1/cleanup/apply` | Archive, label, trash, mark read, set a category on, or keep one group (`expect`: the previewed count). Large groups are run as a bulk job. Signed-in users only. |
| `POST /api/v1/cleanup/rule` | A filter for a group's future mail. |
| `POST /api/v1/cleanup/suggest` | Ask your sorting model for categories of uncategorised groups, tonight or now. Suggestions only. |
| `GET /api/v1/cleanup/suggest` | The state of the last suggestion run. |
| `GET /api/v1/changes` | What changed since a state token, for offline caches. |
| `GET /api/v1/compose/reply` | Recipients, subject and quoted text for a reply, reply all or forward. |
| `GET /api/v1/conversations` | List conversations: a view, a label or a search (`q`), paged. |
| `POST /api/v1/conversations/actions` | Act on conversations: archive, delete, read, star, label, move, snooze, mute, category, show in tab, follow-up and more. |
| `POST /api/v1/conversations/merge` | Merge conversations into one. |
| `GET /api/v1/conversations/{cid}` | One conversation with its messages. |
| `POST /api/v1/conversations/{cid}/split` | Split messages out into a new conversation. |
| `POST /api/v1/drafts` | Save a new draft. |
| `PUT /api/v1/drafts/{mid}` | Update a draft. Send `base_version` (the version you read) to have changes saved meanwhile merged in; an overlapping change is answered with 409 (see [Live editing](../agents/rest-api.md#live-editing)). |
| `GET /api/v1/drafts/{mid}` | Read a draft, with its `version`. |
| `DELETE /api/v1/drafts/{mid}` | Discard a draft. |
| `GET /api/v1/live/{kind}/{item}` | The current version and editable content of a draft, event, task, memory or filter (`kind`: `draft`, `event`, `task`, `memory`, `rule`). |
| `POST /api/v1/live/typing` | Report typing from an open editor (signed-in apps only), so that an assistant write is held until the user pauses. |
| `GET /api/v1/identities/default` | The address you send from by default. |
| `GET /api/v1/labels` | Your labels with unread and total counts. |
| `POST /api/v1/labels` | Create a label (nest it with `/` in the name). |
| `POST /api/v1/labels/colours` | Recolour several labels at once, or apply a colour scheme. |
| `PATCH /api/v1/labels/{lid}` | Rename, recolour, nest or hide a label. |
| `DELETE /api/v1/labels/{lid}` | Delete a label (the mail stays). |
| `POST /api/v1/messages/actions` | Act on single messages instead of whole conversations. |
| `GET /api/v1/messages/{mid}` | One message: headers, people, attachments. |
| `GET /api/v1/messages/{mid}/attachments/{part}` | Download an attachment. `download=1` always downloads it; `thumb=1` returns a large image as a small copy (320 pixels on its long side, without metadata), as the Attachments view shows it. |
| `GET /api/v1/messages/{mid}/attachments/{part}/email` | An attached email as the reader shows it: its headers, its body as safe HTML (`images=allowed` to show remote images) and its files, numbered as parts of the outer message. |
| `GET /api/v1/messages/{mid}/body` | The message body as safe HTML. |
| `GET /api/v1/messages/{mid}/cid/{cid}` | An inline image of the message. |
| `GET /api/v1/messages/{mid}/images/{h}` | A remote image of the message, fetched through the image proxy. |
| `GET /api/v1/messages/{mid}/raw` | The original message source (.eml). |
| `POST /api/v1/messages/{mid}/unsubscribe` | Unsubscribe from a mailing list (one-click when the sender supports it). |
| `GET /api/v1/outbox` | Messages waiting to send: in the undo window, scheduled, or waiting for approval. `active=true`: what has not left yet (queued to go within a minute, sending, waiting for approval, not sent this week) and what left in the last two minutes, each with its `draft_id`. |
| `POST /api/v1/outbox/{oid}/approve` | Approve a held message. |
| `POST /api/v1/outbox/{oid}/cancel` | Cancel a message before it is sent (undo send). |
| `POST /api/v1/outbox/{oid}/reject` | Reject a held message. |
| `POST /api/v1/outbox/{oid}/send-now` | Send a scheduled or held message now. |
| `POST /api/v1/outbox/{oid}/retry` | Put a message that was not sent back in the queue, to go now. |
| `POST /api/v1/send` | Send a message, now or at `send_at`. |
| `GET /api/v1/subscriptions` | Your mailing lists and bulk senders, one row each (`sort`, `filter`, `q`), with volume, reading and the unsubscribe method. |
| `GET /api/v1/subscriptions/unsubscribe` | How unsubscribing from one (`key`) would work, before anything is sent. |
| `POST /api/v1/subscriptions/unsubscribe` | Unsubscribe from one (`{key}`): a one-click request or an unsubscribe mail. Signed-in users only; agents use the `unsubscribe` tool. |
| `GET /api/v1/subscriptions/preview` | What Archive all or Label would change for one subscription. |
| `POST /api/v1/subscriptions/actions` | `archive_all`, `label`, `auto_archive`, `remove_auto_archive`, `keep` or `unkeep` one subscription. Signed-in users only. |
| `POST /api/v1/uploads` | Upload a file to attach or an image to insert. |
| `GET /api/v1/uploads/{blob}` | An uploaded image. |

## Inbox layouts

Scope: `read`, `rules` to change.

| Route | Description |
|---|---|
| `GET /api/v1/layouts` | Your inbox layouts and the presets. |
| `PUT /api/v1/layouts` | Save your custom layouts. |
| `POST /api/v1/layouts/preview` | Count what a section you are editing would show. |
| `GET /api/v1/layouts/show-in-tab` | The tabs "Show in tab" offers for `conversation_ids`, and the tab they show in now. |
| `GET /api/v1/layouts/{layout_id}/counts` | Counts and indicators for every tab of a layout. |
| `GET /api/v1/inbox/count` | The number by Inbox: unread mail in the active layout's first tab, or in the whole Inbox (the `inbox_count` setting). |
| `GET /api/v1/counts` | Every number shown in the sidebar, in one answer: for each view (Inbox, Drafts, Spam, Trash, Snoozed, Scheduled, each label and the rest) its unread and total, counted as in that view's list, plus the Inbox number. The same numbers are sent on the live stream after each change. |

## Filters and forwarding

Scope: `rules`.

| Route | Description |
|---|---|
| `GET /api/v1/forwarding` | Your forwarding addresses. |
| `POST /api/v1/forwarding` | Add a forwarding address; a confirmation code is sent to it. |
| `POST /api/v1/forwarding/verify` | Confirm a forwarding address with its code. |
| `DELETE /api/v1/forwarding/{email}` | Remove a forwarding address. |
| `GET /api/v1/rules` | Your filters, in order. |
| `POST /api/v1/rules` | Create a filter from a search and its actions. |
| `POST /api/v1/rules/import-gmail` | Import filters exported from Gmail (XML). |
| `POST /api/v1/rules/preview` | Show the mail a filter's search matches. |
| `POST /api/v1/rules/reorder` | Change the order filters run in. |
| `PATCH /api/v1/rules/{rid}` | Change a filter. |
| `DELETE /api/v1/rules/{rid}` | Delete a filter. |
| `POST /api/v1/rules/{rid}/apply` | Apply a filter to mail you already have. |

## Identities

Scope: `read`, `rules` to change.

| Route | Description |
|---|---|
| `GET /api/v1/identities` | Your send-as addresses and signatures. |
| `POST /api/v1/identities` | Add a send-as address. |
| `PATCH /api/v1/identities/{iid}` | Change a send-as address: name, signature, default. |
| `DELETE /api/v1/identities/{iid}` | Remove a send-as address. |

## Templates

Scope: `read`, `draft` to change.

| Route | Description |
|---|---|
| `GET /api/v1/templates` | Your templates. |
| `POST /api/v1/templates` | Create a template. |
| `GET /api/v1/templates/{tid}` | One template. |
| `PATCH /api/v1/templates/{tid}` | Change a template. |
| `DELETE /api/v1/templates/{tid}` | Delete a template. |
| `POST /api/v1/templates/{tid}/expand` | A template with its variables filled for a draft's recipients. |

## Pictures

| Route | Description |
|---|---|
| `GET /api/v1/avatars/{email}` | A person's picture: from their address book card, an address of a user of this server (that address's picture, else that user's account picture), or looked up when `sender_pictures` is on. |
| `PUT /api/v1/identities/{iid}/avatar` | Set the picture of one of your addresses. |
| `GET /api/v1/identities/{iid}/avatar` | The picture of one of your addresses. |
| `DELETE /api/v1/identities/{iid}/avatar` | Remove the picture of one of your addresses. |
| `PUT /api/v1/people/{email}/avatar` | Set a person's picture. |
| `DELETE /api/v1/people/{email}/avatar` | Remove a person's picture. |
| `GET /api/v1/me/picture` | Your account picture (a session, or a token with `read` or `profile`). |
| `GET /api/v1/users/me/avatar` | The same picture at a stable address for integrations (a token with `profile` is enough). |
| `GET /api/v1/me/picture/info` | Whether you have an account picture, where it came from, and its version. |
| `PUT /api/v1/me/picture` | Upload your account picture (multipart `file`; signed-in user only). |
| `DELETE /api/v1/me/picture` | Remove your account picture. |
| `POST /api/v1/me/picture/use-sign-in` | Use the picture from your sign-in provider. |
| `GET /api/v1/pictures/local` | Hashed addresses of this server's users that have a picture, so that `/avatars/{email}` is requested only for those. |
| `GET /api/v1/me/picture/providers` | Per sending address: whether mail from it is sent through Google or Microsoft, and whether its domain belongs to a shared mailbox provider. |
| `POST /api/v1/me/picture/publish-check` | Ask Gravatar and Libravatar whether they have a picture for each of your addresses, or for those in `{"identity_ids": [...]}`. |
| `POST /api/v1/me/picture/bimi-check` | Check the BIMI requirements of one sending address's domain (`{"identity_id": …}`). Admins only. |

## People, groups and contacts

Scope: `read`.

| Route | Description |
|---|---|
| `GET /api/v1/address-books` | Your connected address books. |
| `POST /api/v1/address-books/sync` | Read the address books now (they are only read, never written). |
| `GET /api/v1/contacts/places` | Postal addresses from your contacts, for an event's location. |
| `GET /api/v1/groups` | Your contact groups, including suggestions. |
| `POST /api/v1/groups` | Create a contact group. |
| `POST /api/v1/groups/suggestions/refresh` | Look for new group suggestions now. |
| `GET /api/v1/groups/{gid}` | One group with its members. |
| `PATCH /api/v1/groups/{gid}` | Rename a group or change its members. |
| `DELETE /api/v1/groups/{gid}` | Delete a group. |
| `POST /api/v1/groups/{gid}/accept` | Accept a suggested group. |
| `POST /api/v1/groups/{gid}/dismiss` | Dismiss a suggested group. |
| `GET /api/v1/people` | Find people you correspond with, best match first. |
| `GET /api/v1/people/blocked` | Your blocked senders. |
| `GET /api/v1/people/{email}` | A person's profile: details, history, recent conversations. |
| `POST /api/v1/people/{email}/block` | Block a sender: their mail is moved to Spam. |
| `DELETE /api/v1/people/{email}/block` | Unblock a sender. |

## The Screener

Scope: `read` to list, `label` to decide. An API token's decision is always held for approval
(class `mail.screener`). Changing the settings and Screen again require a signed-in session (no token).

| Route | Description |
|---|---|
| `GET /api/v1/screener` | The first-time senders waiting in the Screener, newest first, with the latest message's subject and preview, the number of messages waiting, who a decision covers (an address or a domain) and the authentication hint. |
| `POST /api/v1/screener/{sid}/decide` | Allow, block (to Spam or Trash) or label a sender (`decision`, `block_to`, `label_id` or `feed`). The mail waiting is moved at once; the decision also applies to later mail. |
| `DELETE /api/v1/screener/{sid}` | Screen a sender again: undo a decision. |
| `GET /api/v1/screener/settings` | Whether the Screener is on, the accounts it covers, and the decisions you made. |
| `PUT /api/v1/screener/settings` | Turn the Screener on or off (when it is off, the waiting mail is moved to the Inbox), and choose its accounts. |
| `POST /api/v1/people/{email}/suggestions/{sid}/accept` | Accept a contact detail found in their mail (a title, a phone number). |
| `POST /api/v1/people/{email}/suggestions/{sid}/dismiss` | Dismiss a suggested contact detail. |

## Sorting, signals and search

Scope: `read`.

| Route | Description |
|---|---|
| `POST /api/v1/conversations/{cid}/deadline/dismiss` | Mark a detected deadline as not a deadline. |
| `GET /api/v1/conversations/{cid}/summary` | The short summary of a conversation (computed when missing). |
| `POST /api/v1/decisions/{did}/accept` | Accept a suggestion (a label or a category). |
| `POST /api/v1/decisions/{did}/reject` | Reject a suggestion. |
| `GET /api/v1/spam/domains` | Domains (or addresses) whose mail is moved to Spam because you reported their mail as spam. |
| `DELETE /api/v1/spam/domains/{key}` | Stop sending a learned domain's mail to Spam (scope `rules`). |
| `POST /api/v1/intel/backfill` | Sort older mail you already have. |
| `GET /api/v1/intel/importance/review` | Mail judged important or not in the last day, for your review. |
| `POST /api/v1/intel/reembed` | Build missing passages and re-embed mail with the current embedding model. |
| `POST /api/v1/intel/retriage` | Sort recent mail again (categories and signals). |
| `GET /api/v1/intel/status` | Sorting and embedding progress for your mail. |
| `POST /api/v1/intel/train` | Retrain your label and importance models now. |
| `POST /api/v1/intel/lab` | Run every sorting decision on some emails with the rules and each sorting model, side by side. Nothing is stored. Session only. |
| `GET /api/v1/intel/lab/endpoints` | The sorting models the lab may use for you. |
| `GET /api/v1/messages/{mid}/body-highlight` | The message body with a quoted passage highlighted (for citations). |
| `GET /api/v1/messages/{mid}/decisions` | Every sorting decision on a message, with its reason. |
| `GET /api/v1/messages/{mid}/explain` | Why each filter and standing order was or was not applied to a message. |
| `POST /api/v1/messages/{mid}/importance` | Your own verdict on a message: important or not. |
| `GET /api/v1/messages/{mid}/reply-suggestions` | Three short suggested replies to a personal email. |
| `POST /api/v1/messages/{mid}/translate` | The message's text in another language. |
| `GET /api/v1/passages/search` | The passages of mail that best answer a question. |
| `GET /api/v1/search/semantic` | Search by meaning and keywords, ranked per conversation. |
| `GET /api/v1/smart` | Counts of the smart lists (needs reply, waiting, deadlines). |
| `GET /api/v1/smart/{name}` | The conversations of one smart list. |
| `GET /api/v1/suggestions` | Pending label suggestions. |

## Mail accounts (connectors)

| Route | Description |
|---|---|
| `GET /api/v1/connectors` | Your mail accounts. |
| `POST /api/v1/connectors` | Add a mail account (or a Speakr account). |
| `POST /api/v1/connectors/discover` | Find the server settings for an address (presets and autodiscovery). |
| `GET /api/v1/connectors/presets` | The known providers and their settings. |
| `POST /api/v1/connectors/test` | Test account settings before you save them. |
| `POST /api/v1/connectors/uploads` | Upload an mbox or Maildir archive to import. |
| `GET /api/v1/connectors/{cid}` | One account with its sync state. An IMAP account in `mirror` mode (Keep in sync) has `sent_copies`: the `setting`, whether it is `active`, whether the provider saves its own copy (`provider_saves`), and the number of sent messages that could not be saved there (`failed`). |
| `PATCH /api/v1/connectors/{cid}` | Change an account: name, settings, mode, days before deletion at the provider, local-only, and for an IMAP account `save_sent` (`auto`, `on` or `off`; see [Sent mail at the provider](../admin/store-and-mirror.md#sent-mail-at-the-provider)). |
| `DELETE /api/v1/connectors/{cid}` | Remove an account from Mailr. Mail already delivered from it stays. |
| `POST /api/v1/connectors/{cid}/arm-deletion` | Turn on deletion at the provider for an account in `store` mode (Mailr keeps the mail), after its checks pass. |
| `POST /api/v1/connectors/{cid}/disarm-deletion` | Turn deletion at the provider off again. |
| `POST /api/v1/connectors/{cid}/pause` | Pause an account's sync. |
| `POST /api/v1/connectors/{cid}/resume` | Resume a paused account. |
| `POST /api/v1/connectors/{cid}/resync` | Scan the account again from scratch; mail already stored is skipped. |
| `POST /api/v1/connectors/{cid}/sync` | Sync an account now. |
| `POST /api/v1/connectors/{cid}/sent-copies/retry` | Try again to save the sent messages that could not be saved in the provider's Sent folder (IMAP accounts in `mirror` mode). Answers `{"queued": n}`. Session only. |
| `POST /api/v1/connectors/{cid}/sent-backfill` | Copy the account's past sent mail that is missing in the provider's Sent folder, or count it (`dry_run`, the default), optionally `since` a date. A run already going for the account is returned. Session only. |
| `GET /api/v1/connectors/{cid}/sent-backfill` | The newest count or copy for this account, or `{"status": "none"}`. |
| `GET /api/v1/connectors/{cid}/sent-backfill/{job_id}` | The progress of one count or copy: `done`, `total` and, when finished, `result` (`total`, `present`, `copied`, `failed`). |
| `POST /api/v1/connectors/{cid}/migration` | Copy a connected CalDAV account into your Mailr calendar: a preview (`dry_run`) or the copy. Session only. |
| `GET /api/v1/connectors/{cid}/migration` | The newest preview or copy for this account, or `{"status": "none"}`. |
| `GET /api/v1/connectors/{cid}/migration/{job_id}` | The progress of one preview or copy. |
| `POST /api/v1/connectors/{cid}/migration/cutover` | After a copy: make the copies your defaults, hide the account's copied calendars, or both. Nothing changes on the account's server. |

## Calendar

Scope: `read`, `calendar` to change.

| Route | Description |
|---|---|
| `GET /api/v1/calendar/accounts` | Your CalDAV accounts. |
| `PATCH /api/v1/calendar/accounts/{cid}` | Change a CalDAV account. |
| `DELETE /api/v1/calendar/accounts/{cid}` | Remove a CalDAV account from Mailr (its data stays on the server). |
| `POST /api/v1/calendar/accounts/{cid}/check` | Test a CalDAV account's connection. |
| `GET /api/v1/calendar/agenda` | Events and tasks for a range, for the side panel and agendas. |
| `GET /api/v1/calendar/calendars` | Your calendars and task lists. |
| `POST /api/v1/calendar/collections` | Create a calendar or a task list. |
| `POST /api/v1/calendar/collections/reorder` | Change the order of calendars or task lists. |
| `PATCH /api/v1/calendar/collections/{calendar_id}` | Rename, recolour, show or hide a calendar or list; set its default reminders. |
| `DELETE /api/v1/calendar/collections/{calendar_id}` | Delete a calendar or list (confirmed with its name). |
| `GET /api/v1/calendar/collections/{calendar_id}/export` | Download a calendar or list as one .ics file. |
| `GET /api/v1/calendar/collections/{calendar_id}/shares` | Who a calendar is shared with, at which level, and who else you can add. Signed-in only. |
| `PUT /api/v1/calendar/collections/{calendar_id}/shares/{user_id}` | Share a calendar with a person, or change their level (`view`, `edit`, `share`). Signed-in only. |
| `DELETE /api/v1/calendar/collections/{calendar_id}/shares/{user_id}` | Stop sharing a calendar with a person. Signed-in only. |
| `GET /api/v1/calendar/share-notices` | Calendars people shared with you since you last dismissed the notice. |
| `DELETE /api/v1/calendar/share-notices` | Dismiss those notices. |
| `POST /api/v1/calendar/connect` | Connect a CalDAV account. |
| `GET /api/v1/calendar/defaults` | Your default calendar and default task list. |
| `PUT /api/v1/calendar/defaults` | Set your default calendar or task list. |
| `GET /api/v1/calendar/delta` | Everything changed since a cursor, for offline caches. |
| `POST /api/v1/calendar/events` | Create an event (guests get invitations). |
| `GET /api/v1/calendar/events/search` | Events in a range, optionally matching words. |
| `GET /api/v1/calendar/events/{uid}` | One event, with its repeat rule and exceptions. |
| `PATCH /api/v1/calendar/events/{uid}` | Change an event, one occurrence, or this and following. |
| `DELETE /api/v1/calendar/events/{uid}` | Delete an event or an occurrence. |
| `POST /api/v1/calendar/events/{uid}/cancel` | Cancel a meeting you organise; guests get a cancellation. |
| `POST /api/v1/calendar/events/{uid}/counter/accept` | Accept a guest's proposed new time. |
| `POST /api/v1/calendar/events/{uid}/counter/decline` | Decline a guest's proposed new time. |
| `GET /api/v1/calendar/events/{uid}/ics` | Download one event as an .ics file. |
| `POST /api/v1/calendar/events/{uid}/move` | Move an event to another calendar. |
| `GET /api/v1/calendar/events/{uid}/prep` | The meeting prep of an event occurrence (`?occurrence=`). |
| `POST /api/v1/calendar/events/{uid}/prep/summary` | A short summary of the meeting prep, written by your assistant model. |
| `POST /api/v1/calendar/events/{uid}/rsvp` | Answer an invitation already in your calendar. |
| `POST /api/v1/calendar/find-time` | Free slots in your calendars inside working hours, best first. With `people`, the busy times of people in your groups who share their free/busy are added. |
| `GET /api/v1/calendar/freebusy` | Your busy times in a range (no titles). |
| `GET /api/v1/calendar/household/freebusy` | The busy times of people in your groups who share their free/busy (signed-in person only). |
| `POST /api/v1/calendar/from-message` | Suggest an event or task from an email (nothing is saved). |
| `POST /api/v1/calendar/from-message/confirm` | Save the event or task made from an email, linked to it. |
| `POST /api/v1/calendar/import` | Import the events of an .ics file into a calendar. |
| `POST /api/v1/calendar/subscriptions` | Subscribe to a calendar link (`{url, name?, refresh_hours?}`); fetched now and then on a schedule, read-only. |
| `GET /api/v1/calendar/subscriptions` | Your calendar links. |
| `PATCH /api/v1/calendar/subscriptions/{cid}` | Rename a calendar link or change how often it is fetched. |
| `POST /api/v1/calendar/subscriptions/{cid}/refresh` | Fetch a calendar link now. |
| `DELETE /api/v1/calendar/subscriptions/{cid}` | Remove a calendar link and its events. |
| `POST /api/v1/calendar/invite-reply` | Answer an invitation in an email (Yes, Maybe, No). |
| `GET /api/v1/calendar/search` | Search events and tasks with words and operators. |
| `POST /api/v1/calendar/sync` | Sync your calendars now. |
| `GET /api/v1/calendar/sync-status` | When each calendar account last synced. |
| `GET /api/v1/conversations/{cid}/links` | Events, tasks and recordings linked to a conversation. |
| `GET /api/v1/messages/{mid}/invite` | The invitation in an email, with your answer and conflicts. |
| `POST /api/v1/messages/{mid}/itip/apply` | Apply a calendar change from this email that was kept back because its sender could not be verified (signed-in user only). |

## Tasks

Scope: `read`, `calendar` to change.

| Route | Description |
|---|---|
| `GET /api/v1/tasks` | List tasks: a smart list (today, overdue, upcoming…), a list, a tag or a search. |
| `POST /api/v1/tasks` | Create a task or a subtask. |
| `POST /api/v1/tasks/bulk` | One action on many tasks: complete, reschedule, move, tag, priority, delete. |
| `GET /api/v1/tasks/counts` | Open tasks per smart list, list and tag. |
| `GET /api/v1/tasks/tags` | Your task tags with their colours. |
| `PATCH /api/v1/tasks/tags/{name}` | Rename or recolour a tag on every task. |
| `DELETE /api/v1/tasks/tags/{name}` | Remove a tag from every task. |
| `GET /api/v1/tasks/{uid}` | One task. |
| `PATCH /api/v1/tasks/{uid}` | Change a task; complete or reopen it. |
| `DELETE /api/v1/tasks/{uid}` | Delete a task (with `cascade=true`, its subtasks too). |
| `POST /api/v1/tasks/{uid}/move` | Move a task to another list, under a parent, or to a new place in the order. |
| `POST /api/v1/tasks/{uid}/skip` | Skip one occurrence of a repeating task. |

## Notes and checklists

Scope: a signed-in session. API tokens cannot use these routes yet. A note's id starts with `n`;
items and sections have UUIDs. Every change is answered with an `undo`: the request for taking it back.

| Route | Description |
|---|---|
| `GET /api/v1/notes` | Your notes and tables as cards: Notes, Lists, Tables, Archive, Trash or one label (`view`, `label`, `sort`), and your labels. |
| `POST /api/v1/notes` | Create a note or a checklist; `lines` are read by the item grammar. |
| `POST /api/v1/notes/capture` | Add items from one line: `#List` picks the checklist, `Name:` a section. |
| `POST /api/v1/notes/quick` | "New note…": with `#List` first, items are added to that list; anything else becomes a text note. |
| `POST /api/v1/notes/{note_id}/presence` | Say that you have a shared note open (`here`, every 15 s) or closed it (`left`). |
| `GET /api/v1/notes/search` | Search notes and tables (`q`, with `is:list`, `is:table`, `label:`, `color:`, `section:`, `in:trash` and more). |
| `GET /api/v1/notes/export` | A zip of every note: Markdown files and `notes.json`. |
| `GET /api/v1/notes/sync` | Notes changed since a time, with their items (`since`). |
| `POST /api/v1/notes/from-email` | A note or a checklist from a conversation, linked to it. |
| `POST /api/v1/notes/from-recording` | A text note from a recording (title, date, people, summary, action items), linked to it. |
| `GET /api/v1/notes/templates` | The templates: the starters, then your own. |
| `POST /api/v1/notes/templates` | Make a template from its content (`kind`, `name`, `content`). |
| `DELETE /api/v1/notes/templates/{template_id}` | Delete one of your templates. |
| `POST /api/v1/notes/from-template` | A new note, list or table from a template (`template`, `title`, `labels`). |
| `GET /api/v1/notes/linked` | The notes linked to a conversation, an event, a task or a recording. |
| `POST /api/v1/notes/trash/empty` | Delete every note and table in Trash forever. |
| `GET /api/v1/notes/labels` | Your note labels with their counts. |
| `POST /api/v1/notes/labels` | Create a note label. |
| `PATCH /api/v1/notes/labels/{name}` | Rename, recolour or move a note label. |
| `DELETE /api/v1/notes/labels/{name}` | Delete a note label; the notes stay. |
| `POST /api/v1/notes/labels/restore` | Put a deleted label back on its notes (the undo of a delete). |
| `GET /api/v1/notes/{note_id}` | One note with its sections and items in display order. |
| `PATCH /api/v1/notes/{note_id}` | Change the title, text, colour or switches; send `base_version` to merge. |
| `DELETE /api/v1/notes/{note_id}` | Move a note to Trash; with `forever=true`, it is deleted from Trash. |
| `POST /api/v1/notes/{note_id}/restore` | Restore a note from Trash. |
| `POST /api/v1/notes/{note_id}/duplicate` | Make a copy of a note. |
| `POST /api/v1/notes/{note_id}/convert` | Turn a text note into a checklist, or back (`to`). |
| `PUT /api/v1/notes/{note_id}/state` | Your own pin, archive, labels and place in the grid. |
| `GET /api/v1/notes/{note_id}/export` | One note as Markdown. |
| `POST /api/v1/notes/{note_id}/revert` | Apply the undo of an item or section change. |
| `POST /api/v1/notes/{note_id}/items` | Add items: lines, item objects or usual items; duplicates are merged. |
| `PATCH /api/v1/notes/{note_id}/items/{item_id}` | Check, uncheck or edit an item. |
| `DELETE /api/v1/notes/{note_id}/items/{item_id}` | Delete an item. |
| `POST /api/v1/notes/{note_id}/items/{item_id}/move` | Move an item, or move it to another section. |
| `POST /api/v1/notes/{note_id}/items/{item_id}/to-task` | Make a task from an item. |
| `DELETE /api/v1/notes/{note_id}/items/{item_id}/task` | Delete the task made from an item. |
| `POST /api/v1/notes/{note_id}/items/clear-checked` | Delete the checked items of a list. |
| `POST /api/v1/notes/{note_id}/items/uncheck-all` | Uncheck every item of a list. |
| `POST /api/v1/notes/{note_id}/sections` | Add a section. |
| `PATCH /api/v1/notes/{note_id}/sections/{section_id}` | Rename, move, collapse a section, set its order, or reset its learned order. |
| `DELETE /api/v1/notes/{note_id}/sections/{section_id}` | Delete a section, keeping its items or not (`items`). |
| `GET /api/v1/notes/{note_id}/usual` | The usual items of a list, and the ones you hid. |
| `POST /api/v1/notes/{note_id}/usual/{key}/dismiss` | Hide a usual item for a while, or for good. |
| `DELETE /api/v1/notes/{note_id}/usual/{key}/dismiss` | Show a hidden usual item again. |
| `GET /api/v1/notes/{note_id}/revisions` | The saved versions of a note. |
| `GET /api/v1/notes/{note_id}/revisions/{rev}` | One saved version with its content. |
| `POST /api/v1/notes/{note_id}/revisions/{rev}/restore` | Restore a saved version. |
| `POST /api/v1/notes/{note_id}/revisions/checkpoint` | End an editing session, so the next change starts a new version. |
| `POST /api/v1/notes/{note_id}/links` | Link a note to a conversation, an event, a task or a recording. |
| `DELETE /api/v1/notes/{note_id}/links/{link_id}` | Remove a link. |
| `PUT /api/v1/notes/{note_id}/reminders` | Your reminders on the note (`at`: times with their zone; with none, they are removed). |
| `POST /api/v1/notes/{note_id}/save-template` | Save the note or the list as a template (`name`). |
| `GET /api/v1/notes/{note_id}/shares` | Who the note is shared with, at which level, and who you may add. |
| `PUT /api/v1/notes/{note_id}/shares/{principal}` | Share with a person (their user id) or one of your groups (`group:<name>`) at `view`, `edit` or `share`, or change the level. For a person outside your groups who is not on the note yet, also send `email`, the address a people search showed. Without it, the id is answered as an unknown person (404). |
| `DELETE /api/v1/notes/{note_id}/shares/{principal}` | Stop sharing with a person or a group. |
| `DELETE /api/v1/notes/{note_id}/shares/me` | Remove a note shared with you from your notes; it stays for the others. |
| `POST /api/v1/notes/{note_id}/shares/me` | Add a note you removed back (the undo). |
| `POST /api/v1/notes/{note_id}/move-to-group` | Move your note into one of your groups (`group`), or out of its group (`group: null`). |
| `GET /api/v1/notes/share-notices` | Notes and tables people shared with you since you last dismissed the notice. |
| `DELETE /api/v1/notes/share-notices` | Dismiss the notice. |

The sharing routes take a signed-in session only, never an API token.

## Tables

Scope: a signed-in session. With an API token, tables are reached through Ambrose's table tools with the
`notes` scope ([Tools](../agents/tools.md#tables)). A table's id starts with `t`; rows have UUIDs.
Every change is answered with an `undo`; most undos are `POST …/revert` with the change's batch.

| Route | Description |
|---|---|
| `GET /api/v1/tables` | Your tables as cards: Tables, Archive, Trash or one label (`view`, `label`). |
| `POST /api/v1/tables` | Create a table with its columns (or a template), and optional first rows. |
| `GET /api/v1/tables/linked` | The tables linked to an email, event, task or recording, and the rows with a link to it in a link cell. |
| `POST /api/v1/tables/import` | Preview a CSV file (`preview`), or import it into a new table. |
| `GET /api/v1/tables/sync` | The phone's changes since a time (`since`), as `GET /notes/sync`: changed and shared tables, rows added, changed or deleted, tables in Trash, and every table you see now. |
| `GET /api/v1/tables/{table_id}` | One table with its columns and views. |
| `PATCH /api/v1/tables/{table_id}` | Rename, describe, recolour, mark local-only or choose the primary column; send `base_version` to merge. |
| `DELETE /api/v1/tables/{table_id}` | Move a table to Trash; with `forever=true`, it is deleted from Trash. |
| `POST /api/v1/tables/{table_id}/restore` | Restore a table from Trash. |
| `POST /api/v1/tables/{table_id}/duplicate` | Make a copy, with or without rows (`rows`). |
| `PUT /api/v1/tables/{table_id}/state` | Your own pin, archive, labels and place in the Notes grid, and the date column on your calendar (`calendar_column`). |
| `GET /api/v1/tables/calendar` | The dated rows of the tables you put on your calendar, from `start` to `end`. |
| `POST /api/v1/tables/{table_id}/presence` | Say that you have a shared table open, or closed it. |
| `GET /api/v1/tables/{table_id}/rows` | The rows of a view in its order, with groups, footers and link titles (`view`, `spec`, `q`, `offset`, `limit`). |
| `POST /api/v1/tables/{table_id}/rows` | Add one row or many; typed values, or text to read (`parse`). |
| `POST /api/v1/tables/{table_id}/rows/bulk` | Set a column on many rows, fill down, paste a range, or delete many rows. |
| `POST /api/v1/tables/{table_id}/rows/restore` | Bring back deleted rows. |
| `GET /api/v1/tables/{table_id}/rows/{row_id}` | One row with its link titles and the rows that link to it. |
| `PATCH /api/v1/tables/{table_id}/rows/{row_id}` | Change cells; send `base_version` to merge per cell. |
| `DELETE /api/v1/tables/{table_id}/rows/{row_id}` | Delete a row (restorable for 30 days). |
| `POST /api/v1/tables/{table_id}/rows/{row_id}/duplicate` | Copy a row below itself. |
| `POST /api/v1/tables/{table_id}/rows/{row_id}/move` | Move a row in the manual order. |
| `GET /api/v1/tables/{table_id}/rows/{row_id}/changes` | A row's changes and each cell's earlier values. |
| `POST /api/v1/tables/{table_id}/rows/{row_id}/restore` | Put a row, or one cell, back as it was after a change. |
| `GET /api/v1/tables/{table_id}/deleted-rows` | Deleted rows with who deleted them and when. |
| `POST /api/v1/tables/{table_id}/columns` | Add a column. |
| `PATCH /api/v1/tables/{table_id}/columns/{col_id}` | Rename, retype (with a `preview`), change options or settings, move or resize a column. |
| `DELETE /api/v1/tables/{table_id}/columns/{col_id}` | Delete a column (restorable for 30 days with its values). |
| `POST /api/v1/tables/{table_id}/columns/{col_id}/restore` | Bring back a deleted column. |
| `GET /api/v1/tables/{table_id}/columns/{col_id}/targets` | What a link cell can point at, by title (`q`). |
| `GET /api/v1/tables/{table_id}/deleted-columns` | Deleted columns with their value counts. |
| `POST /api/v1/tables/{table_id}/views` | Save a view (new, or over an existing one). |
| `PATCH /api/v1/tables/{table_id}/views/{view_id}` | Change a view's sort, filters, group, hidden columns, footers or name. |
| `DELETE /api/v1/tables/{table_id}/views/{view_id}` | Delete a view (not the last one). |
| `POST /api/v1/tables/{table_id}/stats` | Totals and counts for a view or a filter, optionally per group. |
| `GET /api/v1/tables/{table_id}/export.csv` | The current view or the whole table as CSV (`scope`, `bom`). |
| `GET /api/v1/tables/{table_id}/export.xlsx` | The whole table or the current view (`scope=view`) as an Excel workbook with typed cells. |
| `GET /api/v1/tables/export.xlsx` | Up to 20 whole tables (`ids`, comma-separated) in one workbook, one sheet each. |
| `POST /api/v1/tables/{table_id}/import` | Append the rows of a CSV file; columns are matched by name. |
| `GET /api/v1/tables/{table_id}/changes` | The table's changes in sessions, newest first. |
| `POST /api/v1/tables/{table_id}/restore-point` | Restore the whole table to a moment in the last 30 days (`preview` first). |
| `POST /api/v1/tables/{table_id}/revert` | Take back the changes of one batch (the undo of every table change). |
| `POST /api/v1/tables/{table_id}/links` | Link a table to a conversation, an event, a task or a recording. |
| `DELETE /api/v1/tables/{table_id}/links/{link_id}` | Remove a link. |
| `POST /api/v1/tables/{table_id}/save-template` | Save the table's columns as a template (`name`). |
| `GET /api/v1/tables/{table_id}/shares` | Who the table is shared with, as for notes. |
| `PUT /api/v1/tables/{table_id}/shares/{principal}` | Share a table, or change a level, as for notes. |
| `DELETE /api/v1/tables/{table_id}/shares/{principal}` | Stop sharing a table with a person or a group. |
| `DELETE /api/v1/tables/{table_id}/shares/me` | Remove a table shared with you from your notes. |
| `POST /api/v1/tables/{table_id}/shares/me` | Add a table you removed back (the undo). |
| `POST /api/v1/tables/{table_id}/move-to-group` | Move your table into one of your groups, or out of its group. |

## Reminders and browser push

| Route | Description |
|---|---|
| `POST /api/v1/push/webpush` | Turn on browser push for this browser. |
| `DELETE /api/v1/push/webpush` | Turn off browser push for this browser. |
| `GET /api/v1/push/webpush/key` | The VAPID key for subscribing a browser to push. |
| `POST /api/v1/reminders/act` | A button on a browser push notification: snooze, done or dismiss. No session: the signed token in the request names you and the reminder. |
| `GET /api/v1/reminders/active` | Reminders that fired and are waiting, and snoozes that came due. |
| `GET /api/v1/today` | What today holds in one answer: events, tasks due, missed reminders, mail waiting on a reply, approvals (`date=` for another day). |
| `GET /api/v1/activity` | What you, Ambrose and your agents changed, and your administrator's changes to your account, newest first, each with its `undo` request (signed-in person only). |
| `POST /api/v1/activity/{entry_id}/undone` | Mark an Activity entry undone after its `undo` request was sent. |
| `POST /api/v1/reminders/add` | Add a one-off reminder some minutes before an event. |
| `POST /api/v1/reminders/handle` | Dismiss reminders. |
| `GET /api/v1/reminders/settings` | Your reminder defaults. |
| `PUT /api/v1/reminders/settings` | Change your reminder defaults. |
| `POST /api/v1/reminders/snooze` | Snooze a task or event reminder. |
| `GET /api/v1/reminders/state` | Reminder changes since a time, so every device stays in step. With `kinds=e,t,n`, note reminders are included. |

## Phone push targets

| Route | Description |
|---|---|
| `GET /api/v1/push/targets` | Your phones registered for push, Mailr's VAPID key, and `server`: the server's own ntfy (`MAILR_NTFY_URL`) for the phone's setup. |
| `POST /api/v1/push/targets` | Register a phone for push (UnifiedPush). |
| `DELETE /api/v1/push/targets/{tid}` | Remove a push target. |
| `GET /api/v1/admin/push-setup` | Admins: what a new member does for push on Android, and counts of people per push server. See [Member setup](../admin/push.md#member-setup). |
| `POST /api/v1/push/targets/{tid}/test` | Send a test notification. |

## Assistant: orders, memory, skills, dictation

Scope: `read`, `memory` or `rules` to change.

| Route | Description |
|---|---|
| `GET /api/v1/assistant/entities` | People, organisations, projects and places known to Ambrose. |
| `POST /api/v1/assistant/entities` | Add one. |
| `GET /api/v1/assistant/entities/{eid}` | One, with its memories. |
| `PATCH /api/v1/assistant/entities/{eid}` | Change one. |
| `DELETE /api/v1/assistant/entities/{eid}` | Delete one. |
| `GET /api/v1/assistant/memories` | Ambrose's memories about you and your work, and its suggestions. With `ids=1,2`, only those notes are returned (the Used memories line). |
| `GET /api/v1/assistant/memories/learning` | How learning from mail went: the last passes, with messages read, new suggestions and an error code. |
| `POST /api/v1/assistant/memories` | Add a memory. |
| `POST /api/v1/assistant/memories/learn` | Learn from your recent sent mail now (results arrive as suggestions). |
| `PATCH /api/v1/assistant/memories/{mid}` | Change a note. |
| `DELETE /api/v1/assistant/memories/{mid}` | Delete a note. |
| `POST /api/v1/assistant/memories/{mid}/accept` | Accept a suggested note. |
| `POST /api/v1/assistant/memories/{mid}/reject` | Dismiss a suggested note. |
| `GET /api/v1/assistant/orders` | Your standing orders and routines. |
| `POST /api/v1/assistant/orders` | Create a standing order or routine. |
| `POST /api/v1/assistant/orders/preview` | Test an order on recent mail before you save it. |
| `PATCH /api/v1/assistant/orders/{oid}` | Change, pause or resume an order. The built-in order takes only `enabled` and `permission` (`ask` or `allow`). |
| `DELETE /api/v1/assistant/orders/{oid}` | Delete an order (never the built-in one). |
| `POST /api/v1/assistant/orders/{oid}/move` | Move an order up or down (`direction`); the top one is applied first. |
| `POST /api/v1/assistant/orders/{oid}/run` | Run an order now. |
| `POST /api/v1/assistant/orders/{oid}/test` | Ask whether an order would apply to one conversation (nothing is changed). |
| `GET /api/v1/assistant/profile` | Ambrose's information about you (About you). |
| `PUT /api/v1/assistant/profile` | Change About you. |
| `GET /api/v1/assistant/recall` | The notes Ambrose would recall for a request. |
| `POST /api/v1/messages/{mid}/invite/link` | Give an event already in your calendar the invitation's identity (`uid`: the event), so the organiser's updates reach it. |
| `POST /api/v1/messages/{mid}/invite/unlink` | Undo of the link: your event as it was. |
| `POST /api/v1/messages/{mid}/itip/remove` | Remove the event an organiser cancelled from your calendar; its copy is kept for Undo. |
| `POST /api/v1/messages/{mid}/itip/restore` | Undo of the removal. |
| `POST /api/v1/calendar/events/{uid}/revert` | Undo an organiser change applied from your forward (`message_id`: the forward). |
| `POST /api/v1/messages/{mid}/event-change` | Close a suggested move: `status` `applied` or `dismissed`. |
| `GET /api/v1/assistant/trusted-addresses` | Your addresses trusted to speak for you, addresses that look like yours, and the addresses handled by another assistant. Signed-in users only. |
| `POST /api/v1/assistant/trusted-addresses` | Add an address of yours; a confirmation code is mailed to it. Signed-in users only. |
| `PUT /api/v1/assistant/trusted-addresses/mode` | For older app versions: with `off`, the order Follow my forwarded notes is turned off; with `ask` or `allow`, it is turned on with that permission. |
| `DELETE /api/v1/assistant/trusted-addresses/{tid}` | Remove an address. |
| `POST /api/v1/assistant/trusted-addresses/{tid}/confirm` | Confirm an address with its code. |
| `POST /api/v1/assistant/trusted-addresses/{tid}/resend` | Send a new confirmation code. |
| `GET /api/v1/assistant/runs` | Recent runs of your standing orders and routines. |
| `GET /api/v1/assistant/skills` | Your skills. |
| `POST /api/v1/assistant/skills` | Create a skill. |
| `GET /api/v1/assistant/skills/resolve` | What the `/name` words in a prompt refer to. |
| `POST /api/v1/assistant/skills/restore-builtins` | Bring back built-in skills you deleted. |
| `POST /api/v1/assistant/skills/test` | Try a skill on a sample text. |
| `GET /api/v1/assistant/skills/{sid}` | One skill. |
| `PATCH /api/v1/assistant/skills/{sid}` | Change a skill. |
| `DELETE /api/v1/assistant/skills/{sid}` | Delete a skill. |
| `POST /api/v1/assistant/skills/{sid}/duplicate` | Copy a skill. |
| `POST /api/v1/assistant/skills/{sid}/reset` | Restore a built-in skill's original text. |
| `POST /api/v1/assistant/transcribe` | Turn a short voice clip into text (dictation). The clip is not stored. |
| `GET /api/v1/conversations/{cid}/brief` | The brief a standing order left on a conversation. |

## Assistant chat and agent tools

Scope: depends on the tool.

| Route | Description |
|---|---|
| `POST /api/v1/agent/chat` | Send a message to Ambrose; the answer is streamed back. `local_only: true` keeps the chat on the local model. |
| `POST /api/v1/agent/chat/{session_id}/stop` | Stop Ambrose's current answer. |
| `GET /api/v1/agent/chat/{session_id}/stream` | Reconnect to an answer that is still streaming. |
| `POST /api/v1/agent/draft` | Help me write: draft a message or edit the current draft. |
| `GET /api/v1/agent/commands` | The chat's slash commands, with what each does in your language. |
| `GET /api/v1/agent/sessions` | Your past chats. With `q`, the chats whose title, questions or answers match, each with a snippet. |
| `GET /api/v1/agent/sessions/{session_id}` | One past chat. |
| `PATCH /api/v1/agent/sessions/{session_id}` | Rename a chat (`title`; when empty, the chat's short title, or else its first question, is used). |
| `POST /api/v1/agent/sessions/{session_id}/retitle` | Make a new short title for a chat from its first question. |
| `GET /api/v1/agent/reviews/{rid}` | A bulk review's progress: how many conversations are done and set aside, and its review card once ready. |
| `POST /api/v1/agent/reviews/{rid}/stop` | Stop a bulk review after the batch in progress; what it decided so far is kept. |
| `POST /api/v1/agent/reviews/{rid}/resume` | Continue a stopped or failed bulk review where it stopped. |
| `GET /api/v1/agent/review-choices` | The moves you chose on review cards, remembered per sender (sender, move, count, when), latest first. Signed-in apps only. |
| `DELETE /api/v1/agent/review-choices/{cid}` | Forget one remembered choice: that sender's mail goes to the model again in later reviews. |
| `DELETE /api/v1/agent/review-choices` | Forget every remembered choice. |
| `DELETE /api/v1/agent/sessions/{session_id}` | Delete a chat. |
| `GET /api/v1/agent/tools` | The agent tools your token may call. |
| `POST /api/v1/agent/tools/{name}` | Call one agent tool by name. |
| `POST /api/v1/context` | Gather a conversation, its people and related mail into one bundle for a reply. |

## Approvals and permission rules

| Route | Description |
|---|---|
| `GET /api/v1/approvals` | Actions waiting for approval, and recent decisions. |
| `GET /api/v1/approvals/{aid}` | One approval request. |
| `POST /api/v1/approvals/{aid}/decide` | Approve or deny a request (`scope`: `once`, `session` or `always`). With `except_ids`, a review card or a cleanup plan is approved without those conversations. With `changes` (conversation id → `trash`, `archive`, `read`, `keep` or `label:<id>`), a review card is approved with your own moves for conversations it showed. Session only. |
| `POST /api/v1/approvals/decide-many` | Decide several waiting requests from one requester at once (**Allow all**). Session only. |
| `POST /api/v1/approvals/grants/{rule_id}/undo` | Undo a grant made from a card: the rule is deleted, and the requests it decided that have not started wait again. |
| `GET /api/v1/permissions` | Your permission rules for Ambrose and agents. |
| `POST /api/v1/permissions` | Add a permission rule. |
| `GET /api/v1/permissions/effective` | What each tool may do for a token or Ambrose: allow, ask or deny. |
| `PATCH /api/v1/permissions/{rid}` | Change a permission rule. |
| `DELETE /api/v1/permissions/{rid}` | Delete a permission rule. |

## API tokens and audit log

Scope: a signed-in session.

| Route | Description |
|---|---|
| `GET /api/v1/audit` | The audit log of everything agents did with your tokens. |
| `GET /api/v1/tokens` | Your API tokens. |
| `POST /api/v1/tokens` | Create a token (shown once). |
| `PATCH /api/v1/tokens/{tid}` | Rename a token or change its scopes. |
| `DELETE /api/v1/tokens/{tid}` | Revoke a token. |

## Models per user

| Route | Description |
|---|---|
| `GET /api/v1/me/models` | The model used for each assistant task, and the models available to choose from. |
| `PUT /api/v1/me/models/{task}` | Choose your model for a task. |
| `GET /api/v1/me/local-models` | **Local models only for me**: the household rule, your choice, the result, and whether the switch is locked. |
| `PUT /api/v1/me/local-models` | Turn **Local models only for me** on or off. |
| `GET /api/v1/me/model-use` | Per feature, which model did the work, local or hosted, and how often in the last `days` days (at most 90). |

## Recordings (Speakr)

Scope: `recordings`.

| Route | Description |
|---|---|
| `POST /api/v1/integrations/speakr/{hook}/webhook` | The address for Speakr's notices of new or changed recordings. |
| `POST /api/v1/speakr/connectors/{cid}/webhook` | Turn on Speakr's webhook for this account. |
| `DELETE /api/v1/speakr/connectors/{cid}/webhook` | Turn the webhook off. |
| `POST /api/v1/speakr/connectors/{cid}/webhook/test` | Test the webhook. |
| `GET /api/v1/speakr/events/last-time` | The recording of the last meeting in a series. |
| `GET /api/v1/speakr/events/recordings` | Recordings linked to (or suggested for) an event. |
| `GET /api/v1/speakr/folders` | Your Speakr folders. |
| `GET /api/v1/speakr/linked-conversations` | Conversations linked to recordings. |
| `POST /api/v1/speakr/links` | Link a recording to an event, a conversation or a task. |
| `DELETE /api/v1/speakr/links/{link_id}` | Remove a link. |
| `GET /api/v1/speakr/mail-card` | The recording card shown on a conversation. |
| `GET /api/v1/speakr/participants` | Everyone who appears in your recordings, grouped by person. |
| `POST /api/v1/speakr/participants/dismiss` | Hide a participant name. |
| `POST /api/v1/speakr/participants/merge` | Merge two participant names into one person. |
| `POST /api/v1/speakr/participants/rename` | Rename a participant in every recording. |
| `POST /api/v1/speakr/participants/separate` | Separate a name from a merged person. |
| `GET /api/v1/speakr/people/recordings` | Recordings a person took part in. |
| `GET /api/v1/speakr/recordings` | Your recordings, newest first. |
| `POST /api/v1/speakr/recordings` | Upload a new recording to Speakr with its title, participants and date. |
| `GET /api/v1/speakr/recordings/{cid}/{rid}` | One recording. |
| `PATCH /api/v1/speakr/recordings/{cid}/{rid}` | Change a recording's title, participants, notes or tags. |
| `POST /api/v1/speakr/recordings/{cid}/{rid}/action-items` | Propose the action items of a recording. |
| `GET /api/v1/speakr/recordings/{cid}/{rid}/events` | Meetings mentioned in a recording. |
| `POST /api/v1/speakr/recordings/{cid}/{rid}/follow-up` | Draft the follow-up email of a meeting. |
| `GET /api/v1/speakr/recordings/{cid}/{rid}/notes` | A recording's notes. |
| `GET /api/v1/speakr/recordings/{cid}/{rid}/people` | The people in a recording, matched to your contacts. |
| `POST /api/v1/speakr/recordings/{cid}/{rid}/share` | Create a share link to a recording. |
| `GET /api/v1/speakr/recordings/{cid}/{rid}/shares` | A recording's share links. |
| `DELETE /api/v1/speakr/recordings/{cid}/{rid}/shares/{share_id}` | Revoke a share link. |
| `GET /api/v1/speakr/recordings/{cid}/{rid}/speakers` | The speakers of a recording. |
| `GET /api/v1/speakr/recordings/{cid}/{rid}/summary` | A recording's summary. |
| `POST /api/v1/speakr/recordings/{cid}/{rid}/tasks` | Create tasks from chosen action items. |
| `GET /api/v1/speakr/recordings/{cid}/{rid}/transcript` | A recording's transcript, or part of it. |
| `GET /api/v1/speakr/search` | Search recordings by words. |
| `POST /api/v1/speakr/speakers/{cid}/person` | Say who a speaker is. |
| `DELETE /api/v1/speakr/speakers/{cid}/person` | Forget who a speaker is. |
| `GET /api/v1/speakr/status` | Whether you have a Speakr account connected. |
| `GET /api/v1/speakr/tags` | Your Speakr tags. |
| `GET /api/v1/speakr/tasks/recordings` | Recordings a task came from or is linked to. |
| `POST /api/v1/speakr/upload` | Send an audio or video attachment to Speakr. |

## Administration

Scope: a signed-in admin session. API tokens are refused, except an admin's token with the `tls` scope for the two `/admin/tls` routes.

| Route | Description |
|---|---|
| `GET /api/v1/admin/models` | Model endpoints. |
| `POST /api/v1/admin/models` | Add a model endpoint. |
| `GET /api/v1/admin/phone-apps` | The Android apps offered on this server: the version downloaded to phones, the previous one, and the oldest version the server answers. |
| `POST /api/v1/admin/phone-apps/{app}` | Publish a release APK of `mailr` or `notes` (multipart field `file`). |
| `POST /api/v1/admin/phone-apps/{app}/rollback` | Make the previous version current again. |
| `PUT /api/v1/admin/phone-apps/notes/min-version` | The oldest Notes app version the server answers (`{"min_version": "1.1.0"}`; empty for no limit). Older Notes apps get 426. |
| `GET /api/v1/admin/onboarding/presets` | Prepared defaults for new users (by email or group). |
| `POST /api/v1/admin/onboarding/presets` | Prepare a new user. |
| `PUT /api/v1/admin/onboarding/presets/{pid}` | Change what is prepared. |
| `DELETE /api/v1/admin/onboarding/presets/{pid}` | Remove a preparation. |
| `GET /api/v1/admin/models/defaults` | The server's model for each task, and the model for local-only mail. |
| `PUT /api/v1/admin/models/defaults/{task}` | Set the server's model for a task. |
| `POST /api/v1/admin/models/probe` | List the models served by an endpoint, before you save it. |
| `PATCH /api/v1/admin/models/{eid}` | Change a model endpoint. |
| `DELETE /api/v1/admin/models/{eid}` | Remove a model endpoint. |
| `POST /api/v1/admin/models/{eid}/probe` | List the models a saved endpoint serves. |
| `POST /api/v1/admin/models/{eid}/test` | Send one tiny request to an endpoint: the time it took, or the error in plain words. |
| `GET /api/v1/admin/policies` | Server-wide policies. |
| `PUT /api/v1/admin/policies` | Change server-wide policies. |
| `GET /api/v1/admin/access` | Access policies, the identity-provider groups seen at sign-in, and what a policy can hold. |
| `POST /api/v1/admin/access/policies` | Add an access policy. |
| `PUT /api/v1/admin/access/policies/{pid}` | Change an access policy. |
| `DELETE /api/v1/admin/access/policies/{pid}` | Delete an access policy. |
| `GET /api/v1/admin/users/{uid}/access` | A user's groups and the access that results. |
| `PUT /api/v1/admin/users/{uid}/groups` | Assign policy groups to a password-only account. An account managed by the identity provider takes its groups from the provider (409). |
| `GET /api/v1/admin/calendars` | Whether everyone gets the built-in Mailr calendar, and how many users have it. |
| `PUT /api/v1/admin/calendars` | Turn the built-in calendar on for everyone, or only for some groups, or off. Nothing is deleted when it is turned off. |
| `POST /api/v1/admin/calendars/phone-check` | Check from the server that the public calendar address is reachable and restricted. |
| `GET /api/v1/admin/calendars/groups` | Every known group with its members and its calendars. |
| `POST /api/v1/admin/calendars/groups` | Create a group calendar. |
| `PATCH /api/v1/admin/calendars/groups/{cid}` | Change a group calendar. |
| `GET /api/v1/admin/calendars/groups/{cid}/export` | A group calendar as one `.ics` file. |
| `DELETE /api/v1/admin/calendars/groups/{cid}` | Delete a group calendar and everything in it, for every member (`confirm` must be its name). |
| `GET /api/v1/admin/models/local-policy` | The household rule for hosted models: each person chooses, or local models only for everyone. |
| `PUT /api/v1/admin/models/local-policy` | Change the household rule for hosted models. |
| `GET /api/v1/admin/websearch` | The web search providers and page readers in the order they are tried. See [Web search](../admin/web-search.md). |
| `POST /api/v1/admin/websearch/providers` | Add a search provider or a page reader. |
| `PATCH /api/v1/admin/websearch/providers/{pid}` | Change a provider or a page reader. |
| `DELETE /api/v1/admin/websearch/providers/{pid}` | Remove a provider or a page reader. |
| `POST /api/v1/admin/websearch/providers/{pid}/test` | One test search with a saved provider. |
| `POST /api/v1/admin/websearch/test` | One test search with unsaved settings. |
| `GET /api/v1/admin/diagnostics` | Every readiness check with its plain name, backup, push, background work, account health, model health and trusted proxies. With `?refresh=true`, the checks are run now. |
| `GET /api/v1/admin/status` | Health of the app, worker, mail store, queues and connectors. |
| `GET /api/v1/admin/tls` | The IMAP certificate in use. |
| `PUT /api/v1/admin/tls` | Install a certificate for IMAP. |
| `GET /api/v1/admin/users` | Find users. |
| `POST /api/v1/admin/users` | Add a user. |
| `PATCH /api/v1/admin/users/{uid}` | Change a user: name, role, disabled. |
| `DELETE /api/v1/admin/users/{uid}` | Remove a user and their data. |
| `POST /api/v1/admin/users/{uid}/password` | Set a user's password. |

## System

| Route | Description |
|---|---|
| `GET /api/v1/events` | A live stream (server-sent events) of changes to your mail, calendar and reminders. |
| `GET /api/v1/health` | A health check for monitoring (no sign-in needed). |
| `GET /api/v1/i18n/messages` | The texts of the server's error codes in a language (`lang=de`), with English for missing texts; an error response carries its `code` and `params` beside the English `detail` (no sign-in needed). |
| `GET /api/v1/i18n/languages` | The languages the server has texts for (no sign-in needed). |
| `GET /api/v1/health/egress` | For a monitor: 200 while the VPN egress for remote content works, 503 otherwise. The kind of failure is shown only with the monitor token (`X-Health-Token`) or to an admin. |
| `GET /api/v1/errors/config` | The browser's error-reporting settings, or `{"dsn": null}` while error reporting is off (no sign-in needed). |
| `POST /api/v1/errors/envelope` | The web app's error reports, cleaned of personal data and passed on to the configured error server (no sign-in needed). |
| `GET /api/v1/health/ready` | Readiness checks for monitoring: database, mail store, worker, sync, export, backup target and push; `503` when a required one fails (no sign-in needed). See [Monitoring](../admin/monitoring.md). |
| `GET /api/v1/health/live` | Liveness, the app container's healthcheck: `200` while the database and the mail store answer, `503` otherwise (no sign-in needed). See [Monitoring](../admin/monitoring.md#container-healthchecks). |
