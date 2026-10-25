// Find a time: duration, range, working hours (editable here), "avoid" limits and a buffer; up to
// 8 ranked slots and a timeline per day with my busy blocks. The server answers through
// POST /calendar/find-time; a server without the route gets the same ranking on the client
// (findtime.ts) over the events of the range. People in my groups who share their free/busy
// (household.ts) can be chosen: their busy times count, and each gets a row in the timeline.
// Guests who are such people are chosen at once; other guests' availability is not known.

import { CalendarSearch } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { Spinner } from '../../components/bits'
import { addDaysISO, formatDate, formatHour, formatTime, minutesOfDay, startOfDay, utcIsoformat, type ISODate } from '../../lib/dates'
import { useCal } from './context'
import { isMissingRoute, useNormalized, useRange } from './data'
import { findSlots, rangeDays, type Slot } from './findtime'
import { chosenBusy, guestsInHousehold, useHousehold, type HouseholdPerson } from './household'
import './household.css'
import { formatDuration } from './geometry'
import { useCalUI } from './store'
import { t } from '../../i18n'

const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
const fromHHMM = (s: string) => {
  const [h, m] = s.split(':').map(Number)
  return h * 60 + (m || 0)
}

export function FindTimePanel({ duration: initial, guests = [], onPick, exclude }: {
  duration: number; guests?: { email: string; name?: string }[]; onPick: (s: Slot) => void; exclude?: string
}) {
  const cal = useCal()
  const { prefs } = cal
  const [duration, setDuration] = useState(initial)
  const [range, setRange] = useState<'this' | 'next' | 'next5' | 'pick'>('next5')
  const [pickFrom, setPickFrom] = useState(cal.today)
  const [work, setWork] = useState({ start: prefs.workStart, end: prefs.workEnd })
  const [avoid, setAvoid] = useState(false)
  const [buffer, setBuffer] = useState(0)
  const days = useMemo(() => range === 'pick'
    ? Array.from({ length: 7 }, (_, i) => addDaysISO(pickFrom, i))
    : rangeDays(range, cal.today, prefs.weekStart, prefs.workDays), [range, pickFrom, cal.today, prefs.weekStart, prefs.workDays])
  const from = startOfDay(days[0], prefs.zone)
  const to = startOfDay(addDaysISO(days[days.length - 1], 1), prefs.zone)
  const evq = useRange(from, to)
  const household = useHousehold(from, to)
  const people = useMemo(() => household.data?.people ?? [], [household.data])
  const [picked, setPicked] = useState<number[] | null>(null)
  // Until the person picks, the guests who share their free/busy are chosen.
  const chosen = useMemo(() => picked ?? guestsInHousehold(people, guests), [picked, people, guests])
  const others = useMemo(() => chosenBusy(people, chosen), [people, chosen])
  const events = useNormalized(evq.data?.events, prefs.zone, cal.collections, new Set())
  const busy = useMemo(() => events.filter((e) => !e.allDay && !e.transparent && e.key !== exclude && (e.my_status ?? '').toUpperCase() !== 'DECLINED' && (e.status ?? '').toUpperCase() !== 'CANCELLED'), [events, exclude])
  const avoidBefore = avoid ? Math.max(work.start, 10 * 60) : null
  const avoidAfter = avoid ? Math.min(work.end, 16 * 60) : null

  const server = useQuery({
    queryKey: ['calendar', 'find-time', utcIsoformat(from), utcIsoformat(to), duration, work, buffer, avoid, chosen.join()],
    queryFn: async () => {
      try {
        // ACT event.find_time @web.editor @web.calendar
        const r = await api.post<{ slots: { start: string; end: string; score: number }[] }>('/calendar/find-time', {
          duration, from: utcIsoformat(from), to: utcIsoformat(to), tz: prefs.zone, now: utcIsoformat(new Date()), buffer_minutes: buffer, granularity: 15, limit: 8,
          working_hours: { start: hhmm(avoidBefore ?? work.start), end: hhmm(avoidAfter ?? work.end), days: prefs.workDays.map((d) => d || 7) },
          ...(chosen.length ? { people: chosen } : {}),
        })
        return r.slots.map((s) => ({ start: new Date(s.start), end: new Date(s.end), score: s.score }))
      } catch (e) {
        if (isMissingRoute(e)) return null
        throw e
      }
    },
    retry: false,
    staleTime: 30_000,
  })
  const local = useMemo(() => findSlots({
    busy: [...busy.map((e) => ({ start: e.startAt, end: e.endAt })), ...others], days, zone: prefs.zone, workStart: work.start, workEnd: work.end,
    workDays: range === 'pick' ? [0, 1, 2, 3, 4, 5, 6] : prefs.workDays, duration, buffer, now: cal.now, avoidBefore, avoidAfter,
  }), [busy, others, days, prefs.zone, work, prefs.workDays, range, duration, buffer, cal.now, avoidBefore, avoidAfter])
  const slots = server.data ?? local
  const loading = evq.isLoading || server.isLoading
  const span = { start: Math.min(work.start, 8 * 60), end: Math.max(work.end, 18 * 60) }

  return (
    <div className="cal-ft" data-testid="cal-findtime">
      <div className="cal-ft-inputs">
        <label className="cal-field inline"><span>{t('calendar.findTime.duration')}</span>
          <select className="input" value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
            {[15, 30, 45, 60, 90, 120, 180].map((m) => <option key={m} value={m}>{formatDuration(m)}</option>)}
          </select>
        </label>
        <label className="cal-field inline"><span>{t('common.when')}</span>
          <select className="input" value={range} onChange={(e) => setRange(e.target.value as typeof range)}>
            <option value="this">{t('calendar.findTime.thisWeek')}</option>
            <option value="next">{t('common.nextWeek')}</option>
            <option value="next5">{t('calendar.findTime.next5WorkingDays')}</option>
            <option value="pick">{t('calendar.findTime.pick2')}</option>
          </select>
        </label>
        {range === 'pick' && <input type="date" className="input" value={pickFrom} onChange={(e) => e.target.value && setPickFrom(e.target.value)} aria-label={t('common.from')} />}
        <label className="cal-field inline"><span>{t('calendar.findTime.workingHours')}</span>
          <span className="cal-ft-hours">
            <input type="time" className="input" step={900} value={hhmm(work.start)} onChange={(e) => e.target.value && setWork({ ...work, start: fromHHMM(e.target.value) })} aria-label={t('calendar.findTime.workingHoursStart')} />
            –
            <input type="time" className="input" step={900} value={hhmm(work.end)} onChange={(e) => e.target.value && setWork({ ...work, end: fromHHMM(e.target.value) })} aria-label={t('calendar.findTime.workingHoursEnd')} />
          </span>
        </label>
        <label className="cal-check"><input type="checkbox" className="checkbox" checked={avoid} onChange={(e) => setAvoid(e.target.checked)} />{' '}{t('calendar.findTime.avoidBefore1000After')}</label>
        <label className="cal-field inline"><span>{t('calendar.findTime.buffer')}</span>
          <select className="input" value={buffer} onChange={(e) => setBuffer(Number(e.target.value))}>
            {[0, 5, 10, 15].map((m) => <option key={m} value={m}>{m ? t('calendar.findTime.min', { m }) : t('common.none')}</option>)}
          </select>
        </label>
      </div>
      {people.length > 0 && (
        <fieldset className="cal-ft-people" data-testid="cal-ft-people">
          <legend>{t('calendar.findTime.include')}</legend>
          {people.map((p) => (
            <label key={p.id} className="cal-check">
              <input type="checkbox" className="checkbox" checked={chosen.includes(p.id)}
                onChange={(e) => setPicked(e.target.checked ? [...chosen, p.id] : chosen.filter((x) => x !== p.id))} />
              {p.name}
            </label>
          ))}
        </fieldset>
      )}
      <div className="cal-ft-slots" role="listbox" aria-label={t('calendar.findTime.freeTimes')}>
        {loading && <Spinner small />}
        {!loading && !slots.length && <p className="cal-note">{t('calendar.findTime.noFreeTimeOfIn', { duration: formatDuration(duration) })}</p>}
        {slots.map((s) => (
          <button key={s.start.toISOString()} type="button" role="option" aria-selected={false} className="cal-ft-slot" onClick={() => onPick(s)}>
            <b>{formatDate(s.start, 'day', prefs.zone)}</b>
            <span>{formatTime(s.start, prefs)} – {formatTime(s.end, prefs)}</span>
          </button>
        ))}
      </div>
      <div className="cal-ft-timeline" aria-label={t('calendar.findTime.busyTimes')}>
        <div className="cal-ft-scale" aria-hidden>
          <span />
          <div>
            {Array.from({ length: Math.ceil((span.end - span.start) / 120) + 1 }, (_, i) => span.start + i * 120).filter((m) => m <= span.end).map((m) => (
              <span key={m} style={{ insetInlineStart: `${((m - span.start) / (span.end - span.start)) * 100}%` }}>{formatHour(Math.floor(m / 60), prefs.hour12)}</span>
            ))}
          </div>
        </div>
        {days.map((d) => (
          <DayTrack key={d} day={d} span={span} work={work} busy={busy.filter((e) => e.firstDay <= d && e.lastDay >= d)} slots={slots} onPick={onPick}
            others={people.filter((p) => chosen.includes(p.id))} />
        ))}
        {guests.filter((g) => !people.some((p) => chosen.includes(p.id) && p.email.toLowerCase() === g.email.toLowerCase())).map((g) => (
          <div key={g.email} className="cal-ft-row guest">
            <span className="cal-ft-label">{g.name || g.email}</span>
            <span className="cal-ft-track unknown">{t('calendar.findTime.availabilityNotShared')}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function DayTrack({ day, span, work, busy, slots, onPick, others = [] }: {
  day: ISODate; span: { start: number; end: number }; work: { start: number; end: number }
  busy: ReturnType<typeof useNormalized>; slots: Slot[]; onPick: (s: Slot) => void; others?: HouseholdPerson[]
}) {
  const cal = useCal()
  const zone = cal.prefs.zone
  const pct = (m: number) => `${((Math.min(span.end, Math.max(span.start, m)) - span.start) / (span.end - span.start)) * 100}%`
  const w = (a: number, b: number) => `${((Math.min(span.end, b) - Math.max(span.start, a)) / (span.end - span.start)) * 100}%`
  return (
    <div className="cal-ft-row">
      <span className="cal-ft-label">{formatDate(day, 'day')}</span>
      <div className="cal-ft-track">
        <div className="cal-ft-work" style={{ insetInlineStart: pct(work.start), width: w(work.start, work.end) }} />
        {busy.map((e) => {
          const a = e.firstDay < day ? 0 : minutesOfDay(e.startAt, zone)
          const b = e.lastDay > day ? 1440 : minutesOfDay(e.endAt, zone) || 1440
          if (b <= span.start || a >= span.end) return null
          return <div key={e.key} className={`cal-ft-busy${(e.my_status ?? e.status ?? '').toUpperCase() === 'TENTATIVE' ? ' hatched' : ''}`} style={{ insetInlineStart: pct(a), width: w(a, b) }} title={`${e.summary} ${formatTime(e.startAt, cal.prefs)}`} />
        })}
        {/* The chosen people's busy times: a band along the bottom of the day (no titles). */}
        {others.flatMap((p) => [...p.busy, ...p.tentative].map((b) => {
          const s0 = new Date(b.start)
          const e0 = new Date(b.end)
          const d0 = startOfDay(day, zone)
          const d1 = startOfDay(addDaysISO(day, 1), zone)
          if (e0 <= d0 || s0 >= d1) return null
          const a = s0 <= d0 ? 0 : minutesOfDay(s0, zone)
          const z = e0 >= d1 ? 1440 : minutesOfDay(e0, zone)
          if (z <= span.start || a >= span.end) return null
          return <div key={`${p.id}-${b.start}`} className="cal-ft-other" style={{ insetInlineStart: pct(a), width: w(a, z) }} title={t('calendar.findTime.isBusy', { itemName: p.name, time: formatTime(s0, cal.prefs), time2: formatTime(e0, cal.prefs) })} />
        }))}
        {slots.filter((s) => formatDate(s.start, 'day', zone) === formatDate(day, 'day')).map((s) => {
          const a = minutesOfDay(s.start, zone)
          const b = a + Math.round((s.end.getTime() - s.start.getTime()) / 60_000)
          return <button key={s.start.toISOString()} type="button" className="cal-ft-free" style={{ insetInlineStart: pct(a), width: w(a, b) }} onClick={() => onPick(s)} aria-label={t('calendar.findTime.pick', { time: formatTime(s.start, cal.prefs) })} />
        })}
      </div>
    </div>
  )
}

/** `f`: Find a time as a dialog; a chosen slot opens quick create there. */
export function FindTimeDialog() {
  const open = useCalUI((s) => s.findTime)
  const cal = useCal()
  if (!open) return null
  const close = () => useCalUI.getState().set({ findTime: false })
  return (
    <Dialog open wide onClose={close} title={<span className="cal-dialog-title"><CalendarSearch size={20} aria-hidden />{' '}{t('common.findATime')}</span>}>
      <FindTimePanel
        duration={cal.prefs.defaultDuration}
        onPick={(s) => useCalUI.getState().set({ findTime: false, quick: { start: s.start, end: s.end, allDay: false, anchor: null } })}
      />
    </Dialog>
  )
}
