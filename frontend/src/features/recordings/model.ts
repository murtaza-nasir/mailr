// The Recordings app's list logic (web.recordings), kept free of React so it is tested on its own
// (model.test.ts): which recordings a filter keeps, the counts the left column shows (people by
// the person each participant string resolves to, so "Alex" and "Alex Rivera" count once as
// You), the day groups of the list, and the parts of a summary line that match the search.

import type { Recording, RecordingLink } from '../speakr/api'
import { personName } from '../../lib/format'
import type { RecordingFilter } from '../../lib/routes'
import { t as tr, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'

/** A row of the list: the mirror's recording with the app's annotations (GET /speakr/recordings?annotate=true). */
export interface RecItem extends Recording {
  /** The event the recording is linked to: a confirmed link first, else the best suggestion. */
  event_link?: RecordingLink | null
  /** The participants, each with the address mailr knows for them (null when none) and the person
   *  the string stands for (participants.py): `key` is "me", "email:<address>" or "name:<name>". */
  people?: Participant[]
  /** The first summary line that holds a search word (searches only). */
  summary_match?: string | null
}

/** A participant string and the person it resolves to (GET /speakr/recordings?annotate=true). */
export interface Participant {
  name: string
  email: string | null
  key?: string
  label?: string
  me?: boolean
  /** How the person was found: override | mapped | me | me_first | people | alias | raw. */
  how?: string
}

/** One person among a recording's participants: the strings that name them share one key. */
export interface Identity {
  key: string
  label: string
  email: string | null
  me: boolean
}

export const RECENT_DAYS = 7

/** When the meeting was: the meeting date, else the time Speakr received the recording. */
export function whenOf(r: Pick<Recording, 'meeting_date' | 'created_at'>): Date | null {
  const v = r.meeting_date ?? r.created_at
  if (!v) return null
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? null : d
}

/** The local calendar day of a date, as YYYY-MM-DD. */
export function dayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate())

/** Speakr's status, in the three kinds the list shows. */
export function statusKind(status: string | null | undefined): 'done' | 'working' | 'failed' {
  const s = (status ?? '').toLowerCase()
  if (s === 'failed' || s === 'error') return 'failed'
  if (!s || s === 'completed' || s === 'complete' || s === 'done') return 'done'
  return 'working'
}

export function statusLabel(status: string | null | undefined): string {
  const s = (status ?? '').toLowerCase()
  if (statusKind(s) === 'failed') return tr('common.failed')
  if (s === 'summarizing') return tr('recordings.model.summarising')
  if (s === 'pending' || s === 'queued' || s === 'uploading') return tr('common.waiting')
  return tr('recordings.model.processing')
}

/** Linked to an event: a confirmed link or a suggestion the user has not answered yet. */
export const isLinked = (r: RecItem) => !!r.event_link

// Names compare in "First Last" order, whatever case and spaces ("Lopez, Mara" is "mara lopez").
const norm = (s: string) => personName(s).replace(/\s+/g, ' ').trim().toLowerCase()

/** The key of a participant string the server did not resolve: its name in "First Last" order. */
export const nameKey = (raw: string) => `name:${norm(raw)}`

/** Each participant string with what the server said about it (a string it did not annotate stands for itself). */
function participantsOf(r: Pick<RecItem, 'participants' | 'people'>): Participant[] {
  const by = new Map((r.people ?? []).map((p) => [norm(p.name), p]))
  return r.participants.map((name) => by.get(norm(name)) ?? { name, email: null })
}

/** The people of a recording, once each, in the order of its participants: "Alex" and
 *  "Alex Rivera" in one recording are one person when they resolve to the same key. */
export function identities(r: Pick<RecItem, 'participants' | 'people'>): Identity[] {
  const list = participantsOf(r)
  const out = new Map<string, Identity>()
  for (const p of list) {
    const key = p.key ?? nameKey(p.name)
    if (!out.has(key)) out.set(key, { key, label: p.label || personName(p.name), email: p.email, me: !!p.me })
  }
  return [...out.values()]
}

/** The recordings a left-column choice and the chosen participants (identity keys) keep. */
export function applyFilter(items: RecItem[], filter: RecordingFilter, now: Date, who: string[] = []): RecItem[] {
  const since = startOfDay(new Date(now.getTime() - (RECENT_DAYS - 1) * 86_400_000))
  return items.filter((r) => {
    if (who.length) {
      const keys = new Set(identities(r).map((p) => p.key))
      if (!who.every((k) => keys.has(k))) return false
    }
    switch (filter.kind) {
      case 'recent': {
        const w = whenOf(r)
        return !!w && w >= since
      }
      case 'linked':
        return isLinked(r)
      case 'unlinked':
        return !isLinked(r)
      case 'folder':
        return norm(r.folder ?? '') === norm(filter.id)
      case 'tag':
        return r.tags.some((t) => norm(t) === norm(filter.id))
      default:
        return true
    }
  })
}

export interface Counts {
  all: number
  recent: number
  linked: number
  unlinked: number
  /** By name, as the recordings spell them. */
  folders: Map<string, number>
  tags: Map<string, number>
  /** People by how many recordings they are in (You first, then most first, then by name), each
   *  with the strings that name them and how each joined (alias and me_first can be separated). */
  people: PersonCount[]
}

export interface PersonCount extends Identity {
  count: number
  names: { name: string; how: string }[]
}

export function counts(items: RecItem[], now: Date): Counts {
  const folders = new Map<string, number>()
  const tags = new Map<string, number>()
  const people = new Map<string, PersonCount>()
  for (const r of items) {
    if (r.folder) folders.set(r.folder, (folders.get(r.folder) ?? 0) + 1)
    for (const t of new Set(r.tags)) tags.set(t, (tags.get(t) ?? 0) + 1)
    for (const id of identities(r)) {
      if (!id.label) continue
      const cur = people.get(id.key) ?? { ...id, count: 0, names: [] }
      cur.count += 1
      cur.email = cur.email ?? id.email
      people.set(id.key, cur)
    }
    for (const p of participantsOf(r)) {
      const cur = people.get(p.key ?? nameKey(p.name))
      const name = personName(p.name)
      if (cur && name && !cur.names.some((n) => norm(n.name) === norm(name))) cur.names.push({ name, how: p.how ?? 'raw' })
    }
  }
  const linked = items.filter(isLinked).length
  return {
    all: items.length,
    recent: applyFilter(items, { kind: 'recent' }, now).length,
    linked,
    unlinked: items.length - linked,
    folders,
    tags,
    people: [...people.values()].sort((a, b) => Number(b.me) - Number(a.me) || b.count - a.count || a.label.localeCompare(b.label)),
  }
}

export interface DayGroup {
  key: string
  title: string
  items: RecItem[]
}

/** The list's day headings: Today, Yesterday, the weekday within the week, else the date. */
export function dayTitle(day: Date, now: Date): string {
  const diff = Math.round((startOfDay(now).getTime() - startOfDay(day).getTime()) / 86_400_000)
  if (diff === 0) return tr('common.today')
  if (diff === 1) return tr('common.yesterday')
  if (diff === -1) return tr('common.tomorrow')
  if (diff > 1 && diff < 7) return day.toLocaleDateString(appLocale(), { weekday: 'long' })
  return day.toLocaleDateString(appLocale(), {
    weekday: 'short', month: 'short', day: 'numeric', ...(day.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}),
  })
}

/** Newest first, one group per local day; recordings without a date come last. */
export function groupByDay(items: RecItem[], now: Date): DayGroup[] {
  const sorted = [...items].sort((a, b) => (whenOf(b)?.getTime() ?? -Infinity) - (whenOf(a)?.getTime() ?? -Infinity) || b.recording_id - a.recording_id)
  const out: DayGroup[] = []
  for (const r of sorted) {
    const w = whenOf(r)
    const key = w ? dayKey(w) : 'undated'
    let g = out[out.length - 1]
    if (!g || g.key !== key) {
      g = { key, title: w ? dayTitle(w, now) : tr('common.noDate'), items: [] }
      out.push(g)
    }
    g.items.push(r)
  }
  return out
}

/** The text cut where the search's words start (word prefixes, as the summary index matches). */
export function highlight(text: string, q: string): { text: string; hit: boolean }[] {
  const words = q.toLowerCase().split(/\s+/).map((w) => w.replace(/[^\p{L}\p{N}]+/gu, '')).filter((w) => w.length >= 2)
  if (!words.length || !text) return [{ text, hit: false }]
  const out: { text: string; hit: boolean }[] = []
  const re = /[\p{L}\p{N}]+/gu
  let last = 0
  for (let m = re.exec(text); m; m = re.exec(text)) {
    const tok = m[0].toLowerCase()
    const w = words.filter((x) => tok.startsWith(x)).sort((a, b) => b.length - a.length)[0]
    if (!w) continue
    if (m.index > last) out.push({ text: text.slice(last, m.index), hit: false })
    out.push({ text: text.slice(m.index, m.index + w.length), hit: true })
    last = m.index + w.length
  }
  if (last < text.length) out.push({ text: text.slice(last), hit: false })
  // Neighbouring pieces of the same kind join up.
  return out.reduce<{ text: string; hit: boolean }[]>((acc, p) => {
    const prev = acc[acc.length - 1]
    if (prev && prev.hit === p.hit) prev.text += p.text
    else acc.push({ ...p })
    return acc
  }, [])
}

/** The left column's name for a filter (the list's heading). */
export function filterTitle(f: RecordingFilter): string {
  switch (f.kind) {
    case 'recent':
      return tr('common.recent')
    case 'linked':
      return tr('recordings.model.linkedToEvents')
    case 'unlinked':
      return tr('common.notLinked')
    case 'folder':
    case 'tag':
      return f.id
    default:
      return tr('recordings.model.allRecordings')
  }
}

/** "10:30", the meeting's start in the list. */
export function timeOf(r: RecItem): string {
  const w = whenOf(r)
  return w ? w.toLocaleTimeString(appLocale(), { hour: 'numeric', minute: '2-digit', ...clockOpts() }) : ''
}
