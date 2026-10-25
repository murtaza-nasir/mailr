# Settings

This page lists every per-user setting, grouped by area. **Settings** in the web app has nine groups: You, Look,
Mail, Calendar & tasks, Notes & tables, Notifications, Ambrose, Agents & API and (for admins)
Admin. See [Settings](../user-guide/settings.md) in the User guide. Settings are changed there, in
**Quick settings** (the gear at the top), or through the API with
`PATCH /api/v1/me/settings`. They apply on every device; a few are kept per phone in
the Android app (see [App settings](../android/settings.md)).

## Mail list and reading

Where: Settings → Mail → Reading; Settings → Look (Text & spacing, This layout); Quick settings.

| Key | Default | Values | Description |
|---|---|---|---|
| `conversation_view` | `true` | on / off | Group messages into conversations, or list each message on its own. |
| `page_size` | `50` | number | How many conversations are shown on a page of the list. |
| `density` | `default` | `default`, `comfortable`, `compact` | List density: default, comfortable or compact. |
| `reading_pane` | `none` | `none`, `right`, `below` | Where a conversation opens: full page, to the right of the list, or below it. |
| `reading_pane_by_theme` | none |  | The reading pane position remembered per theme. |
| `auto_advance` | `list` | `list`, `newer`, `older` | Where to go after you archive or delete an open conversation. |
| `images` | `always` | `always`, `ask`, `never` | Remote images: always (through Mailr's image proxy), ask first, or never. |
| `sender_pictures` | `false` | on / off | Look up sender pictures (BIMI, Gravatar, Libravatar). Off by default. |
| `importance_markers` | `true` | on / off | Show the importance marker next to important mail. |
| `keyboard_shortcuts` | `true` | on / off | Turn keyboard shortcuts on or off. |

## Inbox and navigation

Where: Settings → Mail → Inbox, the navigation.

| Key | Default | Values | Description |
|---|---|---|---|
| `inbox_type` | `default` |  | Which inbox layout is active (Default, Important first, Unread first, Priority, or a custom layout). |
| `inbox_count` | `first_tab` | `first_tab`, `all` | The number by Inbox in the navigation: unread mail in the layout's first tab ("Inbox tab only"), or every unread conversation in the Inbox ("All tabs"). In the apps, it is read from `GET /api/v1/inbox/count`. |
| `categories` | `["primary", "promotions", "social", "updates", "forums"]` | list | Which inbox categories (Primary, Promotions, Social, Updates, Forums) are shown as tabs. |
| `priority_sections` | `["important", "unread", "everything"]` |  | The sections of the Custom sections inbox type, in order. |
| `multiple_inboxes` | none |  | Extra inbox panes, each a saved search. |
| `nav_favorites` | `["inbox", "sent", "drafts"]` | list | The favourites in the mail navigation, in order. |
| `account_folders` | `false` | on / off | Show each account separately: with two or more mail accounts, the navigation gets a group per account. |
| `account_folder_pins` | `{}` | account id → list of label ids | The folders pinned to each account's group, in order. They show above **More folders**. Unknown accounts and labels are dropped when it is saved. |
| `account_folders_more_open` | `[]` | list of account ids | The accounts whose **More folders** is open. |
| `account_folders_show_unread` | `false` | on / off | Show folders with unread mail above **More folders** until they are read. |
| `account_folders_hint_dismissed` | `false` | on / off | The hint in the navigation that offers **Show each account separately** was dismissed (or the setting was changed). It does not come back. |
| `side_panel` | none | `none`, `calendar`, `tasks`, `agent` | The side panel shown next to mail: none, calendar, tasks or Ambrose. |

## Archiving

Where: Settings → Mail → Reading → Archive.

| Key | Default | Values | Description |
|---|---|---|---|
| `archive_also_removes` | `[]` | list of labels | Labels also removed whenever you archive. |
| `archive_removes_current_label` | `true` | on / off | When you archive, the label of the list you archived from is also removed. |

## Writing and sending

Where: Settings → Mail → Writing.

| Key | Default | Values | Description |
|---|---|---|---|
| `default_reply` | `reply` | `reply`, `reply_all` | Whether Reply or Reply all is the default. |
| `undo_send_seconds` | `10` | number | How long Undo is offered after you press Send. |
| `send_and_archive` | `false` | on / off | Show a Send and archive button in replies. |
| `signature_placement` | `below` | `below`, `above` | Put your signature below or above the quoted text in replies. |
| `reply_suggestions` | `true` | on / off | Offer short suggested replies under personal email. |
| `vacation` | off |  | The away message: dates, subject, text, contacts only, and how often to resend. |

## Sorting, importance and spam

Where: Settings → Mail → Inbox (VIP senders), Settings → Mail → Labels (Learn from my choices),
Settings → Mail → Spam & Screener. With **Strictness** on the spam page, three of the
numbers are set at once: Gentle (`spam_upstream_unknown_threshold` 0, `spam_junk_score` 25), Normal (4 and 15, the
default) and Strict (3 and 10). The numbers themselves are under **Fine-tune the numbers**.

| Key | Default | Values | Description |
|---|---|---|---|
| `llm_triage` | `true` |  | Let a language model help sort new mail (categories, importance, signals). |
| `vip` | `[]` | list | VIP senders: their mail is always important, and a notification for it is shown at once. |
| `label_recency` | `{"enabled": true, "half_life_months": 12}` |  | Label learning: recent labels count more; how quickly older ones fade. |
| `auto_label` | `true` | on / off | Learn from my choices: the labels you give are used to learn which labels to suggest and, when the prediction is sure enough, to apply them. |
| `auto_label_precision` | `0.9` | 0.5 to 0.99 | How sure a label model must be before a label is applied automatically. Cautious is 0.95, Balanced 0.9, Eager 0.8. |
| `auto_label_min_examples` | `30` | 3 to 1000 | How many of your own examples a label must have first. Cautious is 50, Balanced 30, Eager 15. |
| `spam_classifier` | `junk` | `off`, `score`, `junk` | The mode of Mailr's own spam filter: off, score only, or move to Spam. |
| `spam_junk_score` | `15.0` | number | The spam score at which mail is moved to Spam. |
| `spam_upstream_unknown_threshold` | `4.0` | number | The provider spam score from which mail from new senders is moved to Spam (off at 0). |
| `spam_suspect_score` | `5` | number | The spam score from which mail from new senders is not read by any model (off at 0). |
| `screener` | `false` | on / off | **Screen first-time senders**: hold mail from first-time senders in **Mail → Screener** until you allow, block or label them. Change it with `PUT /api/v1/screener/settings`; turning it off releases the held mail. See [The Screener](../user-guide/screener.md). |
| `screener_accounts` | `{}` | account id: on / off | Which mail accounts the Screener covers. Empty: personal accounts are screened; university, government, work and local-only accounts are not. Changed with the same route as `screener`. |

## Notifications

Where: Settings → Notifications. One choice for every device: Web Push, open web windows and the
Android app. In a browser, you can only turn mail off for that browser.

| Key | Default | Values | Description |
|---|---|---|---|
| `mail_notifications` | `important` | `off`, `vip`, `important`, `primary`, `all` | Which new mail you are notified about: none, VIP senders only, important mail, important mail and the Primary tab, or all new mail. Replaces `desktop_notifications`, which is still accepted from older apps. |
| `notify_approvals` | `true` | on / off | Notify when an agent or Ambrose waits for approval, or a message could not be sent. |
| `notify_assistant` | `true` | on / off | Notify about messages from Ambrose and standing orders. |
| `notify_accounts` | `true` | on / off | Notify once when the password of a mail account must be entered again. |

## Look

Where: Settings → Look, Quick settings. Label colours are under Settings → Mail → Labels.

| Key | Default | Values | Description |
|---|---|---|---|
| `ui_theme` | `classic` | text | The look of the web app (Classic and the other themes). |
| `ui_accent` | the theme's | `{preset, seed, harmony}` or null | The colours of the style: one of its presets (`cards.teal`), or a colour of your own (`seed`, a hex colour) with partner colours (`harmony`: `single`, `complementary`, `analogous`, `triadic`, `split`). Null is the style's own colours. The Android app can follow it. |
| `theme` | `system` | `system`, `light`, `dark` | Light, dark, or follow the system. |
| `card_bundles` | the theme's | `flat`, `always`, `never` | Whether similar mail is bundled in the Cards theme. |
| `label_scheme` | `default` |  | The colour scheme of the label colour picker. Change it with `POST /api/v1/labels/colours`, which recolours the labels too. |

## Region

Where: Settings → You → Region. The language, and one time zone for everything: the calendar, tasks,
reminders, tables and Ambrose. `timezone` on `GET` and `PUT /api/v1/assistant/profile` is the same setting.

| Key | Default | Values | Description |
|---|---|---|---|
| `language` | none (Automatic) | a language tag such as `en`, `de`, `pt-BR` | The language of Mailr's words on every device, of notifications, of Activity and of Ambrose's replies. Invitations and replies to invitations sent through Mailr are written in it too. Without it the web follows the browser, the phone apps follow the phone, Activity and error messages follow the browser's `Accept-Language`, and notifications and invitation mail are in English. Mailr has `en`, `de`, `es`, `fr`, `pt-BR`, `ru` and `zh-Hans` ([Languages](../admin/languages.md)); another tag reads as English, and Ambrose still replies in it. |
| `timezone` | your device's zone | an IANA zone | Your time zone. |
| `time_format` | `auto` | `12h`, `24h` | 12-hour, 24-hour, or follow the device. Ambrose writes times in this clock; with `auto`, in the clock of the device last used. |
| `week_start` | your locale's | 0 to 6, or a weekday | The first day of the week. |
| `date_order` | `mdy` | `mdy`, `dmy` | How a date such as 12/10 reads in tables and imports: month first or day first. |
| `currency` | `USD` | an ISO 4217 code | The currency of a new currency column. |

## Assistant

Where: Settings → Ambrose (Briefs & replies, Models, Trusted addresses, Standing orders & routines, Sweep); `advanced_settings` is the switch at the top of Settings.

| Key | Default | Values | Description |
|---|---|---|---|
| `models` | the server defaults |  | The model used for each assistant task. |
| `advanced_settings` | `false` | on / off | Show the advanced parts of Settings: Agents & API, detailed filters, learning detail and the decisions lab. Admins always see them. |
| `brief_position` | `top` | `top`, `side` | Where an assistant brief sits: above the messages or beside them. |
| `local_models_only` | `false` | on / off | **Local models only for me**: Ambrose and every other model feature use only this server's local models for your mail, notes and tables. An admin's rule for everyone can lock it on. Change it with `PUT /api/v1/me/local-models`. See [Models](../admin/models.md). |
| `handled_elsewhere` | `[]` | list of addresses | **Addresses for other assistants** (under Trusted addresses): your own addresses that another program reads and acts on. Mail sent only to them is left alone by standing orders, unless an order names the address. At most 20. See [Addresses for other assistants](../user-guide/forwarded-notes.md#addresses-for-other-assistants). |
| `sweep_rules` | empty | text, at most 2000 characters | **Your rules** (under Sweep): your standing rules for every bulk review (`/sweep`, a review you ask for in the chat, a routine's review). They are followed in every review before what you ask for in one review; where the two disagree, the request for that review wins. See [Clean up a whole view](../user-guide/assistant.md#clean-up-a-whole-view). |
| `meeting_prep_digest` | `false` | on / off | **Add today's meetings to digests** (under Routines): a routine's digest ends with today's remaining meetings that have guests, each with a link to its meeting prep. See [Standing orders](../user-guide/standing-orders.md). |

## Calendar

Where: Settings → Calendar & tasks.

| Key | Default | Values | Description |
|---|---|---|---|
| `calendar_default_duration` | `60` |  | The length of a new event, in minutes. |
| `calendar_snap` | `15` |  | The minutes a dragged event snaps to. |
| `calendar_conflicts` | `true` |  | Show an 'Overlaps' line for events that clash. |
| `calendar_dim_past` | `false` |  | Dim events that have ended. |
| `calendar_week_numbers` | `false` |  | Show week numbers. |
| `calendar_working_hours` | `09:00–17:00, Monday to Friday` |  | Your working hours and days (used by Find a time). |
| `calendar_view` | `today` |  | The view shown when Calendar is opened: the last one chosen (`today`, `day`, `ndays`, `workweek`, `week`, `month`, `schedule`). |
| `calendar_share_free_busy` | `false` |  | Share my free/busy with my groups: people in your groups see when you are busy in Find a time (busy times only). |
| `calendar_video_template` | `https://meet.jit.si/mailr-{id}` |  | The video call link added to events ({id} becomes a room name). |
| `calendar_add_new_invites` | `off` |  | Add new invitations to the calendar as 'needs action' (off by default). |
| `morning_hour` | `9` |  | The hour of 'morning' reminders and date-only reminders. |

## Reminders

Where: Settings → Notifications, Settings → Calendar & tasks.

| Key | Default | Values | Description |
|---|---|---|---|
| `reminders.events` | `true` |  | Reminders for calendar events. |
| `reminders.tasks` | `true` |  | Reminders for tasks. |
| `reminders.default_timed_event` | `10` |  | The reminder an event gets when it has none (minutes before). |
| `reminders.default_all_day_event` | none |  | The reminder an all-day event gets when it has none. |
| `reminders.default_task` | `true` |  | Remind me of a task at its due time when it has no reminder. |
| `reminders.morning_hour` | `9` |  | The hour date-only reminders fire. |
| `reminders.missed` | `true` |  | Show reminders that fired while you were away. |
| `reminders.repeat` | `{"enabled": false, "every_min": 10, "max": 3}` |  | Remind again every few minutes until you act (off by default). |
| `reminders.sound` | `false` |  | Play a sound with a reminder. |
| `reminders.web` | `{"desktop": true, "push": false}` |  | Where web reminders appear: desktop notifications and browser push. |

## Changing settings from the API or an agent

Send only the keys you change:

```http
PATCH /api/v1/me/settings
Content-Type: application/json

{"density": "compact", "reading_pane": "right", "undo_send_seconds": 20}
```

Only the keys on this page are accepted. A key with its own route (layouts, the Screener, Local
models only, the label colour scheme, model choices, the setup guide's progress) is refused with
the route to use, and any other key is refused with `400`: Mailr keeps some state of its own beside
the settings (what it learned, calendar bookkeeping, sign-in state), and no client can set it.

Agents change a safe subset with the `update_settings` tool (see [Agent tools](../agents/tools.md)).
