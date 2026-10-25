// Day, N days, Work week and Week: the all-day strip,
// the time gutter (with an optional second zone), hour rows, working-hours shading, the now line,
// overlap-layout blocks, and direct manipulation: click or drag on empty space to create, drag a block to
// move it (Alt or Ctrl duplicates), drag an edge to resize, drag into or out of the all-day strip,
// and the keyboard form (Shift+arrows, Alt+Shift+↑/↓, Enter, Esc).

import { Lock, Repeat, Users, Video, EyeOff, CircleHelp } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as RPointerEvent } from 'react'
import { Popover } from '../../components/Popover'
import { addDaysISO, formatDate, formatHour, formatTime, minutesOfDay, parseISO, toISODate, weekdayOf, zoneAbbr, zoneParts, type ISODate } from '../../lib/dates'
import { layoutDay, splitDays, type DayLayout } from '../../lib/eventLayout'
import { displayColour, textOn, tint } from './colour'
import { useCal } from './context'
import { conflictLine } from './actions'
import {
  clamp, dragResult, gridInstant, isoWeek, layoutBars, rubberBand, snap, snapDown, wallString, type DragKind, type GridSpan,
} from './geometry'
import {
  conferenceLink, hasGuests, isCancelled, isPrivate, joinable, layoutPartstat, needsReply, timeSpan, titleOf,
} from './eventInfo'
import { useShallow } from 'zustand/react/shallow'
import { dismissOpenPopover, useCalUI } from './store'
import type { ApiTask, CalEvent } from './types'
import { TaskChip } from './TaskChip'
import { t as tr } from '../../i18n'
import { xFromStart } from '../../lib/rtl'
import { ItemBoundary } from '../../components/bits'

const STRIP_ROWS = 3
const BAR_H = 22

interface Drag {
  kind: 'create' | DragKind | 'bar'
  ev?: CalEvent
  orig: GridSpan
  pressX: number
  pressY: number
  pressDay: number
  pressMin: number
  cur: GridSpan
  /** The pointer is over the all-day strip (a timed event becomes all-day there). */
  toStrip: boolean
  /** An all-day bar dragged into the grid (it becomes a timed hour). */
  toGrid: boolean
  dup: boolean
  moved: boolean
  alt: boolean
}

/** A timed event spanning 24 hours or more is drawn in the all-day strip. */
const inStrip = (e: CalEvent) => e.allDay || e.endAt.getTime() - e.startAt.getTime() >= 24 * 3600_000

export function TimeGrid({ days }: { days: ISODate[] }) {
  const cal = useCal()
  const { prefs, events, now, today, actions, readOnly } = cal
  const ui = useCalUI(useShallow((st) => ({ kbdDrag: st.kbdDrag, slot: st.slot })))
  const scroller = useRef<HTMLDivElement>(null)
  const body = useRef<HTMLDivElement>(null)
  // Hours are 48px (44 on a phone), or taller when the whole day fits the window with room to spare.
  const [fitH, setFitH] = useState(0)
  useLayoutEffect(() => {
    const el = scroller.current
    if (!el) return
    const measure = () => {
      const top = body.current ? body.current.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop : 0
      setFitH(Math.floor((el.clientHeight - top) / 24))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const hourH = Math.max(cal.narrow ? 44 : 48, Math.min(fitH, 96))
  const colsRef = useRef<HTMLDivElement>(null)
  const stripRef = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  const dragRef = useRef<Drag | null>(null)
  dragRef.current = drag
  const [stripOpen, setStripOpen] = useState(false)
  const [overflow, setOverflow] = useState<{ ids: string[]; anchor: HTMLElement } | null>(null)
  const zone = prefs.zone
  const step = prefs.snap

  // ---------- layout ----------

  const byKey = useMemo(() => new Map(events.map((e) => [e.key, e])), [events])
  const timed = useMemo(() => events.filter((e) => !inStrip(e)), [events])
  const stripEvents = useMemo(() => events.filter(inStrip), [events])

  const layouts = useMemo(() => {
    const segs = splitDays(timed.map((e) => ({ id: e.key, start: wallString(e.startAt, zone), end: wallString(e.endAt, zone) })))
    const out = new Map<ISODate, DayLayout & { segs: Map<string, { continued: boolean; continues: boolean }> }>()
    for (const d of days) {
      const mine = segs.filter((s) => s.date === d)
      const lay = layoutDay(mine.map((s) => ({ id: s.id, start: s.start, end: s.end, partstat: layoutPartstat(byKey.get(s.id)!), continued: s.continued })))
      out.set(d, { ...lay, segs: new Map(mine.map((s) => [s.id, { continued: s.continued, continues: s.continues }])) })
    }
    return out
  }, [timed, days, zone, byKey])

  const bars = useMemo(() => layoutBars(stripEvents.map((e) => ({
    key: e.key, first: e.firstDay, last: e.lastDay, allDay: e.allDay, start: e.startAt.getTime(),
  })), days), [stripEvents, days])
  const tasksByDay = useMemo(() => {
    const m = new Map<ISODate, { timed: ApiTask[]; dated: ApiTask[] }>()
    for (const t of cal.tasks) {
      if (!t.due) continue
      const dateOnly = t.due_all_day || t.due.length === 10
      const day = dateOnly ? t.due.slice(0, 10) : toISODate(parseISO(t.due, zone)!, zone)
      const slot = m.get(day) ?? { timed: [], dated: [] }
      ;(dateOnly ? slot.dated : slot.timed).push(t)
      m.set(day, slot)
    }
    return m
  }, [cal.tasks, zone])
  // Lanes per column: the bars first, then that day's date-only tasks.
  const colLanes = days.map((d, i) => {
    const base = Math.max(0, ...bars.filter((b) => b.from <= i && b.to >= i).map((b) => b.lane + 1))
    return { base, tasks: tasksByDay.get(d)?.dated ?? [] }
  })
  const lanes = Math.max(0, ...bars.map((b) => b.lane + 1), ...colLanes.map((c) => c.base + c.tasks.length))
  const overflowing = lanes > STRIP_ROWS
  // Collapsed past three rows: two rows of items, then "+n more" in the third.
  const limit = stripOpen || !overflowing ? Infinity : STRIP_ROWS - 1
  const shownLanes = stripOpen ? lanes : Math.min(lanes, STRIP_ROWS)
  const hiddenIn = (i: number) =>
    bars.filter((b) => b.lane >= limit && b.from <= i && b.to >= i).length +
    colLanes[i].tasks.filter((_, j) => colLanes[i].base + j >= limit).length

  // ---------- first scroll ----------

  useLayoutEffect(() => {
    const el = scroller.current
    if (!el) return
    const showsToday = days.includes(today)
    const target = showsToday ? Math.max(0, minutesOfDay(now, zone) - 120) : Math.max(0, prefs.workStart - 60)
    el.scrollTop = (target / 60) * hourH
    // Only when the period changes, not on every tick of the clock.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days[0], hourH])

  // ---------- pointer maths ----------

  const at = useCallback((x: number, y: number) => {
    const r = colsRef.current!.getBoundingClientRect()
    const w = r.width / days.length
    const day = clamp(Math.floor(xFromStart(x, r) / w), 0, days.length - 1)
    const min = ((y - r.top) / hourH) * 60
    // The body scrolls under the sticky head, so the strip's own box says where the grid starts.
    const strip = stripRef.current?.getBoundingClientRect()
    const overStrip = !!strip && y >= strip.top - 8 && y <= strip.bottom + 2
    return { day, min, overStrip, inGrid: !strip || y > strip.bottom + 2 }
  }, [days.length, hourH])

  const spanOf = useCallback((e: CalEvent, day: ISODate): GridSpan => {
    const i = Math.max(0, days.indexOf(day))
    const start = e.firstDay < day ? 0 : minutesOfDay(e.startAt, zone)
    const dur = Math.max(step, Math.round((e.endAt.getTime() - e.startAt.getTime()) / 60_000))
    return { day: i, start, end: start + dur }
  }, [days, zone, step])

  const begin = (ev: RPointerEvent, kind: Drag['kind'], e?: CalEvent, day?: ISODate) => {
    if (ev.button !== 0) return
    const p = at(ev.clientX, ev.clientY)
    let orig: GridSpan
    if (kind === 'create') {
      const s = snapDown(p.min, step)
      orig = { day: p.day, start: s, end: s + step }
    } else if (kind === 'bar') {
      orig = { day: days.indexOf(e!.firstDay < days[0] ? days[0] : e!.firstDay), start: 0, end: 60 }
      if (orig.day < 0) orig.day = p.day
    } else orig = spanOf(e!, day!)
    if (kind !== 'create' && (e?.readOnly || readOnly)) {
      // Read-only: a click still opens the detail; nothing moves.
      kind = kind === 'bar' ? 'bar' : 'move'
    }
    const d: Drag = {
      kind, ev: e, orig, pressX: ev.clientX, pressY: ev.clientY, pressDay: p.day, pressMin: p.min, cur: orig,
      toStrip: false, toGrid: false, dup: false, moved: false, alt: ev.altKey,
    }
    setDrag(d)
    ev.preventDefault()
  }

  useEffect(() => {
    if (!drag) return
    const onMove = (ev: PointerEvent) => {
      const d = dragRef.current
      if (!d) return
      const moved = d.moved || Math.abs(ev.clientX - d.pressX) > 4 || Math.abs(ev.clientY - d.pressY) > 4
      if (!moved) return
      if (d.ev && (d.ev.readOnly || readOnly)) {
        document.body.classList.add('cal-no-drop')
        return
      }
      const p = at(ev.clientX, ev.clientY)
      const sc = scroller.current
      if (sc) {
        const r = sc.getBoundingClientRect()
        if (ev.clientY > r.bottom - 28) sc.scrollTop += 12
        else if (ev.clientY < r.top + 28 + (d.kind === 'create' ? 0 : 0) && p.inGrid) sc.scrollTop -= 12
      }
      let cur: GridSpan
      let toStrip = false
      let toGrid = false
      if (d.kind === 'create') {
        const s = ev.altKey ? 5 : step
        const band = rubberBand(d.pressMin, p.min, s)
        cur = { day: d.pressDay, ...band }
      } else if (d.kind === 'bar') {
        toGrid = p.inGrid
        if (toGrid) {
          const s = snap(p.min - 30, step)
          cur = { day: p.day, start: clamp(s, 0, 1440 - 60), end: clamp(s, 0, 1440 - 60) + 60 }
        } else cur = { day: d.orig.day + (p.day - d.pressDay), start: 0, end: 60 }
      } else {
        toStrip = d.kind === 'move' && p.overStrip
        cur = dragResult(d.kind as DragKind, d.orig, p.min - d.pressMin, p.day - d.pressDay, step, days.length)
      }
      setDrag({ ...d, cur, moved: true, toStrip, toGrid, dup: ev.altKey || ev.ctrlKey || ev.metaKey })
    }
    const onUp = (ev: PointerEvent) => {
      document.body.classList.remove('cal-no-drop')
      const d = dragRef.current
      setDrag(null)
      if (!d) return
      if (!d.moved) {
        if (d.ev) {
          const el = document.querySelector(`[data-key="${CSS.escape(d.ev.key)}"]`)
          cal.openEvent(d.ev, el)
        } else if (d.kind === 'create') {
          // ACT event.create @web.calendar: a click on an empty slot opens quick create with the default length.
          const start = gridInstant(days[d.pressDay], snapDown(d.pressMin, 30 >= step ? Math.max(step, 30) : step), zone)
          const end = new Date(start.getTime() + prefs.defaultDuration * 60_000)
          useCalUI.getState().set({ quick: { start, end, allDay: false, anchor: rectAt(ev.clientX, ev.clientY) } })
        }
        return
      }
      if (d.ev && (d.ev.readOnly || readOnly)) {
        cal.actions.refresh()
        return
      }
      void finish(d, ev)
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        ev.preventDefault()
        ev.stopPropagation()
        document.body.classList.remove('cal-no-drop')
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
  }, [!!drag, at, step, days, zone, readOnly])

  const finish = async (d: Drag, ev: PointerEvent) => {
    const { cur } = d
    if (d.kind === 'create') {
      // ACT event.create: a drag over empty space gives the range.
      const start = gridInstant(days[cur.day], cur.start, zone)
      const end = gridInstant(days[cur.day], cur.end, zone)
      useCalUI.getState().set({ quick: { start, end, allDay: false, anchor: rectAt(ev.clientX, ev.clientY) } })
      return
    }
    const e = d.ev!
    if (d.kind === 'bar') {
      const shift = cur.day - d.orig.day
      if (d.toGrid) {
        // ACT event.edit @web.calendar: out of the strip it becomes one hour at the drop time.
        const start = gridInstant(days[cur.day], cur.start, zone)
        await actions.reschedule(e, start, new Date(start.getTime() + 60 * 60_000), false)
        return
      }
      if (!shift) return
      const span = Math.max(1, Math.round((parseISO(addDaysISO(e.lastDay, 1), 'UTC')!.getTime() - parseISO(e.firstDay, 'UTC')!.getTime()) / 86_400_000))
      const first = addDaysISO(e.firstDay, shift)
      if (d.dup) await actions.duplicateAt(e, first, addDaysISO(first, span), true)
      else await actions.reschedule(e, first, addDaysISO(first, span), true)
      return
    }
    if (d.toStrip) {
      // ACT event.edit @web.calendar: into the strip it becomes all-day on that day.
      const day = days[cur.day]
      await actions.reschedule(e, day, addDaysISO(day, 1), true)
      return
    }
    if (cur.day === d.orig.day && cur.start === d.orig.start && cur.end === d.orig.end) return // dragged back: nothing
    const start = gridInstant(days[cur.day], cur.start, zone)
    const end = gridInstant(days[cur.day], cur.end, zone)
    if (d.kind === 'move' && d.dup) await actions.duplicateAt(e, start, end, false) // ACT event.duplicate @web.calendar
    else if (d.kind === 'move') await actions.reschedule(e, start, end, false) // ACT event.reschedule @web.calendar
    else await actions.reschedule(e, start, end, false, tr('calendar.timeGrid.resize')) // ACT event.resize @web.calendar
  }

  // ---------- keyboard drag ----------

  const kbd = ui.kbdDrag

  // ---------- render ----------

  const nowMin = minutesOfDay(now, zone)
  const cols = days.length
  const gridStyle = { '--hour-h': `${hourH}px`, '--cols': cols } as CSSProperties
  const secondary = prefs.secondaryZone

  return (
    <div className={`cal-tg${cal.loading ? ' is-loading' : ''}`} style={gridStyle} data-testid="cal-timegrid" data-days={days.length}>
      <div className="cal-tg-scroll" ref={scroller}>
        <div className="cal-tg-head">
          <div className="cal-tg-row cal-tg-dayheads">
            <div className={`cal-tg-corner${secondary ? ' two' : ''}`}>
              {prefs.weekNumbers && <span className="cal-weekno" title={tr('calendar.timeGrid.weekNumber')}>W{isoWeek(days[0])}</span>}
              {secondary && <span className="cal-zone-label">{zoneAbbr(now, secondary)}</span>}
              <span className="cal-zone-label">{zoneAbbr(now, zone)}</span>
            </div>
            {/* Day heads are buttons in a labelled group, not a grid row. */}
            <div className="cal-tg-days" role="group" aria-label={tr('calendar.timeGrid.days')}>
              {days.map((d) => {
                const wd = weekdayOf(d)
                const isToday = d === today
                return (
                  <button
                    key={d}
                    type="button"
                    className={`cal-dayhead${isToday ? ' today' : ''}${d < today ? ' past' : ''}${wd === 0 || wd === 6 ? ' weekend' : ''}`}
                    aria-label={`${formatDate(d, 'long')}${isToday ? ', today' : ''}`}
                    onClick={() => cal.go('day', d)}
                  >
                    <span className="cal-dayhead-wd">{formatDate(d, 'weekday')}</span>
                    <span className="cal-dayhead-num">{Number(d.slice(8))}</span>
                  </button>
                )
              })}
            </div>
          </div>
          <div className="cal-tg-row cal-tg-striprow">
            <div className={`cal-tg-corner cal-strip-label${secondary ? ' two' : ''}`}>
              {overflowing ? (
                <button type="button" className="cal-strip-toggle" aria-expanded={stripOpen} onClick={() => setStripOpen(!stripOpen)}>
                  {stripOpen ? tr('common.less') : tr('common.allDay')}
                </button>
              ) : (
                <span>{tr('common.allDay')}</span>
              )}
            </div>
            <div
              ref={stripRef}
              className={`cal-strip${drag?.toStrip ? ' drop' : ''}`}
              style={{ height: Math.max(1, shownLanes) * (BAR_H + 2) + 6 }}
              onPointerDown={(ev) => {
                if (ev.target !== ev.currentTarget || ev.button !== 0) return
                if (dismissOpenPopover()) return
                // ACT event.create: the all-day strip creates all-day events.
                const r = ev.currentTarget.getBoundingClientRect()
                const i = clamp(Math.floor((xFromStart(ev.clientX, r) / r.width) * cols), 0, cols - 1)
                useCalUI.getState().set({ quick: { start: days[i], end: addDaysISO(days[i], 1), allDay: true, anchor: rectAt(ev.clientX, ev.clientY) } })
              }}
            >
              {days.map((d, i) => (
                <div key={d} className={`cal-strip-cell${d === today ? ' today' : ''}`} style={{ insetInlineStart: `${(i / cols) * 100}%`, width: `${100 / cols}%` }} />
              ))}
              {bars.filter((b) => b.lane < limit).map((b) => {
                const e = byKey.get(b.key)!
                const moving = drag?.ev?.key === e.key && drag.moved
                return (
                  <Block
                    key={b.key}
                    e={e}
                    bar
                    style={{ top: 3 + b.lane * (BAR_H + 2), height: BAR_H, insetInlineStart: `calc(${(b.from / cols) * 100}% + 2px)`, width: `calc(${((b.to - b.from + 1) / cols) * 100}% - 5px)` }}
                    className={`${b.clippedStart ? ' clip-start' : ''}${b.clippedEnd ? ' clip-end' : ''}${moving ? ' dragging' : ''}`}
                    onPointerDown={(ev) => begin(ev, 'bar', e)}
                  />
                )
              })}
              {days.map((_, i) => {
                const { base, tasks: list } = colLanes[i]
                return list.map((t, j) => {
                  const lane = base + j
                  if (lane >= limit) return null
                  return (
                    <TaskChip key={t.uid} t={t} style={{ position: 'absolute', top: 3 + lane * (BAR_H + 2), height: BAR_H, insetInlineStart: `calc(${(i / cols) * 100}% + 2px)`, width: `calc(${100 / cols}% - 5px)` }} />
                  )
                })
              })}
              {limit !== Infinity && days.map((d, i) => {
                const n = hiddenIn(i)
                return n > 0 ? (
                  <button key={`more-${d}`} type="button" className="cal-strip-more" style={{ insetInlineStart: `calc(${(i / cols) * 100}% + 2px)`, top: 3 + limit * (BAR_H + 2) }} onClick={() => setStripOpen(true)}>
                    {tr('calendar.timeGrid.more', { n })}
                  </button>
                ) : null
              })}
              {drag?.toStrip && (
                <div className="cal-ghost bar" style={{ top: 3, height: BAR_H, insetInlineStart: `calc(${(drag.cur.day / cols) * 100}% + 2px)`, width: `calc(${100 / cols}% - 5px)` }}>
                  {titleOf(drag.ev!)}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="cal-tg-body" ref={body} style={{ height: 24 * hourH }}>
          <div className={`cal-gutter${secondary ? ' two' : ''}`} aria-hidden>
            {Array.from({ length: 24 }, (_, h) => (
              <div key={h} className="cal-hour" style={{ top: h * hourH }}>
                {h > 0 && secondary && <span className="cal-hour-2">{secondaryHour(days[0], h, zone, secondary, prefs.hour12)}</span>}
                {h > 0 && <span>{formatHour(h, prefs.hour12)}</span>}
              </div>
            ))}
            {days.includes(today) && <div className="cal-now-dot" style={{ top: (nowMin / 60) * hourH }} />}
          </div>
          <div
            className="cal-cols"
            ref={colsRef}
            role="group"
            aria-label={tr('calendar.timeGrid.calendarGrid')}
          >
            {days.map((d, i) => {
              const lay = layouts.get(d)
              const wd = weekdayOf(d)
              const work = prefs.workDays.includes(wd)
              const slot = ui.slot?.day === d ? ui.slot : null
              return (
                <div
                  key={d}
                  className={`cal-col${d === today ? ' today' : ''}${work ? '' : ' offday'}`}
                  data-day={d}
                  role="group"
                  aria-label={formatDate(d, 'long')}
                  onPointerDown={(ev) => {
                    if ((ev.target as HTMLElement).closest('.cal-block, .cal-more, .cal-task')) return
                    if (ev.button === 0 && dismissOpenPopover()) return
                    begin(ev, 'create')
                  }}
                >
                  {work ? (
                    <>
                      <div className="cal-offhours" style={{ top: 0, height: (prefs.workStart / 60) * hourH }} />
                      <div className="cal-offhours" style={{ top: (prefs.workEnd / 60) * hourH, bottom: 0 }} />
                    </>
                  ) : (
                    <div className="cal-offhours" style={{ top: 0, bottom: 0 }} />
                  )}
                  {slot && <div className="cal-slot-focus" style={{ top: (slot.min / 60) * hourH, height: (step / 60) * hourH }} />}
                  {lay?.placed.map((p) => {
                    const e = byKey.get(p.id)!
                    const seg = lay.segs.get(p.id)
                    const moving = (drag?.ev?.key === e.key && drag.moved) || kbd?.key === e.key
                    return (
                      <Block
                        key={p.id}
                        e={e}
                        compact={p.compact}
                        continued={seg?.continued}
                        continues={seg?.continues}
                        className={moving ? ' dragging' : ''}
                        style={{
                          top: (p.top / 60) * hourH + 1,
                          height: Math.max(14, ((p.bottom - p.top) / 60) * hourH - 2),
                          insetInlineStart: `calc(${(p.col / p.cols) * 100}% + 1px)`,
                          width: `calc(${(p.span / p.cols) * 100}% - ${p.col + p.span >= p.cols ? 10 : 3}px)`,
                          zIndex: 1 + p.col,
                        }}
                        onPointerDown={(ev) => {
                          const edge = (ev.target as HTMLElement).dataset.edge
                          begin(ev, edge === 'end' ? 'resize-end' : edge === 'start' ? 'resize-start' : 'move', e, d)
                        }}
                      />
                    )
                  })}
                  {lay?.chips.map((c) => (
                    <button
                      key={c.ids.join()}
                      type="button"
                      className="cal-more"
                      style={{ top: (c.top / 60) * hourH + 1, height: Math.max(18, ((c.bottom - c.top) / 60) * hourH - 2), insetInlineStart: `${(c.col / c.cols) * 100}%`, width: `calc(${100 / c.cols}% - 10px)` }}
                      onClick={(ev) => setOverflow({ ids: c.ids, anchor: ev.currentTarget })}
                      aria-label={tr('calendar.timeGrid.moreEvents', { count: c.ids.length })}
                    >
                      +{c.ids.length}
                    </button>
                  ))}
                  {(tasksByDay.get(d)?.timed ?? []).map((t) => {
                    const m = minutesOfDay(parseISO(t.due!, zone)!, zone)
                    return <TaskChip key={t.uid} t={t} timed style={{ position: 'absolute', top: (m / 60) * hourH, insetInlineStart: 2, insetInlineEnd: 10, height: 20, zIndex: 6 }} />
                  })}
                  {d === today && (
                    <div className="cal-now" role="img" style={{ top: (nowMin / 60) * hourH }} aria-label={tr('calendar.timeGrid.now', { time: formatTime(now, prefs) })} />
                  )}
                  {drag?.moved && !drag.toStrip && drag.cur.day === i && (drag.kind !== 'bar' || drag.toGrid) && (
                    <Ghost d={drag} hourH={hourH} days={days} />
                  )}
                  {kbd && kbd.span.day === i && <KbdGhost hourH={hourH} days={days} />}
                </div>
              )
            })}
          </div>
        </div>
      </div>
      {overflow && (
        <Popover anchor={overflow.anchor} open onClose={() => setOverflow(null)} role="dialog" label={tr('calendar.timeGrid.moreEvents2')} className="cal-pop cal-more-pop">
          <div className="cal-more-list">
            {overflow.ids.map((id) => {
              const e = byKey.get(id)
              if (!e) return null
              return (
                <button
                  key={id}
                  type="button"
                  className="cal-more-item"
                  onClick={(ev) => {
                    setOverflow(null)
                    cal.openEvent(e, ev.currentTarget)
                  }}
                >
                  <span className="cal-dot" style={{ background: displayColour(e.colour, cal.dark) }} />
                  <span className="cal-more-time">{formatTime(e.startAt, prefs, { compact: true })}</span>
                  <span className="cal-more-title" dir="auto">{titleOf(e)}</span>
                </button>
              )
            })}
          </div>
        </Popover>
      )}
    </div>
  )
}

function rectAt(x: number, y: number): DOMRect {
  return new DOMRect(x, y, 1, 1)
}

function secondaryHour(day: ISODate, h: number, zone: string, other: string, hour12: boolean): string {
  const p = zoneParts(gridInstant(day, h * 60, zone), other)
  return p.minute ? `${hour12 ? (p.hour % 12 || 12) : String(p.hour).padStart(2, '0')}:${String(p.minute).padStart(2, '0')}` : formatHour(p.hour, hour12)
}

/** The drag ghost: the new time, snapped, with its label (and a conflict hint while it moves). */
function Ghost({ d, hourH, days }: { d: Drag; hourH: number; days: ISODate[] }) {
  const cal = useCal()
  const { cur } = d
  const start = gridInstant(days[cur.day], cur.start, cal.prefs.zone)
  const end = gridInstant(days[cur.day], Math.min(cur.end, 1440 * 2), cal.prefs.zone)
  const label = timeSpan(start, end, cal.prefs)
  const colour = d.ev ? displayColour(d.ev.colour, cal.dark) : undefined
  const clash = d.kind === 'create' ? null : conflictLine(cal.busy, start, end, d.ev?.key, cal.prefs)
  return (
    <div
      className={`cal-ghost${d.kind === 'create' ? ' create' : ''}`}
      style={{ top: (cur.start / 60) * hourH, height: Math.max(12, ((Math.min(cur.end, 1440) - cur.start) / 60) * hourH - 1), background: colour, color: colour ? textOn(colour) : undefined }}
      aria-live="polite"
    >
      <b>{d.ev ? `${d.dup ? 'Copy: ' : ''}${titleOf(d.ev)}` : tr('common.noTitle')}</b>
      <span>{label}</span>
      {clash && <span className="cal-ghost-clash">{clash}</span>}
    </div>
  )
}

function KbdGhost({ hourH, days }: { hourH: number; days: ISODate[] }) {
  const cal = useCal()
  const k = useCalUI((s) => s.kbdDrag)!
  const e = cal.events.find((x) => x.key === k.key)
  if (!e) return null
  const start = gridInstant(days[k.span.day], k.span.start, cal.prefs.zone)
  const end = gridInstant(days[k.span.day], k.span.end, cal.prefs.zone)
  const colour = displayColour(e.colour, cal.dark)
  return (
    <div className="cal-ghost kbd" style={{ top: (k.span.start / 60) * hourH, height: Math.max(12, ((Math.min(k.span.end, 1440) - k.span.start) / 60) * hourH - 1), background: colour, color: textOn(colour) }} role="status">
      <b dir="auto">{titleOf(e)}</b>
      <span>{tr('calendar.timeGrid.enterToSaveEscTo', { timeSpan: timeSpan(start, end, cal.prefs) })}</span>
    </div>
  )
}

/** One event block (or an all-day bar): colour, title, time and place when there is room, icons. */
type BlockProps = {
  e: CalEvent; style: CSSProperties; className?: string; compact?: boolean; bar?: boolean; continued?: boolean; continues?: boolean
  onPointerDown?: (ev: RPointerEvent<HTMLDivElement>) => void
}

/** One event in the grid; a failure shows "Couldn't show this event" in its place. */
export function Block(props: BlockProps) {
  return (
    <ItemBoundary what="event" compact>
      <BlockBody {...props} />
    </ItemBoundary>
  )
}

function BlockBody({ e, style, className = '', compact, bar, continued, continues, onPointerDown }: BlockProps) {
  const cal = useCal()
  const focus = useCalUI((s) => s.focus)
  const { prefs, now } = cal
  const colour = displayColour(e.colour, cal.dark)
  const partstat = layoutPartstat(e)
  const outlined = partstat === 'declined' || partstat === 'needs-action'
  const hatched = partstat === 'tentative'
  const cancelled = isCancelled(e)
  const past = prefs.dimPast && e.endAt < now
  const fg = outlined ? 'var(--text)' : textOn(colour)
  const time = e.allDay ? '' : timeSpan(e.startAt, e.endAt, prefs)
  const conf = conferenceLink(e)
  const icons = (
    <span className="cal-block-icons" aria-hidden>
      {e.recurring && <Repeat />}
      {hasGuests(e) && <Users />}
      {isPrivate(e) && <EyeOff />}
      {conf && <Video />}
      {needsReply(e) && <CircleHelp />}
      {e.readOnly && <Lock />}
    </span>
  )
  const status = [
    e.allDay ? tr('common.allDay') : time, e.location, cancelled && tr('common.cancelled'), partstat === 'tentative' && tr('calendar.timeGrid.tentative'),
    partstat === 'needs-action' && tr('common.needsReply'), partstat === 'declined' && tr('calendar.timeGrid.declined'), e.recurring && tr('calendar.timeGrid.repeats'),
  ].filter(Boolean).join(', ')
  return (
    <div
      className={`cal-block${bar ? ' bar' : ''}${compact ? ' compact' : ''}${outlined ? ' outlined' : ''}${hatched ? ' hatched' : ''}${cancelled ? ' cancelled' : ''}${past ? ' past' : ''}${e.readOnly || cal.readOnly ? ' ro' : ''}${focus === e.key ? ' focused' : ''}${className}`}
      style={{ ...style, '--ev': colour, '--ev-fg': fg, '--ev-tint': tint(colour, cal.dark ? 0.22 : 0.14) } as CSSProperties}
      data-key={e.key}
      data-testid="cal-event"
      data-shortcuts=""
      role="button"
      tabIndex={cal.tabStop === e.key ? 0 : -1}
      aria-label={`${titleOf(e)}, ${status}`}
      onPointerDown={onPointerDown}
      onFocus={() => useCalUI.getState().set({ focus: e.key })}
      onKeyDown={(ev) => {
        if (ev.key === 'Enter' && !ev.shiftKey && !useCalUI.getState().kbdDrag) {
          ev.preventDefault()
          cal.openEvent(e, ev.currentTarget)
        }
      }}
    >
      {!bar && !e.readOnly && !compact && <div className="cal-edge start" data-edge="start" />}
      <div className="cal-block-in">
        <span className="cal-block-title" dir="auto">
          {continued && <span className="cal-cont" aria-label="continued">↳ </span>}
          {titleOf(e)}
        </span>
        {!compact && !bar && <span className="cal-block-time">{time}{continues ? ' ↓' : ''}</span>}
        {!compact && !bar && e.location && <span className="cal-block-loc" dir="auto">{e.location}</span>}
        {bar && !e.allDay && <span className="cal-block-time"> {time}</span>}
        {icons}
        {!bar && joinable(e, now) && conf && (
          // ACT event.join @web.calendar: from five minutes before the start until the end.
          <a className="cal-join" href={conf} target="_blank" rel="noreferrer" onPointerDown={(ev) => ev.stopPropagation()}>
            <Video aria-hidden />{' '}{tr('common.join')}
          </a>
        )}
      </div>
      {!bar && !e.readOnly && <div className="cal-edge end" data-edge="end" />}
    </div>
  )
}
