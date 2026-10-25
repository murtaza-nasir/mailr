// Meeting prep pack (event.prep; backend calendar/meeting_prep.py): the types, the requests and
// the small rules the event detail and the reminder sheet share.

import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { eventPath } from '../../lib/routes'
import { t } from '../../i18n'

export interface PrepGuest { email: string; name: string }
export interface PrepTask { uid: string; summary: string; due: string | null; why?: 'recording' | 'thread' | 'mentions'; list?: string | null }
export interface PrepThread {
  conversation_id: number
  message_id: number
  subject: string
  snippet: string
  last_date: string | null
  messages: number
  people: PrepGuest[]
  signals: string[]
  from_me: boolean
}
export interface PrepRecording {
  connector_id: number
  recording_id: number
  title: string
  meeting_date: string | null
  created_at: string | null
  summary_line: string | null
  why: string
  local_only: boolean
  action_items: { open: PrepTask[]; done: number }
}
export interface PrepNote {
  entity: { id: number; name: string; kind: string; summary: string; emails: string[] }
  notes: { id: number; text: string; kind: string; from_mail: boolean }[]
}
export interface PrepAttachment {
  message_id: number
  conversation_id: number
  filename: string
  type: string
  size: number
  date: string | null
  from: string
  from_name?: string
  direction: 'sent' | 'received'
}
export interface PrepPack {
  event: { uid: string; occurrence: string; title: string; start: string | null; end: string | null; guests: PrepGuest[]; url: string; local_only: boolean }
  upcoming: boolean
  has_guests: boolean
  recording: PrepRecording | null
  threads: PrepThread[]
  tasks: PrepTask[]
  notes: PrepNote[]
  attachments: PrepAttachment[]
  open_items: {
    questions: { conversation_id: number; message_id: number; from: string; date: string | null; question: string }[]
    waiting_on: { conversation_id: number; message_id: number; subject: string; since: string | null; follow_up_at: string | null }[]
    promised: { conversation_id: number; message_id: number; date: string | null; to: string[]; text: string }[]
  }
  hidden: number
  local_only: boolean
}
export interface PrepSummary {
  status: 'ok' | 'refused' | 'unavailable' | 'empty' | 'error'
  text: string | null
  message?: string
  model?: string
}

export const prepKeys = {
  pack: (uid: string, occurrence: string | null | undefined) => ['calendar', 'prep', uid, occurrence ?? ''] as const,
}

// ACT event.prep @web.sheet
export function usePrep(uid: string, occurrence: string | null | undefined, enabled: boolean) {
  return useQuery<PrepPack>({
    queryKey: prepKeys.pack(uid, occurrence),
    queryFn: () => api.get<PrepPack>(`/calendar/events/${encodeURIComponent(uid)}/prep`, { occurrence: occurrence ?? '' }),
    enabled: enabled && !!uid,
    staleTime: 60_000,
  })
}

export function fetchPrepSummary(uid: string, occurrence: string | null | undefined): Promise<PrepSummary> {
  return api.post<PrepSummary>(`/calendar/events/${encodeURIComponent(uid)}/prep/summary`, { occurrence: occurrence ?? '' })
}

/** The event detail with its prep pack open (the reminder's Prep link, the digest's links). */
export function prepPath(uid: string, occurrence: string | null | undefined): string {
  return `${eventPath(uid, occurrence ?? '')}?prep=1`
}

/** An event offers prep while it has guests and has not ended more than half an hour ago. */
export function offersPrep(e: { attendee_list?: unknown[] | null; endAt: Date }, now: Date): boolean {
  return (e.attendee_list?.length ?? 0) > 0 && e.endAt.getTime() > now.getTime() - 30 * 60_000
}

/** Nothing in any section. */
export function prepEmpty(p: PrepPack): boolean {
  const o = p.open_items
  return !p.recording && !p.threads.length && !p.tasks.length && !p.notes.length && !p.attachments.length
    && !o.questions.length && !o.waiting_on.length && !o.promised.length
}

/** The collapsed line: "Last recording · 3 threads · 2 open questions · 4 tasks". */
export function prepCounts(p: PrepPack): string {
  const n = (k: number, one: string, many = `${one}s`) => (k ? `${k} ${k === 1 ? one : many}` : '')
  const o = p.open_items
  return [
    p.recording ? t('calendar.prep.lastRecording') : '',
    n(p.threads.length, 'thread'),
    n(o.questions.length, 'open question'),
    n(o.waiting_on.length, t('calendar.prep.waitingOnThem'), t('calendar.prep.waitingOnThem')),
    n(p.tasks.length, 'task'),
    n(p.notes.length, 'note'),
    n(p.attachments.length, 'file'),
  ].filter(Boolean).join(' · ')
}

/** The guest's name when the pack knows it, else the address. */
export function guestName(p: PrepPack, email: string): string {
  return p.event.guests.find((g) => g.email === email)?.name || email
}
