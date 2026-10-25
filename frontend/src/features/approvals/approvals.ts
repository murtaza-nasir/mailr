// Pending agent actions: GET /approvals, POST /approvals/{id}/decide.

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { AlsoCovers } from './grant'
import type { Display, DisplayField, Outcome } from '../rail/assistant/present/types'
import { cardToRef } from '../rail/assistant/cardRef'
import type { ApprovalPreview, ApprovalRef } from '../rail/assistant/useAssistant'
import { t } from '../../i18n'

export interface PendingApproval {
  id: number
  subject: string // "token:12" | "session:<id>" | "assistant"
  actor: string
  action: string
  target: string | null
  preview: ApprovalPreview | null
  status: string
  result: Record<string, unknown> | null
  decided_by: string | null
  created_at: string | null
  expires_at: string | null
  decided_at: string | null
  executed_at: string | null
  chat_session_id: string | null
  /** The display model and outcome (agent/present.py). */
  display?: Display | null
  outcome?: Outcome | null
  /** How many other waiting requests each grant on this card would approve too. */
  also_covers?: AlsoCovers | null
  /** False when a hard rule held the card: it is decided once. */
  grants?: boolean | null
}

export const approvalsKey = (status: string) => ['approvals', status] as const

export function useApprovals(status = 'pending', opts: { refetchInterval?: number; limit?: number } = {}) {
  return useQuery<PendingApproval[]>({
    queryKey: approvalsKey(status),
    queryFn: ({ signal }) => api.get<PendingApproval[]>('/approvals', { status, limit: opts.limit ?? 100 }, signal),
    refetchInterval: opts.refetchInterval ?? 30_000,
    staleTime: 5_000,
  })
}

/** Adapt a pending action to the card the assistant also uses. */
export function toRef(p: PendingApproval): ApprovalRef {
  return cardToRef(p)
}

/** "Claude Code", Ambrose (the assistant's name), or the token name the backend recorded. */
export function actorLabel(p: PendingApproval): string {
  if (p.subject === 'assistant' || p.subject.startsWith('session:')) return assistantNameNow()
  return p.actor || t('approvals.approvals.anAgent')
}

export function sessionLabelFor(p: PendingApproval): string {
  return p.subject.startsWith('token:') ? t('approvals.approvals.allowFor12Hours') : t('approvals.approvals.allowForThisChat')
}

export const STATUS_TEXT: Record<string, string> = {
  pending: t('common.waiting'),
  approved: t('approvals.approvals.approved'),
  executing: t('approvals.approvals.running'),
  executed: t('common.done'),
  denied: t('approvals.approvals.denied'),
  expired: t('common.expired'),
  failed: t('common.failed'),
}

/** Values too long or too structured for one line of the history table. */
const LONG_VALUES = new Set(['quote', 'choices', 'query'])

/**
 * One line under an approval in the history: the card's own wording (agent/present.py), so a
 * calendar change reads "When: Wed 30 Sep, 2:00–3:00 PM · Calendar: Work", never the event's uid or
 * the calendar's URL. The summary when the card has one, else its first short fields.
 */
export function historyLine(d: Display | null | undefined, max = 3): string | null {
  if (!d) return null
  if (d.summary) return d.summary
  const one = (f: DisplayField) => {
    const v = f.value.text.length > 60 ? `${f.value.text.slice(0, 59)}…` : f.value.text
    return f.label ? `${f.label}: ${v}` : v
  }
  const parts = d.fields.filter((f) => f.value.text && !LONG_VALUES.has(f.value.t)).slice(0, max).map(one)
  return parts.length ? parts.join(' · ') : null
}

/** Who asked, for "Allow all": one chat of the assistant, else the agent token or the assistant
 * (the backend's own grouping, POST /approvals/decide-many). */
export function requesterKey(p: Pick<PendingApproval, 'chat_session_id' | 'subject'>): string {
  return p.chat_session_id ? `chat:${p.chat_session_id}` : p.subject
}

export interface ApprovalGroup<T extends PendingApproval = PendingApproval> {
  key: string
  items: T[]
  /** The waiting ones "Allow all" would decide, oldest first (the order they were asked in). */
  pending: T[]
}

/** Cards grouped by who asked, each group where its newest card falls (the list stays newest first). */
export function groupByRequester<T extends PendingApproval>(shown: T[]): ApprovalGroup<T>[] {
  const out: ApprovalGroup<T>[] = []
  const at = new Map<string, ApprovalGroup<T>>()
  for (const p of shown) {
    const k = requesterKey(p)
    let g = at.get(k)
    if (!g) {
      g = { key: k, items: [], pending: [] }
      at.set(k, g)
      out.push(g)
    }
    g.items.push(p)
    if (p.status === 'pending') g.pending.unshift(p)
  }
  return out
}

/** "this chat", "Claude Code", "Ambrose": who a group of requests came from. */
export function groupSource(p: PendingApproval): string {
  if (p.chat_session_id) return t('approvals.approvals.thisChat')
  if (p.subject === 'assistant' || p.subject.startsWith('session:')) return t('approvals.approvals.text', { name: assistantNameNow() })
  return p.actor || t('approvals.approvals.anAgent')
}
