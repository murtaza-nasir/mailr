// Every sidebar number from one server answer (GET /counts). The server counts each view with
// the conditions its list uses, so a number and the list it opens cannot disagree (a draft in
// Trash counts in neither Drafts nor the Drafts list). The SSE `counts` event replaces the cached
// answer after every change (api/events.ts); the key sits under ['labels'], so whatever refreshes
// the label counts refreshes these too.

import { useQuery, type QueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { Label, MailAccount } from '../../api/types'
import type { InboxCount } from '../../lib/inboxCount'

export interface ViewCount {
  unread: number
  total: number
}

export interface SidebarCounts {
  cursor: number
  /** By view name: inbox, drafts, spam, trash, snoozed, scheduled, sent, archive, starred,
   *  important, category:<name>, label:<id>. */
  views: Record<string, ViewCount>
  inbox_count: InboxCount
  waiting: number
  approvals: number
  failed: number
  /** The mail accounts the caller may see. With "Show each account separately" on, `views` also
   *  holds their folders as `acct:<id>:<view>`. */
  accounts?: MailAccount[]
}

export const sidebarCountsKey = ['labels', 'sidebar-counts'] as const

export function useSidebarCounts() {
  return useQuery<SidebarCounts>({
    queryKey: sidebarCountsKey,
    queryFn: ({ signal }) => api.get<SidebarCounts>('/counts', undefined, signal),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  })
}

/** The view a label opens (role labels open their system view). */
export function labelView(l: Pick<Label, 'id' | 'role'>): string {
  const roles: Record<string, string> = { inbox: 'inbox', sent: 'sent', drafts: 'drafts', junk: 'spam', trash: 'trash', archive: 'archive' }
  return (l.role && roles[l.role]) || `label:${l.id}`
}

/** A `counts` event from the server: the new numbers replace the cache, and each label's
 *  unread/total follow (the label list carries them for the label entries). An older answer
 *  (a lower change cursor) never replaces a newer one. */
export function applyServerCounts(qc: QueryClient, counts: SidebarCounts) {
  const prev = qc.getQueryData<SidebarCounts>(sidebarCountsKey)
  if (prev && prev.cursor > counts.cursor) return
  qc.setQueryData<SidebarCounts>(sidebarCountsKey, counts)
  // The Inbox count query (features/layout/inboxCount.ts) holds the same number.
  qc.setQueriesData<InboxCount>({ queryKey: ['labels', 'inbox-count'] }, (old) =>
    old && counts.inbox_count && old.mode === counts.inbox_count.mode ? counts.inbox_count : old,
  )
  qc.setQueryData<Label[]>(['labels'], (labels) =>
    Array.isArray(labels)
      ? labels.map((l) => {
          const v = counts.views[labelView(l)]
          // Drafts shows its total as its number (GET /labels does the same).
          return v ? { ...l, unread: l.role === 'drafts' ? v.total : v.unread, total: v.total } : l
        })
      : labels,
  )
}

/** The user's own action: move `n` conversations (`unread` of them unread) from one view to
 *  another at once; the server's `counts` event then confirms or corrects the numbers. */
export function adjustCounts(qc: QueryClient, from: string | null, to: string | null, n: number, unread = 0) {
  qc.setQueryData<SidebarCounts>(sidebarCountsKey, (c) => {
    if (!c) return c
    const views = { ...c.views }
    const move = (v: string | null, sign: number) => {
      if (!v || !views[v]) return
      views[v] = { unread: Math.max(0, views[v].unread + sign * unread), total: Math.max(0, views[v].total + sign * n) }
    }
    move(from, -1)
    move(to, 1)
    const inbox_count = from === 'inbox' && c.inbox_count ? { ...c.inbox_count, count: Math.max(0, c.inbox_count.count - unread) } : c.inbox_count
    return { ...c, views, inbox_count }
  })
}
