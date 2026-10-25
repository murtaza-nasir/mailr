// Month: a grid of whole weeks (5 rows when the month fits, else 6). Multi-day
// and all-day events are bars across the cells; timed events are chips (dot, time, title). A full
// cell ends with "+n more", which lists the day. A day number opens Day; a click on empty space
// creates an all-day event (a drag across days gives several); a chip dragged to another day keeps
// its time (month chips).

import { Repeat, Users } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Popover } from '../../components/Popover'
import { addDaysISO, diffDaysISO, formatDate, formatInstant, formatTime, parseISODate, weekdayOf, type ISODate } from '../../lib/dates'
import { displayColour, textOn, tint } from './colour'
import { useCal } from './context'
import { needsReply, isDeclined, isTentative, isCancelled, titleOf } from './eventInfo'
import { isoWeek, layoutBars, monthWeeks, moveDays } from './geometry'
import { dismissOpenPopover, useCalUI } from './store'
import { TaskChip } from './TaskChip'
import type { ApiTask, CalEvent } from './types'
import { t as tr } from '../../i18n'
import { xFromStart } from '../../lib/rtl'
import { ItemBoundary } from '../../components/bits'

const LANE = 22
const HEAD = 28

const isBar = (e: CalEvent) => e.allDay || e.firstDay !== e.lastDay

interface MDrag {
  kind: 'chip' | 'create'
  ev?: CalEvent
  from: ISODate
  over: ISODate
  x: number
  y: number
  moved: boolean
  dup: boolean
}

export function MonthView() {
  const cal = useCal()
  const { prefs, date, today, events, tasks } = cal
  const weeks = useMemo(() => monthWeeks(date, prefs.weekStart), [date, prefs.weekStart])
  const month = parseISODate(date).month
  const ref = useRef<HTMLDivElement>(null)
  const [rowH, setRowH] = useState(120)
  const [more, setMore] = useState<{ day: ISODate; anchor: HTMLElement } | null>(null)
  const [drag, setDrag] = useState<MDrag | null>(null)
  const dragRef = useRef<MDrag | null>(null)
  dragRef.current = drag

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setRowH(el.clientHeight / weeks.length))
    ro.observe(el)
    return () => ro.disconnect()
  }, [weeks.length])

  const fit = Math.max(1, Math.floor((rowH - HEAD - 4) / LANE))
  const byKey = useMemo(() => new Map(events.map((e) => [e.key, e])), [events])
  const tasksByDay = useMemo(() => {
    const m = new Map<ISODate, ApiTask[]>()
    for (const t of tasks) {
      if (!t.due) continue
      const d = t.due.length === 10 ? t.due : formatDayOf(t.due, prefs.zone)
      m.set(d, [...(m.get(d) ?? []), t])
    }
    return m
  }, [tasks, prefs.zone])

  const dayItems = (day: ISODate) => events.filter((e) => e.firstDay <= day && e.lastDay >= day)

  // ---------- dragging ----------

  useEffect(() => {
    if (!drag) return
    const dayAt = (x: number, y: number) => (document.elementFromPoint(x, y)?.closest('[data-mday]') as HTMLElement | null)?.dataset.mday
    const onMove = (ev: PointerEvent) => {
      const d = dragRef.current!
      const moved = d.moved || Math.abs(ev.clientX - d.x) > 4 || Math.abs(ev.clientY - d.y) > 4
      if (!moved) return
      if (d.ev && (d.ev.readOnly || cal.readOnly)) return
      const over = dayAt(ev.clientX, ev.clientY) ?? d.over
      setDrag({ ...d, over, moved: true, dup: ev.altKey || ev.ctrlKey || ev.metaKey })
    }
    const onUp = (ev: PointerEvent) => {
      const d = dragRef.current!
      setDrag(null)
      if (!d.moved) {
        if (d.ev) cal.openEvent(d.ev, document.querySelector(`[data-mkey="${CSS.escape(d.ev.key)}"]`))
        else {
          // ACT event.create @web.calendar: a click on empty space in a day creates an all-day event.
          useCalUI.getState().set({ quick: { start: d.from, end: addDaysISO(d.from, 1), allDay: true, anchor: new DOMRect(ev.clientX, ev.clientY, 1, 1) } })
        }
        return
      }
      if (d.kind === 'create') {
        const [a, b] = d.from <= d.over ? [d.from, d.over] : [d.over, d.from]
        useCalUI.getState().set({ quick: { start: a, end: addDaysISO(b, 1), allDay: true, anchor: new DOMRect(ev.clientX, ev.clientY, 1, 1) } })
        return
      }
      const e = d.ev!
      const shift = diffDaysISO(d.from, d.over)
      if (!shift) return
      if (e.allDay) {
        const first = addDaysISO(e.firstDay, shift)
        const end = addDaysISO(addDaysISO(e.lastDay, 1), shift)
        void (d.dup ? cal.actions.duplicateAt(e, first, end, true) : cal.actions.reschedule(e, first, end, true))
      } else {
        // The time is kept; the day changes.
        const { start, end } = moveDays(e.startAt, e.endAt, e.firstDay, shift, prefs.zone)
        void (d.dup ? cal.actions.duplicateAt(e, start, end, false) : cal.actions.reschedule(e, start, end, false)) // ACT event.reschedule @web.calendar
      }
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        ev.stopPropagation()
        setDrag(null)
      }
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('keydown', onKey, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [!!drag])

  const dropRange = drag?.moved ? dropDays(drag) : null

  return (
    <div className={`cal-month${cal.loading ? ' is-loading' : ''}`} data-testid="cal-month" style={{ '--weeks': weeks.length } as CSSProperties}>
      {/* No grid roles (the weeks are not a navigable grid); each day is a labelled group, and
          the weekday names are visual only, since every day's label says its weekday. */}
      <div className={`cal-month-head${prefs.weekNumbers ? ' wn' : ''}`} aria-hidden>
        {prefs.weekNumbers && <span className="cal-weekno-head" />}
        {weeks[0].map((d) => (
          <span key={d} className="cal-month-wd">{formatDate(d, cal.narrow ? 'weekday' : 'weekday')}</span>
        ))}
      </div>
      <div className="cal-month-grid" ref={ref} role="group" aria-label={formatDate(date, 'monthYear')}>
        {weeks.map((week) => {
          const bars = layoutBars(events.filter(isBar).map((e) => ({ key: e.key, first: e.firstDay, last: e.lastDay, allDay: e.allDay, start: e.startAt.getTime() })), week)
          const totalOf = (i: number) => bars.filter((b) => b.from <= i && b.to >= i).length + dayItems(week[i]).filter((e) => !isBar(e)).length + (tasksByDay.get(week[i])?.length ?? 0)
          // A week with a full day keeps its last row for "+n more".
          const barRoom = week.some((_, i) => totalOf(i) > fit) ? fit - 1 : fit
          return (
            <div key={week[0]} className={`cal-week${prefs.weekNumbers ? ' wn' : ''}`}>
              {prefs.weekNumbers && (
                <button type="button" className="cal-weekno-cell" onClick={() => cal.go('week', week[0])} aria-label={tr('calendar.monthView.week', { isoWeek: isoWeek(week[0]) })}>
                  {isoWeek(week[0])}
                </button>
              )}
              <div className="cal-week-cells">
                {week.map((d, i) => {
                  const wd = weekdayOf(d)
                  const inMonth = parseISODate(d).month === month
                  const laneTop = Math.max(0, ...bars.filter((b) => b.from <= i && b.to >= i).map((b) => b.lane + 1))
                  const chips = dayItems(d).filter((e) => !isBar(e))
                  const tasks = tasksByDay.get(d) ?? []
                  const total = totalOf(i)
                  const room = total > fit ? fit - 1 : barRoom
                  const shownChips = chips.slice(0, Math.max(0, room - laneTop))
                  const shownTasks = tasks.slice(0, Math.max(0, room - laneTop - shownChips.length))
                  const hidden = total - bars.filter((b) => b.from <= i && b.to >= i && b.lane < barRoom).length - shownChips.length - shownTasks.length
                  const dropping = dropRange && d >= dropRange[0] && d <= dropRange[1]
                  return (
                    <div
                      key={d}
                      data-mday={d}
                      role="group"
                      aria-label={formatDate(d, 'long')}
                      className={`cal-mcell${inMonth ? '' : ' out'}${wd === 0 || wd === 6 ? ' weekend' : ''}${d === today ? ' today' : ''}${dropping ? ' drop' : ''}`}
                      onPointerDown={(ev) => {
                        if (ev.button !== 0 || (ev.target as HTMLElement).closest('.cal-mchip, .cal-block, .cal-mday-num, .cal-mmore, .cal-task')) return
                        ev.preventDefault()
                        if (dismissOpenPopover()) return
                        setDrag({ kind: 'create', from: d, over: d, x: ev.clientX, y: ev.clientY, moved: false, dup: false })
                      }}
                    >
                      <button type="button" className="cal-mday-num" onClick={() => cal.go('day', d)} aria-label={tr('calendar.monthView.open', { date: formatDate(d, 'long') })}>
                        {Number(d.slice(8)) === 1 ? `${formatDate(d, 'short')}` : Number(d.slice(8))}
                      </button>
                      <div className="cal-mchips" style={{ top: HEAD + laneTop * LANE }}>
                        {shownChips.map((e) => (
                          <MonthChip
                            key={e.key}
                            e={e}
                            dragging={drag?.moved && drag.ev?.key === e.key && !drag.dup}
                            onPointerDown={(ev) => {
                              if (ev.button !== 0) return
                              ev.preventDefault()
                              setDrag({ kind: 'chip', ev: e, from: d, over: d, x: ev.clientX, y: ev.clientY, moved: false, dup: false })
                            }}
                          />
                        ))}
                        {shownTasks.map((t) => <TaskChip key={t.uid} t={t} style={{ height: LANE - 2 }} />)}
                      </div>
                      {hidden > 0 && (
                        <button type="button" className="cal-mmore" style={{ top: HEAD + (room) * LANE }} onClick={(ev) => setMore({ day: d, anchor: ev.currentTarget })}>
                          {tr('calendar.monthView.more', { hidden })}
                        </button>
                      )}
                    </div>
                  )
                })}
                {bars.filter((b) => b.lane < barRoom).map((b) => {
                  const e = byKey.get(b.key)!
                  const colour = displayColour(e.colour, cal.dark)
                  const out = needsReply(e) || isDeclined(e)
                  return (
                    <div
                      key={b.key}
                      data-mkey={e.key}
                      data-key={e.key}
                      data-testid="cal-event"
                      data-shortcuts=""
                      role="button"
                      tabIndex={cal.tabStop === e.key ? 0 : -1}
                      onFocus={() => useCalUI.getState().set({ focus: e.key, slot: null })}
                      aria-label={`${titleOf(e)}, ${e.allDay ? tr('calendar.monthView.allDay') : formatTime(e.startAt, prefs)}`}
                      className={`cal-block bar month${out ? ' outlined' : ''}${isTentative(e) ? ' hatched' : ''}${isCancelled(e) ? ' cancelled' : ''}${b.clippedStart ? ' clip-start' : ''}${b.clippedEnd ? ' clip-end' : ''}${drag?.moved && drag.ev?.key === e.key && !drag.dup ? ' dragging' : ''}`}
                      style={{
                        top: HEAD + b.lane * LANE, height: LANE - 2, insetInlineStart: `calc(${(b.from / 7) * 100}% + 3px)`, width: `calc(${((b.to - b.from + 1) / 7) * 100}% - 6px)`,
                        '--ev': colour, '--ev-fg': out ? 'var(--text)' : textOn(colour), '--ev-tint': tint(colour, cal.dark ? 0.22 : 0.14),
                      } as CSSProperties}
                      onPointerDown={(ev) => {
                        if (ev.button !== 0) return
                        ev.preventDefault()
                        // The day under the pointer: bars sit above the cells, so it comes from x.
                        const r = ev.currentTarget.parentElement!.getBoundingClientRect()
                        const from = week[Math.min(6, Math.max(0, Math.floor((xFromStart(ev.clientX, r) / r.width) * 7)))]
                        setDrag({ kind: 'chip', ev: e, from, over: from, x: ev.clientX, y: ev.clientY, moved: false, dup: false })
                      }}
                    >
                      <div className="cal-block-in">
                        <span className="cal-block-title" dir="auto">{!e.allDay && <span className="cal-block-time">{formatTime(e.startAt, prefs, { compact: true })} </span>}{titleOf(e)}</span>
                        <span className="cal-block-icons" aria-hidden>{e.recurring && <Repeat />}{(e.attendee_list?.length ?? 0) > 0 && <Users />}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
      {more && (
        <Popover anchor={more.anchor} open onClose={() => setMore(null)} role="dialog" label={formatDate(more.day, 'long')} className="cal-pop cal-day-pop">
          <div className="cal-day-pop-head">
            <span className="cal-day-pop-wd">{formatDate(more.day, 'weekday')}</span>
            <button type="button" className="cal-day-pop-num" onClick={() => { setMore(null); cal.go('day', more.day) }}>{Number(more.day.slice(8))}</button>
          </div>
          <div className="cal-more-list">
            {dayItems(more.day).map((e) => (
              <MonthChip key={e.key} e={e} wide onClick={(ev) => { setMore(null); cal.openEvent(e, ev.currentTarget) }} />
            ))}
            {(tasksByDay.get(more.day) ?? []).map((t) => <TaskChip key={t.uid} t={t} />)}
          </div>
        </Popover>
      )}
    </div>
  )
}

function dropDays(d: MDrag): [ISODate, ISODate] {
  if (d.kind === 'create') return d.from <= d.over ? [d.from, d.over] : [d.over, d.from]
  const e = d.ev!
  const shift = diffDaysISO(d.from, d.over)
  return [addDaysISO(e.firstDay, shift), addDaysISO(e.lastDay, shift)]
}

function formatDayOf(iso: string, zone: string): ISODate {
  return formatInstant(new Date(iso), 'en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' }, zone)
}

type MonthChipProps = {
  e: CalEvent; wide?: boolean; dragging?: boolean
  onPointerDown?: (ev: React.PointerEvent<HTMLDivElement>) => void
  onClick?: (ev: React.MouseEvent<HTMLDivElement>) => void
}

/** One event in a month cell; a failure shows "Couldn't show this event" in its place. */
function MonthChip(props: MonthChipProps) {
  return (
    <ItemBoundary what="event" compact>
      <MonthChipBody {...props} />
    </ItemBoundary>
  )
}

function MonthChipBody({ e, onPointerDown, onClick, wide, dragging }: MonthChipProps) {
  const cal = useCal()
  const colour = displayColour(e.colour, cal.dark)
  const focus = useCalUI((s) => s.focus)
  const declined = isDeclined(e)
  return (
    <div
      className={`cal-mchip${wide ? ' wide' : ''}${declined ? ' declined' : ''}${needsReply(e) ? ' needs' : ''}${isCancelled(e) ? ' cancelled' : ''}${dragging ? ' dragging' : ''}${focus === e.key ? ' focused' : ''}${cal.prefs.dimPast && e.endAt < cal.now ? ' past' : ''}`}
      data-mkey={e.key}
      data-key={e.key}
      data-testid="cal-event"
      data-shortcuts=""
      role="button"
      tabIndex={cal.tabStop === e.key ? 0 : -1}
      aria-label={`${titleOf(e)}, ${e.allDay ? tr('calendar.monthView.allDay') : formatTime(e.startAt, cal.prefs)}`}
      onPointerDown={onPointerDown}
      onClick={onClick}
      onKeyDown={(ev) => {
        if (ev.key === 'Enter') {
          ev.preventDefault()
          cal.openEvent(e, ev.currentTarget)
        }
      }}
      onFocus={() => useCalUI.getState().set({ focus: e.key })}
    >
      {e.allDay || e.firstDay !== e.lastDay ? (
        <span className="cal-mchip-bar" dir="auto" style={{ background: colour, color: textOn(colour) }}>{titleOf(e)}</span>
      ) : (
        <>
          <span className="cal-dot" style={{ background: needsReply(e) ? 'transparent' : colour, borderColor: colour }} />
          <span className="cal-mchip-time">{formatTime(e.startAt, cal.prefs, { compact: true })}</span>
          <span className="cal-mchip-title" dir="auto">{titleOf(e)}</span>
        </>
      )}
    </div>
  )
}
