// Shapes of the /assistant API (backend/mailr/knowledge/{routes,memory,skills,profile}.py).

import { t } from '../../../i18n'
import { assistantNameNow } from '../../rail/assistant/assistantName'

export interface AssistantOptions {
  learn_from_mail: boolean
  /** Suggest memories from what you tell Ambrose in a chat. */
  learn_from_chats: boolean
  auto_recall: boolean
  /** The assistant's name; "Ambrose" by default. */
  assistant_name: string
  /** Quiet hours for assistant notifications, HH:MM or "" (off). */
  quiet_from: string
  quiet_to: string
}

export interface AssistantProfile {
  about: string
  preferred_name: string
  signoff_name: string
  role: string
  timezone: string
  languages: string
  writing: string
  options: AssistantOptions
  account_name: string
  account_email: string
  updated_at: string | null
}

export type ProfileField = 'about' | 'preferred_name' | 'signoff_name' | 'role' | 'timezone' | 'languages' | 'writing'

export type SkillContext = 'compose' | 'reply' | 'chat' | 'summarize'

export interface Skill {
  id: number
  name: string
  title: string
  description: string
  instructions: string
  applies_to: SkillContext[]
  auto: boolean
  enabled: boolean
  builtin: boolean
  source: 'user' | 'builtin' | 'agent'
  modified: boolean
  created_at: string | null
  updated_at: string | null
}

export interface SkillList {
  items: Skill[]
  contexts: SkillContext[]
  builtins: string[]
}

export type MemoryKind = 'fact' | 'preference' | 'routine' | 'commitment'
export type EntityKind = 'person' | 'organization' | 'project' | 'topic' | 'place'

export interface MemoryRef {
  message_id?: number
  conversation_id?: number
  chat_session_id?: string
  chat_message_id?: number
  about?: { name?: string; emails?: string[]; kind?: EntityKind; self?: boolean }
}

export interface Memory {
  id: number
  text: string
  kind: MemoryKind
  entity_id: number | null
  entity: { id: number; name: string; kind: EntityKind; is_self?: boolean } | null
  source: 'user' | 'agent' | 'extracted' | 'chat'
  status: 'active' | 'suggested'
  confidence: number | null
  refs: MemoryRef[]
  sensitive: boolean
  pinned: boolean
  expires_at: string | null
  use_count: number
  last_used_at: string | null
  created_at: string | null
  updated_at: string | null
  /** The chat turn a memory from a chat came from, while that chat exists ("From a chat" opens it). */
  chat?: { session_id: string; chat_message_id: number | null } | null
}

export interface MemoryList {
  items: Memory[]
  counts: { active: number; suggested: number }
}

export interface Entity {
  id: number
  kind: EntityKind
  name: string
  aliases: string[]
  emails: string[]
  domains: string[]
  conversation_ids: number[]
  summary: string
  sensitive: boolean
  source: 'user' | 'agent' | 'extracted'
  /** The user's own entity: its notes show under "About you". */
  is_self?: boolean
  /** The contact (People) this person is linked to. */
  person_id?: number | null
  notes?: number
  created_at: string | null
  updated_at: string | null
}

export interface EntityContact {
  email: string
  name: string
  organization: string
  received_count: number
  sent_count: number
  last_seen: string | null
}

export interface EntityDetail extends Entity {
  memories: Memory[]
  contacts: EntityContact[]
}

/** The four kinds, each with an example (the add dialog and the filter tips show it). */
export const MEMORY_KINDS: { id: MemoryKind; label: string; example: string }[] = [
  { id: 'fact', label: t('settings.assistant.types.fact'), example: t('settings.assistant.types.factExample') },
  { id: 'preference', label: t('settings.assistant.types.preference'), example: t('settings.assistant.types.preferenceExample') },
  { id: 'routine', label: t('settings.assistant.types.routine'), example: t('settings.assistant.types.routineExample') },
  { id: 'commitment', label: t('settings.assistant.types.commitment'), example: t('settings.assistant.types.commitmentExample') },
]

export const ENTITY_KINDS: { id: EntityKind; label: string; plural: string }[] = [
  { id: 'person', label: t('settings.assistant.types.person'), plural: t('common.people') },
  { id: 'organization', label: t('settings.assistant.types.organization'), plural: t('settings.assistant.types.organizations') },
  { id: 'project', label: t('settings.assistant.types.project'), plural: t('settings.assistant.types.projects') },
  { id: 'topic', label: t('settings.assistant.types.topic'), plural: t('settings.assistant.types.topics') },
  { id: 'place', label: t('settings.assistant.types.place'), plural: t('settings.assistant.types.places') },
]

/** Everything that is not a person shows as a topic. */
export const TOPIC_KINDS = ENTITY_KINDS.filter((k) => k.id !== 'person')

/** How learning from mail went (GET /assistant/memories/learning; knowledge/tasks.py). */
export interface LearningRun {
  at: string
  messages: number
  skipped?: number
  suggested: number
  error: null | 'model_failed' | 'cut_off' | 'unreadable' | 'gave_up'
  cause?: string
}

export interface LearningStatus {
  enabled: boolean
  last: LearningRun | null
  runs: LearningRun[]
  since: string | null
}

export const CONTEXT_LABELS: Record<SkillContext, string> = {
  compose: t('settings.assistant.types.newEmail'),
  reply: t('settings.assistant.types.replies'),
  get chat() { return t('settings.assistant.types.chatWith', { name: assistantNameNow() }) },
  summarize: t('settings.assistant.types.summaries'),
}

export const SOURCE_LABELS: Record<Memory['source'], string> = {
  user: t('common.you'),
  get agent() { return t('settings.assistant.types.text', { name: assistantNameNow() }) },
  extracted: t('settings.assistant.types.learnedFromMail'),
  chat: t('settings.assistant.types.fromAChat'),
}

export const qkA = {
  profile: ['assistant', 'profile'] as const,
  skills: ['assistant', 'skills'] as const,
  memories: (status: string, q = '', kind = '', entity: number | null = null) => ['assistant', 'memories', status, q, kind, entity] as const,
  entities: (q = '', kind = '') => ['assistant', 'entities', q, kind] as const,
  entity: (id: number) => ['assistant', 'entity', id] as const,
  learning: ['assistant', 'learning'] as const,
  memoriesById: (ids: number[]) => ['assistant', 'memories', 'ids', ids.join(',')] as const,
  orders: ['assistant', 'orders'] as const,
  runs: (orderId: number | null = null) => ['assistant', 'runs', orderId] as const,
}

export type OrderActions = { add_labels?: number[]; remove_labels?: number[]; star?: boolean; important?: boolean; archive?: boolean;
  mark_read?: boolean; category?: string; notify?: boolean
  /** Message orders: add the email's invitation (or a forwarded one) to the calendar, with no reply. */
  add_to_calendar?: boolean }

export interface OrderChoice { key?: string; name: string; description: string; actions: OrderActions }

/** A standing order or routine (backend/mailr/assistant/orders.py order_out). */
export interface AssistantOrder {
  id: number
  name: string
  trigger: 'message' | 'schedule' | 'recording'
  schedule: string
  timezone: string
  query: string
  question: string
  /** A choice order: the decision model picks one option (or none); its actions run. */
  choices: OrderChoice[]
  threshold: number
  actions: OrderActions
  errand: string
  deliver: ('brief' | 'notify' | 'digest')[]
  enabled: boolean
  position: number
  source: 'user' | 'assistant' | 'builtin'
  runs: number
  last_run_at: string | null
  next_run_at: string | null
  /** Speakr recordings: a recording order's filter (q in the title or participants, tag, participant),
   * or a routine's recording source (enabled, days). */
  recordings?: RecordingFilter
  /** Message orders: only mail you forward from your trusted addresses (Trusted addresses). */
  from_trusted?: boolean
  /** Message orders: only mail from you or your trusted addresses, verified by its domain. */
  from_me?: boolean
  /** A built-in order: turned off and moved, never deleted or edited. */
  builtin?: 'forwarded_notes' | null
  /** A built-in order's permission: Ask me or Allow (Off is the order turned off). */
  permission?: 'ask' | 'allow'
  permission_source?: 'default' | 'rule'
}

export interface RecordingFilter {
  enabled?: boolean
  q?: string
  tag?: string
  participant?: string
  days?: number
}

/** The recording a recording order ran on (integrations/speakr/orders.py recording_ref). */
export interface RunRecording {
  connector_id: number
  recording_id: number
  title: string
  meeting_date: string | null
}

/** One run of an order (the activity log; a brief is the latest delivered run). */
export interface AssistantRun {
  id: number
  order_id: number | null
  order_name: string | null
  /** The order is built in: the apps show its own translated name. */
  order_builtin?: 'forwarded_notes' | null
  trigger: 'message' | 'schedule' | 'manual' | 'test' | 'recording' | 'forward'
  conversation_id: number | null
  message_id: number | null
  status: 'queued' | 'running' | 'done' | 'skipped' | 'failed'
  condition: { query?: string; question?: string; answer?: boolean; choice?: string | null; confidence?: number; reason?: string;
    threshold?: number; conversations?: number; recording?: RunRecording }
  /** result "duplicate": an earlier order of the same email did it (`by`), or an approval card for it waits. */
  actions: { action: string; affected?: number; result?: string; by?: { order_id: number; name: string; builtin?: string } }[]
  session_id: string | null
  output: string
  citations: import('../../rail/assistant/citations').Citation[]
  delivered: string[]
  error: string
  created_at: string | null
  finished_at: string | null
}

/** An order's name: a built-in order's own translated name, else the name the user gave it. */
export function orderName(o: { name: string | null; builtin?: string | null }): string {
  if (o.builtin === 'forwarded_notes') return t('settings.assistant.ordersSection.builtinForwardsName')
  return o.name ?? ''
}
