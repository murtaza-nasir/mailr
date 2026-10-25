// Pure formatting helpers (unit-tested in format.test.ts).

import type { Address, Participant } from '../api/types'
import { nameAddressMismatch, safeText } from './safeText'
import { t as tr, appLocale, dateLocale } from '../i18n'
import { clockOpts } from './dates'

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

/** Gmail list date: time today, "Sep 24" this year, "9/24/25" older. */
export function formatListDate(iso: string | null | undefined, now: Date = new Date(), locale = dateLocale('en-US')): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  if (sameDay(d, now)) return d.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit', ...clockOpts() })
  if (d.getFullYear() === now.getFullYear()) return d.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
  return d.toLocaleDateString(locale, { month: 'numeric', day: 'numeric', year: '2-digit' })
}

/** "3 minutes ago", "2 hours ago", "5 days ago". */
export function formatRelative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return ''
  const d = new Date(iso)
  const diff = now.getTime() - d.getTime()
  const future = diff < 0
  const abs = Math.abs(diff)
  let text: string
  if (abs < MINUTE) return future ? tr('lib.format.inAMoment') : tr('lib.format.justNow')
  if (abs < HOUR) {
    const n = Math.round(abs / MINUTE)
    text = tr('lib.format.minutes', { count: n })
  } else if (abs < DAY) {
    const n = Math.round(abs / HOUR)
    text = tr('lib.format.hours', { count: n })
  } else if (abs < 30 * DAY) {
    const n = Math.round(abs / DAY)
    text = tr('lib.format.days', { count: n })
  } else if (abs < 365 * DAY) {
    const n = Math.round(abs / (30 * DAY))
    text = tr('lib.format.months', { count: n })
  } else {
    const n = Math.round(abs / (365 * DAY))
    text = tr('lib.format.years', { count: n })
  }
  return future ? tr('lib.format.in', { text }) : tr('lib.format.ago', { text })
}

/** Message header date: "Sep 24, 2026, 3:15 PM (2 days ago)". */
export function formatFullDate(iso: string | null | undefined, now: Date = new Date(), locale = dateLocale('en-US')): string {
  if (!iso) return ''
  const d = new Date(iso)
  const abs = d.toLocaleString(locale, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit', ...clockOpts(),
  })
  const rel = formatRelative(iso, now)
  return now.getTime() - d.getTime() < 30 * DAY ? `${abs} (${rel})` : abs
}

export function formatDateTime(iso: string | null | undefined, locale = dateLocale('en-US')): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString(locale, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit', ...clockOpts(),
  })
}

export function formatSize(bytes: number | null | undefined): string {
  if (!bytes || bytes < 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1).replace(/\.0$/, '')} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1).replace(/\.0$/, '')} GB`
}

/** Best display name for an address: name, or local part of the email. */
// Words after the comma that make "X, Y" a name with a suffix or a company, not "Last, First".
const NOT_FIRST = /^(jr|sr|ii|iii|iv|v|phd|md|dds|esq|inc|llc|ltd|co|corp|gmbh|plc|bv|sa|ag|pc|lp|llp)\.?$/i

/** A person's name in "First Last" order: "Turner, Ross" becomes "Ross Turner".
 *  Names without exactly one comma, and "Smith, Jr." or "Acme, Inc.", stay as they are. */
export function personName(name: string): string {
  const n = (name ?? '').trim()
  const m = /^([^,@<>]+),\s*([^,@<>]+)$/.exec(n)
  if (!m) return n
  const last = m[1].trim()
  const first = m[2].trim()
  if (!last || !first || NOT_FIRST.test(first) || first.split(/\s+/).length > 3 || last.split(/\s+/).length > 3) return n
  return `${first} ${last}`
}

export function displayName(a: Address | Participant | null | undefined): string {
  if (!a) return ''
  const name = safeText(a.name ?? '').trim()
  // A name that shows another address is shown with the real one beside it.
  if (name && nameAddressMismatch(name, a.email)) return `${name} (${a.email})`
  if (name) return personName(name)
  return a.email.split('@')[0] ?? a.email
}

export function firstName(a: Address | Participant): string {
  if (nameAddressMismatch(a.name, a.email)) return a.email
  const n = displayName(a)
  if (!a.name) return n
  // "Last, First" → First; "First Last" → First
  if (n.includes(',')) return n.split(',')[1]?.trim() || n
  return n.split(/\s+/)[0] ?? n
}

/**
 * Gmail-style sender column: "Alice", "me, Alice 3", "Alice .. Carol 5".
 * Participants are in order of appearance; `count` is the conversation's message count.
 */
export function formatParticipants(parts: Participant[], count: number, draftCount = 0): string {
  const list = parts.filter((p) => p.email || p.name)
  let names: string
  if (list.length === 0) names = draftCount > 0 ? '' : tr('lib.format.unknownSender')
  else if (list.length === 1) {
    const p = list[0]
    names = p.me ? 'me' : displayName(p)
  } else {
    const label = (p: Participant) => (p.me ? 'me' : firstName(p))
    if (list.length === 2) names = `${label(list[0])}, ${label(list[1])}`
    else if (list.length === 3) names = `${label(list[0])}, ${label(list[1])}, ${label(list[2])}`
    else names = `${label(list[0])} .. ${label(list[list.length - 2])}, ${label(list[list.length - 1])}`
  }
  const suffix = count > 1 ? ` ${count}` : ''
  return `${names}${suffix}`
}

const AVATAR_COLORS = [
  '#e8453c', '#f06292', '#ab47bc', '#7e57c2', '#5c6bc0', '#1e88e5', '#039be5', '#00acc1',
  '#00897b', '#43a047', '#7cb342', '#c0ca33', '#f4b400', '#fb8c00', '#f4511e', '#8d6e63', '#78909c',
]

export function hashString(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0
  return Math.abs(h)
}

export function avatarColor(key: string): string {
  return AVATAR_COLORS[hashString(key.toLowerCase()) % AVATAR_COLORS.length]
}

export function initials(a: Address | Participant | null | undefined): string {
  if (!a) return '?'
  const n = displayName(a).replace(/[^\p{L}\p{N}\s]/gu, ' ').trim()
  if (!n) return a.email.charAt(0).toUpperCase() || '?'
  return n.charAt(0).toUpperCase()
}

export function addressLine(a: Address): string {
  return a.name ? `${a.name} <${a.email}>` : a.email
}

/** Parse "Name <a@b>, c@d" into addresses (used for paste into recipient fields). */
export function parseAddressList(input: string): Address[] {
  const out: Address[] = []
  // A quoted name may hold commas and escaped quotes ("Nguyen, Bich" <bich@example.com>, as the
  // reader's Copy name and address gives it).
  const re = /(?:"((?:[^"\\]|\\.)*)"\s*<([^<>\s,]+@[^<>\s,]+)>|"?([^"<>,]*?)"?\s*<([^<>\s,]+@[^<>\s,]+)>|([^\s<>,;]+@[^\s<>,;]+))/g
  let m: RegExpExecArray | null
  while ((m = re.exec(input))) {
    const email = (m[2] ?? m[4] ?? m[5] ?? '').trim().toLowerCase()
    const name = (m[1]?.replace(/\\(.)/g, '$1') ?? m[3] ?? '').trim()
    if (email) out.push(name ? { email, name } : { email })
  }
  return out
}

export function isValidEmail(s: string): boolean {
  return /^[^\s@<>(),;:"]+@[^\s@<>(),;:"]+\.[^\s@<>(),;:"]+$/.test(s.trim())
}

/** "1–50 of 1,234" */
export function formatRange(start: number, count: number, total: number, capped = false): string {
  const t = `${total.toLocaleString(appLocale())}${capped ? '+' : ''}`
  if (total === 0 || count === 0) return tr('lib.format.n0Of', { t })
  return tr('lib.format.of', { value: (start + 1).toLocaleString(appLocale()), value2: (start + count).toLocaleString(appLocale()), t })
}

export function plural(n: number, word: string, pluralWord = `${word}s`): string {
  return `${n.toLocaleString(appLocale())} ${n === 1 ? word : pluralWord}`
}

/** Label display: last path component for nested labels. */
export function labelLeaf(name: string): string {
  const parts = name.split('/')
  return parts[parts.length - 1] ?? name
}
