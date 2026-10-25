// Prefetching a conversation before it opens: its messages and the bodies the reader will show
// expanded, so a click finds them in the query cache and the reader draws at once.
//
// When: a list row hovered with a mouse for HOVER_DELAY ms, the keyboard cursor resting on a row
// (j/k), and the open conversation's neighbours in the list (the next one only on a phone).
// Never on a data-saver connection, and at most BUDGET conversations a minute.
//
// Both requests are plain reads: GET /conversations/{id} and GET /messages/{id}/body change
// nothing (the read flag is set by ConversationView when it shows the conversation, never by a
// fetch), so a prefetched conversation stays unread until it is opened.
import { useCallback } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { conversationQuery, messageBodyQuery, useLabelList, useSettings } from '../../api/hooks'
import type { Label, Message, Settings } from '../../api/types'
import { baseView } from '../../lib/views'
import { imagesShownByDefault } from './imageTrust'

export const HOVER_DELAY = 120
export const FOCUS_DELAY = 150
export const NEIGHBOUR_DELAY = 300
const BUDGET = 40 // conversations a minute
const MAX_BODIES = 3 // bodies per conversation
const PREFETCH_STALE = 30_000

const recent: number[] = []

function underBudget(now = Date.now()): boolean {
  while (recent.length && now - recent[0] > 60_000) recent.shift()
  if (recent.length >= BUDGET) return false
  recent.push(now)
  return true
}

/** Data saver on (Chromium's Network Information API): no prefetching at all. */
function saveData(): boolean {
  const c = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection
  return !!c?.saveData
}

/** The messages the reader lists: outside Trash and Spam, deleted ones stay folded away. */
export function readerMessages(all: Message[], view: string, labelsById: Map<number, Label>, showDeleted = false): { messages: Message[]; deletedCount: number } {
  if (baseView(view) === 'trash' || baseView(view) === 'spam' || showDeleted) return { messages: all, deletedCount: 0 }
  const isDeleted = (m: Message) => m.label_ids.some((l) => ['trash', 'junk'].includes(labelsById.get(l)?.role ?? ''))
  const live = all.filter((m) => !isDeleted(m))
  return live.length ? { messages: live, deletedCount: all.length - live.length } : { messages: all, deletedCount: 0 }
}

/** The messages the reader opens expanded: the last one that is not a draft, and every unread one. */
export function initiallyExpanded(messages: Message[]): Set<number> {
  const ex = new Set<number>()
  const nonDraft = messages.filter((m) => !m.draft)
  const last = nonDraft[nonDraft.length - 1] ?? messages[messages.length - 1]
  if (last) ex.add(last.id)
  messages.forEach((m) => !m.seen && ex.add(m.id))
  return ex
}

/** Fetch a conversation and the bodies its reader shows expanded, unless the cache has them. */
export async function prefetchConversation(qc: QueryClient, id: number, ctx: { view: string; labels: Label[]; images: Settings['images'] }): Promise<void> {
  if (!id || saveData()) return
  // Only a request that goes to the server counts against the budget.
  const state = qc.getQueryState(conversationQuery(id).queryKey)
  const cached = !!state?.dataUpdatedAt && Date.now() - state.dataUpdatedAt < PREFETCH_STALE
  if (!cached && !underBudget()) return
  let detail
  try {
    detail = await qc.fetchQuery({ ...conversationQuery(id), staleTime: PREFETCH_STALE })
  } catch {
    return // the reader shows the error if it is opened
  }
  const labelsById = new Map(ctx.labels.map((l) => [l.id, l]))
  const junk = (ids: number[]) => ids.some((l) => labelsById.get(l)?.role === 'junk')
  const spam = baseView(ctx.view) === 'spam' || junk(detail.conversation.label_ids ?? [])
  const { messages } = readerMessages(detail.messages, ctx.view, labelsById)
  const open = initiallyExpanded(messages)
  // Newest first: the last message is the one on screen.
  const wanted = messages.filter((m) => open.has(m.id) && !m.draft).reverse().slice(0, MAX_BODIES)
  for (const m of wanted) void qc.prefetchQuery(messageBodyQuery(m.id, imagesShownByDefault(ctx.images, spam, m.from.email)))
}

/** prefetch(id) for list rows and readers; it reads the settings and labels it needs itself. */
export function usePrefetchConversation(view: string): (id: number | null | undefined) => void {
  const qc = useQueryClient()
  const settings = useSettings()
  const labels = useLabelList()
  return useCallback(
    (id) => {
      if (id) void prefetchConversation(qc, id, { view, labels, images: settings.images })
    },
    [qc, view, labels, settings.images],
  )
}

/** A timer that prefetches one conversation after a pause, replaced by the next call. */
export function delayed(fn: (id: number) => void) {
  let timer: ReturnType<typeof setTimeout> | null = null
  let pending: number | null = null
  return {
    schedule(id: number, ms: number) {
      if (pending === id && timer) return
      if (timer) clearTimeout(timer)
      pending = id
      timer = setTimeout(() => {
        timer = null
        pending = null
        fn(id)
      }, ms)
    },
    cancel() {
      if (timer) clearTimeout(timer)
      timer = null
      pending = null
    },
  }
}
