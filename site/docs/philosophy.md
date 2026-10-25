# Philosophy

The design of Mailr follows the principles below. Each principle is stated with the mechanism that
implements it.

## Mail storage

Mailr is a mail client for any number of addresses from any providers. Each provider keeps
receiving and sending the mail for its own addresses, so delivery does not depend on a home
connection, and outgoing mail keeps the provider's sending reputation. New mail is collected within
seconds of arrival.

In the mode "Mailr keeps the mail", the mail also stays at the provider unless deletion there is turned on. A message is then removed from the provider only after it has
been stored and included in a backup. A Maildir export of each user's mail is also written on a
schedule. Maildir is a plain format that any mail tool can read, so the mail remains accessible
without Mailr's database.

## Learned sorting

Importance, categories and labels are learned from the user's own actions: messages opened, answered,
starred and archived, and verdicts given in the review queue. Filters written by the user take
precedence over any model. The reason for each automatic decision is recorded, and each decision can
be undone with one click. Corrections are used to train only that user's models; mail from one user
is never used to train a model for another.

## Optional models

All core functions work without a language model. Without models, search is by keyword and sorting
is by filters. Models are configured per task, and any OpenAI-compatible server, local or hosted, can
be used. Model work is done in the background; a request waits on a model only when it is a direct
question to Ambrose.

## Local-only accounts

Some mail must not leave the local network: student records, patient notes, or a board's private
papers, for example. An account can be marked **local-only**. Mail in such an account is sent only to
models marked as local, and it can be read only by agents holding a local-only token. The rule is
checked on every model call and every API request. It also follows what was read: once a request or a
background job has read something local-only, its later model calls stay local too. A model counts as
local only while its server's addresses are on the local network, and that is checked on every
connection.

## Approval of assistant actions

Ambrose reads, searches, drafts and organises mail. Actions with consequences (sending, deleting,
unsubscribing, changing filters) are held until they are approved. Each held action is shown, with
the exact message or change, on an approval card on the web or as a phone notification. Sending can
be cancelled during a short hold window, and the first message to a new address always requires
approval. Mail content is treated as untrusted text, so instructions embedded in an email are not
followed.

## One action layer

Each action, such as archive, snooze, reschedule, approve or dictate, is implemented once and behaves
identically in the web app, on the phone, in a notification, in Ambrose and through the API. External
agents are given the same tools as the built-in assistant, under the same rules, with scoped tokens
and an audit log. Actions are idempotent, so the queued actions of a phone that was offline can be
replayed safely.

## The mail client

Conversations, labels, custom inbox sections, categories, snooze, scheduled send and undo send are
provided. Search uses Gmail-style operators, and the keyboard shortcuts are Gmail-style as well. The
overall layout is set by one of five interface styles, from a sidebar layout to a dense column view
or a keyboard-driven single column. With quick add, an event or a task is created from one typed line,
using a fixed grammar and no model.

## Several users on one server

Mailr is multi-user. Accounts, labels, filters, models and tokens are kept per user, and no user's
mail is visible to another user or to another user's Ambrose. Sign-in is through any OIDC provider or
with a password. The whole system is deployed as one Docker Compose stack.

## Licence

Mailr is free software under the GNU AGPLv3, with a commercial licence available on request.
