// The Calendar app's server state: collections, the visible range, sync status, and the writes.
// Reads go through TanStack Query under qk.calendar, so live changes
// (api/events.ts) refresh them. Writes carry the version the client saw (`etag`) and turn a 409
// into a ConflictError the app shows as the conflict sheet. Endpoints that a server does not have yet
// (collections PATCH, search, find-time, sync-status) degrade: the caller gets null or a fallback.

import { keepPreviousData, useQuery, type QueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api, ApiError, buildUrl } from '../../api/client'
import { qk } from '../../api/hooks'
import { canonicalOccurrence, addDaysISO, isISODate, parseISO, toISODate, utcIsoformat } from '../../lib/dates'
import { fallbackColour, normalizeColour } from './colour'
import { busyTitle } from './phone'
import type {
  Account, AgendaResponse, ApiEvent, ApiTask, CalEvent, Collection, ConflictBody, EventWrite, Scope, ShareLevel,
  ShareNotice, ShareState, SyncStatus,
} from './types'
import { t as tr } from '../../i18n'

// ---------- errors ----------

export class ConflictError extends Error {
  body: ConflictBody
  constructor(body: ConflictBody) {
    super(body.detail || tr('common.changedElsewhere'))
    this.body = body
  }
}

/** A route this server does not have yet (404 on the route itself, 405, 501). */
export function isMissingRoute(e: unknown): boolean {
  if (!(e instanceof ApiError)) return false
  if (e.status === 405 || e.status === 501) return true
  return e.status === 404 && /^not found$/i.test(e.message)
}

export function isGone(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404 && !isMissingRoute(e)
}

/** A 409 as a ConflictError. The client keeps only `detail`, so the server copy is read separately. */
function conflictOf(e: unknown): ConflictError | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null
  const d = e.detail as ConflictBody | string | undefined
  if (d && typeof d === 'object' && 'error' in d) return new ConflictError(d)
  const exists = typeof d === 'string' && /exist/i.test(d)
  return new ConflictError({ error: exists ? 'exists' : 'conflict', detail: typeof d === 'string' ? d : tr('calendar.data.theEventChangedOnAnother') })
}

/** A path with a query string (api.del takes none of its own). */
function withQuery(path: string, q: Record<string, string | undefined>): string {
  return buildUrl(path, q).replace(/^\/api\/v1/, '')
}

async function write<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn()
  } catch (e) {
    throw conflictOf(e) ?? e
  }
}

// ---------- normalising ----------

export interface Ctx {
  zone: string
  collections: Map<string, Collection>
}

/** A collection's default reminders for a timed or an all-day event. */
export function defaultReminders(c: Collection | undefined, allDay: boolean): number[] | undefined {
  const d = c?.default_reminders
  if (!d) return undefined
  if (Array.isArray(d)) return d
  return (allDay ? d.all_day : d.timed) ?? undefined
}

/** The user's default reminders for a new timed or all-day event (GET /reminders/settings); [] for
 *  none, for event reminders turned off, and while the settings load. */
export function userDefaultReminders(s: { events: boolean; default_timed_event: number | null; default_all_day_event: number | null } | undefined, allDay: boolean): number[] {
  if (!s || !s.events) return []
  const m = allDay ? s.default_all_day_event : s.default_timed_event
  return m == null ? [] : [m]
}

/** `reminders` of a create from the editor: left out while the chips show the untouched defaults
 *  (the server writes the same defaults); otherwise the chips as they are, where [] is "no
 *  reminders" and marks the event silent (no fallback default fires). */
export function reminderField(f: { reminders: number[]; remindersAuto?: boolean }): number[] | undefined {
  return f.remindersAuto ? undefined : f.reminders
}

/** The colour a collection draws with: its own, else a stable palette colour. */
export function collectionColour(c: Collection | undefined, index = 0): string {
  return normalizeColour(c?.color) ?? fallbackColour(c?.calendar_id ?? c?.id ?? index)
}

/** An API event as the views use it (times parsed in the display zone, colour and identity resolved). */
export function normalizeEvent(e: ApiEvent, ctx: Ctx): CalEvent | null {
  const allDay = !!e.all_day || (isISODate(e.start) && (!e.end || isISODate(e.end)))
  let occurrence = e.occurrence || e.recurrence_id || ''
  try {
    if (!occurrence) occurrence = canonicalOccurrence(e.start, allDay, ctx.zone)
  } catch {
    return null
  }
  const col = e.calendar ? ctx.collections.get(e.calendar) : undefined
  let startAt: Date | null
  let endAt: Date | null
  let firstDay: string
  let lastDay: string
  if (allDay) {
    firstDay = e.start.slice(0, 10)
    const endDay = e.end ? e.end.slice(0, 10) : addDaysISO(firstDay, 1)
    lastDay = endDay > firstDay ? addDaysISO(endDay, -1) : firstDay
    startAt = parseISO(firstDay, ctx.zone)
    endAt = parseISO(addDaysISO(lastDay, 1), ctx.zone)
  } else {
    startAt = parseISO(e.start, ctx.zone)
    endAt = e.end ? parseISO(e.end, ctx.zone) : null
    if (!startAt) return null
    if (!endAt || endAt < startAt) endAt = new Date(startAt.getTime())
    firstDay = toISODate(startAt, ctx.zone)
    // An event ending exactly at midnight stays on its last real day.
    lastDay = toISODate(new Date(Math.max(startAt.getTime(), endAt.getTime() - 1)), ctx.zone)
  }
  if (!startAt || !endAt) return null
  const colour = normalizeColour(e.color) ?? normalizeColour(e.calendar_color) ?? collectionColour(col)
  return {
    ...e, key: `${e.uid}|${occurrence}`, occurrence, startAt, endAt, allDay, firstDay, lastDay, colour,
    // A phone calendar's busy time reads "Busy" in the app's language and is never edited here; a
    // block with details keeps the title the phone sent.
    ...(e.phone_busy && !e.phone_details ? { summary: busyTitle() } : {}),
    readOnly: !!col?.read_only || !!e.phone_busy,
  }
}

export function sortEvents(a: CalEvent, b: CalEvent): number {
  return a.startAt.getTime() - b.startAt.getTime() || b.endAt.getTime() - a.endAt.getTime() || a.key.localeCompare(b.key)
}

// ---------- reads ----------

export function useCollections() {
  return useQuery<Collection[]>({
    queryKey: qk.calendar.calendars,
    queryFn: async ({ signal }) => {
      const data = await api.get<Collection[] | { items: Collection[] }>('/calendar/calendars', undefined, signal)
      return Array.isArray(data) ? data : data.items ?? []
    },
    staleTime: 60_000,
    retry: (n, e) => n < 1 && !(e instanceof ApiError && e.status < 500),
  })
}

export function useAccounts() {
  return useQuery<Account[]>({
    queryKey: [...qk.calendar.all, 'accounts'],
    queryFn: ({ signal }) => api.get<Account[]>('/calendar/accounts', undefined, signal),
    staleTime: 5 * 60_000,
  })
}

/** Calendars people shared with this user since the last dismissal (calendar/sharing.py).
 *  ACT calendar.share_notices @web.calendar */
export const SHARE_NOTICES_KEY = [...qk.calendar.all, 'share-notices']
export function useShareNotices() {
  return useQuery<ShareNotice[]>({
    queryKey: SHARE_NOTICES_KEY,
    queryFn: async ({ signal }) => {
      try {
        return (await api.get<{ notices: ShareNotice[] }>('/calendar/share-notices', undefined, signal)).notices ?? []
      } catch (e) {
        if (isMissingRoute(e)) return []
        throw e
      }
    },
    staleTime: 5 * 60_000,
  })
}

export function useDefaults() {
  return useQuery<{ calendar: string | null; task_list: string | null }>({
    queryKey: qk.calendar.defaults,
    queryFn: ({ signal }) => api.get('/calendar/defaults', undefined, signal),
    staleTime: 5 * 60_000,
  })
}

/** GET /calendar/sync-status; null when the server does not have it. */
export function useSyncStatus() {
  return useQuery<SyncStatus | null>({
    queryKey: qk.calendar.syncStatus,
    queryFn: async ({ signal }) => {
      try {
        return await api.get<SyncStatus>('/calendar/sync-status', undefined, signal)
      } catch (e) {
        if (isMissingRoute(e)) return null
        throw e
      }
    },
    refetchInterval: 60_000,
    retry: false,
  })
}

export interface RangeData {
  connected: boolean
  events: ApiEvent[]
  sync: AgendaResponse['sync'] | null
}

/** Occurrences in [from, to): instants, ISO strings. */
/** GET /calendar/agenda for [from, to) (UTC isoformat), as a range query holds it. */
export async function fetchRange(f: string, t: string, signal?: AbortSignal): Promise<RangeData> {
  const r = await api.get<AgendaResponse | ApiEvent[]>('/calendar/agenda', { from: f, to: t }, signal)
  if (Array.isArray(r)) return { connected: true, events: r, sync: null }
  return { connected: r.connected !== false, events: r.events ?? [], sync: r.sync ?? null }
}

export function useRange(from: Date, to: Date, enabled = true) {
  const f = utcIsoformat(from)
  const t = utcIsoformat(to)
  return useQuery<RangeData>({
    queryKey: qk.calendar.range(f, t),
    queryFn: ({ signal }) => fetchRange(f, t, signal),
    placeholderData: keepPreviousData,
    enabled,
    staleTime: 15_000,
    retry: (n, e) => n < 1 && !(e instanceof ApiError && e.status < 500),
  })
}

/** What a query's error banner shows: its last error, kept until a request succeeds.
 *  React Query clears `error` (status back to pending) when a refetch starts on a query that has no
 *  data, so a banner tied to `isError` left the page during every Retry or live refetch while the
 *  server was still failing. Placeholder data from another range is not a success. */
export function settledError(prev: unknown, q: { error: unknown; isSuccess: boolean; isPlaceholderData: boolean }): unknown {
  if (q.error != null) return q.error
  if (q.isSuccess && !q.isPlaceholderData) return null
  return prev
}

export function useSettledError(q: { error: unknown; isSuccess: boolean; isPlaceholderData: boolean }): unknown {
  const [last, setLast] = useState<unknown>(null)
  const next = settledError(last, q)
  // Derived state, adjusted during render (React's pattern for state that follows its inputs).
  if (next !== last) setLast(next)
  return next
}

/** The range's events, normalised and sorted, without the hidden calendars. */
export function useNormalized(events: ApiEvent[] | undefined, zone: string, collections: Collection[] | undefined, hidden: Set<string>): CalEvent[] {
  return useMemo(() => {
    const byUrl = new Map((collections ?? []).map((c) => [c.url ?? c.id, c]))
    const ctx: Ctx = { zone, collections: byUrl }
    const out: CalEvent[] = []
    const seen = new Set<string>()
    for (const e of events ?? []) {
      if (e.calendar && hidden.has(e.calendar)) continue
      const n = normalizeEvent(e, ctx)
      if (!n || seen.has(n.key)) continue
      seen.add(n.key)
      out.push(n)
    }
    return out.sort(sortEvents)
  }, [events, zone, collections, hidden])
}

/** Open tasks with a due date in [from, to] (dates), for "Show tasks on calendar". */
export function useTasksInRange(fromDay: string, toDay: string, zone: string, enabled: boolean) {
  return useQuery<ApiTask[]>({
    queryKey: qk.tasks.onCalendar(fromDay, toDay),
    queryFn: async ({ signal }) => {
      const r = await api.get<{ items: ApiTask[] } | ApiTask[]>('/tasks', { status: 'open', has_due: 1, due_after: fromDay, due_before: addDaysISO(toDay, 1) }, signal)
      const items = Array.isArray(r) ? r : r.items ?? []
      return items.filter((t) => {
        if (!t.due) return false
        const d = isISODate(t.due) ? t.due : toISODate(parseISO(t.due, zone) ?? new Date(0), zone)
        return d >= fromDay && d <= toDay
      })
    },
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
}

// ---------- writes ----------

export interface PatchArgs {
  uid: string
  changes: EventWrite
  etag?: string | null
  scope?: Scope
  occurrence?: string | null
}

export const calApi = {
  /** ACT event.create @web.calendar @web.editor */
  create: (body: EventWrite) => write(() => api.post<ApiEvent>('/calendar/events', compact(body))),

  /** ACT event.edit @web.calendar @web.editor, ACT event.reschedule @web.calendar, ACT event.resize @web.calendar */
  patch: ({ uid, changes, etag, scope, occurrence }: PatchArgs) =>
    write(() => api.patch<ApiEvent>(`/calendar/events/${encodeURIComponent(uid)}`, {
      ...changes, ...(etag ? { etag } : {}), scope: scope ?? 'all', ...(occurrence ? { occurrence } : {}),
    } as Record<string, unknown>)),

  /** ACT event.delete @web.calendar @web.sheet @web.editor */
  remove: (uid: string, o: { scope?: Scope; occurrence?: string | null; etag?: string | null; sendUpdates?: boolean; message?: string } = {}) =>
    write(() => api.del(withQuery(`/calendar/events/${encodeURIComponent(uid)}`, {
      scope: o.scope ?? 'all', occurrence: o.occurrence ?? undefined, etag: o.etag ?? undefined,
      send_updates: o.sendUpdates === undefined ? undefined : String(o.sendUpdates),
      message: o.message || undefined,
    }))),

  /** ACT event.cancel @web.sheet @web.editor: STATUS:CANCELLED and CANCEL to the guests, with an optional message (organizer only). */
  cancel: (uid: string, o: { scope?: Scope; occurrence?: string | null; etag?: string | null; message?: string } = {}) =>
    write(() => api.post<ApiEvent>(`/calendar/events/${encodeURIComponent(uid)}/cancel`, {
      scope: o.scope ?? 'all', ...(o.occurrence ? { occurrence: o.occurrence } : {}), ...(o.etag ? { etag: o.etag } : {}),
      ...(o.message ? { message: o.message } : {}),
    })),

  get: (uid: string) => api.get<ApiEvent>(`/calendar/events/${encodeURIComponent(uid)}`),

  /** ACT event.move_calendar @web.sheet @web.editor: the whole series into another calendar (UID kept). Null without the route. */
  move: async (uid: string, calendar: string, etag?: string | null): Promise<ApiEvent | null> => {
    try {
      return await write(() => api.post<ApiEvent>(`/calendar/events/${encodeURIComponent(uid)}/move`, { calendar, ...(etag ? { etag } : {}) }))
    } catch (e) {
      if (isMissingRoute(e)) return null
      throw e
    }
  },

  /** ACT event.rsvp @web.sheet @web.calendar */
  rsvp: (uid: string, response: 'accepted' | 'tentative' | 'declined', comment = '', scope: Scope = 'all', occurrence?: string | null) =>
    api.post(`/calendar/events/${encodeURIComponent(uid)}/rsvp`, { response, comment, scope, ...(scope !== 'all' && occurrence ? { occurrence } : {}) }),

  /** ACT calendar.sync @web.calendar */
  sync: () => api.post<{ queued?: boolean }>('/calendar/sync', {}),

  /** ACT calendar.set_default @web.calendar */
  setDefault: (calendar: string) => api.put('/calendar/defaults', { calendar }),

  /** PATCH /calendar/collections/{id}; null when the server does not have the route. */
  patchCollection: async (c: Collection, body: Record<string, unknown>): Promise<Collection | null> => {
    if (c.calendar_id == null) return null
    try {
      return await api.patch<Collection>(`/calendar/collections/${c.calendar_id}`, body)
    } catch (e) {
      if (isMissingRoute(e)) return null
      throw e
    }
  },

  /** Sharing a calendar with people on this server (calendar/sharing.py). */
  shares: (c: Collection) => api.get<ShareState>(`/calendar/collections/${c.calendar_id}/shares`),
  /** ACT calendar.share @web.calendar @web.settings */
  setShare: (c: Collection, userId: number, level: ShareLevel) =>
    api.put<ShareState>(`/calendar/collections/${c.calendar_id}/shares/${userId}`, { level }),
  unshare: (c: Collection, userId: number) => api.del<ShareState>(`/calendar/collections/${c.calendar_id}/shares/${userId}`),

  /** ACT calendar.create @web.calendar @web.settings */
  createCollection: (body: { connector_id?: number; name: string; color?: string; kind: 'events' | 'tasks' | 'both' }) =>
    api.post<Collection>('/calendar/collections', body),

  /** ACT calendar.delete @web.calendar @web.settings */
  deleteCollection: (c: Collection) => api.del(withQuery(`/calendar/collections/${c.calendar_id}`, { confirm: c.name })),

  /** ACT task.complete @web.calendar (a task chip on the grid) */
  completeTask: (uid: string, etag?: string | null) => api.patch(`/tasks/${encodeURIComponent(uid)}`, { completed: true, ...(etag ? { etag } : {}) }),

  /** A task chip's new due. Unused: the grid moves task chips through TaskChip.tsx (task.reschedule). */
  rescheduleTask: (uid: string, due: string, etag?: string | null) => api.patch(`/tasks/${encodeURIComponent(uid)}`, { due, ...(etag ? { etag } : {}) }),
}

/** A create payload without empty values (the server rejects null for list fields). */
function compact(body: EventWrite): Record<string, unknown> {
  return Object.fromEntries(Object.entries(body).filter(([, v]) => v !== undefined && v !== null))
}

// ---------- the cache ----------

/** Changes one occurrence in every cached range at once (optimistic drag, delete). */
/**
 * Changed occurrences of a series among the loaded ranges (the series question: "All" warns "n changed
 * occurrences will keep their own time"). The API marks each occurrence with `is_override`;
 * it gives no count for the whole series, so only loaded weeks count.
 */
export function overridesLoaded(qc: QueryClient, uid: string): number {
  const seen = new Set<string>()
  for (const [, d] of qc.getQueriesData<RangeData>({ queryKey: [...qk.calendar.all, 'range'] })) {
    for (const e of d?.events ?? []) if (e.uid === uid && e.is_override) seen.add(e.occurrence || e.recurrence_id || e.start)
  }
  return seen.size
}

export function patchCached(qc: QueryClient, key: string, fn: (e: ApiEvent) => ApiEvent | null) {
  qc.setQueriesData<RangeData>({ queryKey: [...qk.calendar.all, 'range'] }, (old) => {
    if (!old) return old
    let changed = false
    const events: ApiEvent[] = []
    for (const e of old.events) {
      const k = `${e.uid}|${e.occurrence || e.recurrence_id || ''}`
      const alt = `${e.uid}|${(() => {
        try {
          return canonicalOccurrence(e.start, !!e.all_day)
        } catch {
          return ''
        }
      })()}`
      if (k === key || alt === key) {
        changed = true
        const n = fn(e)
        if (n) events.push(n)
      } else events.push(e)
    }
    return changed ? { ...old, events } : old
  })
}
