// "Add to calendar" for an .ics file that is not an invitation (event.import_ics). The server
// route POST /calendar/import is used when it exists; until then the file is read here and each
// VEVENT is written with POST /calendar/events. Only what an event needs is read: UID, SUMMARY,
// DTSTART/DTEND (or DURATION), LOCATION, DESCRIPTION, RRULE, URL.

import { t as tr } from '../../i18n'

export interface IcsEvent {
  uid: string | null
  summary: string
  start: string
  end: string | null
  all_day: boolean
  tz?: string
  location?: string
  description?: string
  recurrence?: string
  url?: string
}

/** Unfolded content lines (RFC 5545 §3.1). */
function lines(text: string): string[] {
  return text.replace(/\r\n[ \t]/g, '').replace(/\n[ \t]/g, '').split(/\r?\n/).filter(Boolean)
}

function unescape(v: string): string {
  return v.replace(/\\n/gi, '\n').replace(/\\([,;\\])/g, '$1')
}

interface Prop {
  name: string
  params: Record<string, string>
  value: string
}

function prop(line: string): Prop | null {
  // NAME;PARAM=a;PARAM="b:c":value — the first colon outside quotes ends the parameters.
  let q = false
  let i = 0
  for (; i < line.length; i++) {
    const c = line[i]
    if (c === '"') q = !q
    else if (c === ':' && !q) break
  }
  if (i >= line.length) return null
  const head = line.slice(0, i).split(';')
  const params: Record<string, string> = {}
  for (const p of head.slice(1)) {
    const eq = p.indexOf('=')
    if (eq > 0) params[p.slice(0, eq).toUpperCase()] = p.slice(eq + 1).replace(/^"|"$/g, '')
  }
  return { name: head[0].toUpperCase(), params, value: line.slice(i + 1) }
}

/** An iCalendar date or date-time as the API takes it: a date, a UTC instant, or a naive wall time. */
function when(p: Prop): { value: string; date: boolean; tz?: string } | null {
  const v = p.value.trim()
  const d = /^(\d{4})(\d{2})(\d{2})$/.exec(v)
  if (d || p.params.VALUE === 'DATE') {
    const m = d ?? /^(\d{4})(\d{2})(\d{2})/.exec(v)
    return m ? { value: `${m[1]}-${m[2]}-${m[3]}`, date: true } : null
  }
  const t = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$/.exec(v)
  if (!t) return null
  const wall = `${t[1]}-${t[2]}-${t[3]}T${t[4]}:${t[5]}:${t[6]}`
  if (t[7]) return { value: `${wall}+00:00`, date: false }
  return { value: wall, date: false, tz: p.params.TZID }
}

/** "PT1H30M", "P1D" → milliseconds. */
function duration(v: string): number | null {
  const m = /^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/.exec(v.trim())
  if (!m) return null
  const [, sign, w, d, h, min, s] = m
  const ms = ((+(w ?? 0) * 7 + +(d ?? 0)) * 86_400 + +(h ?? 0) * 3600 + +(min ?? 0) * 60 + +(s ?? 0)) * 1000
  return sign === '-' ? -ms : ms
}

function addMs(start: { value: string; date: boolean }, ms: number): string {
  if (start.date) {
    const d = new Date(`${start.value}T00:00:00Z`)
    return new Date(d.getTime() + ms).toISOString().slice(0, 10)
  }
  const utc = start.value.endsWith('+00:00')
  const d = new Date(utc ? start.value : `${start.value}Z`)
  const out = new Date(d.getTime() + ms).toISOString().slice(0, 19)
  return utc ? `${out}+00:00` : out
}

/** The VEVENTs of a calendar file (cancelled ones and overrides of a series are left out). */
export function parseIcs(text: string): IcsEvent[] {
  const out: IcsEvent[] = []
  let cur: Prop[] | null = null
  let depth = 0
  for (const line of lines(text)) {
    const u = line.toUpperCase()
    if (u === 'BEGIN:VEVENT') {
      cur = []
      depth = 0
      continue
    }
    if (!cur) continue
    if (u.startsWith('BEGIN:')) depth++
    else if (u.startsWith('END:') && u !== 'END:VEVENT') depth--
    else if (u === 'END:VEVENT') {
      const get = (n: string) => cur!.find((p) => p.name === n)
      const ds = get('DTSTART')
      const start = ds ? when(ds) : null
      if (start && !get('RECURRENCE-ID') && (get('STATUS')?.value ?? '').toUpperCase() !== 'CANCELLED') {
        const de = get('DTEND')
        const dur = get('DURATION')
        const endW = de ? when(de) : null
        const end = endW ? endW.value : dur && duration(dur.value) != null ? addMs(start, duration(dur.value)!) : start.date ? addMs(start, 86_400_000) : null
        const ev: IcsEvent = { uid: get('UID')?.value ?? null, summary: unescape(get('SUMMARY')?.value ?? '').trim() || tr('common.noTitle'), start: start.value, end, all_day: start.date }
        if (start.tz) ev.tz = start.tz
        const loc = get('LOCATION')
        if (loc?.value) ev.location = unescape(loc.value)
        const desc = get('DESCRIPTION')
        if (desc?.value) ev.description = unescape(desc.value).slice(0, 8000)
        const rr = get('RRULE')
        if (rr?.value) ev.recurrence = rr.value
        const url = get('URL')
        if (url?.value) ev.url = url.value
        out.push(ev)
      }
      cur = null
      continue
    }
    if (depth === 0) {
      const p = prop(line)
      if (p) cur.push(p)
    }
  }
  return out
}

/** Is this calendar part an invitation (it has a METHOD other than PUBLISH)? */
export function isInvitationMethod(method: string | null | undefined): boolean {
  const m = (method ?? '').toUpperCase()
  return !!m && m !== 'PUBLISH'
}
