// Citations in assistant answers.
//
// The server verifies every citation and rewrites markers to Markdown links `[n](mailr-cite:n)`;
// the `citations` list says where each one points. It does so while the answer streams too: a `cite`
// event brings each citation before the text that links to it. `streamingText` and `finalText`
// clean up any raw marker that is left (citeGrammar.ts), and hide one that is still being typed.

import { convSlug } from '../../../lib/pubid'
import { browserZone, isValidZone, clockOpts, formatInstant, type ZonelessFormatOptions } from '../../../lib/dates'
import { eventPath, notePath, tableRoutePath, taskPath } from '../../../lib/routes'
import { recordingPath } from '../../speakr/format'
import { cleanMarkers } from './citeGrammar'
import { t, appLocale } from '../../../i18n'

export interface Citation {
  n: number
  /** `event` and `task` cite a calendar record ([[event:uid@occurrence]], [[task:uid]]); `recording`
   *  a Speakr recording or a moment of it ([[r:id@seconds "quote"]]); `web` a page the assistant's web
   *  search found ([[web:url]]), opened outside mailr; `note` a note or one item of a list
   *  ([[note:id]], [[note:id#item]]); `table` a table or one row ([[table:id]], [[table:id#row]]). */
  kind: 'message' | 'conversation' | 'event' | 'task' | 'recording' | 'web' | 'note' | 'table'
  conversation_id: number | null
  message_id: number | null
  subject: string | null
  from: string | null
  date: string | null
  quote: string | null
  verified: boolean
  start: number | null
  end: number | null
  url: string | null
  // Events and tasks: what the chip shows (title, when, colour) and where it opens.
  uid?: string
  occurrence?: string | null
  title?: string
  starts_at?: string | null
  ends_at?: string | null
  due?: string | null
  all_day?: boolean
  completed?: boolean
  color?: string | null
  location?: string | null
  // Recordings: which one, the moment (seconds, also in `start`), who said it, Speakr's deep link in `url`.
  recording_id?: number
  connector_id?: number
  t?: number | null
  speaker?: string | null
  meeting_date?: string | null
  // Web pages: the site (host without www) and the search provider; the page is `url`.
  site?: string | null
  provider?: string | null
  // Tables ([[table:<id>]], [[table:<id>#<row>]]): the table's id and the row; `title` is the row's
  // primary value (or the table's name) and `from` the table's name.
  table?: string
  row?: string | null
  // Notes: which note and item, its kind, a line of it (the item's text or the note's first line).
  note_id?: string
  item_id?: string | null
  note_kind?: 'text' | 'checklist'
  snippet?: string | null
  checked?: boolean | null
}

/** A web citation's address, when it is a real http(s) link (else null). */
export function webUrl(c: Pick<Citation, 'kind' | 'url'>): string | null {
  if (c.kind !== 'web' || !c.url) return null
  return /^https?:\/\//i.test(c.url) ? c.url : null
}

/** Open a web citation in a new tab, outside mailr (no referrer, no opener). True when it did.
 *  ACT agent.web_search @web.panel */
export function openWebCitation(c: Pick<Citation, 'kind' | 'url'>): boolean {
  const url = webUrl(c)
  if (!url) return false
  window.open(url, '_blank', 'noopener,noreferrer')
  return true
}

export const CITE_SCHEME = 'mailr-cite:'

/** Text shown while the answer streams. The server sends each marker as its numbered link once it
 *  closes (with a `cite` event first), so links show at once; anything still raw is cleaned up and a
 *  marker still being typed is hidden (citeGrammar.ts). */
export function streamingText(raw: string): string {
  return cleanMarkers(raw, true)
}

/** A finished answer: raw markers an older answer may hold never show (citeGrammar.ts). */
export function finalText(text: string): string {
  return cleanMarkers(text)
}

/** The number in a `mailr-cite:n` href, or null. */
export function citeNumber(href: string | undefined | null): number | null {
  if (!href || !href.startsWith(CITE_SCHEME)) return null
  const n = Number(href.slice(CITE_SCHEME.length))
  return Number.isInteger(n) && n > 0 ? n : null
}

/** In-app route for a citation: the conversation, scrolled to the message, quote highlighted. */
/** Where a citation opens: the cited conversation in the view the user is in (so the list
 *  beside it stays as it was), keeping a search; All Mail when not in a mail view. */
export function citationRoute(
  c: Pick<Citation, 'conversation_id' | 'message_id' | 'quote' | 'verified'> & Partial<Pick<Citation, 'kind' | 'uid' | 'occurrence' | 'recording_id' | 'connector_id' | 't' | 'note_id' | 'item_id' | 'table' | 'row'>>,
  from?: { view: string | null; q?: string | null },
): string | null {
  if (c.kind === 'recording' && c.recording_id && c.connector_id) return recordingPath(c.connector_id, c.recording_id, c.t)
  if (c.kind === 'event' && c.uid) return eventPath(c.uid, c.occurrence ?? '')
  if (c.kind === 'task' && c.uid) return taskPath(c.uid)
  if (c.kind === 'table' && c.table) return tableRoutePath(c.table, null, c.row ?? null)
  // A note citation opens the note, with its item focused.
  if (c.kind === 'note' && c.note_id) return notePath(c.note_id, c.item_id)
  if (!c.conversation_id) return null
  const params = new URLSearchParams()
  if (from?.q) params.set('q', from.q)
  if (c.message_id) params.set('m', String(c.message_id))
  if (c.quote && c.verified) params.set('hl', c.quote)
  const qs = params.toString()
  const view = from?.view || 'all'
  return `/mail/${encodeURIComponent(view)}/${convSlug(c.conversation_id)}${qs ? `?${qs}` : ''}`
}

/** The mail view (and search) of a location, e.g. /mail/label%3A12/55?q=x → label:12. */
export function viewOfLocation(pathname: string, search: string): { view: string | null; q: string | null } {
  const m = /^\/mail\/([^/]+)/.exec(pathname)
  return { view: m ? decodeURIComponent(m[1]) : null, q: new URLSearchParams(search).get('q') }
}

/** Keep only the citations the text still refers to, in order of first appearance. */
export function usedCitations(text: string, cites: Citation[]): Citation[] {
  const seen = new Set<number>()
  const re = /\(mailr-cite:(\d+)\)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) seen.add(Number(m[1]))
  return cites.filter((c) => seen.has(c.n))
}

/** The record a citation points to, for the Sources list: every citation of one email thread
 *  (the conversation, or any of its messages, with or without a quote) is one source; so is every
 *  citation of one event occurrence, task, note item, table row, recording or web page. Shared with
 *  Android (Citation.sourceKey); the cases are spec/cases/source-cases.yaml. */
export function sourceKey(c: Pick<Citation, 'n' | 'kind' | 'conversation_id' | 'message_id' | 'url'> & Partial<Pick<Citation, 'uid' | 'occurrence' | 'recording_id' | 'connector_id' | 'note_id' | 'item_id' | 'table' | 'row'>>): string {
  if (c.kind === 'table' && c.table) return `t:${c.table}/${c.row ?? ''}`
  if (c.kind === 'web' && c.url) return `w:${c.url}`
  if (c.kind === 'note' && c.note_id) return `n:${c.note_id}#${c.item_id ?? ''}`
  if (c.kind === 'recording' && c.recording_id != null) return `r:${c.connector_id ?? ''}:${c.recording_id}`
  if ((c.kind === 'event' || c.kind === 'task') && c.uid) return `${c.kind}:${c.uid}@${c.occurrence ?? ''}`
  if (c.conversation_id != null) return `c:${c.conversation_id}`
  if (c.message_id != null) return `m:${c.message_id}`
  return `#${c.n}`
}

export interface SourceRow<C extends Citation = Citation> {
  key: string
  /** The citation the row opens: the first one met in the answer. */
  first: C
  /** Every citation of the record, by number. */
  all: C[]
}

/** The Sources list: one row per cited record, in the order the answer first cites them. The numbers
 *  in the text stay as they are; a row lists all of its numbers ("Propuesta…" showed twice when the
 *  answer cited the thread once and three quotes of its message). */
export function sourceRows<C extends Citation>(cites: C[]): SourceRow<C>[] {
  const rows = new Map<string, SourceRow<C>>()
  for (const c of [...cites].sort((a, b) => a.n - b.n)) {
    const key = sourceKey(c)
    const r = rows.get(key)
    if (r) r.all.push(c)
    else rows.set(key, { key, first: c, all: [c] })
  }
  return [...rows.values()]
}

export function shortDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameYear = d.getFullYear() === now.getFullYear()
  return d.toLocaleDateString(appLocale(), { month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) })
}

/** When a cited event or task is: "Thu, Oct 2, 3:00 PM", "Thu, Oct 2" (all day), "Due Oct 2". A time
 *  is read in `zone`, the user's zone from Settings (the calendar shows the same); the zone's short name
 *  is added when it is not the browser's. */
export function citeWhen(c: Pick<Citation, 'kind'> & Partial<Pick<Citation, 'starts_at' | 'due' | 'all_day' | 'date'>>, zone?: string): string {
  const v = c.kind === 'task' ? c.due : c.kind === 'event' ? c.starts_at : c.date
  if (!v) return c.kind === 'task' ? t('common.noDueDate') : ''
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(v)
  const d = dateOnly ? new Date(`${v}T12:00:00`) : new Date(v)
  if (Number.isNaN(d.getTime())) return ''
  const timed = !(dateOnly || c.all_day)
  const own = timed && zone && isValidZone(zone) ? zone : undefined
  const opts: ZonelessFormatOptions = !timed
    ? { weekday: 'short', month: 'short', day: 'numeric' }
    : { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...clockOpts(),
        ...(own && own !== browserZone() ? { timeZoneName: 'short' as const } : {}) }
  const text = formatInstant(d, appLocale(), opts, own)
  return c.kind === 'task' ? t('rail.assistant.citations.due', { text }) : text
}
