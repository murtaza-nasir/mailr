// Types mirror the REST API. Keep in sync with the backend contract.
//
// The types of the typed routes (backend mailr/api/contract) are generated into schema.gen.ts by
// tools/api-types; they are re-exported here under their usual names. Change those in the backend
// model, never here. The rest of this file is still written by hand (backend/README.md, "Typed API contract").

import type * as Api from './schema.gen'

export type Scope = 'read' | 'label' | 'draft' | 'send' | 'delete' | 'rules' | 'calendar' | 'memory' | 'recordings' | 'notes' | 'profile' | 'web' | 'admin'
export const ALL_SCOPES: Scope[] = ['read', 'label', 'draft', 'send', 'delete', 'rules', 'calendar', 'memory', 'recordings', 'notes', 'profile', 'web', 'admin']

export interface User {
  id: number
  email: string
  name: string
  role: 'admin' | 'user'
}

export type InboxType = 'default' | 'simple' | 'important_first' | 'unread_first' | 'starred_first' | 'priority' | 'multiple' | `layout:${string}`

/** Inbox layouts (backend core/layouts.py): optional tabs, each with one or more sections. */
export type LayoutFilter = 'important' | 'important_unread' | 'unread' | 'starred'
/** A tab of the active layout as the "Show in tab" menu offers it (backend core/tabplace.py). */
export interface ShowInTabTarget {
  id: string
  name: string
  icon: string | null
  /** first: the Inbox tab; label / category / inbox: what picking it sets; disabled: see reason. */
  kind: 'first' | 'label' | 'category' | 'inbox' | 'disabled'
  label_id: number | null
  category: string | null
  reason: string | null
}
export interface ShowInTabChoices {
  layout: string | null
  tabs: ShowInTabTarget[]
  /** The tab every given conversation shows in now, or null (none, or not the same one). */
  current: string | null
  placements: { conversation_id: number; tab: string | null }[]
}
export type PrioritySection = 'important' | 'important_unread' | 'unread' | 'starred' | 'everything'

export interface Settings {
  inbox_type: InboxType
  priority_sections: PrioritySection[]
  reading_pane: 'none' | 'right' | 'below'
  density: 'default' | 'comfortable' | 'compact'
  theme: 'system' | 'light' | 'dark'
  /** The web app's language (Settings → You → Region); absent follows the browser. */
  language?: string | null
  conversation_view: boolean
  undo_send_seconds: number
  categories: string[]
  importance_markers: boolean
  keyboard_shortcuts: boolean
  /** Remote images: always (through mailr's image proxy), ask first, or never. */
  images: 'always' | 'ask' | 'never'
  send_and_archive: boolean
  default_reply: 'reply' | 'reply_all'
  signature_placement: 'below' | 'above'
  /** The away message (backend core/vacation.py). */
  vacation?: { enabled: boolean; start: string; end: string; subject: string; message: string; contacts_only: boolean; resend_days?: number }
  page_size: number
  /** What new mail notifies, on every device: web push, open windows and the Android app
   * (backend core/notifyprefs.py). */
  mail_notifications: MailNotify
  /** Approvals and failed sends notify (every device). */
  notify_approvals: boolean
  /** Messages from the assistant and standing orders notify (every device). */
  notify_assistant: boolean
  /** A mail account that needs its password again notifies (every device). */
  notify_accounts: boolean
  multiple_inboxes?: { name: string; q: string }[]
  side_panel?: 'none' | 'calendar' | 'tasks' | 'agent'
  /** Look up sender pictures (BIMI, Gravatar, Libravatar) on the server. Off by default. */
  sender_pictures?: boolean
  /** Spam (Settings → Mail → Spam & Screener). */
  /** Where to go after archiving/deleting the open conversation (Gmail's auto-advance). */
  auto_advance?: 'list' | 'newer' | 'older'
  brief_position?: 'top' | 'side'
  /** Routines' digests end with today's meetings and links to their meeting prep (event.prep). */
  meeting_prep_digest?: boolean
  /** Addresses of yours another assistant handles: standing orders leave mail sent only to them alone,
   *  unless an order names the address (Settings → Ambrose → Trusted addresses). */
  handled_elsewhere?: string[]
  /** Your standing rules for every bulk review (/sweep, review_mail, routines), plain text up to 2000
   *  characters (Settings → Ambrose → Sweep; backend agent/review.py). */
  sweep_rules?: string
  /** The sender screener (features/mail/screener.ts): first-time senders wait in Mail → Screener.
   *  Changed with PUT /screener/settings, never PATCH /me/settings. */
  screener?: boolean
  reply_suggestions?: boolean
  /** VIP senders: always important; desktop notifications can be VIP-only. */
  vip?: string[]
  /** Archive also removes the label of the list it was done from (e.g. a "Later" tab). */
  archive_removes_current_label?: boolean
  /** Label ids that every archive also removes. */
  archive_also_removes?: number[]
  spam_classifier?: 'off' | 'score' | 'junk'
  spam_junk_score?: number
  /** Provider spam score that sends mail from new senders to Spam; 0 turns it off. */
  spam_upstream_unknown_threshold?: number
  /** Spam score (Stalwart's or the provider's) from which no model reads a new sender's mail (default 5; 0 off). */
  spam_suspect_score?: number
  /** UI theme (layout + style). A per-device choice in localStorage wins. */
  ui_theme: string
  /** The colours: {preset, seed, harmony}; null is the theme's own.
   *  A bare string is the Cards accent saved before 8 Oct 2026 (lib/colour/choice.ts reads both). */
  ui_accent?: { preset?: string; seed?: string; harmony?: 'single' | 'complementary' | 'analogous' | 'triadic' | 'split' } | string | null
  /** Reading pane per theme, for themes that offer a choice (themes/types.ts paneChoices). */
  reading_pane_by_theme?: Record<string, 'none' | 'right' | 'below'>
  card_bundles?: 'flat' | 'always' | 'never'
  /** Favorites in the mail navigation, in order: view keys ('inbox', 'label:<id>'). */
  nav_favorites?: string[]
  /** Label learning: recent labels count more; a label's weight halves every half_life_months
   * (backend intel/classify.py). Changing it retrains the label models. */
  label_recency?: LabelRecency
  /** The sidebar Inbox number (lib/inboxCount.ts): the first tab's unread mail, or every unread
   * inbox conversation. */
  inbox_count: InboxCountMode
  /** "Learn from my choices" (backend intel/classify.py): labels you give teach mailr to suggest
   * and apply them. Off: no automatic labels. */
  auto_label: boolean
  /** How sure the label model must be before it applies a label by itself (0.5 to 0.99). */
  auto_label_precision: number
  /** How many examples a label needs before it is applied by itself (3 to 1000). */
  auto_label_min_examples: number
  /** Show the advanced parts of Settings (Agents & API, detailed filters, learning detail, the
   * decisions lab). Admins always see them. */
  advanced_settings: boolean
  /** Settings → Mail → Inbox → "Show each account separately": with two or more mail accounts,
   *  each account gets its own folders in the navigation (backend core/mailaccounts.py). */
  account_folders?: boolean
  /** The folders pinned to each account's group, in order ({"<account id>": [label id, …]}): they
   *  show above "More folders" (backend core/accountpins.py; features/layout/folderPins.tsx). */
  account_folder_pins?: Record<string, number[]>
  /** The accounts whose "More folders" is open in the navigation. */
  account_folders_more_open?: number[]
  /** Show a folder with unread mail above "More folders" until it is read. */
  account_folders_show_unread?: boolean
  account_folders_hint_dismissed?: boolean
  /** The setup guide's progress (backend core/onboarding.py); changed only through PUT /onboarding. */
  onboarding?: { status?: 'pending' | 'active' | 'done' | 'dismissed'; step?: string; steps?: Record<string, 'done' | 'skipped'> }
}

export type InboxCountMode = 'first_tab' | 'all'

export type MailNotify = 'off' | 'vip' | 'important' | 'primary' | 'all'

export type HalfLifeMonths = 6 | 12 | 24

export interface LabelRecency {
  enabled: boolean
  half_life_months: HalfLifeMonths
}

export const DEFAULT_SETTINGS: Settings = {
  inbox_type: 'default',
  inbox_count: 'first_tab',
  priority_sections: ['important', 'unread', 'everything'],
  reading_pane: 'none',
  density: 'default',
  theme: 'system',
  conversation_view: true,
  undo_send_seconds: 10,
  categories: ['primary', 'promotions', 'social', 'updates', 'forums'],
  importance_markers: true,
  keyboard_shortcuts: true,
  images: 'always',
  send_and_archive: false,
  default_reply: 'reply',
  signature_placement: 'below',
  page_size: 50,
  mail_notifications: 'important',
  notify_approvals: true,
  notify_assistant: true,
  notify_accounts: true,
  ui_theme: 'classic',
  nav_favorites: ['inbox', 'sent', 'drafts'],
  label_recency: { enabled: true, half_life_months: 12 },
  auto_label: true,
  auto_label_precision: 0.9,
  auto_label_min_examples: 30,
  advanced_settings: false,
}

/** Fixed text inserted in compose (`;shortcut` or the Templates menu). */
export interface Template {
  id: number
  name: string
  shortcut: string
  subject: string
  body_html: string
  created_at: string | null
  updated_at: string | null
}

/** The policy features (backend core/access.FEATURES). */
export type AccessFeature = 'assistant' | 'choose_models' | 'lab' | 'api_tokens' | 'local_only_tokens' | 'hosted_models'
export type AccessTask = 'chat' | 'classify'

export interface AccessTaskInfo {
  /** true: every model (admins, unrestricted). */
  all: boolean
  /** models null: any model of that endpoint. */
  allowed: { endpoint: string; locality: string; models: string[] | null }[]
  default: { endpoint: string; model: string | null } | null
}

/** A user's effective access (GET /me "access", GET /admin/users/{id}/access "access"). */
export interface AccessInfo {
  unrestricted: boolean
  groups: string[]
  source: 'idp' | 'assigned'
  /** The group names that have a policy. */
  policies: string[]
  features: Record<AccessFeature, boolean>
  tasks: Record<AccessTask, AccessTaskInfo>
  needs_endpoints: boolean
  /** The no-assistant message when the assistant is off. */
  message: string | null
}

export interface AuthConfig {
  local_login: boolean
  oidc: boolean
  oidc_name?: string
  signup: boolean
  /** Where phones download the Android app (the Get the app page). */
  android_apk_url?: string
}


export interface Page<T> {
  items: T[]
  next_cursor: string | null
  total: number
  /** The server stopped counting at `total` (shown as "200,000+"). */
  total_capped?: boolean
}

/** GET /messages/{id}/attachments/{part}/email: an attached email as the reader shows it. Its
 *  files carry the outer message's part numbers (null: it cannot be downloaded on its own). */
export interface AttachedEmailBody extends MessageBody {
  part: number
  subject: string
  from: Address
  to: Address[]
  cc: Address[]
  date: string | null
  attachments: (Omit<Attachment, 'part'> & { part: number | null })[]
}

export type ActionName =
  | 'archive' | 'inbox' | 'trash' | 'untrash' | 'delete_forever' | 'spam' | 'not_spam'
  | 'read' | 'unread' | 'star' | 'unstar' | 'important' | 'not_important'
  | 'add_labels' | 'remove_labels' | 'move' | 'snooze' | 'unsnooze' | 'mute' | 'unmute' | 'category'
  | 'follow_up' | 'cancel_follow_up' | 'restore_category' | 'show_in_tab' | 'restore_tab'

export interface ActionRequest {
  ids: number[]
  action: ActionName
  label_ids?: number[]
  label_id?: number
  from_label_id?: number
  until?: string
  category?: string
  /** Show in tab: a tab id of the active layout (GET /layouts/show-in-tab). */
  tab?: string
  /** Instead of ids: every conversation of a list (the server's ListQuery). */
  query?: { view: string; q: string; layout?: string; tab?: string; sec?: string; exclude_ids?: number[] }
}

export interface ActionResponse {
  ok: boolean
  affected: number
  undo: (Partial<ActionRequest> & { action: ActionName }) | null
}

export interface ComposeAttachment {
  blob: string
  filename: string
  type: string
  size?: number
}

export interface ComposePayload {
  identity_id: number | null
  to: Address[]
  cc: Address[]
  bcc: Address[]
  subject: string
  html: string | null
  text?: string | null
  in_reply_to_message_id?: number | null
  forward_of_message_id?: number | null
  attachments: ComposeAttachment[]
  headers?: Record<string, string>
}

export type OutboxStatus = 'queued' | 'awaiting_approval' | 'sending' | 'sent' | 'cancelled' | 'failed' | 'rejected' | 'uncertain'

export interface Outbox {
  id: number
  status: OutboxStatus
  send_at: string
  recipients: string[]
  subject: string
  identity_id: number
  token_name: string | null
  approval_reason: string
  error: string
  conversation_id?: number | null
  /** The saved draft this send came from. */
  draft_id?: number | null
  sent_message_id?: string | null
  sent_at?: string | null
  created_at?: string
  attempts?: number
}

export interface Person {
  email: string
  name: string
  organization?: string
  received_count?: number
  sent_count?: number
  first_seen?: string | null
  last_seen?: string | null
  last_sent?: string | null
  recent_conversations?: ConversationSummary[]
  open_threads?: ConversationSummary[]
  blocked?: boolean
}

export interface RuleActions {
  add_labels?: number[]
  remove_labels?: number[]
  archive?: boolean
  mark_read?: boolean
  star?: boolean
  important?: boolean | null
  category?: string | null
  spam?: boolean
  never_spam?: boolean
  trash?: boolean
  forward_to?: string | null
}

export interface Rule {
  id: number
  name: string
  query: string
  actions: RuleActions
  position: number
  enabled: boolean
  stop: boolean
  source: string
  hits: number
}

export interface Preset {
  id: string
  name: string
  domains?: string[]
  imap?: ServerSettings
  smtp?: ServerSettings
  notes?: string
  kind?: string
}

export interface ServerSettings {
  host: string
  port: number
  tls: 'ssl' | 'starttls' | 'none'
  username?: string
}

export interface DiscoverResult {
  preset?: string | null
  imap?: ServerSettings | null
  smtp?: ServerSettings | null
  source?: string
  kind?: string
  /** false: the provider cannot be connected with a password (Microsoft 365). */
  supported?: boolean
  notes?: string
}

export interface TestResult {
  ok: boolean
  detail: string
  folders: { name: string; role?: string | null; count?: number; messages?: number; kind?: 'system' | 'label'; sync?: boolean; target?: [string, string] | null }[]
  identities: string[]
  extensions: string[]
}

export type ConnectorMode = 'store' | 'mirror' | 'import'

export interface Connector {
  id: number
  kind: string
  name: string
  preset: string | null
  mode: ConnectorMode
  data_class: 'normal' | 'local_only'
  config: Record<string, unknown>
  retention_days: number
  delete_upstream: boolean
  enabled: boolean
  /** The built-in "mailr calendar" on this server (calendar/builtin.py): it cannot be removed and
   *  its address and sign-in cannot be changed; it can be hidden (enabled false). */
  builtin?: boolean
  status: 'new' | 'syncing' | 'idle' | 'error' | 'paused'
  status_detail: string
  last_sync_at: string | null
  created_at?: string
  /** Import progress (backend connector_out): the first import, and the older mail brought in
   *  afterwards when the account started with recent mail (config backfill). */
  progress?: ConnectorProgress
  /** The status in plain words for the owner (backend core/accounthealth.py). */
  health?: AccountHealth
  /** An administrator connected it for this person (backend core/managed.py). */
  managed_by_admin?: boolean
  /** Mail sent from mailr saved in the provider's Sent folder (backend connectors/sentcopy.py):
   *  null except for a mirror-mode IMAP account. */
  sent_copies?: SentCopies | null
}

export interface SentCopies {
  setting: 'auto' | 'on' | 'off'
  /** Saving now: the setting, and on Automatic whether the provider keeps its own copy. */
  active: boolean
  /** The provider keeps its own copy of mail sent through its server (Gmail, Microsoft). */
  provider_saves: boolean
  provider: string
  /** Sent messages that could not be saved there after all retries. */
  failed: number
}

export interface AccountHealth {
  state: 'ok' | 'syncing' | 'paused' | 'signin' | 'stale' | 'error'
  text: string
}

/** The last success and the last error of a model task or endpoint, or a push transport
 * (backend core/healthlog.py). `ok`: the newer of the two was a success. */
export interface LastOutcome {
  ok: boolean
  ok_at?: string
  error_at?: string
  error?: string
  endpoint?: string | null
  model?: string | null
}

export interface ConnectorProgress {
  initial_done: boolean
  folders?: Record<string, { last_uid: number | null }>
  backfill?: { done: boolean; total: number | null; imported: number; since?: string | null; until?: string | null }
}

export interface Decision {
  id: number
  /** triage: a note on how triage ran (target model_skipped: no model read likely spam). */
  kind: 'label' | 'category' | 'importance' | 'spam' | 'smart' | 'triage'
  target: string
  value: boolean
  /** llm: a chat model; decision_model: a Jev-protocol decision model; heuristic: mailr's rules. */
  source: 'rule' | 'classifier' | 'llm' | 'decision_model' | 'heuristic' | 'provider' | 'vip' | 'user'
  source_ref: string
  confidence: number | null
  reason: string
  status: 'applied' | 'suggested' | 'accepted' | 'rejected' | 'withdrawn'
  created_at: string
  message_id?: number
  conversation_id?: number
  subject?: string
}

export interface SendPolicy {
  mode: 'approve_all' | 'policy'
  replies_only?: boolean
  known_only?: boolean
  domains?: string[]
  max_per_hour?: number
  max_per_day?: number
  identities?: number[]
}

export interface ApiToken {
  id: number
  name: string
  prefix: string
  scopes: Scope[]
  connector_ids: number[]
  local_only: boolean
  send_policy: SendPolicy
  expires_at: string | null
  revoked: boolean
  last_used_at: string | null
  created_at: string
}

export interface AuditEntry {
  id: number
  token_id: number | null
  actor: string
  action: string
  target: string
  inputs: Record<string, unknown>
  result: Record<string, unknown>
  ok: boolean
  created_at: string
}

export interface CalendarEvent {
  uid: string
  summary: string
  start: string
  end: string | null
  all_day?: boolean
  location?: string
  description?: string
  calendar?: string
  color?: string
  url?: string
  recurrence_id?: string | null
  recurring?: boolean
  reminders?: number[]
  my_status?: string | null
  is_organizer?: boolean
  conversation_id?: number | null
}

export interface Task {
  uid: string
  summary: string
  due: string | null
  due_all_day?: boolean
  status: 'needs-action' | 'completed' | 'in-process' | 'cancelled' | string
  completed_at?: string | null
  notes?: string
  priority?: number
  list?: string
  reminders?: number[]
  conversation_id?: number | null
}

export interface CalendarDefaults {
  calendar: string | null
  task_list: string | null
  connector_id: number | null
}

export interface AdminUser extends User {
  disabled: boolean
  /** The effective groups: the identity provider's, else the ones an admin assigned. */
  groups?: string[]
  groups_source?: 'idp' | 'assigned'
  created_at: string
  last_login_at: string | null
  /** The setup guide (backend core/onboarding.summary): none for accounts from before it. */
  setup?: { status: 'none' | 'pending' | 'active' | 'done' | 'dismissed'; step: string; completed: number; total: number; finished_at: string | null }
  /** Live tokens the user marked local-only: metadata only. `in_effect` is false when the user's
   *  policy no longer allows local-only agents (the token then works as an ordinary one). */
  local_only_tokens?: { id: number; name: string; created_at: string; last_used_at: string | null; in_effect: boolean }[]
}

export interface ModelEndpoint {
  id: number
  name: string
  base_url: string
  locality: 'local' | 'hosted'
  adapter: 'openai' | 'jev' | string
  models: string[]
  defaults: Record<string, string>
  enabled: boolean
  has_api_key?: boolean
  /** Its last success and last error (backend intel/modelhealth.py). */
  health?: LastOutcome | null
  /** Set up by MAILR_ASR_URL: its address, locality and API are changed there only. */
  from_env?: boolean
  /** A local endpoint's connect-time check, run now (core/outbound.py); null for a hosted one. */
  locality_check?: { ok: boolean; error: string | null } | null
}

export interface SessionInfo {
  id: string
  device: string
  user_agent: string
  ip: string
  created_at: string
  last_seen_at: string
  current?: boolean
}

export interface Summary {
  status: 'ready' | 'pending' | 'unavailable'
  /** Bullet sentences. */
  summary?: string[] | string | null
  action_items?: string[]
  dates?: { what: string; when: string }[]
  /** Verified sources referenced as [n](mailr-cite:n) in the bullets. */
  citations?: import('../features/rail/assistant/citations').Citation[]
  model?: string
  local_only?: boolean
  reason?: string
}

// ---------------------------------------------------------------- generated (tools/api-types)

export type Address = Api.Address
export type Attachment = Api.Attachment
export type AttachedEmailSummary = Api.AttachedEmailSummary
export type Label = Api.Label
export type LabelRole = NonNullable<Api.Label['role']>
export type Participant = Api.Participant
export type PrimaryAction = Api.PrimaryAction
export type ConversationSummary = Api.ConversationSummary
export type ConversationPage = Api.ConversationPage
export type EventChange = Api.EventChange
export type Message = Api.Message
export type ConversationDetail = Api.ConversationDetail
export type MessageBody = Api.MessageBody
export type MailAccount = Api.MailAccount
export type SidebarCounts = Api.SidebarCounts
export type InboxCount = Api.InboxCount
export type LayoutSection = Api.LayoutSection
export type LayoutTab = Api.LayoutTab
export type InboxLayout = Api.InboxLayout
export type LayoutsResponse = Api.LayoutsResponse
export type ChangeEntry = Api.ChangeEntry
export type Changes = Api.Changes
export type AccountPicture = Api.AccountPicture
export type Identity = Api.Identity
/** GET /me for a signed-in session (the web never signs in with a token, so the answer is always
 *  the full one). `settings` and `access` have no model on the server yet; their types here are
 *  written by hand. */
export type Me = Omit<Api.Me, 'user' | 'settings' | 'access' | 'identities' | 'picture'> & {
  user: User
  settings: Partial<Settings>
  identities: Identity[]
  picture?: AccountPicture
  /** What the user's access policy allows (backend core/access.py). Absent from older servers. */
  access?: AccessInfo
}
