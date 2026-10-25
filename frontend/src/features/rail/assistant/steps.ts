// The assistant's activity list: one row per tool call.
//
// The server sends a human label for every step (`tool_start.label`, e.g. "Reading “PADS…”") and a
// result summary when it finishes ("50 conversations"). These helpers supply fallbacks for older
// servers, pick an icon family, and build the collapsed one-line summary.

import { t as tr } from '../../../i18n'

export type StepStatus = 'running' | 'done' | 'failed' | 'waiting'

export interface Step {
  id: string
  name: string
  label: string
  args?: unknown
  status: StepStatus
  summary?: string
  count?: number | null
  ms?: number
  approvalId?: number
  /** The server's icon family for the step (agent/present.py step_icon). */
  icon?: string
  /** A failed step's own error text: shown only under Technical details, never on the step. */
  error?: string
  /** The step this one repeated with the same arguments (the server's `repeat_of`): it was not run
   *  again, and its line folds into that step's ("Searching your tasks ×3"). */
  repeatOf?: string
}

/** A row of the activity list: a step and how many times it was asked for (1 + its repeats). */
export interface StepRow extends Step {
  times: number
}

/** Steps with their repeats folded into the step they repeat: one row each, with `times`. */
export function foldRepeats(steps: Step[]): StepRow[] {
  const rows: StepRow[] = []
  const at = new Map<string, number>()
  for (const s of steps) {
    const i = s.repeatOf != null ? at.get(s.repeatOf) : undefined
    if (i != null) {
      rows[i] = { ...rows[i], times: rows[i].times + 1 }
      at.set(s.id, i)
      continue
    }
    at.set(s.id, rows.length)
    rows.push({ ...s, times: 1 })
  }
  return rows
}

/** Server keys of a repeat's summary: before `repeat_of` existed, a repeat said "already done above". */
const REPEAT_KEYS = new Set(['approvals.result.already_done', 'approvals.result.same_again'])

/** `repeatOf` for stored steps of an older server: a step whose summary says it repeated a call
 *  points at the earlier step with the same tool and arguments. */
export function markLegacyRepeats(steps: (Step & { summary_msg?: unknown })[]): Step[] {
  const seen = new Map<string, string>()
  return steps.map((s) => {
    const key = `${s.name}\u0000${JSON.stringify(s.args ?? {})}`
    const m = s.summary_msg as { key?: unknown } | undefined
    const legacy = s.repeatOf == null && typeof m?.key === 'string' && REPEAT_KEYS.has(m.key) ? seen.get(key) : undefined
    if (!seen.has(key) && !legacy && s.repeatOf == null) seen.set(key, s.id)
    return legacy ? { ...s, repeatOf: legacy } : s
  })
}

/** What a step's row says after its label. A failed step never shows a tool's error text: it
 * says "retried" when a later call of the same tool worked, else nothing (the mark says it failed). */
export function stepResult(s: Step, all: Step[]): string | undefined {
  if (s.status === 'waiting') return tr('rail.assistant.steps.waitingForYourApproval')
  if (s.status !== 'failed') return s.summary
  const i = all.indexOf(s)
  return all.slice(i + 1).some((x) => x.name === s.name && x.status === 'done') ? 'retried' : undefined
}

export type StepIcon = 'search' | 'read' | 'person' | 'calendar' | 'task' | 'draft' | 'send' | 'label' | 'context' | 'recording' | 'memory' | 'settings' | 'note' | 'other'

const ICONS: Record<string, StepIcon> = {
  search_conversations: 'search',
  search_semantic: 'search',
  find_passages: 'search',
  get_conversation: 'read',
  get_message_body: 'read',
  get_person: 'person',
  build_context: 'context',
  list_labels: 'label',
  apply_label: 'label',
  archive: 'label',
  modify_conversations: 'label',
  list_calendar: 'calendar',
  search_events: 'calendar',
  get_event: 'calendar',
  create_event: 'calendar',
  update_event: 'calendar',
  delete_event: 'calendar',
  rsvp: 'calendar',
  get_calendar_defaults: 'calendar',
  list_tasks: 'task',
  search_tasks: 'task',
  create_task: 'task',
  update_task: 'task',
  complete_task: 'task',
  move_task: 'task',
  delete_task: 'task',
  list_notes: 'note',
  get_note: 'note',
  search_notes: 'note',
  create_note: 'note',
  update_note: 'note',
  checklist_items: 'note',
  delete_note: 'note',
  note_history: 'note',
  manage_note_labels: 'note',
  create_draft: 'draft',
  update_draft: 'draft',
  send_draft: 'send',
  get_outbox_status: 'send',
  get_approval_status: 'send',
}

export function stepIcon(name: string): StepIcon {
  return ICONS[name] ?? 'other'
}

/** Label for a step when the server did not send one. */
export function fallbackLabel(name: string, args: unknown): string {
  const a = (args && typeof args === 'object' ? args : {}) as Record<string, unknown>
  const q = typeof a.q === 'string' && a.q ? `“${truncate(a.q, 48)}”` : ''
  switch (name) {
    case 'search_conversations':
      return q ? tr('rail.assistant.steps.searchingYourMailFor', { q }) : tr('rail.assistant.steps.lookingThroughYourMail')
    case 'search_semantic':
      return tr('rail.assistant.steps.searchingByMeaningFor', { q: q || tr('rail.assistant.steps.yourQuestion') })
    case 'find_passages':
      return tr('rail.assistant.steps.findingPassagesAbout', { q: q || tr('rail.assistant.steps.yourQuestion') })
    case 'get_conversation':
      return tr('rail.assistant.steps.readingAConversation')
    case 'get_message_body':
      return tr('rail.assistant.steps.readingAMessage')
    case 'get_person':
      return typeof a.email === 'string' ? tr('rail.assistant.steps.lookingUp', { email: a.email }) : tr('rail.assistant.steps.lookingUpAPerson')
    case 'build_context':
      return tr('rail.assistant.steps.gatheringContext')
    default:
      return humanize(name)
  }
}

export function humanize(name: string): string {
  const s = name.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function truncate(s: string, n: number): string {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

/** "Looked at 6 conversations · 13 steps · 11.2 s" */
export function activitySummary(steps: Step[], seconds?: number | null, conversations?: number | null): string {
  const parts: string[] = []
  if (conversations) parts.push(tr('rail.assistant.steps.lookedAtConversations', { count: conversations }))
  parts.push(tr('rail.assistant.steps.steps', { count: steps.length }))
  const failed = steps.filter((s) => s.status === 'failed').length
  if (failed) parts.push(tr('rail.assistant.steps.failed', { failed }))
  if (seconds != null) parts.push(`${seconds < 60 ? seconds.toFixed(1) : Math.round(seconds)} s`)
  return parts.join(' · ')
}
