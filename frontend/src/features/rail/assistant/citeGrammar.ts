// The citation marker grammar (spec/cases/citation-cases.yaml). The server turns markers into
// numbered links `[n](mailr-cite:n)`, also while the answer streams; this is the safety net for
// text that still holds raw markers (an older answer, an older server). A marker that can be read is
// left out, one that names a kind but cannot be read becomes its quoted text, and raw `[[…]]` markup
// never shows. Twins: backend/mailr/intel/citegrammar.py and Android's ui/present/CiteGrammar.kt.

export interface Marker {
  kind: 'm' | 'p' | 'c' | 'r' | 'event' | 'task' | 'web' | 'note' | 'table'
  id: string
  at: string | null
  quote: string | null
}

const KINDS: Record<string, Marker['kind']> = {
  m: 'm', message: 'm', msg: 'm', p: 'p', passage: 'p', c: 'c', conversation: 'c', thread: 'c',
  r: 'r', recording: 'r', event: 'event', task: 'task', web: 'web', note: 'note',
  // Tables: [[table:<id>]] and [[table:<id>#<row_id>]].
  table: 'table',
}
const TABLE_ID = /^t[0-9a-z]{6,15}$/
const ROW_ID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

/** A table (`t…`) or one of its rows (`t…#<uuid>`); spaces around "#" are dropped. */
function tableMarker(head: string, quote: string | null): Marker | null {
  const token = head.replace(/\s*#\s*/g, '#').split(/\s+/).filter(Boolean)[0] ?? ''
  const i = token.indexOf('#')
  const pid = i < 0 ? token : token.slice(0, i)
  const row = i < 0 ? '' : token.slice(i + 1)
  if (!TABLE_ID.test(pid) || (i >= 0 && !ROW_ID.test(row))) return null
  return { kind: 'table', id: pid, at: row ? row.toLowerCase() : null, quote }
}
const QUOTES = '"“”'
const OCC = String.raw`\d{4}-?\d{2}-?\d{2}(?:T\d{2}:?\d{2}(?::?\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?`
const OCC_TAIL = new RegExp(String.raw`^(.+?)@(${OCC})$`)
const REC = /^(\d{1,9})(?:@(\d{1,2}:\d{2}(?::\d{2})?|\d{1,6}(?:\.\d+)?))?/
const KIND = /^\s*([A-Za-z]+)\s*:\s*/
/** A note: its id (letters and digits), and after "#" one of its items. */
const NOTE = /^([A-Za-z0-9]{2,40})(?:#([A-Za-z0-9-]{1,64}))?$/
/** One marker: `[[body]]` or a legacy `[c:12]`, `[event:uid]`, `[task:uid]` (not a Markdown link's text). */
export const MARKER = /([ \t]*)(?:\[\[((?:(?!\]\]|\[\[)[^\n]){0,500})\]\]|\[(c:\d{1,12}|(?:event|task):[^\]\s]{1,300})\](?!\())/g
/** A marker left open at the end of the text. */
const OPEN = /[ \t]*\[\[\s*[A-Za-z]+\s*:[^\]\n]{0,500}$/
/** While streaming: anything at the end that may still become a marker. */
const PARTIAL = /[ \t]*\[\[?[^\]\n]{0,500}$/

export function looksLikeMarker(body: string): boolean {
  return KIND.test(body)
}

function lastQuote(s: string): number {
  return Math.max(...QUOTES.split('').map((q) => s.lastIndexOf(q)))
}

function firstQuote(s: string): number {
  for (let i = 0; i < s.length; i++) if (QUOTES.includes(s[i])) return i
  return -1
}

/** The marker in `body` (between `[[` and `]]`), or null when it cannot be read. */
export function parseMarker(body: string): Marker | null {
  const m = KIND.exec(body)
  if (!m) return null
  const kind = KINDS[m[1].toLowerCase()]
  if (!kind) return null
  const rest = body.slice(m[0].length)
  const qi = firstQuote(rest)
  let quote: string | null = null
  let head = rest
  if (qi >= 0) {
    const last = lastQuote(rest)
    quote = (last > qi ? rest.slice(qi + 1, last) : rest.slice(qi + 1)).trim() || null
    head = rest.slice(0, qi)
  }
  head = head.replace(/\s*@\s*/g, '@').trim().replace(/[\s|,]+$/, '')
  if (kind === 'm' || kind === 'p' || kind === 'c') {
    const d = /^(\d{1,12})(?![\w@])/.exec(head)
    return d ? { kind, id: d[1], at: null, quote } : null
  }
  if (kind === 'r') {
    const d = REC.exec(head)
    if (!d || (head.length > d[0].length && !/\s/.test(head[d[0].length]))) return null
    return { kind, id: d[1], at: d[2] ?? null, quote }
  }
  if (kind === 'table') return tableMarker(head, quote)
  const token = head.split(/\s+/).filter(Boolean)[0] ?? ''
  if (!token || token.includes('[') || token.includes(']')) return null
  if (kind === 'web') return /^https?:\/\/[^/\s]/i.test(token) ? { kind, id: token, at: null, quote } : null
  if (kind === 'note') {
    const n = NOTE.exec(token)
    return n ? { kind, id: n[1], at: n[2] ?? null, quote } : null
  }
  if (kind === 'event') {
    const o = OCC_TAIL.exec(token)
    if (o) return { kind, id: o[1], at: o[2], quote }
  }
  return { kind, id: token, at: null, quote }
}

/** What a marker that cannot be read shows: its quoted text, else nothing. */
export function fallbackText(body: string): string {
  const qi = firstQuote(body)
  if (qi < 0) return ''
  const last = lastQuote(body)
  return (last > qi ? body.slice(qi + 1, last) : body.slice(qi + 1)).trim()
}

/** Every marker in `text` that can be read, in order. */
export function scanMarkers(text: string): Marker[] {
  const out: Marker[] = []
  for (const mt of text.matchAll(MARKER)) {
    const mk = mt[3] != null ? parseMarker(mt[3]) : looksLikeMarker(mt[2]) ? parseMarker(mt[2]) : null
    if (mk) out.push(mk)
  }
  return out
}

/** The text a reader sees where no numbered link replaced a marker (citation-cases.yaml `clean` and `stream`). */
export function cleanMarkers(text: string, streaming = false): string {
  let out = text.replace(MARKER, (all: string, sp: string, body: string | undefined, legacy: string | undefined) => {
    if (legacy != null) return ''
    if (!looksLikeMarker(body ?? '')) return all
    if (parseMarker(body ?? '')) return ''
    const fb = fallbackText(body ?? '')
    return fb ? sp + fb : ''
  })
  out = out.replace(OPEN, '')
  if (streaming) out = out.replace(PARTIAL, '')
  return out
}
