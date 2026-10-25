// "Download .ics first" before a calendar is deleted: the server's export of the collection;
// without that route, the calendar's events from a year back to a year ahead, written as one
// iCalendar file in the browser (repeating events once, from their master, with their rule).

import { API, api } from '../../api/client'
import { addDaysISO, todayISO, startOfDay, utcIsoformat } from '../../lib/dates'
import { toastError } from '../../stores/ui'
import type { AgendaResponse, ApiEvent, Collection } from './types'
import { t } from '../../i18n'

const esc = (s: string) => s.replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n')

/** 75-octet folding (RFC 5545 3.1), counted in characters, which is close enough for a backup. */
function fold(line: string): string {
  const out: string[] = []
  for (let i = 0; i < line.length; i += 73) out.push((i ? ' ' : '') + line.slice(i, i + 73))
  return out.join('\r\n')
}

const stamp = (iso: string) => iso.replace(/[-:]/g, '').replace(/\+00:00$/, 'Z').replace(/\.\d+/, '')

export function toIcs(events: ApiEvent[], name: string): string {
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', t('calendar.ics.prodidMailrCalendarExportEn'), fold(`X-WR-CALNAME:${esc(name)}`)]
  const now = stamp(utcIsoformat(new Date()))
  for (const e of events) {
    lines.push('BEGIN:VEVENT', fold(`UID:${e.uid}`), `DTSTAMP:${now}`)
    if (e.all_day) {
      lines.push(`DTSTART;VALUE=DATE:${e.start.slice(0, 10).replace(/-/g, '')}`)
      if (e.end) lines.push(`DTEND;VALUE=DATE:${e.end.slice(0, 10).replace(/-/g, '')}`)
    } else {
      lines.push(`DTSTART:${stamp(utcIsoformat(new Date(e.start)))}`)
      if (e.end) lines.push(`DTEND:${stamp(utcIsoformat(new Date(e.end)))}`)
    }
    lines.push(fold(`SUMMARY:${esc(e.summary || '')}`))
    if (e.location) lines.push(fold(`LOCATION:${esc(e.location)}`))
    if (e.description) lines.push(fold(`DESCRIPTION:${esc(e.description)}`))
    if (e.rrule) lines.push(fold(`RRULE:${e.rrule}`))
    if (e.status) lines.push(`STATUS:${e.status}`)
    lines.push('END:VEVENT')
  }
  lines.push('END:VCALENDAR')
  return lines.join('\r\n') + '\r\n'
}

/** Fetch an .ics file from the API and save it (e.g. an admin's copy of a group calendar). */
export async function downloadIcs(path: string, name: string): Promise<void> {
  const r = await fetch(`${API}${path}`, { credentials: 'include' })
  if (!r.ok) throw new Error(t('calendar.ics.theCalendarCouldNotBe', { status: r.status }))
  save(await r.blob(), name)
}

function save(blob: Blob, name: string) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${name.replace(/[^\w .-]+/g, '_') || 'calendar'}.ics`
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000)
}

/**
 * ACT event.export_ics @web.calendar: the server's copy of the whole collection (GET
 * /calendar/collections/{calendar_id}/export: every resource as stored, rules, overrides and
 * zones). A server without that route gets the copy written here from the agenda.
 */
export async function exportIcs(c: Collection, zone: string): Promise<void> {
  if (c.calendar_id != null) {
    try {
      const r = await fetch(`${API}/calendar/collections/${c.calendar_id}/export`, { credentials: 'include' })
      if (r.ok) {
        save(await r.blob(), c.name)
        return
      }
      if (r.status !== 404 && r.status !== 405) throw new Error(t('calendar.ics.theCalendarCouldNotBe', { status: r.status }))
    } catch (e) {
      toastError(e, t('calendar.ics.couldNotExportTheCalendar'))
      return
    }
  }
  try {
    const url = c.url ?? c.id
    const today = todayISO(zone)
    const spans = [[addDaysISO(today, -365), today], [today, addDaysISO(today, 365)]]
    const all: ApiEvent[] = []
    for (const [a, b] of spans) {
      const r = await api.get<AgendaResponse>('/calendar/agenda', { from: utcIsoformat(startOfDay(a, zone)), to: utcIsoformat(startOfDay(b, zone)), calendar: url })
      all.push(...(r.events ?? []).filter((e) => !e.calendar || e.calendar === url))
    }
    const byUid = new Map<string, ApiEvent>()
    for (const e of all) {
      if (byUid.has(e.uid)) continue
      byUid.set(e.uid, e.recurring ? await api.get<ApiEvent>(`/calendar/events/${encodeURIComponent(e.uid)}`).catch(() => e) : e)
    }
    save(new Blob([toIcs([...byUid.values()], c.name)], { type: 'text/calendar' }), c.name)
  } catch (e) {
    toastError(e, t('calendar.ics.couldNotExportTheCalendar'))
  }
}

/**
 * The copy to keep before a delete or a cancel is confirmed: one event as stored (GET
 * /calendar/events/{uid}/ics: the series master with its overrides and zones). ACT event.export_ics
 */
export async function downloadEventIcs(uid: string, summary: string): Promise<void> {
  try {
    const r = await fetch(`${API}/calendar/events/${encodeURIComponent(uid)}/ics`, { credentials: 'include' })
    if (!r.ok) throw new Error(t('calendar.ics.theEventCouldNotBe', { status: r.status }))
    save(await r.blob(), summary || 'event')
  } catch (e) {
    toastError(e, t('calendar.ics.couldNotDownloadTheEvent'))
  }
}
