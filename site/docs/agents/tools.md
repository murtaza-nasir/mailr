# Agent tools

The built-in assistant, the MCP server and the tool API share the tools below. Only the tools allowed by the token's scopes are shown to an agent. Nothing is changed by **Read-only** tools, and they are marked as read-only for MCP clients.

Tools for changes are checked by the permission engine. Many are carried out at once; others return `pending_approval` and are held for a decision. See [Approvals for agents](approvals.md).

When the id is left out, most calendar and task tools are applied to the event or task focused on screen; a request to Ambrose such as "move this to Friday" is resolved this way.

Call any tool over HTTP with `POST /api/v1/agent/tools/{name}`. See [REST API](rest-api.md#calling-agent-tools-over-http).

## Search and read mail

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `search_conversations` | Search mail with the usual search operators (`from:`, `is:unread`, `newer_than:7d`, quoted phrases). Returns conversation summaries. | `read` | yes |
| `search_semantic` | Find conversations by meaning ("the thread where we discussed the budget overrun"), each with its best matching passage. | `read` | yes |
| `find_passages` | Find the exact paragraphs that answer a question, optionally inside one conversation, each with a `url` that opens the email at that spot. | `read` | yes |
| `get_conversation` | Read a whole conversation: every message with sender, recipients, date, attachments and text, with quoted history removed. | `read` | yes |
| `read_screen` | Read the subjects and senders of the mail on the user's screen: the open conversation, other tabs, the checked rows and the list's rows. The prompt names them by id only. | `read` | yes |
| `list_view` | Page through every conversation of a list in short rows (sender, subject, date, preview), up to 100 a page. By default the list on the user's screen, with its search: on an inbox tab every section of the tab, or the one section shown alone. `{"view": "inbox"}` is the Inbox across all tabs. | `read` | yes |
| `get_message_body` | Read one message in full, including quoted history, with its attachments listed by index. | `read` | yes |
| `list_attachments` | List the attachments of a message or of a whole conversation: index, file name, type, size, and whether the file is inline. | `read` | yes |
| `get_attachment` | Fetch one attachment by index or file name. Images are returned as images, turned upright, scaled down (`max_px`, default 1600) and stripped of metadata such as location. PDFs are returned as text plus one rendered page (`page`). Text files, CSV, JSON, calendar files, HTML and attached emails are returned as text. For other files, only their name, size and type are returned, and files over 25 MB are refused. | `read` | yes |
| `build_context` | Gather the material for a reply: the thread, the people in it, related conversations, your past replies to them, open questions and nearby calendar times. | `read` | yes |
| `get_signals` | Triage signals: conversations waiting for your reply or on someone else, and conversations with a deadline. | `read` | yes |
| `get_brief` | Read the brief a standing order left on a conversation. | `read` | yes |
| `list_labels` | List your labels with unread and total counts. | `read` | yes |
| `get_outbox_status` | The status of a message sent with `send_draft`: queued, awaiting approval, sent, cancelled, rejected or failed. | `read` | yes |
| `get_approval_status` | The status of an action waiting for approval, with its result once it has run. | `read` | yes |

## People

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `get_person` | A correspondent's profile: name, organisation, how often you write, recent conversations and threads waiting on your reply. | `read` | yes |
| `find_people` | Find people you correspond with by name or address, best match first, with organisation, title and phone number. | `read` | yes |
| `list_groups` | List your contact groups and their members. Put `group:<name>` among recipients or guests to address a whole group. | `read` | yes |

## Drafts and sending

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `list_drafts` | Your drafts: compose windows open on screen (with unsaved text) and saved drafts. | `read` | yes |
| `get_draft` | Read one draft in full, or the text of an open compose window. | `read` | yes |
| `create_draft` | Create a draft, new or as a reply, reply all or forward. It is saved in Drafts and never sent. | `draft` | no |
| `update_draft` | Change a draft in place: small find-and-replace edits, a new body, subject or recipients. Pass `base_version` (the version you read): changes saved since then are merged in, and an overlapping change is refused. The change is shown at once in an open compose window, with Undo; new recipients are held until you accept them. | `draft` | no |
| `send_draft` | Send a draft, now or at a set time. It is checked at the send gate: held for a short time, and waiting for approval unless a rule allows it. | `draft` | no |
| `update_brief` | Rewrite the brief on a conversation (shorter, another tone or language), or restore its original text. | `draft` | no |

## Organise mail

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `archive` | Archive conversations. They are removed from the inbox and stay searchable. Takes ids, or `scope` (the list on screen, or a view and a search) with `except_ids`; a scope asks once and runs in the background with Undo. From an API token, a scope is always held for approval. | `label` | no |
| `apply_label` | Add or remove a label on conversations, found by name (`Clients/Lumen` for nested labels), optionally creating it. | `label` | no |
| `modify_conversations` | Mark read or unread, star, mark important, move back to the inbox, trash, report spam, snooze or mute conversations. Takes ids, or `scope` with `except_ids`, like `archive`. | `label` | no |
| `review_mail` | Review every conversation in a scope and propose what to trash, archive, label or mark read, on one review card. The scope `"screen"` is the list on screen as for `list_view`; the result names it ("Inbox tab (all sections)", "Inbox: Unread", "Inbox (all tabs)") with its size. Runs in the background; starred, important and replied-to mail, people the user writes to and invitations are kept without asking. The built-in assistant only. | `label` | no |
| `show_in_tab` | Make conversations show in one tab of the inbox layout, named by the tab's name: adds the tab's label or category and removes the labels of tabs that would take them first. | `label` | no |
| `manage_label` | Create, rename, nest, recolour, show or hide, or delete a label. | `label` | no |
| `list_screened_senders` | The first-time senders waiting in the [Screener](../user-guide/screener.md), with who a decision covers, the number of messages waiting, the authentication hint and the latest subject and preview. Mail content is marked untrusted. | `read` | no |
| `screener_decide` | Allow, block or label a sender waiting in the Screener. Always waits for approval (`mail.screener`). | `label` | no |
| `list_subscriptions` | List mailing lists and bulk senders: mail in the last 30 and 90 days, how much was read, the last one read, replies, and whether one-click unsubscribe is available. | `read` | yes |
| `unsubscribe` | Unsubscribe from one subscription by one-click request or by mail to the list's own address. Always waits for approval; for a list with only a web page, the link is returned. | `send` | no |
| `list_cleanup_groups` | Group mail for a cleanup by sender, domain, list or category, in a scope (archived, Inbox or all; older than; uncategorised; bulk only), with counts, sizes, what is left alone and any suggested category. | `read` | yes |
| `propose_cleanup` | Propose one action on one group (archive, label, trash, mark read, category, or a filter for future mail). Always waits for approval; the approval shows the counts, what is left alone and sample subjects. Starred, important and replied-to conversations are left alone unless included. | `label` | no |

## Configuration

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `get_configuration` | Read how Mailr is set up: settings with their allowed values, labels, filters, layouts, identities and skills. | `read` | yes |
| `manage_filter` | Create, change, delete or apply a filter. A filter is a search matched against new mail, with actions to label, archive, star, mark read or important, set a category, or send to Spam or Trash. A new or changed search is first counted against the last 6 months of mail (`matches_existing`; `check_search` when it matches none). | `rules` | no |
| `manage_layout` | Activate an inbox layout preset or one of your own, save a custom layout, or delete one. | `rules` | no |
| `manage_order` | Create, change, pause, resume, delete or list standing orders and routines. A standing order for new mail with a search gets the same match check as `manage_filter`. | `rules` | no |
| `update_settings` | Change an allowed set of your settings, such as density, reading pane, theme, undo-send time and notifications. | `rules` | no |
| `update_signature` | Set the signature of one of your send-as identities. | `rules` | no |
| `update_profile` | Update Ambrose's profile of you ("About you"): name, sign-off, role, time zone (the account's one time zone, as in Settings → You → Region), languages and writing preferences. | `rules` | no |

## Calendar

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `list_calendar` | List events for a day or span (`when`: "tomorrow", "next week") or between two times, recurrences expanded. Times are in your zone. | `read` | yes |
| `search_events` | List or search events in a day, span or date range. Times are in your zone. | `read` | yes |
| `search_calendar` | Search events and tasks with words and operators (`cal:`, `list:`, `tag:`, `after:`, `before:`, `due:`, `is:recurring`). | `read` | yes |
| `get_event` | One event with its description, guests, reminders and recurrence. | `read` | yes |
| `meeting_prep` | The meeting prep of an upcoming event: the last recording and its open action items, recent threads with the guests, open questions and waiting-on items, related tasks, memories and recent files. Mail content is marked untrusted, and only what its accounts and data class allow is shown to a token. | `read` | no |
| `get_free_busy` | Your busy times between two times, in your zone, without titles. | `read` | yes |
| `find_time` | Free slots in your calendars inside working hours, best first. | `read` | yes |
| `get_calendar_defaults` | Your default calendar and task list, and every calendar and task list. | `read` | yes |
| `create_event` | Create an event, optionally with guests (who get invitations), a video link, reminders and recurrence. | `calendar` | no |
| `update_event` | Change an event, one occurrence or the series, or move it to another calendar. | `calendar` | no |
| `delete_event` | Delete an event, one occurrence, this and following, or the series. | `calendar` | no |
| `cancel_event` | Cancel a meeting you organise. It stays in the calendar marked cancelled and the guests get a cancellation. | `calendar` | no |
| `rsvp` | Answer an invitation: accepted, declined or tentative, from the email or from the calendar. | `calendar` | no |
| `manage_calendars` | Create, rename, recolour or delete a calendar. | `calendar` | no |
| `list_reminders` | Reminders that fired and wait, and those due soon. | `read` | yes |

## Tasks

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `list_tasks` | List tasks by smart list (today, overdue, upcoming, scheduled, no date, high priority, from email, all, completed), by list or by tag. | `read` | yes |
| `search_tasks` | List or search tasks by status, words, list and due date. | `read` | yes |
| `create_task` | Create a task with due and start dates, notes, priority, repeat, tags, reminders (minutes before the due, or a date and time), a parent task and a link to an email. A reminder needs a due. The result lists the reminders that will fire (`will_remind`). | `calendar` | no |
| `update_task` | Change a task, move it to another list, or reopen it. `reminders` is the whole list: it replaces relative and absolute reminders, and `[]` removes them all. An unknown field, or a change that did not take, is an error that names the field. The result lists the reminders that will fire (`will_remind`) and the fields changed. | `calendar` | no |
| `complete_task` | Complete a task. A repeating task is moved to its next date; with `skip`, it is moved on without being completed. | `calendar` | no |
| `move_task` | Move a task to another list, under a parent task, or before or after another task. | `calendar` | no |
| `delete_task` | Delete a task, optionally with its subtasks. | `calendar` | no |
| `bulk_tasks` | One action on many tasks: complete, reopen, reschedule, move, tag, set priority or delete. | `calendar` | no |
| `manage_lists` | Create, rename, recolour or delete a task list. | `calendar` | no |

## Notes and checklists

The note tools are for your [notes and checklists](../user-guide/notes.md). An agent has your own
access on each note: on a note shared with you at view level it can only read. For a write tool, the note is
found by id or by title ("the shopping list" also matches the list marked Shopping); when you name none,
the note open on screen is used. A write tool's result has ids, counts and the text written, never
another person's text. Every note read is untrusted: a note can hold other people's items, pasted
mail or a web page. A local-only note is never sent to a hosted model; in the result, some notes are
then reported as hidden.

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `list_notes` | List notes and checklists with their ids, titles, item counts, labels and pins. Filters: kind, label, pinned, archived, in Trash, shared, words. With `templates`, the templates instead. | `notes` | yes |
| `get_note` | Read one note: a text note's body, or a checklist's items with their ids, sections, quantities, checked state and who added them; with `usual`, the usual items that are due. | `notes` | yes |
| `search_notes` | Search notes by words and by meaning, with the notes search operators; for an item match, the item is returned. | `notes` | yes |
| `note_history` | A note's versions of the last 30 days, one version's content, or restore one. | `notes` | no |
| `create_note` | Make a note or a checklist. List lines go through the item grammar (quantities, sections); a link to the email it came from is optional. With `template`, start from a template (its id or name), which may make a table. | `notes` | no |
| `update_note` | Change a note's title or text (whole, or find and replace), colour, the Shopping list switch, its kind, pin, archive and labels, link it to an email, or set your reminders on it (`remind_at`). A reminder is never snoozed or dismissed with it. | `notes` | no |
| `checklist_items` | A list of changes to one checklist: add items (to a section, or where each was last time), add usual items, check, uncheck, edit, move, delete, make a task from an item, add, rename, move, order and delete sections, clear the checked items, uncheck everything. | `notes` | no |
| `delete_note` | Move a note to Trash, or bring it back. Never deletes forever. | `notes` | no |
| `manage_note_labels` | List, create, rename, recolour and delete your note labels. | `notes` | no |

## Memory and skills

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `recall` | Search Ambrose's memory: memories about people, organisations, projects, preferences and commitments. Only memories that fit the request come back, each with why it matched. | `read` | yes |
| `get_entity` | Everything stored in memory about one person, organisation, project, topic, thread or place. | `read` | yes |
| `remember` | Save a durable note about you or about a person, organisation or project. | `memory` | no |
| `forget` | Delete one note from memory. | `memory` | no |
| `upsert_entity` | Create or update a person, organisation, project, topic, thread or place in memory. | `memory` | no |
| `list_skills` | List your skills. | `read` | yes |
| `get_skill` | Read one skill in full. | `read` | yes |
| `use_skill` | Load a skill's instructions and follow them for the rest of the chat. | `read` | yes |
| `create_skill` | Save a new skill, invoked as `/name`. | `memory` | no |
| `update_skill` | Change a skill's instructions, description, title or name, or turn it off. | `memory` | no |

## Recordings (Speakr)

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `list_recordings` | List your Speakr meeting recordings, newest first, with filters for words, dates, participants, tags and links. | `recordings` | yes |
| `search_recordings` | Search recordings by words in titles, participants, tags, summaries and transcripts. | `recordings` | yes |
| `get_recording` | One recording: its details and the parts you ask for (summary, notes, events, speakers, links). | `recordings` | yes |
| `get_summary` | The Speakr summary of a recording. | `recordings` | yes |
| `get_transcript` | A recording's transcript in a time range, optionally one speaker's turns or the turns containing some words. | `recordings` | yes |
| `recordings_for_event` | Recordings linked to, or suggested for, an event occurrence, or the previous meeting of a series. | `recordings` | yes |
| `propose_action_items` | Propose the action items of a recording with owners and due dates. Nothing is created. | `recordings` | yes |
| `ask_recording` | Ask Speakr's own model a question about one recording, with cited moments. | `recordings` | yes |
| `link_recording` | Link a recording to an event occurrence, a conversation or a task, or confirm a suggested link. | `recordings` | no |
| `update_recording` | Change a recording in Speakr: its title, participants, tags, or a line added to its notes. | `recordings` | no |
| `draft_follow_up` | Draft the follow-up email of a meeting recording to its guests. It is never sent. | `recordings` | no |
| `send_to_speakr` | Send an audio or video attachment of an email to Speakr for transcription. | `recordings` | no |
| `share_recording` | Create or reuse a public Speakr link to a recording. Always requires approval. | `recordings` | no |

## The web

The two tools below are available only while a web search provider is set up and **Web search** is turned on in the user's access policy (see [Web search](../admin/web-search.md)). Both are refused in a chat where local-only mail was read, and for a local-only token: nothing from local-only accounts is put into a web query.

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `web_search` | Search the web through the admin's providers, tried in order until one returns results. Takes `query`, `count` (1 to 20, default 8) and `freshness` (`day`, `week`, `month`, `year`). Returns titles, links, sites and snippets, each with a `cite` value. Titles and snippets are marked untrusted. | `web` | yes |
| `read_page` | Read one web page as plain text, cut at 20,000 characters. https only, through the egress proxy when one is set. No scripts are run by the built-in reader; when it fails, the admin's [page readers](../admin/web-search.md#page-readers) are tried. The built-in assistant opens only a page `web_search` returned in the chat, a link you wrote in your message, or a link in your own part of your own email. A token's agent picks its own pages. | `web` | yes |

The built-in assistant cites a web source as `[[web:<url>]]`. The citation is kept only when the address appears in a result Ambrose read in that chat. Both tools are checked against the `web.search` permission class ([Approvals for agents](approvals.md)).

## Tables

The `notes` scope is required for the table tools. Ambrose's access to each table is the same as the user's: it can read and change exactly what the user can. Tables marked local-only stay hidden from a hosted model. Text in table results is marked untrusted, because a shared table can hold other people's rows. A table is found by its id or its name; when two tables have the same name, an error is returned and the id is required.

| Tool | Purpose | Scope | Read-only |
|---|---|---|---|
| `list_tables` | Your tables with their ids, names, row counts, columns and labels. With `query`, names and rows are searched for words. | `notes` | no |
| `get_table` | One table: its columns, its views and a page of rows (up to 200) with row ids and typed values, in a view's order or with your own filters and sort. | `notes` | no |
| `table_stats` | Totals and counts for a table: sums, averages, medians, dates, checked shares and counts per option, for all rows or a filter, optionally per group. | `notes` | no |
| `table_history` | A table's changes in sessions, one row's changes and earlier values, deleted rows; bring back deleted rows or a deleted column, put a row or a cell back, or restore the whole table to a point in time. | `notes` | no |
| `table_rows` | Add, change, duplicate or delete rows. Values are typed by column; a value that does not fit is refused, and the column and the type are named in the error. Rows are given by id or by their primary value. | `notes` | no |
| `table_schema` | Create a table, rename it, add, rename, retype, move or delete columns, change options and column settings, choose the primary column, save or delete a view. | `notes` | no |
| `table_state` | A table's colour, pin, labels and archive, a link to an email, event, task or recording, or its way back from Trash. | `notes` | no |
| `delete_table` | Move a table to Trash. | `notes` | no |

The permission classes are `tables.rows` (add and change rows; allowed), `tables.rows_delete` (delete rows; asks) and `tables.schema` (creating tables, adding columns and saving views are allowed; other column and view changes and a restore to a point in time ask). Moving a table to Trash is `notes.delete` (asks), and its colour, pin, labels and links are `notes.edit`. After Ambrose has read email or another untrusted result in a chat, a row write of text, a new option or a link into a cell is held for approval. Ambrose cites a table as `[[table:<id>]]` and a row as `[[table:<id>#<row_id>]]`.

## Results and citations

- Email text in a result is marked untrusted. Agents must not follow instructions inside it.
- Results have `url` fields for opening a conversation, a message or a passage in Mailr.
- The built-in assistant cites messages and passages in its answers, and every quote is checked against the message before the answer is shown.
- A note is cited as `[[note:<id>]]`, one item of a list as `[[note:<id>#<item id>]]`. The note can be opened from the chip, with the item focused. A note you cannot see is never cited.
