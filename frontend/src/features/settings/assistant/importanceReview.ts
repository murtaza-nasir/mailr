// The importance review queue's pure parts (Settings → Ambrose → Review importance):
// the range's start, the key map, and the list after a choice. ACT mail.review_importance @web.settings

import { t } from '../../../i18n'

export type ReviewRange = 'today' | '3d' | '7d'

export const RANGES: { id: ReviewRange; label: string; days: number }[] = [
  { id: 'today', label: t('common.today'), days: 1 },
  { id: '3d', label: t('common.n3Days'), days: 3 },
  { id: '7d', label: t('settings.assistant.importanceReview.n7Days'), days: 7 },
]

export interface ReviewItem {
  message_id: number
  conversation_id: number
  from: { email: string; name: string | null }
  subject: string | null
  snippet: string | null
  date: string | null
  important: boolean
  judged_important: boolean
  score: number
  borderline: boolean
  reasons: string[]
  source: string
}

export interface ReviewStatus {
  learned_from: number
  since_training: number
  retrain_after: number
  min_each: number
  model: {
    trained_at: string
    examples: number | null
    sources: { user: number; replied_or_starred: number; archived_unread: number; provider_excluded: number } | null
    precision: number | null
    recall: number | null
    current: boolean
  } | null
}

export interface ReviewOut {
  since: string
  threshold: number
  margin: number
  items: ReviewItem[]
  status: ReviewStatus
}

/** Local midnight at the start of the range: today, or 2 / 6 days before today. */
export function rangeStart(range: ReviewRange, now = new Date()): Date {
  const days = RANGES.find((r) => r.id === range)?.days ?? 1
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  d.setDate(d.getDate() - (days - 1))
  return d
}

export type ReviewKey = 'important' | 'not_important' | 'next' | 'prev' | 'open'

/** One key per row: i important, n not important, j/k move, Enter opens the email. */
export function reviewKey(e: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey'>): ReviewKey | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null
  switch (e.key) {
    case 'i': return 'important'
    case 'n': return 'not_important'
    case 'j': case 'ArrowDown': return 'next'
    case 'k': case 'ArrowUp': return 'prev'
    case 'Enter': return 'open'
    default: return null
  }
}

/** The queue without the judged rows, and the cursor on the row after the one just judged. */
export function afterChoice(items: ReviewItem[], judged: Set<number>, cursor: number): { items: ReviewItem[]; cursor: number } {
  const curId = items[cursor]?.message_id
  const left = items.filter((it) => !judged.has(it.message_id))
  if (curId === undefined || !judged.has(curId)) {
    const at = left.findIndex((it) => it.message_id === curId)
    return { items: left, cursor: Math.max(0, at) }
  }
  // The judged row's successor takes its place; at the end, the last row.
  const after = items.slice(cursor + 1).find((it) => !judged.has(it.message_id))
  const at = after ? left.indexOf(after) : left.length - 1
  return { items: left, cursor: Math.max(0, at) }
}

export function pct(x: number | null | undefined): string {
  return x == null ? '—' : `${Math.round(x * 100)}%`
}
