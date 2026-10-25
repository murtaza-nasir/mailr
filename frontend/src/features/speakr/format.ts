// Small formatting helpers for recordings: times in a recording ("12:34", "1:02:03"), the route of
// the recording sheet, Speakr deep links at a moment, and Speakr's Markdown summaries as the
// simple HTML a draft takes (everything escaped first).

/** Seconds as m:ss, or h:mm:ss from an hour on. */
import { t as tr } from '../../i18n'

export function fmtT(t: number | null | undefined): string {
  if (t === null || t === undefined || !Number.isFinite(t)) return ''
  const s = Math.max(0, Math.floor(t))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

/** "45 min", "1 h 05 min". */
export function fmtDuration(s: number | null | undefined): string {
  if (!s) return ''
  const m = Math.round(s / 60)
  return m >= 60 ? tr('speakr.format.hMin', { floor: Math.floor(m / 60), padStart: String(m % 60).padStart(2, '0') }) : tr('speakr.format.min', { m })
}

/** The in-app route of a recording (web.recording), optionally at a moment. */
export function recordingPath(cid: number, rid: number, t?: number | null): string {
  return `/recordings/${cid}/${rid}${t !== null && t !== undefined ? `?t=${Math.floor(t)}` : ''}`
}

/** Speakr's deep link (mailr returns it without ?t=) at a moment: Speakr seeks there and plays. */
export function speakrAt(url: string, t?: number | null): string {
  const base = url.replace(/\?t=\d+$/, '')
  return t !== null && t !== undefined ? `${base}?t=${Math.floor(t)}` : base
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function inline(t: string): string {
  return esc(t)
    .replace(/\*\*(.+?)\*\*|__(.+?)__/g, (_m, a: string | undefined, b: string | undefined) => `<b>${a ?? b}</b>`)
    .replace(/(^|[^\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)/g, '$1<i>$2</i>')
}

/** Markdown (headings, lists, bold, italics, paragraphs) as simple HTML for the editor. */
export function markdownHtml(md: string): string {
  const out: string[] = []
  let list: 'ul' | 'ol' | null = null
  const close = () => {
    if (list) out.push(`</${list}>`)
    list = null
  }
  for (const raw of (md ?? '').split('\n')) {
    const line = raw.trimEnd()
    if (!line.trim()) {
      close()
      continue
    }
    const h = /^\s{0,3}#{1,6}\s+(.*)$/.exec(line)
    const b = /^\s*[-*+]\s+(.*)$/.exec(line)
    const n = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    if (h) {
      close()
      out.push(`<p><b>${inline(h[1])}</b></p>`)
    } else if (b || n) {
      const kind = b ? 'ul' : 'ol'
      if (list !== kind) {
        close()
        out.push(`<${kind}>`)
        list = kind
      }
      out.push(`<li>${inline((b ?? n)![1])}</li>`)
    } else {
      close()
      out.push(`<p>${inline(line.trim())}</p>`)
    }
  }
  close()
  return out.join('')
}

/** Transcript turns as a quoted block: "Dana (12:34): text" per turn. */
export function excerptHtml(title: string, segs: { t: number | null; speaker: string; text: string }[]): string {
  const rows = segs.map((s) => `<div>${s.speaker ? `<b>${esc(s.speaker)}</b>` : ''}${s.t !== null ? ` (${fmtT(s.t)})` : ''}${s.speaker || s.t !== null ? ': ' : ''}${esc(s.text)}</div>`)
  return `<blockquote><div><i>From the recording “${esc(title)}”</i></div>${rows.join('')}</blockquote><p></p>`
}

const MEDIA_EXT = /\.(mp3|m4a|wav|ogg|oga|opus|flac|aac|amr|wma|weba|webm|mp4|m4v|mov|mkv|avi|3gp|3gpp)$/i

/** An attachment Speakr can transcribe: audio or video by type, or by a media file name. */
export function isMedia(a: { type: string; filename: string }): boolean {
  return /^(audio|video)\//i.test(a.type ?? '') || MEDIA_EXT.test(a.filename ?? '')
}

/**
 * The recordings part of a mail search: the text of `recording:` operators and the free words
 * (other operators drop out). `has:recording` alone gives '' (the most recent recordings).
 */
export function recordingQuery(q: string): { text: string; on: boolean } {
  const parts = q.match(/-?\w+:"[^"]*"|-?\w+:\S+|"[^"]*"|\S+/g) ?? []
  const words: string[] = []
  let on = false
  for (const p of parts) {
    const m = /^(-?)(\w+):(.*)$/.exec(p)
    if (m) {
      const [, neg, op, raw] = m
      const v = raw.replace(/^"|"$/g, '')
      if (neg) continue
      if (op.toLowerCase() === 'recording') { on = true; if (v) words.push(v) }
      else if (op.toLowerCase() === 'has' && /^recordings?$/i.test(v)) on = true
      continue
    }
    if (/^(OR|AND)$/.test(p) || p.startsWith('-')) continue
    words.push(p.replace(/^"|"$/g, ''))
  }
  return { text: words.join(' ').trim(), on }
}

/** An event the recording mentions ("let's meet on the 22nd") as the editor's seed. */
/** A floating time (wall-clock, no zone) is read in the user's own zone, even when a zone was added. */
function wallClock(v: string, floating: boolean | null | undefined): Date {
  return new Date(floating ? v.replace(/(Z|[+-]\d\d:?\d\d)$/, '') : v)
}

export function extractedEventSeed(
  ev: { title: string; start: string | null; end: string | null; location: string; description: string;
    attendees?: { name: string; email: string | null }[]; reminder_minutes?: number | null; floating?: boolean | null },
  rec: { title: string; url: string },
) {
  const start = ev.start ? wallClock(ev.start, ev.floating) : null
  if (!start || Number.isNaN(start.getTime())) return null
  const end = ev.end ? wallClock(ev.end, ev.floating) : null
  // Guests with an address go to the guest list; names without one stay in the description.
  const guests = (ev.attendees ?? []).filter((a) => a.email).map((a) => ({ email: a.email!, ...(a.name ? { name: a.name } : {}) }))
  const unnamed = (ev.attendees ?? []).filter((a) => !a.email && a.name).map((a) => a.name)
  const desc = [ev.description, unnamed.length ? tr('speakr.format.alsoMentioned', { unnamed: unnamed.join(', ') }) : '',
    tr('speakr.format.fromTheRecording', { recTitle: rec.title, url: rec.url })].filter(Boolean).join('\n\n')
  return {
    summary: ev.title || tr('speakr.format.meeting'),
    start,
    end: end && !Number.isNaN(end.getTime()) && end > start ? end : new Date(start.getTime() + 3_600_000),
    allDay: false,
    location: ev.location ?? '',
    description: desc,
    ...(guests.length ? { guests } : {}),
    ...(typeof ev.reminder_minutes === 'number' ? { reminders: [ev.reminder_minutes] } : {}),
  }
}
