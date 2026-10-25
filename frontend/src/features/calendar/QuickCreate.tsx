// Quick create (event mode): one line, parsed by lib/quickadd, with each taken word highlighted, a chip row showing the parse, a conflict line
// that never blocks, and the slot it was opened from. Enter saves; Ctrl/⌘+Enter or Shift+Enter
// opens the full editor with everything carried over; Esc closes and keeps the text.

import { AlarmClock, Clock, MapPin, Repeat, UserPlus, Users, Video, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { usePeople } from '../../api/hooks'
import { Floating } from './Floating'
import { formatRange, toISODate, type ISODate } from '../../lib/dates'
import { offsetLabel } from '../../lib/quickChoices'
import { parse, zonedToInstant, type QuickGuest, type QuickResult, type QuickToken } from '../../lib/quickadd'
import { summarizeRRule } from '../../lib/rrule'
import { conflictLine, timesFor } from './actions'
import { displayColour } from './colour'
import { useCal } from './context'
import { defaultReminders, useDefaults } from './data'
import { formatDuration, gridInstant } from './geometry'
import { askSendUpdates } from './Prompts'
import { useCalUI, type QuickState } from './store'
import type { Collection, EventWrite } from './types'
import { t as tr } from '../../i18n'

/** Text typed in quick create survives Esc and an outside click: the next open restores it. */
let draftText = ''
/** "Keep as text": token strings the user turned back into title words (sticky per string). */
const keepAsText: string[] = []

export function videoLink(template: string): string {
  const id = Math.random().toString(36).slice(2, 10)
  return template.includes('{id}') ? template.replace('{id}', id) : `${template.replace(/\/$/, '')}/${id}`
}

import { nextHalfHour } from './halfHour'
export { nextHalfHour }

export interface Resolved {
  start: Date | ISODate
  end: Date | ISODate
  allDay: boolean
}

/** The times quick create will save: the typed ones, else the slot (a typed time keeps the slot's day). */
export function resolveTimes(r: QuickResult, slot: QuickState, zone: string, defaultDuration: number, now: Date): Resolved {
  const hasDate = r.tokens.some((t) => t.kind === 'date' || t.kind === 'span' || t.kind === 'repeat')
  const hasTime = r.tokens.some((t) => t.kind === 'time')
  if (r.start && r.end) {
    if (r.all_day) return { start: r.start, end: r.end, allDay: true }
    let start = zonedToInstant(r.start, zone)
    let end = zonedToInstant(r.end, zone)
    if (hasTime && !hasDate && slot.start) {
      // "3pm" typed into a clicked day: that day at 3 PM.
      const day = typeof slot.start === 'string' ? slot.start : toISODate(slot.start, zone)
      const dur = end.getTime() - start.getTime()
      start = gridInstant(day, Number(r.start.slice(11, 13)) * 60 + Number(r.start.slice(14, 16)), zone)
      end = new Date(start.getTime() + dur)
    }
    return { start, end, allDay: false }
  }
  if (slot.start && slot.end) {
    if (slot.allDay) return { start: slot.start, end: slot.end, allDay: true }
    const s = slot.start as Date
    const e = r.duration ? new Date(s.getTime() + r.duration * 60_000) : (slot.end as Date)
    return { start: s, end: e, allDay: false }
  }
  const s = nextHalfHour(now)
  return { start: s, end: new Date(s.getTime() + (r.duration ?? defaultDuration) * 60_000), allDay: false }
}

const writable = (c: Collection) => c.events !== false && !c.read_only && !c.error

export function QuickCreate({ q }: { q: QuickState }) {
  const cal = useCal()
  const { prefs } = cal
  const defaults = useDefaults().data
  const [text, setText] = useState(draftText)
  const [keep, setKeep] = useState<string[]>(keepAsText)
  const [confirmed, setConfirmed] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const mirror = useRef<HTMLDivElement>(null)
  const close = () => useCalUI.getState().set({ quick: null })

  const cals = cal.collections.filter(writable)
  const places = useMemo(() => [...new Set(cal.events.map((e) => e.location).filter((l): l is string => !!l && l.length < 60))], [cal.events])
  const top = usePeople('').data
  const first = parse(text, { mode: 'event', now: cal.now, tz: prefs.zone, calendars: cals.map((c) => ({ id: c.url ?? c.id, name: c.name })), places, keep, defaultDuration: prefs.defaultDuration, morningHour: prefs.morningHour })
  const guessQuery = first.guests.find((g) => !g.email)?.text ?? ''
  const found = usePeople(guessQuery).data
  const people = useMemo(() => {
    const m = new Map<string, { name: string; email: string }>()
    for (const p of [...(found ?? []), ...(top ?? [])]) if (p.email) m.set(p.email, { name: p.name || p.email, email: p.email })
    return [...m.values()]
  }, [found, top])
  const r = parse(text, { mode: 'event', now: cal.now, tz: prefs.zone, calendars: cals.map((c) => ({ id: c.url ?? c.id, name: c.name })), people, places, keep, defaultDuration: prefs.defaultDuration, morningHour: prefs.morningHour })

  useEffect(() => {
    draftText = text
  }, [text])
  // Floating focuses the line once it is placed and visible (data-autofocus), after a closing
  // menu has put the focus back on its button. A focus here, on mount, would not stick:
  // the card is still hidden while it measures itself.

  const times = resolveTimes(r, q, prefs.zone, prefs.defaultDuration, cal.now)
  const calendarUrl = r.list_or_calendar?.id ?? defaults?.calendar ?? cals[0]?.url ?? cals[0]?.id
  const calendar = cal.collectionByUrl.get(calendarUrl ?? '')
  const guestEmail = (g: QuickGuest) => g.email ?? confirmed[g.text] ?? null
  const guests = r.guests.filter((g) => guestEmail(g)).map((g) => ({ email: guestEmail(g)!, name: g.email ? undefined : g.matches.find((m) => m.email === confirmed[g.text])?.name }))
  const unconfirmed = r.guests.filter((g) => !guestEmail(g))
  const clash = times.allDay ? null : conflictLine(cal.busy, times.start as Date, times.end as Date, undefined, prefs)
  const title = r.title || ''
  const reminders = r.reminders.filter((x) => x.minutes != null).map((x) => x.minutes!)

  const confirmGuest = (g: QuickGuest, email?: string) => {
    const pick = email ?? g.matches[0]?.email
    if (pick) setConfirmed({ ...confirmed, [g.text]: pick })
  }

  const unparse = (t: QuickToken) => {
    // "Keep as text": the token's words go back into the title, for this string from now on.
    keepAsText.push(t.text)
    setKeep([...keepAsText])
  }

  const body = (): EventWrite => ({
    summary: title || tr('common.noTitle'),
    ...timesFor(times.start, times.end, times.allDay),
    tz: times.allDay ? undefined : prefs.homeZone,
    calendar: calendarUrl,
    location: r.location ?? undefined,
    recurrence: r.rrule ?? undefined,
    attendees: guests.length ? guests : undefined,
    reminders: reminders.length ? reminders : defaultReminders(calendar, times.allDay),
    url: r.video ? videoLink(prefs.videoTemplate) : undefined,
  })

  const save = async () => {
    if (saving) return
    let b = body()
    if (guests.length) {
      const send = await askSendUpdates(guests.length, 'invite')
      if (!send) return
      b = { ...b, send_updates: send.send }
    }
    setSaving(true)
    // ACT event.create @web.calendar
    const out = await cal.actions.create(b)
    setSaving(false)
    if (out) {
      draftText = ''
      close()
    }
  }

  const more = () => {
    draftText = ''
    useCalUI.getState().set({
      quick: null,
      editor: {
        mode: 'new',
        seed: {
          summary: title, start: times.start, end: times.end, allDay: times.allDay, location: r.location ?? '', rrule: r.rrule,
          calendar: calendarUrl, guests, reminders: reminders.length ? reminders : undefined, video: r.video,
        },
      },
    })
  }

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey || e.shiftKey)) {
      e.preventDefault()
      more()
    } else if (e.key === 'Enter') {
      e.preventDefault()
      void save()
    } else if (e.key === 'Tab' && !e.shiftKey && unconfirmed.length && unconfirmed[0].matches.length) {
      e.preventDefault()
      confirmGuest(unconfirmed[0])
    } else if (e.key === 'Backspace') {
      const pos = e.currentTarget.selectionStart ?? 0
      const t = r.tokens.find((x) => x.end === pos && e.currentTarget.selectionEnd === pos)
      if (t && t.kind !== 'guest') {
        e.preventDefault()
        unparse(t)
      }
    }
  }

  const whenText = times.allDay
    ? formatRange(times.start as string, times.end as string, true, prefs)
    : `${formatRange(times.start as Date, times.end as Date, false, prefs)} (${formatDuration(Math.round(((times.end as Date).getTime() - (times.start as Date).getTime()) / 60_000))})`

  const content = (
    <div className="cal-qc" data-testid="cal-quick">
      <div className="cal-qc-head">
        <span className="cal-qc-kicker">{tr('common.newEvent')}</span>
        <button type="button" className="icon-btn xs" aria-label={tr('common.close')} onClick={close}><X size={16} /></button>
      </div>
      <div className="cal-qa">
        <div className="cal-qa-mirror" ref={mirror} aria-hidden>
          {renderTokens(text, r.tokens)}
        </div>
        <input
          ref={input}
          data-autofocus
          className="cal-qa-input"
          value={text}
          placeholder={tr('calendar.quickCreate.lunchWithDanaTomorrow12')}
          aria-label={tr('calendar.quickCreate.eventInOneLine')}
          spellCheck={false}
          onChange={(e) => setText(e.target.value)}
          onScroll={(e) => { if (mirror.current) mirror.current.scrollLeft = e.currentTarget.scrollLeft }}
          onKeyDown={onKey}
        />
      </div>
      <div className="cal-qc-chips" aria-label={tr('calendar.quickCreate.whatWasUnderstood')}>
        <Chip icon={<Clock />} label={whenText} kind="time" token={r.tokens.find((t) => t.kind === 'time' || t.kind === 'date' || t.kind === 'span')} onRemove={unparse} />
        {r.rrule && <Chip icon={<Repeat />} label={summarizeRRule(r.rrule, typeof times.start === 'string' ? times.start : toISODate(times.start, prefs.zone))} kind="repeat" token={r.tokens.find((t) => t.kind === 'repeat')} onRemove={unparse} />}
        {calendar && (
          <Chip
            icon={<span className="cal-dot" style={{ background: displayColour(cal.colourOf(calendar), cal.dark) }} />}
            label={calendar.name} kind="collection" token={r.tokens.find((t) => t.kind === 'collection')} onRemove={unparse}
          />
        )}
        {r.location && <Chip icon={<MapPin />} label={r.location} kind="location" token={r.tokens.find((t) => t.kind === 'location')} onRemove={unparse} />}
        {r.guests.map((g) => {
          const email = guestEmail(g)
          if (email) return <Chip key={g.text} icon={<Users />} label={g.matches.find((m) => m.email === email)?.name ?? email} kind="guest" title={email} onRemove={() => { const c = { ...confirmed }; delete c[g.text]; setConfirmed(c) }} />
          if (!g.matches.length) return <span key={g.text} className="cal-chip pending none" title={tr('calendar.quickCreate.noOneInPeopleMatches')}>{g.text}?</span>
          return g.matches.slice(0, 2).map((m) => (
            <button key={m.email} type="button" className="cal-chip pending" onClick={() => confirmGuest(g, m.email)} title={tr('calendar.quickCreate.inviteTab', { email: m.email })}>
              <UserPlus aria-hidden /> {m.name || m.email}
            </button>
          ))
        })}
        {reminders.map((m, i) => <Chip key={i} icon={<AlarmClock />} label={offsetLabel(m)} kind="reminder" token={r.tokens.filter((t) => t.kind === 'reminder')[i]} onRemove={unparse} />)}
        {r.video && <Chip icon={<Video />} label={tr('calendar.quickCreate.videoLink')} kind="video" token={r.tokens.find((t) => t.kind === 'video')} onRemove={unparse} />}
      </div>
      {clash && <p className="cal-clash" role="status">{clash}</p>}
      <div className="cal-qc-foot">
        <button type="button" className="btn text" onClick={more} title="Ctrl+Enter">{tr('calendar.quickCreate.moreOptions')}</button>
        <button type="button" className="btn filled" onClick={() => void save()} disabled={saving || cal.readOnly || !calendarUrl} data-testid="cal-quick-save">
          {guests.length ? tr('calendar.quickCreate.saveInvite') : tr('common.save')}
        </button>
      </div>
    </div>
  )
  return (
    <Floating anchor={q.anchor} onClose={close} label={tr('common.newEvent')} className="cal-qc-pop">
      {content}
    </Floating>
  )
}

function Chip({ icon, label, kind, token, onRemove, title }: {
  icon: React.ReactNode; label: string; kind: string; token?: QuickToken; title?: string
  onRemove?: (t: QuickToken) => void
}) {
  return (
    <span className={`cal-chip k-${kind}${token ? ' parsed' : ''}`} title={title}>
      <span className="cal-chip-icon" aria-hidden>{icon}</span>
      <span className="cal-chip-label">{label}</span>
      {token && onRemove && (
        <button type="button" className="cal-chip-x" aria-label={tr('calendar.quickCreate.keepAsText', { tokenText: token.text })} onClick={() => onRemove(token)}>
          <X aria-hidden />
        </button>
      )}
      {!token && kind === 'guest' && onRemove && (
        <button type="button" className="cal-chip-x" aria-label={tr('calendar.quickCreate.remove', { label })} onClick={() => onRemove({ kind: 'guest', start: 0, end: 0, text: label })}>
          <X aria-hidden />
        </button>
      )}
    </span>
  )
}

/** The input's text with each token wrapped, for the highlight layer under the input. */
function renderTokens(text: string, tokens: QuickToken[]) {
  const out: React.ReactNode[] = []
  let at = 0
  for (const t of [...tokens].sort((a, b) => a.start - b.start)) {
    if (t.start < at) continue
    if (t.start > at) out.push(text.slice(at, t.start))
    out.push(<mark key={t.start} className={`cal-tok k-${t.kind}`}>{text.slice(t.start, t.end)}</mark>)
    at = t.end
  }
  out.push(text.slice(at) + ' ')
  return out
}
