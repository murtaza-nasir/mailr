# Changelog

The changes in each Mailr release are listed here, newest first. A release is tagged `v<version>`
on GitHub, and its image is published as `ghcr.io/murtaza-nasir/mailr:<version>`. The Android app
of a release has the same version number; the separate Notes app has its own.

## 0.40.31

- Android: a phone calendar event that is also in Mailr's calendar (same time and title) is shown
  and counted once, from Mailr's calendar. Meetings declined or cancelled in Mailr no longer appear
  from the phone or count as busy; phone events titled "Canceled:" are skipped too.
- Phone calendars have a fourth choice, Show and send details, which also sends each event's title
  and place to your Mailr server; guests, descriptions and notes are never sent, and busy times
  only stays the default. On the web, such events show their title and place, read-only, with the
  "Updated from …" line, and Ambrose reads them like other read-only events. Switching a calendar
  back to busy times removes its titles and places from the server at once. Settings → Calendar
  → Phone calendars on the web says what each calendar sends.
- API: `PUT /calendar/phone-calendars/{key}` accepts `details` and per-entry `title` and
  `location` (at most 300 characters); a title or place without `details: true` is refused with
  422. The phone calendar list and the calendar list gain `details`; event reads gain
  `phone_details`.
- Web: the day arrows in the calendar panel no longer move when the list of calendars finishes
  loading.

## 0.40.30

- Phone calendars. On Android, Settings → Calendar & tasks → Phone calendars shows the phone's
  own calendars (for example an Exchange account added through the Gmail app) in Mailr's
  calendar, read-only and marked "On this phone", with "Open in Calendar". Each calendar is Off,
  Show on this phone, or Show and send busy times; all start Off.
- For calendars set to "Show and send busy times", only the start, end and all-day flag of busy
  or tentative events are sent to the server; titles, places, guests and descriptions stay on the
  phone. On the web they are shown as read-only "Busy" blocks with the device and the time of
  the last update, they count as busy in free/busy and Find a time, and Ambrose sees them as
  "Busy (calendar name)". They are not shared with household members, not written to the
  calendar server and not exported. Settings → Calendar → Phone calendars on the web lists them
  with Remove.
- API: `PUT`/`GET`/`DELETE /api/v1/calendar/phone-calendars[/{key}]`; calendars carry `phone`
  and events carry `phone_busy`. New permission class `calendar.phone_sync`, allowed by default
  with the `calendar` scope.
- A new event's default calendar is now always one that can be edited.
- Android: on a locked phone, a full-screen reminder shows only "Task reminder" or "Event
  reminder" with Unlock; the title, details and buttons appear after the phone is unlocked, and
  the reminder sheet asks for the unlock first.
- Notes (both apps): an edit made while an autosave was finishing is kept and saved; before, it
  could stay unsaved and the older text was written back. Undoing back to the original text is
  saved too. The Notes app is 2.1.4.
- Android: when chat titles are changed quickly in a row (a rename, then `/retitle`), the newest
  change wins; a late answer to the first no longer puts the old title back.
- Sent mail is stored with the time it went out, so a storing step retried after a database error
  no longer leaves a second copy of the sent message.
- Web: renaming a chat while its list is reloading (a search cleared right after Enter) no
  longer puts the old title back.
- Gmail accounts: new mail is picked up within seconds. IDLE watched only an inbox folder, which a
  Gmail account (synced from All Mail) does not have, so Gmail mail waited for the next full pass
  (typically 3 to 6 minutes); IDLE now watches All Mail.

## 0.40.26

- Calendar panel: Today sits beside the date, and the day arrows stay at the row's end.
- Mailr's spam score no longer moves mail that Gmail delivered outside its Spam folder, or mail
  whose sender passes DMARC for the From domain; the score is still recorded and shown.

## 0.40.25

- A brief from a standing order or routine lists the approvals its run asked for, with their current
  state ("Waiting for your approval", "Approved: Draft saved", "Denied", "Expired"), on the web and in
  the Android app. A decision made after the run finished is shown on the card, and the card checks
  again while an approval is open.
- Background runs never send mail and end with their answer; Ambrose now says so when a draft waits for
  approval instead of promising to send it later. The user guide points to filters with
  **Forward it to:** for automatic forwarding.
- Top-level web files without a precompressed copy (the app icons) answer a cache check with 304
  instead of an error.
- A table column changed to a choice list gets its options in the table's row order.
- A stopping worker no longer claims again a job it has just handed back to the queue.
- An invitation answer whose calendar copy fails says why (for example, the calendar server's HTTP
  status) on the web and in the Android app, and the failure is logged.
- Calendar panel: the day arrows stay in place while clicking through days, and the weekday and
  date stay on one line.
- On a calendar server that schedules by itself (Davis, Nextcloud and other SabreDAV servers), each
  copy written from Mailr is marked `SCHEDULE-AGENT=CLIENT`, so the server sends no duplicate
  invitation or reply mail, and a server without mail settings no longer fails the write (HTTP 500).

## 0.40.21

The first public release. Mailr is a self-hosted mail store with a web client and an Android app.
The existing mail provider continues to receive and send the mail. New mail is collected within
seconds and kept on the server, together with a calendar, tasks, notes and an assistant, Ambrose.

### Mail

- IMAP accounts are connected in one of three modes: **Keep in sync** (`mirror`: the provider stays
  the source of truth, and labels, read state and moves go back to it), **Mailr keeps the mail**
  (`store`: Mailr is the store of record, and mail at the provider is deleted after a retention
  window once deletion is turned on) and **Copy once** (`import`: a one-time copy). Gmail is imported with its labels and
  threads, and mbox and Maildir archives through the API.
- The provider remains the receiving server for the address and the relay for outgoing mail. Mail
  is never sent from Mailr directly to the internet.
- For an account kept in sync, a copy of each message sent from Mailr is saved in the provider's Sent
  folder (**Save sent mail at the provider**: Automatic, On or Off). Automatic leaves it to Gmail
  and Microsoft, which save their own copy. Mail sent before the setting was on can be copied
  afterwards, from the account or with `mailr sent-backfill`.
- The stored mail is kept in a bundled Stalwart mail server and can be read in any mail app over
  IMAP.
- The web client has conversations, labels, categories, custom inbox sections and tabs, snooze,
  follow-up reminders, scheduled send, undo send, templates, Gmail's search operators and keyboard
  shortcuts, in five styles, each in light and dark.
- **Delete forever** in Trash and Spam removes a message from the mailbox, from the database (its
  text, search index, vectors and everything derived from it) and from the daily mail export and its
  backup copy. It asks for confirmation and has no undo.
- Importance and labels are learned from the user's actions and from review-queue verdicts. Filters
  written by the user take precedence, and the reason for each automatic decision is shown.
- The Screener holds mail from first-time senders until the sender is allowed, blocked or given a
  label.
- The Subscriptions and Cleanup pages group newsletters, offers, notifications and old archived
  mail, for unsubscribing and for clearing it in bulk.
- Remote images are fetched on the server and served from Mailr's own address, so senders do not
  see when a message was opened or from which address.

### Calendar, tasks, notes and tables

- Built-in calendars, or any CalDAV server. Invitations are answered from the message, free times
  can be found, and an email can be turned into an event or a task.
- Quick add creates an event or a task from one line, such as `Lunch with Dana fri 12:30`.
- Calendars, task lists and contacts are synchronised in both directions with other calendar and
  contacts apps over CalDAV and CardDAV.
- Notes, checklists and tables are kept on the same server, shared with people and groups, and
  exported every day as plain files.

### Ambrose and approvals

- Ambrose answers questions about mail, events and tasks with citations, takes dictation, and
  follows standing orders, briefs and routines.
- `/sweep` (or "clean up this view") reviews every conversation of the list on screen in the
  background and ends in one review card of proposed moves (Move to Trash, Archive, Label, Mark
  read), grouped by sender. Each sender's move can be changed before **Apply**. Starred and
  important mail, conversations the user replied to, mail from people the user writes to and
  invitations are kept without asking. A routine can run the same review on a schedule; its card
  always waits for approval.
- The user's own sweep rules (Settings → Ambrose → Sweep) apply to every review, and the moves
  chosen on earlier cards are remembered per sender and can be removed.
- By default, sending, deleting and rule changes by Ambrose or by an agent are held on an approval
  card, on the web or on the phone. Each default can be changed.
- No language model is required. When models are connected, an admin sets which mail may be sent to
  which model. Mail from a local-only account is sent only to endpoints marked **Local**, with no
  hosted fallback, and never to an agent that uses a hosted model. An admin can also require local
  models for everyone, or let each person turn on **Local models only for me**.
- One rule decides where data may go. Local-only mail, notes, tables, memories, calendar items and
  recordings reach only local services, and a request or job that has read any of them keeps its
  later model, search and Speakr-model calls local. Every model, speech, search, reader and
  error-report connection goes through one checked client, and the build fails if code opens a
  connection of its own.
- An endpoint counts as local only when every address its name resolves to is private, checked on
  each connection. Admin → Models shows each endpoint as "local (checked)" or "hosted". Host names
  in `MAILR_OUTBOUND_ALLOW` no longer make an endpoint local; only IP addresses and CIDRs do.
- Dictation is the model task **Speech to text**. Hosted speech services can be used for ordinary
  chats, notes and drafts; clips spoken in a local-only context go to a local speech server only.
  `MAILR_ASR_URL` appears as a built-in local endpoint.
- Error reports hide addresses in URL paths and keep only the exception type when the error
  happened while handling local-only data or when the error tracker is hosted.
- Speakr's own models count as local only when Speakr reports them local and its host is on the
  local network. A local-only message's attachment is not sent to a Speakr account that is not
  local-only.
- Ambrose's calendar and Speakr writes from a chat that has read local-only content ask first,
  unless the target calendar or Speakr account is local-only. Switching an account to local-only
  also marks the memories, entities and summaries learned from its mail.
- Web search is available through a search provider set up by an admin; queries are shown, and no
  content from local-only accounts is used in a query.

### Agents and the API

- A REST API and an MCP server, with scoped tokens, approvals and an audit log. Claude Code and other
  MCP clients are connected with a token.
- The web app, the Android app and Ambrose use the same API.

### Android

- The Mailr app has mail, calendar, tasks, notes, reminders with full-screen alerts, approvals,
  bulk review cards, dictation and a share target. Actions taken offline are sent when the phone
  is back online.
- The separate Notes app has widgets, launcher shortcuts, a Quick Settings tile and a share target,
  and uses the same notes and sync as the Notes tab of the Mailr app.
- The Android app is published as an APK under GitHub Releases. A Mailr server can also serve
  both apps to phones (Settings → Admin → Phone apps).

### Administration

- Deployment with Docker Compose, from source or from the published image. The server shows its
  version at the bottom of the web app's Support menu and at the foot of the Android app's Settings.
- Backups: a nightly database dump, and daily plain-file exports of mail (Maildir), calendars and
  contacts, and notes and tables, optionally copied to a backup target. The mail export can be
  turned off (`MAILR_MAIL_EXPORT=false`); **Mailr keeps the mail** then cannot be chosen.
- Before mail of an account where Mailr keeps the mail is deleted at the provider, it is checked against the mail export by
  default. With `MAILR_STORE_BACKUP_CHECK=false`, that check is left to the administrator's own
  backups of the mail store, and the account and Admin → Diagnostics say so.
- Sign-in with email and password, single sign-on with any OIDC provider, or both. Access policies
  set the models and assistant features for each group at the identity provider.
- Notifications in open tabs over the event stream, in browsers through Web Push, and through
  UnifiedPush targets such as an ntfy topic.
- Optional integrations: a speech server for dictation, a web search provider, a VPN egress for the
  image proxy, and Speakr for meeting recordings.
- The interface is available in English, German, Spanish, French, Brazilian Portuguese, Russian and
  Simplified Chinese.
- The user and admin guides are part of the image and are served at `/help/` to signed-in users.
