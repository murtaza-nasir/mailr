// The link picker of a note or a table: one search over the user's mail, events,
// tasks and recordings, grouped by kind. A typed or pasted mailr address is offered first ("Link this
// address"). Arrow keys move, Enter links, Esc closes. Moving the pointer over a row only highlights
// it (onMouseMove, as in the command palette: rows that appear under a resting pointer fire
// mouseenter, and the highlight then left the row the keys chose); a click links. The Recordings
// group stays hidden when the search there fails (no Speakr connection, or no `recordings` scope).

import { useQuery } from '@tanstack/react-query'
import { CalendarDays, CircleCheck, Link2, Mail, Mic } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../../api/client'
import { eventPath, taskPath } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import type { NoteLink } from './types'
import { t as tr, appLocale } from '../../i18n'

export type LinkKind = NoteLink['target_kind']

export interface LinkOption {
  key: string
  group: 'address' | 'mail' | 'event' | 'task' | 'recording'
  kind: LinkKind
  ref: Record<string, unknown>
  title: string
  detail: string
}

interface ConvHit { id: number | string; subject?: string | null; last_date?: string | null; snippet?: string | null }
interface CalHit { kind?: string; uid: string; summary?: string | null; start?: string | null; occurrence?: string | null; due?: string | null }
interface RecHit { connector_id: number; recording_id: number; title?: string | null; meeting_date?: string | null }

export interface PickerData {
  mail?: ConvHit[]
  events?: CalHit[]
  tasks?: CalHit[]
  recordings?: RecHit[] | null
}

const GROUP_TITLE: Record<LinkOption['group'], string> = {
  address: tr('common.address'), mail: tr('common.emails'), event: 'Events', task: tr('common.tasks'), recording: tr('common.recordings'),
}

const day = (s: string | null | undefined) => {
  if (!s) return ''
  const d = new Date(s.length === 10 ? `${s}T12:00:00` : s)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(appLocale(), { month: 'short', day: 'numeric', year: 'numeric' })
}

/** The picker's results in order: a parsed address first, then emails, events, tasks and recordings.
 *  Kinds in `exclude` are left out. */
export function pickerOptions(q: string, data: PickerData, exclude: LinkKind[] = []): LinkOption[] {
  const out: LinkOption[] = []
  const parsed = parseLink(q)
  if (parsed && !exclude.includes(parsed.kind)) {
    out.push({ key: `address:${q.trim()}`, group: 'address', kind: parsed.kind, ref: parsed.ref, title: tr('notes.linkPicker.linkThisAddress'), detail: q.trim() })
  }
  if (!exclude.includes('conversation'))
    for (const c of data.mail ?? [])
      out.push({ key: `c:${c.id}`, group: 'mail', kind: 'conversation', ref: { conversation_id: c.id }, title: c.subject || tr('common.noSubject'), detail: day(c.last_date) })
  if (!exclude.includes('event'))
    for (const e of data.events ?? [])
      out.push({ key: `e:${e.uid}:${e.occurrence ?? ''}`, group: 'event', kind: 'event', ref: { uid: e.uid, ...(e.occurrence ? { occurrence: e.occurrence } : {}) }, title: e.summary || tr('common.noTitle'), detail: day(e.start) })
  if (!exclude.includes('task'))
    for (const t of data.tasks ?? [])
      out.push({ key: `t:${t.uid}`, group: 'task', kind: 'task', ref: { uid: t.uid }, title: t.summary || tr('notes.linkPicker.untitledTask'), detail: t.due ? tr('notes.linkPicker.due', { day: day(t.due) }) : '' })
  if (!exclude.includes('recording'))
    for (const r of data.recordings ?? [])
      out.push({ key: `r:${r.connector_id}:${r.recording_id}`, group: 'recording', kind: 'recording', ref: { connector_id: r.connector_id, recording_id: r.recording_id }, title: r.title || tr('common.recording'), detail: day(r.meeting_date) })
  return out
}

const ICON: Record<LinkOption['group'], ReactNode> = {
  address: <Link2 size={14} />, mail: <Mail size={14} />, event: <CalendarDays size={14} />, task: <CircleCheck size={14} />, recording: <Mic size={14} />,
}

function useDebounced(v: string, ms = 250): string {
  const [d, setD] = useState(v)
  useEffect(() => {
    const t = window.setTimeout(() => setD(v), ms)
    return () => window.clearTimeout(t)
  }, [v, ms])
  return d
}

function useSearch<T>(key: string, q: string, enabled: boolean, fn: (signal: AbortSignal) => Promise<T[]>) {
  return useQuery<T[]>({
    queryKey: ['linkpicker', key, q],
    queryFn: ({ signal }) => fn(signal),
    enabled: enabled && !!q.trim(),
    staleTime: 30_000,
    retry: false,
  })
}

/** Search mail, events, tasks and recordings and pick one to link. */
export function LinkPicker({ onLink, onClose, exclude = [] }: { onLink: (kind: LinkKind, ref: Record<string, unknown>) => void; onClose?: () => void; exclude?: LinkKind[] }) {
  const [text, setText] = useState('')
  const [sel, setSel] = useState(0)
  const q = useDebounced(text.trim())
  const want = (k: LinkKind) => !exclude.includes(k)
  const mail = useSearch<ConvHit>('mail', q, want('conversation'), async (signal) =>
    (await api.get<{ items?: ConvHit[] }>('/conversations', { view: 'all', q, limit: 5 }, signal)).items ?? [])
  const events = useSearch<CalHit>('event', q, want('event'), async (signal) =>
    (await api.get<{ items?: CalHit[] }>('/calendar/search', { q, kind: 'event', limit: 5 }, signal)).items ?? [])
  const tasks = useSearch<CalHit>('task', q, want('task'), async (signal) =>
    (await api.get<{ items?: CalHit[] }>('/calendar/search', { q, kind: 'task', limit: 5 }, signal)).items ?? [])
  const recs = useSearch<RecHit>('recording', q, want('recording'), async (signal) =>
    (await api.get<{ items?: RecHit[] }>('/speakr/recordings', { q, limit: 5 }, signal)).items ?? [])
  const options = useMemo(() => pickerOptions(text, {
    mail: mail.data, events: events.data, tasks: tasks.data, recordings: recs.isError ? null : recs.data,
  }, exclude), [text, mail.data, events.data, tasks.data, recs.data, recs.isError, exclude])
  useEffect(() => setSel(0), [options.length, text])
  const loading = !!q && [mail, events, tasks, recs].some((x) => x.isFetching)
  const pick = (o: LinkOption | undefined) => o && onLink(o.kind, o.ref)

  return (
    <div className="nt-link-picker" data-testid="link-picker">
      <input className="input" type="search" value={text} placeholder={tr('notes.linkPicker.searchEmailsEventsTasksAnd')} aria-label={tr('notes.linkPicker.searchForSomethingToLink')}
        data-autofocus autoFocus role="combobox" aria-expanded={options.length > 0} aria-controls="nt-link-results"
        aria-activedescendant={options[sel] ? `nt-link-opt-${sel}` : undefined}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setSel((s) => Math.min(options.length - 1, s + 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setSel((s) => Math.max(0, s - 1))
          } else if (e.key === 'Enter') {
            e.preventDefault()
            pick(options[sel])
          } else if (e.key === 'Escape' && onClose) {
            e.preventDefault()
            e.stopPropagation()
            onClose()
          }
        }} />
      <ul id="nt-link-results" className="nt-link-results" role="listbox" aria-label={tr('notes.linkPicker.results')}>
        {options.map((o, i) => (
          <li key={o.key} role="presentation">
            {(i === 0 || options[i - 1].group !== o.group) && <div className="nt-link-group">{GROUP_TITLE[o.group]}</div>}
            <button type="button" id={`nt-link-opt-${i}`} role="option" aria-selected={i === sel} className={`nt-link-opt${i === sel ? ' active' : ''}`}
              onMouseMove={() => i !== sel && setSel(i)} onClick={() => pick(o)}>
              {ICON[o.group]}
              <span className="nt-link-opt-title">{o.title}</span>
              {o.detail && <span className="muted nt-link-opt-detail">{o.detail}</span>}
            </button>
          </li>
        ))}
      </ul>
      <p className="muted nt-small" aria-live="polite">
        {!text.trim() ? tr('notes.linkPicker.typeWordsFromASubject')
          : loading && !options.length ? tr('notes.linkPicker.searching')
            : !options.length ? tr('common.nothingMatches') : ''}
      </p>
    </div>
  )
}

/** The kind and reference of a mailr address (an email, an event, a task or a recording), or null. */
export function parseLink(s: string): { kind: LinkKind; ref: Record<string, unknown> } | null {
  let path = s.trim()
  try {
    if (/^https?:/.test(path)) path = new URL(path).pathname
  } catch {
    return null
  }
  let m = /^\/mail\/[^/]+\/c?([0-9A-Za-z]+)$/.exec(path)
  if (m) {
    const id = /^\d+$/.test(m[1]) ? Number(m[1]) : m[1]
    return { kind: 'conversation', ref: { conversation_id: id } }
  }
  m = /^\/calendar\/event\/([^/]+)(?:\/([^/]+))?$/.exec(path)
  if (m) return { kind: 'event', ref: { uid: decodeURIComponent(m[1]), ...(m[2] ? { occurrence: decodeURIComponent(m[2]) } : {}) } }
  m = /^\/tasks(?:\/.*)?\/task\/([^/]+)$/.exec(path)
  if (m) return { kind: 'task', ref: { uid: decodeURIComponent(m[1]) } }
  m = /^\/recordings\/(\d+)\/(\d+)$/.exec(path)
  if (m) return { kind: 'recording', ref: { connector_id: Number(m[1]), recording_id: Number(m[2]) } }
  return null
}

/** Where a link chip opens: the email, the event occurrence, the task or the recording. */
export function linkTarget(l: Pick<NoteLink, 'target_kind' | 'target_ref'>): string {
  const r = l.target_ref as Record<string, string | number | undefined>
  if (l.target_kind === 'conversation') return mailPath('inbox', Number(r.conversation_id))
  if (l.target_kind === 'event') return eventPath(String(r.uid), String(r.occurrence ?? ''))
  if (l.target_kind === 'task') return taskPath(String(r.uid))
  return `/recordings/${r.connector_id ?? ''}/${r.recording_id ?? ''}`
}

/** The key the links panels look a target up by (GET /notes/linked and /tables/linked `ref`). */
export function linkedRef(kind: LinkKind, ref: Record<string, unknown>): string | number | null {
  if (kind === 'conversation') return (ref.conversation_id as number | string | undefined) ?? null
  if (kind === 'recording') return ref.connector_id != null && ref.recording_id != null ? `${ref.connector_id}:${ref.recording_id}` : null
  return (ref.uid as string | undefined) || null
}
