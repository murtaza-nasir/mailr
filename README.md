<p align="center">
  <img src="site/docs/assets/images/logo.png" alt="Mailr logo" width="96" height="96">
</p>

<h1 align="center">Mailr</h1>
<p align="center">Self-hosted webmail with a mail store, calendar, tasks, notes and an AI assistant</p>

<p align="center">
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img alt="AGPL v3" src="https://img.shields.io/badge/License-AGPL_v3-blue.svg"></a>
  <a href="https://github.com/murtaza-nasir/mailr/actions/workflows/test.yml"><img alt="Tests" src="https://github.com/murtaza-nasir/mailr/actions/workflows/test.yml/badge.svg"></a>
  <a href="https://github.com/murtaza-nasir/mailr/actions/workflows/docker-publish.yml"><img alt="Docker Build" src="https://github.com/murtaza-nasir/mailr/actions/workflows/docker-publish.yml/badge.svg"></a>
  <a href="https://github.com/murtaza-nasir/mailr/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/murtaza-nasir/mailr"></a>
</p>

<p align="center">
  <a href="https://murtaza-nasir.github.io/mailr/">Documentation</a> •
  <a href="https://murtaza-nasir.github.io/mailr/getting-started/install/">Install</a> •
  <a href="https://github.com/murtaza-nasir/mailr/releases">Android app</a> •
  <a href="https://murtaza-nasir.github.io/mailr/philosophy/">Philosophy</a>
</p>

---

## Overview

Mailr is a webmail application for hosting your mail inbox and mail storage on your own server.
Mail accounts from one or more providers are connected over IMAP and SMTP, and a copy of the mail
from each account is kept in the Mailr mail store. Mail can be read and written in a web browser or
in the Android app.

Mailr also includes a calendar, task lists, notes and an AI assistant named Ambrose. Ambrose
works as a personal assistant across your mail, calendar, tasks and notes. Ambrose can find
messages and events, compile summaries, draft replies and clean up the inbox. Sending, deleting and rule changes
are held for your approval. Mailr offers a wider range of customization and
AI assistance than most webmail applications. The language and embedding models used by
Ambrose and by search can be run on your own hardware.

![The Mailr inbox with the reading pane and the calendar side panel](site/docs/assets/shots/home-hero.png)

## How Mailr handles your mail

- Mail for your addresses is received and sent by your existing mail provider. Each provider is
  connected over IMAP and SMTP, in the same way as a desktop or mobile mail client.
- No mail is received directly from the internet. An MX record, open mail ports and a sending
  reputation are therefore not required.
- New mail is copied to the Mailr mail store within seconds of arrival. Message lists, search
  results and the answers from Ambrose are based on the copy in the Mailr mail store.
- One of three modes is chosen for each account. With **Keep in sync**, Mailr and the provider
  stay in step. With **Mailr keeps the mail**, Mailr becomes the long-term home of the mail. With
  **Copy once**, an old account is copied a single time.
- The Mailr mail store is a bundled [Stalwart](https://stalw.art) server, run as a store with no SMTP
  service. The stored mail can be read by other mail applications
  over IMAP, and the built-in calendars, tasks and contacts are kept in the same mail store.
- Microsoft 365 and Outlook.com accounts cannot be connected at present, because Microsoft does not
  allow password sign-in for third-party applications. I have not tested Mailr with Microsoft 365,
  which is known to be difficult to work with.

## Key Features

### Mail
- **Accounts and import.** IMAP accounts can be connected, and Gmail accounts can be imported with
  their labels and threads. Mail archives in mbox and Maildir format can be imported through the API.
- **Reading and writing.** Mail is shown as conversations and can be organized with labels,
  categories, custom inbox sections and tabs. Messages can be snoozed, given follow-up reminders,
  scheduled for later sending or recalled with undo send, and templates are available for common
  replies.
- **Search and shortcuts.** Search accepts operators such as `from:`, `has:attachment` and
  `newer_than:7d`, and most actions have keyboard shortcuts.
- **Interface styles.** Five interface styles are available, each in a light and a dark theme. The
  styles range from a sidebar layout to a dense column view and a keyboard-driven single column.
- **Triage.** Importance and labels are learned from your own actions and your review-queue
  decisions. Filters that you write take precedence over learned decisions, and the reason for each
  automatic decision is shown.

### Calendar, tasks and notes
- **Calendars.** Calendars can be kept in Mailr or on a CalDAV server. Meeting invitations can be
  answered from the message, and free times can be found for a meeting.
- **Quick add.** Events and tasks can be created from one line of text, such as
  `Lunch with Dana fri 12:30 at Café Luna`, or from an email.
- **Notes.** Notes can contain checklists and tables and can be shared with other users. A separate
  Notes app for Android is also available.

### Ambrose, the assistant
- **Questions.** Questions about your mail, calendar and tasks can be asked in the Ambrose panel,
  and each answer includes links to the messages that were used.
- **Bulk review.** With the `/sweep` command, a view of mail is reviewed and the proposed moves are
  collected on one card. The proposals follow your sweep rules and the choices you made in earlier
  sweeps.
- **Routines and drafting.** Standing orders and routines can be set up, messages can be dictated,
  and drafts can be written in your own style.
- **Approvals.** By default, sending, deleting and rule changes are held for your approval.
  Approvals can be given on the web or in the Android app.

### Privacy and models
- **Local-only accounts.** Accounts can be marked as local-only. Mail from a local-only account is
  not sent to a hosted model or to an agent that uses a hosted model.
- **Model choice.** Models are connected through OpenAI-compatible endpoints, local or hosted, and
  a different model can be chosen for each task. Mailr can also be used without any model.

### Agents and the API
- A REST API and an MCP server are provided, with scoped tokens, approvals and an audit log. MCP
  clients are connected with a token.

### Android
- The Android app includes mail, calendar, tasks, reminders with full-screen alerts, approvals,
  dictation and a share sheet. Actions taken offline are sent when the phone is back online.

## Screenshots

![An inbox with custom tabs: Inbox, Financial, Later, Code and Shopping, with due dates on the tabs and rows](site/docs/assets/shots/mail-tabs-everyday-light.png)

| Calendar | Ambrose | Android |
|---|---|---|
| ![Calendar week view](site/docs/assets/shots/home-calendar.png) | ![The Ambrose panel with cited answers](site/docs/assets/shots/home-assistant.png) | ![The Android inbox](site/docs/assets/shots/home-android.png) |

## Quick Start

A Linux host with Docker, Docker Compose v2 and BuildKit (the `buildx` plugin) is required, along
with at least one existing mail account.

```sh
git clone https://github.com/murtaza-nasir/mailr.git
cd mailr/deploy
cp .env.example .env
openssl rand -base64 32    # run twice: MAILR_SECRET_KEY and MAILR_SESSION_SECRET
nano .env                  # MAILR_BASE_URL, the secrets, the passwords, the first admin
docker compose up -d
```

Place a TLS reverse proxy in front of port 8025. For a local test, set
`MAILR_BASE_URL=http://localhost:8025` instead. Then open the address set in `MAILR_BASE_URL`, sign
in as the first admin, and add a mail account under **Settings → Mail → Mail accounts**. The reverse
proxy, single sign-on, models and backups are described in the
[installation guide](https://murtaza-nasir.github.io/mailr/getting-started/install/).

## Android App

The APK is available from [GitHub Releases](https://github.com/murtaza-nasir/mailr/releases). After
installing the app, enter your server address and sign in. Setup is described in
[Install the phone app](https://murtaza-nasir.github.io/mailr/getting-started/phone/).

## Speakr

Mailr can be connected to [Speakr](https://github.com/murtaza-nasir/speakr), a self-hosted
transcription and meeting-notes application. Recordings are shown beside their meetings and emails.
From a recording, a follow-up email can be drafted and the action items can be added as tasks.
Details are in [Recordings](https://murtaza-nasir.github.io/mailr/user-guide/recordings/).

## Documentation

The documentation is available at
**[murtaza-nasir.github.io/mailr](https://murtaza-nasir.github.io/mailr/)**. The documentation
includes getting started, the user guide, the Android app, the admin guide, agents and the API, and
the full reference.

## Reporting Issues

Report bugs and suggest features under [Issues](https://github.com/murtaza-nasir/mailr/issues).
Security problems should be reported privately, as described in [SECURITY.md](SECURITY.md).

## License

Mailr is dual-licensed: the [GNU AGPLv3](LICENSE) for everyone, and a commercial licence on request.
See [License](https://murtaza-nasir.github.io/mailr/license/).

## Contributing

Pull requests are welcome. **By submitting a pull request, you agree to the
[Contributor License Agreement](CLA.md).** You keep the copyright of your work. Under the agreement,
your work can be included in Mailr under both the AGPLv3 and the commercial licence. See
[CONTRIBUTING.md](CONTRIBUTING.md) for details.

Copyright © 2026 the Mailr authors. Gmail is a trademark of Google LLC. Mailr is not affiliated with
or endorsed by Google.
