// Helpers for the invitation card and the "Create event" / "Add to tasks" dialog.

import type { Message } from '../api/types'
import { t, dateLocale } from '../i18n'
import { clockOpts } from './dates'

/** A message carries an invitation (an iMIP part or an .ics attachment). */
export function hasInvite(m: Pick<Message, 'attachments'>): boolean {
  // An attached email that is an invitation counts too: the server shows its card only for a
  // forward from one of your trusted addresses (Trusted addresses).
  return m.attachments.some((a) => /^(text\/calendar|application\/ics)$/i.test(a.type) || /\.ics$/i.test(a.filename) || !!a.email?.invite)
}

const pad = (n: number) => String(n).padStart(2, '0')

/** ISO (date or date-time) → the value of an <input type="datetime-local">, in local time. */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return `${iso}T09:00`
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** <input type="datetime-local"> value → ISO with the browser's offset (what the API expects). */
export function fromLocalInput(v: string): string | null {
  if (!v) return null
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return null
  const off = -d.getTimezoneOffset()
  const sign = off >= 0 ? '+' : '-'
  return `${v.length === 16 ? `${v}:00` : v}${sign}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)}`
}

/** One hour after a datetime-local value, for an event whose end is unknown. */
export function plusHour(v: string): string {
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return ''
  d.setHours(d.getHours() + 1)
  return toLocalInput(d.toISOString())
}

export const RSVP_TEXT: Record<string, string> = {
  ACCEPTED: t('lib.calendarForm.youSaidYes'),
  DECLINED: t('lib.calendarForm.youSaidNo'),
  TENTATIVE: t('lib.calendarForm.youSaidMaybe'),
  DELEGATED: t('lib.calendarForm.youDelegatedThis'),
}

/** Human "when" for an invitation: all-day dates or local date-times. */
export function formatWhen(start: string | null, end: string | null, locale = dateLocale('en-US')): string {
  if (!start) return ''
  const allDay = /^\d{4}-\d{2}-\d{2}$/.test(start)
  const opts: Intl.DateTimeFormatOptions = allDay
    ? { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }
    : { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...clockOpts() }
  const s = new Date(allDay ? `${start}T00:00` : start)
  let out = s.toLocaleString(locale, opts)
  if (end && !allDay) {
    const e = new Date(end)
    const same = e.toDateString() === s.toDateString()
    out += ` – ${e.toLocaleString(locale, same ? { hour: 'numeric', minute: '2-digit', ...clockOpts() } : opts)}`
  }
  return out
}
