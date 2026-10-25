// What InviteCard says about an invitation (invite.view): the conflict line, the
// mini timeline of my day around the slot, the diff of an update against my calendar copy, the
// ingest outcome and a guest's proposal. Pure, so the rules are tested without the card.

import { formatDate, formatTime, isISODate, isValidZone, parseISO, toISODate, zoneAbbr, type DatePrefs } from '../../lib/dates'
import { t as tr } from '../../i18n'

export interface Itip {
  applied: boolean
  detail: string
  method: string
  uid?: string
  at?: string
  attendee?: string
  partstat?: string
  comment?: string
  time_changed?: boolean
  /** Where an update moved the event from (the first changed occurrence), canonical strings. */
  previous?: { start: string | null; end: string | null; recurrence_id?: string | null } | null
  /** The zone the organizer wrote the start in (null for all-day or floating times). */
  organizer_tz?: string | null
  /** Kept back: the sender could not be verified; the user may apply it. */
  pending?: boolean
  confirmed?: boolean
  counter?: boolean
  proposed?: { start: string | null; end: string | null; recurrence_id?: string | null }
  /** A COUNTER: whether the sender's domain is verified, and the address it came from. */
  verified?: boolean
  sender?: string
}

export interface Invite {
  method: string
  uid: string
  sequence?: number
  recurrence_id?: string | null
  summary: string
  start: string | null
  end: string | null
  location: string
  description?: string
  organizer: { email: string; name: string }
  attendees: { email: string; name: string; status: string; role?: string }[]
  my_status: string | null
  my_address?: string | null
  recurring: boolean
  cancelled: boolean
  /** A COUNTER (backend calendar/routes.counter_check): whether its sender is
   *  verified, its address, and who gets the updated invitation when it is accepted. */
  counter_check?: { verified: boolean; sender: string; notify: string[] }
  /** Zones whose sender-written definition was not used (too costly); times use a fixed offset. */
  zones_replaced?: string[]
  in_calendar: boolean
  /** The copy of this invitation in the calendar (backend calendar/from_mail.py): the same UID, an
   *  event made from this conversation, or the same times and title without "Fw:". */
  calendar_match?: { uid: string; by: 'uid' | 'link' | 'time_title'; summary: string; start: string | null; end: string | null; occurrence?: string | null } | null
  comment?: string
  /** The organizer's zone of DTSTART (null for dates and floating times). */
  tz?: string | null
  itip?: Itip | null
  /** How far the organizer is verified (backend calendar/routes._invite_full):
   *  "organizer" (the verified sender is the organizer), "forwarded" (the user forwarded it from a
   *  trusted address: the organizer is only claimed), "" (anyone could have written it). */
  sender?: 'organizer' | 'forwarded' | ''
  /** A cancellation the card may call the organizer's, which Remove from calendar acts on. */
  cancel_verified?: boolean
}

export interface DayEvent {
  uid: string
  summary: string
  start: string
  end: string | null
  all_day?: boolean
  transparent?: boolean
  status?: string
  my_status?: string | null
}

type Fmt = Pick<DatePrefs, 'zone' | 'hour12'>

const ms = (s: string | null | undefined, zone: string) => (s ? parseISO(s, zone)?.getTime() ?? NaN : NaN)

/** Events of mine that overlap the slot (busy ones: not free, cancelled or declined; not all-day). */
export function overlaps(events: DayEvent[], start: string, end: string | null, uid: string, zone: string): DayEvent[] {
  const s = ms(start, zone)
  const e = end ? ms(end, zone) : s + 3600_000
  if (Number.isNaN(s) || isISODate(start)) return []
  return events.filter((x) => {
    if (x.uid === uid || x.all_day || isISODate(x.start) || x.transparent) return false
    if ((x.status ?? '').toUpperCase() === 'CANCELLED' || (x.my_status ?? '').toUpperCase() === 'DECLINED') return false
    const xs = ms(x.start, zone)
    const xe = x.end ? ms(x.end, zone) : xs
    return xs < e && xe > s
  })
}

/** "You have Team sync at that time", "You have Team sync and 2 more at that time". */
export function conflictText(c: DayEvent[]): string | null {
  if (!c.length) return null
  const first = c[0].summary || tr('conversation.inviteLogic.anotherEvent')
  return tr('conversation.inviteLogic.youHaveAtThatTime', { first, value: c.length > 1 ? tr('conversation.inviteLogic.andMore', { value: c.length - 1 }) : '' })
}

export interface Timeline {
  from: number
  to: number
  hours: number[]
  slot: { left: number; width: number }
  blocks: { left: number; width: number; title: string; clash: boolean }[]
}

/**
 * My day around the slot: two hours either side (at least 7:00 to 19:00 when the slot is inside
 * it), hour ticks, my busy events and the invitation's slot as fractions of the width.
 */
export function timeline(events: DayEvent[], start: string, end: string | null, uid: string, zone: string): Timeline | null {
  if (!start || isISODate(start)) return null
  const s = ms(start, zone)
  const e = end ? ms(end, zone) : s + 3600_000
  if (Number.isNaN(s)) return null
  const H = 3600_000
  let from = Math.floor((s - 2 * H) / H) * H
  let to = Math.ceil((e + 2 * H) / H) * H
  if (to - from < 6 * H) to = from + 6 * H
  if (to - from > 14 * H) {
    from = Math.floor((s - H) / H) * H
    to = Math.max(from + 6 * H, Math.ceil((e + H) / H) * H)
  }
  const span = to - from
  const frac = (t: number) => Math.min(1, Math.max(0, (t - from) / span))
  const hours: number[] = []
  for (let t = from; t <= to; t += H) hours.push(t)
  const clash = new Set(overlaps(events, start, end, uid, zone).map((x) => x))
  const blocks = events
    .filter((x) => x.uid !== uid && !x.all_day && !isISODate(x.start) && !x.transparent && (x.status ?? '').toUpperCase() !== 'CANCELLED' && (x.my_status ?? '').toUpperCase() !== 'DECLINED')
    .map((x) => ({ x, a: ms(x.start, zone), b: x.end ? ms(x.end, zone) : ms(x.start, zone) }))
    .filter(({ a, b }) => b > from && a < to)
    .map(({ x, a, b }) => ({ left: frac(a), width: Math.max(0.01, frac(b) - frac(a)), title: x.summary, clash: clash.has(x) }))
  return { from, to, hours, slot: { left: frac(s), width: Math.max(0.01, frac(e) - frac(s)) }, blocks }
}

/** "Tue 6 Oct, 3:00 PM", or the date alone for all-day. */
export function moment(s: string | null | undefined, p: Fmt): string {
  if (!s) return ''
  if (isISODate(s)) return formatDate(s.slice(0, 10), 'day')
  const d = parseISO(s, p.zone)
  return d ? `${formatDate(d, 'day', p.zone)}, ${formatTime(d, p, { compact: true })}` : ''
}

/** The changes an update brings, against my calendar copy: "Time changed: was Tue 3 PM". */
export function diffLines(inv: Pick<Invite, 'start' | 'end' | 'location' | 'summary'>, mine: Pick<DayEvent, 'start' | 'end' | 'summary'> & { location?: string } | null, p: Fmt): string[] {
  if (!mine || !inv.start) return []
  const out: string[] = []
  const same = (a: string | null | undefined, b: string | null | undefined) => (a && b ? ms(a, p.zone) === ms(b, p.zone) || a.slice(0, 10) === b && isISODate(b) : a === b)
  if (!same(inv.start, mine.start) || (inv.end && mine.end && !same(inv.end, mine.end))) out.push(tr('conversation.inviteLogic.timeChangedWas', { moment: moment(mine.start, p) }))
  if ((inv.location ?? '') !== (mine.location ?? '') && mine.location != null) out.push(mine.location ? tr('conversation.inviteLogic.placeChangedWas', { location: mine.location }) : tr('conversation.inviteLogic.placeAdded'))
  if (inv.summary && mine.summary && inv.summary !== mine.summary) out.push(tr('conversation.inviteLogic.renamedWas', { summary: mine.summary }))
  return out
}

const city = (zone: string) => zone.split('/').pop()!.replace(/_/g, ' ')

/** The time in the organizer's zone when it differs from mine: "9:00 PM Lisbon", else null. */
export function organizerTime(s: string | null | undefined, zone: string | null | undefined, p: Fmt): string | null {
  // A zone name the browser doesn't know (an old record with Outlook's "Central Standard Time")
  // shows no second time rather than failing the whole message.
  if (!s || !isValidZone(zone) || isISODate(s)) return null
  const d = parseISO(s, p.zone)
  if (!d || zoneAbbr(d, zone) === zoneAbbr(d, p.zone) || formatTime(d, { ...p, zone }) === formatTime(d, p)) return null
  return `${formatTime(d, { ...p, zone }, { compact: true })} ${city(zone)}`
}

/**
 * The update's "was" line from the ingest record (itip.previous): it stays true after the
 * update was applied to my calendar, and for one occurrence of a series. The organizer's own
 * time is added when their zone differs from mine.
 */
export function previousLine(inv: Pick<Invite, 'itip' | 'recurring'>, p: Fmt): string | null {
  const it = inv.itip
  const prev = it?.previous
  if (!it || it.method !== 'REQUEST' || !it.time_changed || !prev?.start) return null
  const theirs = organizerTime(prev.start, it.organizer_tz, p)
  const what = prev.recurrence_id && inv.recurring ? tr('conversation.inviteLogic.thisOccurrenceMoved') : tr('conversation.inviteLogic.timeChanged')
  return tr('conversation.inviteLogic.was', { what, moment: moment(prev.start, p), value: theirs ? tr('conversation.inviteLogic.forTheOrganizer', { theirs }) : '' })
}

/** The name of the guest an outcome is about. */
export function guestName(inv: Invite, email: string | undefined): string {
  if (!email) return tr('conversation.inviteLogic.aGuest')
  const a = inv.attendees.find((x) => x.email.toLowerCase() === email.toLowerCase())
  return a?.name || email.split('@')[0]
}

/** "Dana proposes Thu 3 PM" for a COUNTER on an event I organize. */
export function proposalText(inv: Invite, fromEmail: string, p: Fmt): string | null {
  const it = inv.itip
  if (!it || it.method !== 'COUNTER' || !it.counter || !it.proposed?.start) return null
  return tr('conversation.inviteLogic.proposes', { guestName: guestName(inv, fromEmail), moment: moment(it.proposed.start, p) })
}

/** The day of the slot, in my zone. */
export function dayOf(start: string, zone: string): string {
  if (isISODate(start)) return start.slice(0, 10)
  const d = parseISO(start, zone)
  return d ? toISODate(d, zone) : start.slice(0, 10)
}
