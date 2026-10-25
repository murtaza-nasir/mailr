import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type QueryKey,
} from '@tanstack/react-query'
import { liveCounts } from './liveState'
import { useCallback, useMemo } from 'react'
import { ApiError, api, asList, newIdempotencyKey, notifyUnauthorized, request } from './client'
import {
  DEFAULT_SETTINGS,
  type ActionRequest,
  type AttachedEmailBody,
  type ActionResponse,
  type AuthConfig,
  type ConversationDetail,
  type ConversationSummary,
  type Label,
  type Me,
  type MessageBody,
  type Page,
  type Person,
  type Settings,
  type InboxLayout,
  type LayoutsResponse,
} from './types'
import { toast, toastError, useUI } from '../stores/ui'
import { animateIn, animateOut, pop } from '../lib/motion'
import { calendarKeys, reminderKeys, taskKeys } from './queryKeys'
import { indicatorsOf, type TabCounts } from '../lib/tabIndicators'
import { adjustCounts } from '../features/layout/sidebarCounts'
import { enqueue, discard as discardPending, queueable, usePending, type PendingOp } from '../lib/offlineQueue'
import { closeConversationNotifications } from '../lib/serviceWorker'
import { t as tr } from '../i18n'
import { viewLabelOf } from '../lib/views'
import { filedFromOtherAccount } from '../lib/accountFolders'
import { sidebarCountsKey, type SidebarCounts } from '../features/layout/sidebarCounts'

// ---------- keys ----------

export const qk = {
  me: ['me'] as const,
  authConfig: ['authConfig'] as const,
  labels: ['labels'] as const,
  conversations: ['conversations'] as const,
  conversationList: (p: ListParams) => ['conversations', p] as const,
  conversation: (id: number) => ['conversation', id] as const,
  body: (id: number, images: boolean) => ['body', id, images] as const,
  attachedEmail: (id: number, part: number, images: boolean) => ['attached-email', id, part, images] as const,
  summary: (id: number) => ['summary', id] as const,
  decisions: (id: number) => ['decisions', id] as const,
  people: (q: string) => ['people', q] as const,
  person: (email: string) => ['person', email] as const,
  outbox: (status?: string) => ['outbox', status ?? 'all'] as const,
  layouts: ['layouts'] as const,
  layoutCounts: (id: string) => ['layouts', id, 'counts'] as const,
  // Calendar, Tasks and reminders (api/queryKeys.ts).
  calendar: calendarKeys,
  tasks: taskKeys,
  reminders: reminderKeys,
}

/**
 * Counts that are derived from the mail (label unread counts, layout tab badges) go stale with
 * it. Whenever any conversation list is invalidated, from any code path (actions, sends, live
 * events, imports, refresh), invalidate those too, once per burst. One rule here instead of a
 * reminder at every call site. While the live stream is open the server sends the sidebar and
 * label numbers itself after the change (api/liveState.ts), so only the tab badges refetch.
 */
export function linkDerivedCounts(qc: QueryClient): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null
  return qc.getQueryCache().subscribe((e) => {
    if (e.type !== 'updated' || e.action.type !== 'invalidate' || e.query.queryKey[0] !== 'conversations') return
    if (timer) return
    timer = setTimeout(() => {
      timer = null
      if (!liveCounts.open) void qc.invalidateQueries({ queryKey: qk.labels })
      void qc.invalidateQueries({ queryKey: qk.layouts, predicate: (q) => q.queryKey[2] === 'counts' })
    }, 50)
  })
}

// ---------- generic ----------

export function useApi<T>(key: QueryKey, path: string, query?: Record<string, string | number | boolean | null | undefined>, opts: { enabled?: boolean; refetchInterval?: number | false; staleTime?: number } = {}) {
  return useQuery<T>({
    queryKey: key,
    queryFn: ({ signal }) => api.get<T>(path, query, signal),
    enabled: opts.enabled,
    refetchInterval: opts.refetchInterval,
    staleTime: opts.staleTime,
  })
}

export function useApiList<T>(key: QueryKey, path: string, query?: Record<string, string | number | boolean | null | undefined>, opts: { enabled?: boolean; refetchInterval?: number | false } = {}) {
  return useQuery<T[]>({
    queryKey: key,
    queryFn: async ({ signal }) => asList<T>(await api.get<unknown>(path, query, signal)),
    enabled: opts.enabled,
    refetchInterval: opts.refetchInterval,
  })
}

// ---------- auth / me ----------

export function useAuthConfig() {
  return useQuery<AuthConfig>({
    queryKey: qk.authConfig,
    queryFn: () => api.get<AuthConfig>('/auth/config'),
    staleTime: Infinity,
  })
}

export function useMe() {
  return useQuery<Me>({
    queryKey: qk.me,
    queryFn: async ({ signal }) => {
      // /auth/session answers 200 when signed out, so probing leaves no console error. Only that
      // answer (or a 401) signs out; a network error, a timeout or a 5xx keeps the app.
      let r: Me & { authenticated: boolean }
      try {
        r = await request<Me & { authenticated: boolean }>('/auth/session', { signal, quiet401: true, timeoutMs: 15_000 })
      } catch (e) {
        if ((e as ApiError).status === 401) notifyUnauthorized(true)
        throw e
      }
      if (!r.authenticated) {
        notifyUnauthorized(true)
        throw new ApiError(401, tr('api.hooks.notSignedIn'))
      }
      return r
    },
    staleTime: 60_000,
  })
}

export function useSettings(): Settings {
  const me = useMe().data
  return useMemo(() => ({ ...DEFAULT_SETTINGS, ...(me?.settings ?? {}) }) as Settings, [me?.settings])
}

export function useUpdateSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (patch: Partial<Settings>) => api.patch<unknown>('/me/settings', patch as Record<string, unknown>),
    onMutate: async (patch) => {
      await qc.cancelQueries({ queryKey: qk.me })
      const prev = qc.getQueryData<Me>(qk.me)
      if (prev) qc.setQueryData<Me>(qk.me, { ...prev, settings: { ...prev.settings, ...patch } })
      return { prev }
    },
    onError: (e, _p, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.me, ctx.prev)
      toastError(e, tr('api.hooks.settingsNotSaved'))
    },
    onSettled: (_d, _e, patch) => {
      void qc.invalidateQueries({ queryKey: qk.me })
      // The server counts each account's own folders only while they are shown (GET /counts).
      if (patch && 'account_folders' in patch) void qc.invalidateQueries({ queryKey: ['labels', 'sidebar-counts'] })
    },
  })
}

// ---------- labels ----------

export function useLabels() {
  return useQuery<Label[]>({
    queryKey: qk.labels,
    queryFn: async () => asList<Label>(await api.get<unknown>('/labels')),
    staleTime: 30_000,
  })
}

const NO_LABELS: Label[] = []

/** Labels with a stable empty array while loading (safe in hook dependencies). */
export function useLabelList(): Label[] {
  return useLabels().data ?? NO_LABELS
}

export function useLabelMutations() {
  const qc = useQueryClient()
  const done = () => {
    qc.invalidateQueries({ queryKey: qk.labels })
  }
  const create = useMutation({
    mutationFn: (b: { name: string; color?: string | null; home_account_id?: number }) => api.post<Label>('/labels', b),
    onSuccess: (l, b) => {
      done()
      // A folder made in an account is pinned there by the server (core/accountpins.py).
      if (b.home_account_id) void qc.invalidateQueries({ queryKey: qk.me })
      toast({ message: tr('api.hooks.labelCreated', { itemName: l.name }) })
    },
    onError: (e) => toastError(e, tr('api.hooks.couldNotCreateLabel')),
  })
  const update = useMutation({
    mutationFn: ({ id, ...b }: Partial<Label> & { id: number }) => api.patch<Label>(`/labels/${id}`, b),
    onMutate: async (b) => {
      const prev = qc.getQueryData<Label[]>(qk.labels)
      if (prev) qc.setQueryData<Label[]>(qk.labels, prev.map((l) => (l.id === b.id ? { ...l, ...b } : l)))
      return { prev }
    },
    onError: (e, _b, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.labels, ctx.prev)
      toastError(e, tr('api.hooks.couldNotUpdateLabel'))
    },
    onSettled: done,
  })
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/labels/${id}`),
    onSuccess: () => {
      done()
      qc.invalidateQueries({ queryKey: qk.conversations })
      toast({ message: tr('api.hooks.labelDeleted') })
    },
    onError: (e) => toastError(e, tr('api.hooks.couldNotDeleteLabel')),
  })
  return { create, update, remove }
}

// ---------- conversations ----------

export interface ListParams {
  view: string
  section?: string
  /** Custom sections: all section ids in display order, so the server keeps sections exclusive. */
  sections?: string
  q?: string
  /** A layout section (core/layouts.py): the server resolves its definition. */
  layout?: string
  tab?: string
  sec?: string
  cursor?: string | null
  limit?: number
}

export function useConversations(p: ListParams, enabled = true) {
  // Changes waiting for the connection stay applied over whatever the server or this device's
  // cache answers, until they are sent (lib/offlineQueue.ts).
  const ops = usePending((s) => s.ops)
  const pkey = JSON.stringify(p)
  const select = useCallback((d: Page<ConversationSummary>) => overlayPage(d, JSON.parse(pkey) as ListParams, ops), [ops, pkey])
  return useQuery<Page<ConversationSummary>>({
    ...conversationsQuery(p),
    select: ops.length ? select : undefined,
    placeholderData: keepPreviousData,
    enabled,
  })
}

/** One page of a conversation list as a query: useConversations reads it, and the inbox
 *  prefetches its other tabs with it, so switching tabs finds the rows already there. */
export function conversationsQuery(p: ListParams) {
  return {
    queryKey: qk.conversationList(p),
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      api.get<Page<ConversationSummary>>(
        '/conversations',
        { view: p.view, section: p.section, sections: p.sections, q: p.q, layout: p.layout, tab: p.tab, sec: p.sec, cursor: p.cursor ?? undefined, limit: p.limit ?? 50 },
        signal,
      ),
    staleTime: 10_000,
  }
}

/** One conversation with its messages (no bodies), as a query: the reader reads it, and the list
 *  prefetches it (features/conversation/prefetch.ts). GET /conversations/{id} changes nothing. */
export function conversationQuery(id: number) {
  return {
    queryKey: qk.conversation(id),
    queryFn: ({ signal }: { signal: AbortSignal }) => api.get<ConversationDetail>(`/conversations/${id}`, undefined, signal),
    staleTime: 10_000,
  }
}

export function useConversation(id: number | null) {
  const ops = usePending((s) => s.ops)
  const select = useCallback((d: ConversationDetail) => overlayDetail(d, ops), [ops])
  return useQuery<ConversationDetail>({
    ...conversationQuery(id ?? 0),
    select: ops.length ? select : undefined,
    enabled: !!id,
  })
}

/** A message's sanitised body, as a query. The body never changes, so it never goes stale; GET
 *  /messages/{id}/body changes nothing (no read flag), so it may be prefetched. */
export function messageBodyQuery(id: number, images: boolean) {
  return {
    queryKey: qk.body(id, images),
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      api.get<MessageBody>(`/messages/${id}/body`, { images: images ? 'allowed' : 'blocked' }, signal),
    staleTime: Infinity,
  }
}

export function useMessageBody(id: number, images: boolean, enabled = true) {
  return useQuery<MessageBody>({ ...messageBodyQuery(id, images), enabled })
}

/** An email attached to a message, rendered like a body (mail.open_attached_email). */
export function useAttachedEmail(id: number, part: number | undefined, images: boolean, enabled = true) {
  return useQuery<AttachedEmailBody>({
    queryKey: qk.attachedEmail(id, part ?? -1, images),
    queryFn: ({ signal }) => api.get<AttachedEmailBody>(`/messages/${id}/attachments/${part}/email`, { images: images ? 'allowed' : 'blocked' }, signal),
    enabled: enabled && part !== undefined,
    staleTime: Infinity,
  })
}

export function usePeople(q: string) {
  return useQuery<Person[]>({
    queryKey: qk.people(q),
    queryFn: async ({ signal }) => asList<Person>(await api.get<unknown>('/people', { q, limit: 8 }, signal)),
    enabled: q.trim().length > 0,
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  })
}

// ---------- actions (optimistic) ----------

// Each action: the toast for one conversation, and the plural key for several (a count with {{count}}).
const ACTION_TEXT: Partial<Record<ActionRequest['action'], [string, string]>> = {
  archive: [tr('api.hooks.conversationArchived'), 'api.hooks.conversationsArchived'],
  inbox: [tr('api.hooks.conversationMovedToInbox'), 'api.hooks.conversationsMovedToInbox'],
  trash: [tr('api.hooks.conversationMovedToTrash'), 'api.hooks.conversationsMovedToTrash'],
  untrash: [tr('api.hooks.conversationRestored'), 'api.hooks.conversationsRestored'],
  delete_forever: [tr('api.hooks.conversationDeletedForever'), 'api.hooks.conversationsDeletedForever'],
  spam: [tr('api.hooks.conversationMarkedAsSpam'), 'api.hooks.conversationsMarkedAsSpam'],
  not_spam: [tr('api.hooks.conversationMarkedAsNotSpam'), 'api.hooks.conversationsMarkedAsNotSpam'],
  read: [tr('api.hooks.conversationMarkedAsRead'), 'api.hooks.conversationsMarkedAsRead'],
  unread: [tr('api.hooks.conversationMarkedAsUnread'), 'api.hooks.conversationsMarkedAsUnread'],
  important: [tr('api.hooks.markedAsImportant'), 'api.hooks.conversationsMarkedAsImportant'],
  not_important: [tr('api.hooks.markedAsNotImportant'), 'api.hooks.conversationsMarkedAsNotImportant'],
  snooze: [tr('api.hooks.conversationSnoozed'), 'api.hooks.conversationsSnoozed'],
  unsnooze: [tr('api.hooks.conversationUnsnoozed'), 'api.hooks.conversationsUnsnoozed'],
  cancel_follow_up: [tr('api.hooks.followUpReminderCancelled'), 'api.hooks.followUpRemindersCancelled'],
  mute: [tr('api.hooks.conversationMuted'), 'api.hooks.conversationsMuted'],
  unmute: [tr('api.hooks.conversationUnmuted'), 'api.hooks.conversationsUnmuted'],
  move: [tr('api.hooks.conversationMoved'), 'api.hooks.conversationsMoved'],
  add_labels: [tr('api.hooks.labelApplied'), 'api.hooks.conversationsLabelled'],
  remove_labels: [tr('api.hooks.labelRemoved'), 'api.hooks.conversationsUpdated'],
  category: [tr('api.hooks.conversationMovedToCategory'), 'api.hooks.conversationsMovedToCategory'],
}

// Actions after which a conversation's new-mail notification has nothing left to say.
const CLOSES_NOTIFICATION = new Set<ActionRequest['action']>(['read', 'archive', 'trash', 'spam', 'delete_forever', 'snooze', 'mute', 'move'])

// Actions that remove the conversation from the current list (except "all"-style views).
const REMOVES = new Set<ActionRequest['action']>(['archive', 'trash', 'spam', 'delete_forever', 'snooze', 'move', 'untrash', 'not_spam', 'mute'])

export function patchSummary(c: ConversationSummary, req: ActionRequest): ConversationSummary {
  switch (req.action) {
    case 'star':
      return { ...c, starred: true }
    case 'unstar':
      return { ...c, starred: false }
    case 'read':
      return { ...c, unread_count: 0 }
    case 'unread':
      return { ...c, unread_count: Math.max(1, c.unread_count) }
    case 'important':
      return { ...c, important: true }
    case 'not_important':
      return { ...c, important: false }
    case 'add_labels':
      return { ...c, label_ids: Array.from(new Set([...c.label_ids, ...(req.label_ids ?? [])])) }
    case 'remove_labels':
      return { ...c, label_ids: c.label_ids.filter((l) => !(req.label_ids ?? []).includes(l)) }
    case 'mute':
      return { ...c, muted: true }
    case 'unmute':
      return { ...c, muted: false }
    case 'snooze':
      return { ...c, snoozed_until: req.until ?? null }
    case 'unsnooze':
      return { ...c, snoozed_until: null }
    case 'follow_up':
      return { ...c, follow_up_at: req.until ?? null, nudged_at: null }
    case 'cancel_follow_up':
      return { ...c, follow_up_at: null, nudged_at: null }
    case 'archive':
    case 'trash':
    case 'spam':
      return c.nudged_at ? { ...c, nudged_at: null } : c
    default:
      return c
  }
}

export function shouldRemove(params: ListParams | undefined, req: ActionRequest): boolean {
  if (!params) return false
  const v = params.view
  if (REMOVES.has(req.action)) {
    if (v === 'all' && (req.action === 'archive' || req.action === 'move' || req.action === 'snooze' || req.action === 'mute')) return false
    if (req.action === 'untrash' && v !== 'trash') return false
    if (req.action === 'not_spam' && v !== 'spam') return false
    if (req.action === 'snooze' && v === 'snoozed') return false
    return true
  }
  if (req.action === 'unstar' && v === 'starred') return true
  if (req.action === 'not_important' && v === 'important') return true
  if (req.action === 'remove_labels' && viewLabelOf(v) !== null && (req.label_ids ?? []).includes(viewLabelOf(v)!)) return true
  if (req.action === 'unsnooze' && v === 'snoozed') return true
  // Show in tab: the row leaves the tab it was in (its new tab lists it after the refetch).
  if (req.action === 'show_in_tab' && params.tab && req.tab && params.tab !== req.tab) return true
  if (req.action === 'inbox' && v === 'inbox') return false
  if (params.section === 'unread' && req.action === 'read') return false // keep until refresh (Gmail keeps it)
  return false
}

/** A list page with the waiting offline changes applied (rows they remove are left out). */
export function overlayPage(page: Page<ConversationSummary>, params: ListParams, ops: PendingOp[]): Page<ConversationSummary> {
  const queued = ops.filter((o) => o.state === 'queued')
  if (!queued.length || !page?.items) return page
  let removed = 0
  const items = page.items.flatMap((c) => {
    let cur = c
    for (const o of queued) {
      if (!o.req.ids.includes(c.id)) continue
      if (shouldRemove(params, o.req)) {
        removed++
        return []
      }
      cur = patchSummary(cur, o.req)
    }
    return [cur]
  })
  return removed || items.some((c, i) => c !== page.items[i]) ? { ...page, items, total: Math.max(0, page.total - removed) } : page
}

/** An open conversation with the waiting offline changes applied. */
export function overlayDetail(d: ConversationDetail, ops: PendingOp[]): ConversationDetail {
  const mine = ops.filter((o) => o.state === 'queued' && o.req.ids.includes(d.conversation.id))
  if (!mine.length) return d
  let conv = d.conversation
  let messages = d.messages
  for (const o of mine) {
    conv = patchSummary(conv, o.req)
    if (o.req.action === 'read') messages = messages.map((m) => ({ ...m, seen: true }))
  }
  return { conversation: conv, messages }
}

/** Whether a list on screen will drop any of these conversations for this action. */
function leavesVisibleList(qc: QueryClient, ids: number[], req: ActionRequest): boolean {
  if (!REMOVES.has(req.action)) return false
  const idSet = new Set(ids)
  return qc.getQueryCache().findAll({ queryKey: qk.conversations, type: 'active' }).some((q) => {
    const data = q.state.data as Page<ConversationSummary> | undefined
    const params = (q.queryKey as readonly unknown[])[1] as ListParams | undefined
    return !!data?.items.some((c) => idSet.has(c.id)) && shouldRemove(params, req)
  })
}

/** The title of an Inbox section that lists every conversation of a label, archived ones too
 *  ("Only mail in the Inbox" off), when one of these conversations is in it: archived there, the
 *  row stays in that section, and the toast says so. */
function allMailSectionHolding(qc: QueryClient, ids: number[]): string | null {
  const idSet = new Set(ids)
  const layouts = qc.getQueryData<LayoutsResponse>(qk.layouts)?.layouts ?? []
  for (const q of qc.getQueryCache().findAll({ queryKey: qk.conversations, type: 'active' })) {
    const params = (q.queryKey as readonly unknown[])[1] as ListParams | undefined
    if (!params?.layout || !params.tab || !params.sec) continue
    const data = q.state.data as Page<ConversationSummary> | undefined
    if (!data?.items.some((c) => idSet.has(c.id))) continue
    const sec = layouts.find((l) => l.id === params.layout)?.tabs.find((tb) => tb.id === params.tab)?.sections.find((x) => x.id === params.sec)
    if (sec && sec.base.startsWith('label:') && !sec.inbox_only) return sec.title
  }
  return null
}

/** Where an action that takes conversations out of a list puts them (sidebar counts). */
const MOVES_TO: Partial<Record<ActionRequest['action'], string>> = {
  trash: 'trash', spam: 'spam', archive: 'archive', untrash: 'inbox', not_spam: 'inbox', inbox: 'inbox',
}

function applyOptimistic(qc: QueryClient, ids: number[], req: ActionRequest) {
  const idSet = new Set(ids)
  const held = useUI.getState().held
  if (held && idSet.has(held.id)) useUI.getState().setHeld(REMOVES.has(req.action) ? null : { ...held, c: patchSummary(held.c, req) })
  const lists = qc.getQueriesData<Page<ConversationSummary>>({ queryKey: qk.conversations })
  const gone = new Set<number>()
  for (const [key, data] of lists) {
    if (!data) continue
    const params = (key as readonly unknown[])[1] as ListParams | undefined
    const remove = shouldRemove(params, req)
    let removed = 0
    let removedUnread = 0
    const items = data.items.flatMap((c) => {
      if (!idSet.has(c.id)) return [c]
      if (remove) {
        removed++
        if (c.unread_count > 0) removedUnread++
        gone.add(c.id)
        return []
      }
      return [patchSummary(c, req)]
    })
    qc.setQueryData<Page<ConversationSummary>>(key, { ...data, items, total: Math.max(0, data.total - removed) })
    // The sidebar follows at once; the server's `counts` event then confirms the numbers.
    if (removed && params && !params.cursor && !params.q && !params.section && !params.layout) {
      adjustCounts(qc, params.view ?? 'inbox', MOVES_TO[req.action] ?? null, removed, removedUnread)
    }
  }
  if (gone.size) {
    useUI.getState().noteRemoved(ids)
    // A conversation that left the list leaves the selection too, whichever button, menu or key
    // removed it (the reader's Delete used to leave it selected and out of sight: the toolbar's
    // Archive and the e key then acted on it again, 3 Oct 2026).
    useUI.getState().dropFromSelection([...gone])
    // Nor does the keyboard cursor stay on it (e, #, s … act on the focused row).
    const f = useUI.getState().focusedId
    if (f != null && gone.has(f)) useUI.getState().setFocused(null)
  }
  for (const id of ids) {
    const det = qc.getQueryData<ConversationDetail>(qk.conversation(id))
    if (!det) continue
    const conv = patchSummary(det.conversation, req)
    // Marking a conversation unread marks only its latest message (as the server does).
    const lastId = [...det.messages].reverse().find((m) => !m.draft)?.id
    const messages = det.messages.map((m) => {
      if (req.action === 'unread') return m.id === lastId ? { ...m, seen: false } : m
      switch (req.action) {
        case 'star':
          return m
        case 'unstar':
          return { ...m, flagged: false }
        case 'read':
          return { ...m, seen: true }
        default:
          return m
      }
    })
    qc.setQueryData<ConversationDetail>(qk.conversation(id), { conversation: conv, messages })
  }
}

export interface RunActionOptions {
  scope?: 'conversations' | 'messages'
  /** Suppress the confirmation toast (e.g. auto mark-as-read on open). */
  silent?: boolean
  /** Custom toast text. */
  message?: string
  /** Called after success (e.g. navigate back to list). */
  onDone?: () => void
  /** Conversation ids affected when scope is messages (for optimistic updates). */
  conversationIds?: number[]
}

/** A conversation's row as the cached lists (or its open view) hold it, before the action. */
function cachedSummary(lists: [unknown, unknown][], details: readonly (readonly [unknown, unknown])[], id: number): ConversationSummary | undefined {
  for (const [, d] of lists) {
    const hit = (d as Page<ConversationSummary> | undefined)?.items?.find?.((c) => c.id === id)
    if (hit) return hit
  }
  for (const [, d] of details) {
    const c = (d as ConversationDetail | undefined)?.conversation
    if (c?.id === id) return c
  }
  return undefined
}

export function useActions() {
  const qc = useQueryClient()

  async function run(req: ActionRequest, opts: RunActionOptions = {}): Promise<ActionResponse | null> {
    const scope = opts.scope ?? 'conversations'
    // "All N conversations in this list" is selected and the action is for the selection (from
    // any button, menu, ribbon or shortcut): run it on the whole list on the server, not just
    // on the rows loaded on this page.
    const ui = useUI.getState()
    if (scope === 'conversations' && ui.allMatching && ui.allMatchingQuery && req.ids.length > 0 && !req.query
        && req.ids.every((id) => ui.selected.has(id))) {
      const lq = ui.allMatchingQuery
      const exclude = [...ui.excluded]
      ui.setAllMatching(null)
      ui.clearSelection()
      const { ids: _ids, ...rest } = req
      const query = { ...(lq.layout ? { view: lq.view, q: lq.q, layout: lq.layout, tab: lq.tab, sec: lq.sec } : { view: lq.view, q: lq.q }),
        ...(exclude.length ? { exclude_ids: exclude } : {}) }
      try {
        await runOnQuery(qc, { ...rest, ids: [], query } as ActionRequest, lq.total - exclude.length)
        opts.onDone?.()
      } catch (e) {
        toastError(e, tr('common.actionFailed'))
      }
      return null
    }
    // A section header's menus (Snooze all, Move all to, Label all): the ids stand for the whole list.
    const target = scope === 'conversations' && !req.query ? listTargetOf(req.ids) : undefined
    if (target) {
      const { ids: _ids, ...rest } = req
      if (target.confirm && !(await target.confirm(req))) return null
      try {
        await runOnQuery(qc, { ...rest, ids: [], query: target.query } as ActionRequest, target.total, target.text)
        opts.onDone?.()
      } catch (e) {
        toastError(e, tr('common.actionFailed'))
      }
      return null
    }
    const convIds = scope === 'conversations' ? req.ids : (opts.conversationIds ?? [])
    await qc.cancelQueries({ queryKey: qk.conversations })
    const snapshot = qc.getQueriesData({ queryKey: qk.conversations })
    const detailSnap = convIds.map((id) => [qk.conversation(id), qc.getQueryData(qk.conversation(id))] as const)
    // Offline, the change waits on this device and is sent when the connection is back
    // (lib/offlineQueue.ts); the same idempotency key goes with it, so it is applied once.
    const key = newIdempotencyKey()
    const userId = qc.getQueryData<Me>(qk.me)?.user?.id
    const canQueue = userId != null && queueable(req, scope)
    const queueIt = async (): Promise<null> => {
      const op = await enqueue(userId!, req, key)
      if (!opts.silent) {
        let tid = -1
        const undoFn = () => {
          useUI.getState().setUndo(null)
          useUI.getState().dismissToast(tid)
          if (op) void discardPending(op.id)
          for (const [k, d] of snapshot) qc.setQueryData(k, d)
          for (const [k, d] of detailSnap) qc.setQueryData(k, d)
        }
        useUI.getState().setUndo(undoFn)
        const pair = ACTION_TEXT[req.action]
        const n = req.ids.length
        const what = opts.message ?? (pair ? (n === 1 ? pair[0] : tr(pair[1], { count: n })) : tr('api.hooks.done'))
        tid = toast({ message: tr('api.hooks.itSyncsWhenYouAre', { what }), actionLabel: tr('common.undo'), onAction: undoFn })
      }
      opts.onDone?.()
      return null
    }
    if (canQueue && typeof navigator !== 'undefined' && navigator.onLine === false) {
      if (leavesVisibleList(qc, convIds, req)) await animateOut(convIds, req.action)
      applyOptimistic(qc, convIds, req)
      return queueIt()
    }
    // The request starts now; rows the visible list will drop first play their exit.
    const post = api.post<ActionResponse>(`/${scope}/actions`, req as unknown as Record<string, unknown>, {
      idempotencyKey: key,
    })
    post.catch(() => undefined) // awaited below; this only stops an early "unhandled" warning
    const staysIn = req.action === 'archive' && scope === 'conversations' ? allMailSectionHolding(qc, convIds) : null
    let appliedAt = Date.now()
    if (convIds.length && scope === 'conversations') {
      if (leavesVisibleList(qc, convIds, req)) await animateOut(convIds, req.action)
      applyOptimistic(qc, convIds, req)
      appliedAt = Date.now()
      if (req.action === 'star' || req.action === 'unstar') requestAnimationFrame(() => pop(convIds, 'star'))
      if (req.action === 'important' || req.action === 'not_important') requestAnimationFrame(() => pop(convIds, 'important'))
    }
    try {
      const res = await post
      if (!opts.silent) {
        const n = req.ids.length
        const pair = ACTION_TEXT[req.action]
        // Archive that changed nothing (already archived, or in Trash or Spam) says so.
        const none = req.action === 'archive' && res?.affected === 0
        const said = none ? (tr('api.hooks.nothingToArchiveNoneOf', { count: n }))
          : opts.message ?? (pair ? (n === 1 ? pair[0] : tr(pair[1], { count: n })) : tr('api.hooks.done'))
        // Mail of another account filed in a folder made in an account: it stays where it arrived.
        const home = scope === 'conversations' ? filedFromOtherAccount(req, qc.getQueryData<Label[]>(qk.labels) ?? [],
          convIds.map((id) => cachedSummary(snapshot, detailSnap, id)), qc.getQueryData<SidebarCounts>(sidebarCountsKey)?.accounts ?? []) : null
        const message = home ? tr('api.hooks.staysInItsAccount', { what: said, address: home.address })
          : staysIn && !none ? tr('api.hooks.archivedStaysInSection', { what: said, section: staysIn }) : said
        const undo = res?.undo
        // Undo (the button or z) replaces this toast with its own result: the old Undo never stays
        // on screen to be pressed again.
        let tid = -1
        const undoFn = undo
          ? () => {
              useUI.getState().setUndo(null)
              useUI.getState().dismissToast(tid)
              void run({ ids: req.ids, ...undo } as ActionRequest, { scope, message: tr('api.hooks.actionUndone') }).then(async () => {
                // Rows that come back slide into the place they left from.
                if (!REMOVES.has(req.action) || scope !== 'conversations') return
                await qc.refetchQueries({ queryKey: qk.conversations, type: 'active' })
                requestAnimationFrame(() => animateIn(req.ids, req.action))
              })
            }
          : null
        useUI.getState().setUndo(undoFn)
        tid = toast({ message, actionLabel: undoFn ? tr('common.undo') : undefined, onAction: undoFn ?? undefined })
      }
      opts.onDone?.()
      // Its notification goes too, on every device this browser shows it on.
      if (scope === 'conversations' && CLOSES_NOTIFICATION.has(req.action)) void closeConversationNotifications(convIds)
      return res
    } catch (e) {
      // The network failed (not a refusal): keep the change on screen and send it later.
      if (canQueue && (e as ApiError).status === 0) return queueIt()
      // Back to the snapshot, but only where nothing else (a live change, another action) has
      // written since this change was shown; those caches are refetched below anyway.
      const untouched = (k: readonly unknown[]) => (qc.getQueryState(k)?.dataUpdatedAt ?? 0) <= appliedAt
      for (const [k, d] of snapshot) if (untouched(k)) qc.setQueryData(k, d)
      for (const [k, d] of detailSnap) if (untouched(k)) qc.setQueryData(k, d)
      toastError(e, tr('common.actionFailed'))
      return null
    } finally {
      qc.invalidateQueries({ queryKey: qk.conversations })
      qc.invalidateQueries({ queryKey: qk.labels })
      for (const id of convIds) qc.invalidateQueries({ queryKey: qk.conversation(id) })
    }
  }

  return { run }
}

type BulkUndo = Record<string, unknown> | null | undefined

/** What a select-all or section action said when it finished (the toast). */
export interface BulkText {
  /** e.g. n => t('…', { count: n }): "Archived 3 conversations in Everything else" */
  done?: (n: number) => string
}

/** The Undo of a select-all action: the record the server sent back (a list small enough to run
 *  at once) or, after a job, the one it kept for that job. A job's undo runs as a job too. */
function bulkDone(qc: QueryClient, message: string, undo: BulkUndo) {
  if (!undo) {
    toast({ message })
    return
  }
  let tid = -1
  const fn = () => {
    useUI.getState().setUndo(null)
    useUI.getState().dismissToast(tid)
    void api.post<{ job?: number; total?: number }>('/conversations/actions', { ids: [], ...undo }, { idempotencyKey: newIdempotencyKey() })
      .then(async (res) => {
        if (res?.job) await followJob(qc, res.job, res.total ?? 0, { done: () => tr('api.hooks.actionUndone') }, false)
        else toast({ message: tr('api.hooks.actionUndone') })
      })
      .catch((e) => toastError(e, tr('common.undoFailed')))
      .finally(() => invalidateMail(qc))
  }
  useUI.getState().setUndo(fn)
  tid = toast({ message, actionLabel: tr('common.undo'), onAction: fn })
}

/** Follow a bulk job until it finishes (progress toast), then report it with its Undo. */
async function followJob(qc: QueryClient, job: number, total: number, text: BulkText, offerUndo = true): Promise<void> {
  const id = toast({ message: tr('api.hooks.workingOnConversations', { count: total }), duration: 0 })
  // Every 1.5 s at first, then up to every 10 s; a job that shows no progress for ten minutes is
  // left to the server (a stuck job no longer keeps this tab polling).
  let wait = 1500
  let last = -1
  let moved = Date.now()
  try {
    for (;;) {
      await new Promise((r) => setTimeout(r, wait))
      wait = Math.min(10_000, Math.round(wait * 1.3))
      const st = await api.get<{ status: string; done: number; total: number; error: string; undo?: BulkUndo }>(`/bulk/${job}`)
      if (st.done !== last) {
        last = st.done
        moved = Date.now()
      } else if (Date.now() - moved > 600_000) {
        toast({ message: tr('api.hooks.jobStillRunning'), duration: 10_000 })
        return
      }
      if (st.status === 'done' || st.status === 'failed') {
        if (st.status === 'failed') toast({ message: tr('api.hooks.stoppedAfterOf', { done: st.done, total: st.total, error: st.error || 'error' }), error: true })
        else {
          const n = st.total
          bulkDone(qc, text.done ? text.done(n) : tr('api.hooks.conversationsUpdated2', { count: n }), offerUndo ? st.undo : null)
        }
        return
      }
    }
  } finally {
    useUI.getState().dismissToast(id)
    invalidateMail(qc)
  }
}

/** An action on a whole list (ActionRequest.query); large lists run on the server as a job,
 *  followed here until it finishes. */
export async function runOnQuery(qc: QueryClient, req: ActionRequest, total: number, text: BulkText = {}): Promise<void> {
  const res = await api.post<BulkResult>(
    '/conversations/actions', req as unknown as Record<string, unknown>, { idempotencyKey: newIdempotencyKey() })
  await followBulkResult(qc, res, total, text)
}

/** What an action on many conversations answers: the result with its Undo (run at once), or a job. */
export interface BulkResult {
  job?: number
  total?: number
  affected?: number
  conversations?: number
  undo?: BulkUndo
}

/** Report an action on many conversations: its toast with Undo, or, for a job, a progress toast
 *  until it finishes and then its Undo (the Subscriptions and Clean up pages use it too). */
export async function followBulkResult(qc: QueryClient, res: BulkResult | null | undefined, total: number, text: BulkText = {}): Promise<void> {
  if (!res?.job) {
    // `affected` counts messages; the conversations are what the list counts.
    const undoIds = (res?.undo as { ids?: unknown[] } | null | undefined)?.ids
    const n = res?.conversations ?? undoIds?.length ?? res?.affected ?? total
    bulkDone(qc, text.done ? text.done(n) : tr('api.hooks.conversationsUpdated2', { count: n }), res?.undo)
    invalidateMail(qc)
    return
  }
  await followJob(qc, res.job, res.total ?? total, text)
}

/** A whole list standing in for an id list: a menu that runs `run({ ids })` (Snooze, Move to,
 *  Label as) acts on every conversation of `query` when given ids made here. Keyed by the array
 *  itself, so ordinary id lists never match. */
export interface ListTarget {
  query: NonNullable<ActionRequest['query']>
  total: number
  text?: BulkText
  /** Asked before the action runs (a section's confirmation); false cancels it. */
  confirm?: (req: ActionRequest) => Promise<boolean>
}
const listTargets = new WeakMap<number[], ListTarget>()
/** The list an id array stands for (idsForList), if any. */
export function listTargetOf(ids: number[]): ListTarget | undefined {
  return listTargets.get(ids)
}
export function idsForList(target: ListTarget, loaded: number[] = []): number[] {
  const ids = [...loaded]
  listTargets.set(ids, target)
  return ids
}

// ---------- invalidation helpers ----------

export function invalidateMail(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: qk.conversations })
  // The Subscriptions and Clean up pages count the same mail.
  qc.invalidateQueries({ queryKey: ['mailgroups'] })
  qc.invalidateQueries({ queryKey: qk.labels })
  qc.invalidateQueries({ queryKey: ['conversation'] })
}

// ---------- inbox layouts ----------

export function useLayouts() {
  return useQuery<LayoutsResponse>({
    queryKey: qk.layouts,
    queryFn: ({ signal }) => api.get<LayoutsResponse>('/layouts', undefined, signal),
    staleTime: 60_000,
  })
}

/** The layout the inbox shows: the user's choice, or the preset matching the settings while
 *  the layouts load (so the first paint does not wait). */
export function useActiveLayout(): { layout: InboxLayout | null; loading: boolean } {
  const q = useLayouts()
  const settings = useSettings()
  const want = settings.inbox_type.startsWith('layout:') ? settings.inbox_type.slice(7) : settings.inbox_type
  const layout = q.data?.layouts.find((l) => l.id === want) ?? q.data?.layouts.find((l) => l.id === 'default') ?? null
  return { layout, loading: q.isLoading }
}

/** What each tab of the layout shows by its label (lib/tabIndicators.ts), by tab id. */
export function useLayoutCounts(layout: InboxLayout | null) {
  const any = !!layout && layout.tabs.length > 1 && layout.tabs.some((t) => indicatorsOf(t).length > 0)
  return useQuery<Record<string, TabCounts>>({
    queryKey: qk.layoutCounts(layout?.id ?? ''),
    queryFn: async ({ signal }) =>
      (await api.get<{ tabs: Record<string, TabCounts> }>(`/layouts/${encodeURIComponent(layout!.id)}/counts`, { detail: true }, signal)).tabs,
    enabled: any,
    staleTime: 15_000,
  })
}

export function useSaveLayouts() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { layouts: InboxLayout[]; active?: string }) => api.put<LayoutsResponse>('/layouts', body as unknown as Record<string, unknown>),
    onSuccess: (data) => {
      qc.setQueryData(qk.layouts, data)
      void qc.invalidateQueries({ queryKey: qk.me })
      void qc.invalidateQueries({ queryKey: qk.conversations })
    },
  })
}
