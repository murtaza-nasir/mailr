// The Recordings app's reads (web.recordings): the whole mirror, page by page, with each
// recording's event link and participants (GET /speakr/recordings?annotate=true); a search through
// the summary index (the same route with q); Speakr's folders and tags; and the calendar's
// meetings around now, for linking a new recording.

import { keepPreviousData, useQuery, type QueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { utcIsoformat } from '../../lib/dates'
import type { ApiEvent } from '../calendar/types'
import type { RecItem } from './model'

const PAGE = 200
const MAX_PAGES = 10

export const recKeys = {
  all: ['speakr', 'browse'] as const,
  search: (q: string) => ['speakr', 'browse', 'q', q] as const,
  folders: ['speakr', 'folders'] as const,
  tags: ['speakr', 'tags'] as const,
  around: ['speakr', 'record', 'events'] as const,
  participants: ['speakr', 'participants'] as const,
}

// ACT recording.search @web.recordings
export function useAllRecordings(enabled: boolean) {
  return useQuery({
    queryKey: recKeys.all,
    queryFn: async ({ signal }) => {
      const out: RecItem[] = []
      for (let page = 0; page < MAX_PAGES; page++) {
        const r = await api.get<{ items: RecItem[]; more?: boolean }>('/speakr/recordings', { limit: PAGE, offset: page * PAGE, annotate: true }, signal)
        out.push(...r.items)
        if (!r.more || r.items.length < PAGE) break
      }
      return out
    },
    enabled,
    staleTime: 30_000,
    // New recordings arrive by sync and webhook; the list catches up while it is open.
    refetchInterval: 60_000,
    retry: false,
  })
}

// ACT recording.search
export function useRecordingSearch(q: string, enabled: boolean) {
  return useQuery<RecItem[]>({
    queryKey: recKeys.search(q),
    queryFn: ({ signal }) => api.get<{ items: RecItem[] }>('/speakr/recordings', { q, limit: PAGE, annotate: true }, signal).then((r) => r.items),
    enabled: enabled && q.trim().length > 0,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
    retry: false,
  })
}

export interface SpeakrFolder { id: number; name: string; color?: string | null; group?: boolean }
export interface SpeakrTag { id: number; name: string; color?: string | null; group?: boolean }

// ACT recording.record @web.recordings (the folder and tag pickers, and the left column's folders and tags)
export function useSpeakrFolders(enabled: boolean) {
  return useQuery({
    queryKey: recKeys.folders,
    queryFn: ({ signal }) => api.get<{ connector_id: number; folders: SpeakrFolder[] }>('/speakr/folders', undefined, signal),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export function useSpeakrTags(enabled: boolean) {
  return useQuery({
    queryKey: recKeys.tags,
    queryFn: ({ signal }) => api.get<{ connector_id: number; tags: SpeakrTag[] }>('/speakr/tags', undefined, signal),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  })
}

/** Timed meetings from three hours ago to the end of tomorrow, for "Link to event". */
export function useMeetingsAround(enabled: boolean) {
  return useQuery({
    queryKey: recKeys.around,
    queryFn: async ({ signal }) => {
      const from = new Date(Date.now() - 3 * 3_600_000)
      const to = new Date()
      to.setDate(to.getDate() + 2)
      to.setHours(0, 0, 0, 0)
      const r = await api.get<{ events?: ApiEvent[]; connected?: boolean } | ApiEvent[]>('/calendar/agenda', { from: utcIsoformat(from), to: utcIsoformat(to) }, signal)
      const events = Array.isArray(r) ? r : (r.events ?? [])
      return events.filter((e) => !e.all_day && e.status !== 'CANCELLED' && e.status !== 'cancelled').sort((a, b) => a.start.localeCompare(b.start))
    },
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

/** The meeting going on now, else the next one within 30 minutes: the event a new recording links to. */
export function currentMeeting(events: ApiEvent[], now = Date.now()): ApiEvent | null {
  const t = (s: string | null) => (s ? new Date(s).getTime() : NaN)
  const on = events.find((e) => t(e.start) <= now + 5 * 60_000 && (Number.isNaN(t(e.end)) ? t(e.start) + 3_600_000 : t(e.end)) > now)
  if (on) return on
  return events.find((e) => t(e.start) > now && t(e.start) - now <= 30 * 60_000) ?? null
}

/** A suggested merge: a first name and a full name that never share a recording but share its people, meeting or folder. */
export interface MergeSuggestion {
  connector_id: number
  name: string
  label: string
  count: number
  into: string
  into_label: string
  into_key: string
}

export interface ParticipantsOverview {
  suggestions: MergeSuggestion[]
  aliases: { connector_id: number; name: string; target: string; target_label: string }[]
  /** Connectors whose write-back is on (Also rename in Speakr). */
  write_back: number[]
}

// ACT participant.merge @web.recordings
export function useParticipants(enabled: boolean) {
  return useQuery({
    queryKey: recKeys.participants,
    queryFn: ({ signal }) => api.get<ParticipantsOverview>('/speakr/participants', undefined, signal),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

export interface MergeResult { merged: string[]; skipped: string[]; into: string; into_label: string; write_back: number[] }

/** Same person as…: every name of `key`, or one `name`, becomes an alias of `into`. */
export function mergeParticipant(body: { into: string; key?: string; name?: string; connector_id?: number }) {
  return api.post<MergeResult>('/speakr/participants/merge', body)
}

/** Separate: the name stands on its own again. */
export function separateParticipant(name: string, connector_id?: number) {
  return api.post<{ separated: number }>('/speakr/participants/separate', { name, connector_id })
}

export function dismissMerge(s: Pick<MergeSuggestion, 'connector_id' | 'name' | 'into'>) {
  return api.post<{ ok: boolean }>('/speakr/participants/dismiss', { connector_id: s.connector_id, name: s.name, into: s.into })
}

/** Also rename in Speakr (only with the connector's write-back on). */
export function renameInSpeakr(connector_id: number, name: string, to: string) {
  return api.post<{ renamed: number }>('/speakr/participants/rename', { connector_id, name, to })
}

/** After a merge: the list, its searches, the overview and the recordings' People tabs read again. */
export function refreshParticipants(qc: QueryClient) {
  void qc.invalidateQueries({ queryKey: recKeys.all })
  void qc.invalidateQueries({ queryKey: recKeys.participants })
  void qc.invalidateQueries({ queryKey: ['speakr', 'people'] })
  void qc.invalidateQueries({ queryKey: ['speakr', 'person'] })
}
