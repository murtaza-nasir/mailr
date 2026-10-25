// "Schedule a meeting" from a thread (mail.schedule_meeting) and "Insert times" in a reply
// (event.insert_times): the pure parts. The title comes from the subject without Re: and Fwd:;
// the guests are the thread's people except me, leaving out mailing lists and robots; the
// identity is the one that received the thread; the reply and the inserted times are in my
// date format.

import { formatDate, formatTime, zoneAbbr, type DatePrefs } from '../../lib/dates'
import { t } from '../../i18n'

export interface Person {
  email: string
  name: string
}

interface Msg {
  from: { email: string; name?: string | null }
  to: { email: string; name?: string | null }[]
  cc: { email: string; name?: string | null }[]
  reply_to?: { email: string; name?: string | null }[]
  list_unsubscribe?: string | null
  sent_by_me?: boolean
  identity_hint?: number | null
  draft?: boolean
}

/** The subject without reply and forward prefixes, in any common language. */
export function meetingTitle(subject: string): string {
  return subject.replace(/^\s*((re|fwd?|fw|aw|wg|sv|vs|tr|rv|r)\s*(\[\d+\])?\s*:\s*)+/i, '').trim()
}

const ROBOT = /^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|mailer-daemon|postmaster|bounces?|alerts?|automated|support|news(letter)?|updates?|info|hello|team|marketing|billing|calendar|invitations?)([+._-].*)?@/i

/** An address no one reads: a robot sender or a list. */
export function isRobot(email: string): boolean {
  const e = email.toLowerCase()
  return ROBOT.test(e) || /(^|[.+_-])(bounce|bounces|noreply|no-reply)([.+_-]|@)/.test(e) || /@(lists?|groups|googlegroups)\./.test(e)
}

/** The thread's people except me, robots and lists, in order of appearance, without duplicates. */
export function participants(messages: Msg[], mine: string[]): Person[] {
  const me = new Set(mine.map((x) => x.toLowerCase()))
  const lists = new Set<string>()
  for (const m of messages) if (m.list_unsubscribe) lists.add(m.from.email.toLowerCase())
  const seen = new Map<string, Person>()
  for (const m of messages) {
    if (m.draft) continue
    for (const a of [m.from, ...(m.reply_to ?? []), ...m.to, ...m.cc]) {
      const e = (a.email ?? '').trim().toLowerCase()
      if (!e || me.has(e) || lists.has(e) || isRobot(e) || seen.has(e)) continue
      seen.set(e, { email: a.email.trim(), name: (a.name ?? '').trim() })
    }
  }
  return [...seen.values()]
}

/** The identity that received the thread: the newest message's hint, else one of mine on it. */
export function receivingIdentity<I extends { id: number; email: string }>(messages: Msg[], identities: I[]): I | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const h = messages[i].identity_hint
    if (h != null) {
      const id = identities.find((x) => x.id === h)
      if (id) return id
    }
  }
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    const to = [...m.to, ...m.cc].map((a) => a.email.toLowerCase())
    const id = identities.find((x) => to.includes(x.email.toLowerCase()))
    if (id) return id
  }
  return null
}

type Fmt = Pick<DatePrefs, 'zone' | 'hour12'>

/** "Tue 6 Oct, 3:00 – 3:30 PM" in my clock. */
export function slotText(start: Date, end: Date, p: Fmt): string {
  const a = formatTime(start, p)
  const b = formatTime(end, p)
  const sameHalf = p.hour12 && a.slice(-2) === b.slice(-2)
  return `${formatDate(start, 'day', p.zone)}, ${sameHalf ? a.slice(0, -3) : a} – ${b}`
}

/**
 * Up to `n` slots for "Insert times", spread over days: the best slot of each day first, then the
 * next best ones, shown in time order.
 */
export function spreadSlots<T extends { start: Date }>(ranked: T[], n: number, zone: string): T[] {
  const day = (s: T) => formatDate(s.start, 'day', zone)
  const out: T[] = []
  const days = new Set<string>()
  for (const s of ranked) {
    if (out.length >= n) break
    if (!days.has(day(s))) {
      days.add(day(s))
      out.push(s)
    }
  }
  for (const s of ranked) {
    if (out.length >= n) break
    if (!out.includes(s)) out.push(s)
  }
  return out.sort((a, b) => a.start.getTime() - b.start.getTime())
}

/** The text "Insert times" puts at the cursor. */
export function timesHtml(slots: { start: Date; end: Date }[], p: Fmt): string {
  if (!slots.length) return ''
  const zone = zoneAbbr(slots[0].start, p.zone)
  const items = slots.map((s) => `<li>${slotText(s.start, s.end, p)}</li>`).join('')
  return `<p>Would any of these work?${zone ? ` (${zone})` : ''}</p><ul>${items}</ul>`
}

/** The reply offered after the meeting is saved. */
export function replyText(title: string, start: Date | string, end: Date | string, allDay: boolean, location: string, p: Fmt): string {
  const when = allDay || typeof start === 'string'
    ? formatDate(typeof start === 'string' ? start.slice(0, 10) : start, 'long', p.zone)
    : `${slotText(start, end as Date, p)} (${zoneAbbr(start, p.zone)})`
  return t('conversation.meeting.iveSentAnInvitationFor', { title, when, value: location ? t('conversation.meeting.at', { location }) : '' })
}
