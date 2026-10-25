// Data for the Tasks app. Reads go through react-query under the
// 'tasks' prefix, so a live `task` or `calendar` change refreshes them (api/queryKeys.ts).
//
// The server's smart lists, counts, tags, move and bulk routes may not be there yet: the
// app probes GET /tasks/counts once, and without it reads every task (GET /tasks?status=all) and
// computes the lists, counts and tags itself (model.ts). Move and bulk fall back to PATCH and
// DELETE per task; list and tag management reports when the server cannot do it yet.

import { CLIENT_ID } from '../../lib/live/clientId'
import { useInfiniteQuery, useQueries, useQuery, type QueryClient } from '@tanstack/react-query'
import { useMemo } from 'react'
import { ApiError, buildUrl, getCsrf, notifyUnauthorized } from '../../api/client'
import { codedText } from '../../api/serverMessages'
import { qk, useSettings } from '../../api/hooks'
import type { CalendarDefaults } from '../../api/types'
import { resolveDatePrefs, type DatePrefs } from '../../lib/dates'
import type { TaskListRef } from '../../lib/routes'
import { computeCounts, deriveTags, type Counts, type TagInfo, type TaskItem, type TaskList } from './model'
import { t as tr } from '../../i18n'

// ---------------------------------------------------------------- requests

/** A 409 on a write: the server's copy came back with it. */
export class ConflictError extends Error {
  server: TaskItem | null
  kind: string
  /** The whole 409 answer (a live-editing conflict carries base, theirs and mine). */
  body?: Record<string, unknown>
  constructor(message: string, server: TaskItem | null, kind = 'conflict', body?: Record<string, unknown>) {
    super(message)
    this.server = server
    this.kind = kind
    this.body = body
  }
}

type Query = Record<string, string | number | boolean | null | undefined | string[]>

function urlWith(path: string, query?: Query): string {
  if (!query) return buildUrl(path)
  const flat: Record<string, string> = {}
  const multi: [string, string][] = []
  for (const [k, v] of Object.entries(query)) {
    if (Array.isArray(v)) for (const x of v) multi.push([k, x])
    else if (v !== undefined && v !== null && v !== '') flat[k] = String(v)
  }
  let url = buildUrl(path, flat)
  if (multi.length) url += (url.includes('?') ? '&' : '?') + multi.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&')
  return url
}

/** fetch for the tasks routes: JSON, CSRF, and a 409 body kept whole (ConflictError). */
export async function taskRequest<T>(method: string, path: string, body?: unknown, query?: Query, signal?: AbortSignal): Promise<T> {
  // This tab's id (lib/live/clientId.ts): live events say which client made a change.
  const headers: Record<string, string> = { Accept: 'application/json', 'X-Mailr-Client': CLIENT_ID }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-CSRF-Token'] = getCsrf()
  let res: Response
  try {
    res = await fetch(urlWith(path, query), { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin', signal })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, tr('tasks.api.networkErrorCheckYourConnection'))
  }
  if (res.status === 401) notifyUnauthorized()
  if (res.status === 204) return undefined as T
  const data = (res.headers.get('content-type') ?? '').includes('json') ? await res.json().catch(() => null) : await res.text()
  if (res.status === 409) {
    const d = (data ?? {}) as { error?: string; detail?: unknown; server?: TaskItem }
    throw new ConflictError((await codedText(data)) ?? (typeof d.detail === 'string' ? d.detail : tr('tasks.api.theTaskChangedOnAnother')), d.server ?? null, d.error ?? 'conflict', (data ?? undefined) as Record<string, unknown> | undefined)
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail ?? data
    throw new ApiError(res.status, (await codedText(data)) ?? (typeof detail === 'string' ? detail : `${res.status} ${res.statusText}`), detail)
  }
  return data as T
}

/** A route the server does not have yet (B2 lands it): 404 or 405 from the router. */
export function isMissingRoute(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.status === 405) && (e.message === 'Not Found' || e.message === 'Method Not Allowed')
}

export const enc = encodeURIComponent

// ---------------------------------------------------------------- dates

/** The user's zone, clock and week start (settings, else the browser). */
export function useDatePrefs(): DatePrefs {
  const s = useSettings() as unknown as { timezone?: string; week_start?: number | string; time_format?: string }
  return useMemo(() => resolveDatePrefs(s), [s])
}

// ---------------------------------------------------------------- lists, defaults, counts, tags

export function useTaskLists() {
  return useQuery<TaskList[]>({
    queryKey: qk.tasks.lists,
    queryFn: async ({ signal }) => {
      const all = await taskRequest<(TaskList & { error?: string })[]>('GET', '/calendar/calendars', undefined, undefined, signal)
      return (Array.isArray(all) ? all : [])
        .filter((c) => c.tasks !== false && !c.error && !c.hidden)
        .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
    },
    staleTime: 30_000,
  })
}

export function useDefaults() {
  return useQuery<CalendarDefaults>({
    queryKey: qk.calendar.defaults,
    queryFn: ({ signal }) => taskRequest<CalendarDefaults>('GET', '/calendar/defaults', undefined, undefined, signal),
    staleTime: 60_000,
  })
}

interface ServerCounts {
  smart: { today?: number; overdue?: number; upcoming?: number; nodate?: number; no_date?: number; scheduled?: number; high?: number; email?: number; all?: number }
  overdue?: number
  lists: Record<string, number>
  tags: Record<string, number>
}

/** GET /tasks/counts, or null when the server does not have it (then the app computes counts). */
export function useServerCounts() {
  return useQuery<ServerCounts | null>({
    queryKey: qk.tasks.counts,
    queryFn: async ({ signal }) => {
      try {
        return await taskRequest<ServerCounts>('GET', '/tasks/counts', undefined, undefined, signal)
      } catch (e) {
        if (isMissingRoute(e)) return null
        throw e
      }
    },
    retry: false,
    staleTime: 10_000,
  })
}

export interface TaskPage {
  connected: boolean
  items: TaskItem[]
  next_cursor?: string | null
}

const ALL_KEY = qk.tasks.list({ status: 'all' })

/** Every task, open and completed (the fallback source, and the source while B2 is not there). */
export function useAllTasks(enabled: boolean) {
  return useQuery<TaskPage>({
    queryKey: ALL_KEY,
    queryFn: async ({ signal }) => {
      const since = writeMark()
      const r = await taskRequest<TaskPage | TaskItem[]>('GET', '/tasks', undefined, { status: 'all' }, signal)
      return Array.isArray(r) ? { connected: true, items: freshen(r, since) } : { connected: r.connected !== false, items: freshen(r.items ?? [], since) }
    },
    enabled,
    staleTime: 5_000,
  })
}

/** Server parameters for a view. */
export function serverParams(ref: TaskListRef, lists: TaskList[], o: { showCompleted: boolean; showDeferred: boolean; sort: string }): Query {
  const sort = o.sort === 'auto' ? undefined : o.sort
  if (ref.kind === 'list') {
    const l = lists.find((x) => String(x.calendar_id) === ref.id || x.id === ref.id)
    return { list: l ? [l.id] : [ref.id], status: o.showCompleted ? 'all' : 'open', sort: sort ?? 'manual' }
  }
  if (ref.kind === 'tag') return { tag: [ref.id], status: o.showCompleted ? 'all' : 'open', sort }
  switch (ref.id) {
    case 'completed':
      return { smart: 'completed', status: 'completed' }
    case 'upcoming':
      return { smart: 'upcoming', include_future: 1, sort }
    case 'today':
    case 'all':
      return { smart: ref.id, include_future: o.showDeferred ? 1 : undefined, sort }
    default:
      return { smart: ref.id, sort }
  }
}

/** One view from the server, 200 per page. */
export function useServerView(params: Query | null) {
  return useInfiniteQuery({
    queryKey: ['tasks', 'list', 'server', params] as const,
    queryFn: async ({ pageParam, signal }) => {
      const since = writeMark()
      const r = await taskRequest<TaskPage>('GET', '/tasks', undefined, { ...params, limit: 200, cursor: pageParam ?? undefined }, signal)
      return { connected: r.connected !== false, items: freshen(r.items ?? [], since), next_cursor: r.next_cursor ?? null }
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? null,
    enabled: !!params,
    staleTime: 5_000,
  })
}

/** The subtasks of some parents, open and completed (expanded rows and the pane, in server mode). */
export function useChildren(parents: string[], enabled: boolean): TaskItem[] {
  const qs = useQueries({
    queries: parents.map((parent) => ({
      queryKey: ['tasks', 'list', 'children', parent] as const,
      queryFn: async ({ signal }: { signal: AbortSignal }) => {
        const since = writeMark()
        return freshen((await taskRequest<TaskPage>('GET', '/tasks', undefined, { parent, status: 'all' }, signal)).items ?? [], since)
      },
      enabled,
      staleTime: 5_000,
    })),
  })
  const data = qs.map((q) => q.data)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => data.flatMap((d) => d ?? []), [JSON.stringify(data.map((d) => d?.map((t) => `${t.uid}:${t.etag}:${t.status}`)))])
}

/** How many open tasks are due tomorrow (the empty Today's "3 tasks tomorrow →"), in server mode. */
export function useTomorrowCount(enabled: boolean, tomorrow: string): number | null {
  const q = useQuery({
    queryKey: ['tasks', 'list', 'tomorrow', tomorrow] as const,
    queryFn: async ({ signal }) => {
      const since = writeMark()
      return freshen((await taskRequest<TaskPage>('GET', '/tasks', undefined, { smart: 'upcoming', limit: 100 }, signal)).items ?? [], since)
    },
    enabled,
  })
  return q.data ? q.data.filter((t) => (t.due ?? '').slice(0, 10) === tomorrow || (!!t.due && !/^\d{4}-\d{2}-\d{2}$/.test(t.due) && new Date(t.due).toDateString() === new Date(`${tomorrow}T12:00:00`).toDateString())).length : null
}

export function useServerTags(enabled: boolean) {
  return useQuery<TagInfo[] | null>({
    queryKey: qk.tasks.tags,
    queryFn: async ({ signal }) => {
      try {
        return await taskRequest<TagInfo[]>('GET', '/tasks/tags', undefined, undefined, signal)
      } catch (e) {
        if (isMissingRoute(e)) return null
        throw e
      }
    },
    enabled,
    retry: false,
  })
}

/** Counts and tags, from the server when it has them, else from all tasks. */
export function useSidebarData(all: TaskItem[] | undefined, server: ServerCounts | null | undefined, now: Date, zone: string) {
  const tagsQ = useServerTags(server != null)
  return useMemo(() => {
    const local = all ? computeCounts(all, { now, zone }) : null
    let counts: Counts | null = local
    if (server) {
      const s = server.smart
      counts = {
        smart: {
          today: s.today, overdue: s.overdue ?? server.overdue, upcoming: s.upcoming, nodate: s.nodate ?? s.no_date, scheduled: s.scheduled, all: s.all,
          high: s.high ?? local?.smart.high, email: s.email ?? local?.smart.email,
        },
        lists: server.lists ?? {},
        tags: server.tags ?? {},
      }
    }
    const tags = tagsQ.data ?? (all ? deriveTags(all) : [])
    return { counts, tags }
  }, [all, server, tagsQ.data, now, zone])
}

// ---------------------------------------------------------------- the cache

type Cached = TaskPage | { pages: TaskPage[]; pageParams: unknown[] } | TaskItem[] | undefined

function mapCached(d: Cached, fn: (items: TaskItem[]) => TaskItem[]): Cached {
  if (!d) return d
  if (Array.isArray(d)) return fn(d)
  if ('pages' in d) return { ...d, pages: d.pages.map((p) => ({ ...p, items: fn(p.items) })) }
  if ('items' in d) return { ...d, items: fn(d.items) }
  return d
}

/** Apply a change to every cached task list (optimistic updates). */
export function editCaches(qc: QueryClient, fn: (items: TaskItem[]) => TaskItem[]) {
  for (const [key, data] of qc.getQueriesData<Cached>({ queryKey: ['tasks', 'list'] })) qc.setQueryData(key, mapCached(data, fn))
}

export function snapshotCaches(qc: QueryClient) {
  return qc.getQueriesData<Cached>({ queryKey: ['tasks', 'list'] })
}

function itemsOf(d: Cached): TaskItem[] {
  if (!d) return []
  if (Array.isArray(d)) return d
  if ('pages' in d) return d.pages.flatMap((p) => p.items)
  return d.items
}

/** Puts back the tasks `uids` as the snapshot held them, in every list. Only those: the whole
 *  snapshot would also undo what live changes and other writes did meanwhile. */
export function restoreCaches(qc: QueryClient, snap: ReturnType<typeof snapshotCaches>, uids: Iterable<string>) {
  const want = new Set(uids)
  for (const [k, d] of snap) {
    const before = new Map(itemsOf(d).filter((x) => want.has(x.uid)).map((x) => [x.uid, x]))
    if (!before.size) continue
    qc.setQueryData<Cached>(k, (cur) => mapCached(cur, (items) => items.map((x) => before.get(x.uid) ?? x)))
  }
}

// The server's copy of each task this tab last wrote, numbered in the order the answers came back.
// A list read that started before a write came back may hold the copy from before it, and landing
// after the write it would put the old version back in the caches; the next write would then carry
// the old ETag and meet a 409 against this tab's own write (tasks-app e2e, Undo of complete,
// 10 Oct 2026). Each list read takes the newer copy for the tasks written since it started.
const written = new Map<string, { item: TaskItem; seq: number }>()
let writeSeq = 0

/** Where the writes stand now: pass it to `freshen` with the items a read started here returns. */
export function writeMark(): number {
  return writeSeq
}

/** The items of a read that started at `since`, with each task written since then in its written copy. */
export function freshen(items: TaskItem[], since: number): TaskItem[] {
  if (writeSeq === since) return items
  return items.map((x) => {
    const w = written.get(x.uid)
    return w && w.seq > since ? { ...x, ...w.item } : x
  })
}

/** The server's copy from this tab's last write of a task, if any. */
export function lastWritten(uid: string): TaskItem | undefined {
  return written.get(uid)?.item
}

/** Put a task (new or changed) into the "all tasks" cache. */
export function upsertCached(qc: QueryClient, t: TaskItem) {
  written.set(t.uid, { item: t, seq: ++writeSeq })
  editCaches(qc, (items) => (items.some((x) => x.uid === t.uid) ? items.map((x) => (x.uid === t.uid ? { ...x, ...t } : x)) : items))
  const all = qc.getQueryData<TaskPage>(ALL_KEY)
  if (all && !all.items.some((x) => x.uid === t.uid)) qc.setQueryData<TaskPage>(ALL_KEY, { ...all, items: [...all.items, t] })
}

export function findCached(qc: QueryClient, uid: string): TaskItem | undefined {
  for (const [, d] of snapshotCaches(qc)) {
    let hit: TaskItem | undefined
    mapCached(d, (items) => {
      hit ??= items.find((t) => t.uid === uid)
      return items
    })
    if (hit) return hit
  }
  return undefined
}

export function invalidateTasks(qc: QueryClient) {
  void qc.invalidateQueries({ queryKey: qk.tasks.all })
}
