// Facts about an event the blocks, rows and detail all show: conference link, status, my RSVP,
// the guest count line, whether it is in progress. Kept out of the components so each surface
// words them the same way.

import { formatInstant, formatTime } from '../../lib/dates'
import type { CalPrefs } from './prefs'
import type { ApiEvent, CalEvent } from './types'
import { t as tr, dateLocale } from '../../i18n'

/** Meeting providers. A meeting link is an https URL whose host is one of these or a subdomain of one
 *  ("us02web.zoom.us"); the name anywhere else in the URL does not count (the same rule as the server's
 *  planner.join_url and Android's JoinLink). */
export const CONFERENCE_HOSTS = ['meet.google.com', 'zoom.us', 'teams.microsoft.com', 'teams.live.com', 'meet.jit.si',
  'whereby.com', 'webex.com', 'bluejeans.com', 'gotomeeting.com']
const HTTPS_URL = /https:\/\/[^\s<>"]+/gi

function httpsHost(url: string): string | null {
  try {
    const u = new URL(url.trim())
    return u.protocol === 'https:' && u.hostname ? u.hostname.toLowerCase().replace(/\.$/, '') : null
  } catch {
    return null
  }
}

/** The provider domain of an https meeting link, else null. */
export function meetingProvider(url: string): string | null {
  const host = httpsHost(url)
  if (!host) return null
  return CONFERENCE_HOSTS.find((d) => host === d || host.endsWith(`.${d}`)) ?? null
}

/** The conference link: the URL property when it is a meeting link, else the first one in location or description. */
export function conferenceLink(e: Pick<ApiEvent, 'url' | 'location' | 'description'>): string | null {
  if (e.url && meetingProvider(e.url)) return e.url.trim()
  for (const text of [e.location ?? '', e.description ?? '']) {
    for (const m of text.matchAll(HTTPS_URL)) if (meetingProvider(m[0])) return m[0]
  }
  return null
}

/** What the event's Join button opens and says: "Join" for a meeting provider's link, "Join on <host>"
 *  for any other https URL property, so the host shows before the click. Null when there is neither. */
export function joinTarget(e: Pick<ApiEvent, 'url' | 'location' | 'description'>): { url: string; label: string } | null {
  const conf = conferenceLink(e)
  if (conf) return { url: conf, label: tr('common.join') }
  const host = e.url ? httpsHost(e.url) : null
  return host ? { url: e.url!.trim(), label: tr('calendar.eventInfo.joinOn', { replace: host.replace(/^www\./, '') }) } : null
}

export const isCancelled = (e: ApiEvent) => (e.status ?? '').toUpperCase() === 'CANCELLED'
export const isTentative = (e: ApiEvent) => (e.status ?? '').toUpperCase() === 'TENTATIVE' || (e.my_status ?? '').toUpperCase() === 'TENTATIVE'
export const needsReply = (e: ApiEvent) => (e.my_status ?? '').toUpperCase() === 'NEEDS-ACTION'
export const isDeclined = (e: ApiEvent) => (e.my_status ?? '').toUpperCase() === 'DECLINED'
export const isPrivate = (e: ApiEvent) => ['PRIVATE', 'CONFIDENTIAL'].includes((e.class ?? '').toUpperCase())
export const hasGuests = (e: ApiEvent) => (e.attendee_list?.length ?? e.attendees ?? 0) > 0

/** The partstat the overlap layout draws with: declined and needs-reply outlined, tentative hatched. */
export function layoutPartstat(e: ApiEvent): string | null {
  if (isDeclined(e)) return 'declined'
  if (needsReply(e)) return 'needs-action'
  if (isTentative(e)) return 'tentative'
  return null
}

export function inProgress(e: CalEvent, now: Date): boolean {
  return e.startAt <= now && e.endAt > now
}

/** The Join badge shows in the five minutes before the start, and while it runs. */
export function joinable(e: CalEvent, now: Date): boolean {
  if (!conferenceLink(e)) return false
  const t = now.getTime()
  return t >= e.startAt.getTime() - 5 * 60_000 && t < e.endAt.getTime()
}

/** "4 guests · 2 yes, 1 maybe, 1 awaiting". */
export function guestLine(e: ApiEvent): string {
  const list = e.attendee_list ?? []
  if (!list.length) return ''
  const n = { yes: 0, no: 0, maybe: 0, awaiting: 0 }
  for (const a of list) {
    const p = (a.partstat ?? '').toUpperCase()
    if (p === 'ACCEPTED') n.yes++
    else if (p === 'DECLINED') n.no++
    else if (p === 'TENTATIVE') n.maybe++
    else n.awaiting++
  }
  const parts = [n.yes && tr('calendar.eventInfo.yes', { yes: n.yes }), n.maybe && tr('calendar.eventInfo.maybe', { maybe: n.maybe }), n.no && tr('calendar.eventInfo.no', { no: n.no }), n.awaiting && tr('calendar.eventInfo.awaiting', { awaiting: n.awaiting })].filter(Boolean)
  return tr('calendar.eventInfo.guests', { count: list.length, parts: parts.join(', ') })
}

export const RSVP_LABEL: Record<string, string> = {
  ACCEPTED: tr('calendar.eventInfo.going'), DECLINED: tr('calendar.eventInfo.notGoing'), TENTATIVE: tr('common.maybe'), 'NEEDS-ACTION': tr('common.needsReply'), DELEGATED: tr('calendar.eventInfo.delegated'),
}

/** "Moved from Tue 3 PM" for an exception whose time differs from its original slot. */
export function movedFrom(e: CalEvent, p: CalPrefs): string | null {
  if (!e.is_override || !e.recurrence_id || e.allDay) return null
  const orig = new Date(e.recurrence_id)
  if (Number.isNaN(orig.getTime()) || orig.getTime() === e.startAt.getTime()) return null
  const day = formatInstant(orig, dateLocale('en-GB'), { weekday: 'short' }, p.zone)
  return tr('calendar.eventInfo.movedFrom', { day, time: formatTime(orig, p, { compact: true }) })
}

/** "3–4 PM", "12:30–1:30 PM", "11 AM–12:30 PM" (24-hour: "15:00–16:00"). */
export function timeSpan(start: Date, end: Date, p: Pick<CalPrefs, 'zone' | 'hour12'>): string {
  const a = formatTime(start, p, { compact: true })
  const b = formatTime(end, p, { compact: true })
  if (p.hour12 && a.slice(-2) === b.slice(-2)) return `${a.slice(0, -3)}–${b}`
  return `${a}–${b}`
}

/** The title as shown: "(no title)" for an empty one. */
export const titleOf = (e: Pick<ApiEvent, 'summary'>) => e.summary?.trim() || tr('common.noTitle')
