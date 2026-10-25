// Arrow keys between events. Tab keeps the browser's focus order;
// the arrows move the focus from event to event, the same way in every view:
//
// - ← and →: the previous and next event in time order;
// - ↑ and ↓ in Day and Week: the event above or below in the same day column (the all-day row
//   sits above the timed events); in Month: the first event a week back or on, the step Shift+↑/↓
//   moves an event by; in Schedule (a list): the previous and next row.
//
// With nothing focused, → and ↓ start at the first event and ← and ↑ at the last.

import { addDaysISO, type ISODate } from '../../lib/dates'
import type { CalendarView } from '../../lib/routes'
import type { CalEvent } from './types'

export type EventStep = 'next' | 'prev' | 'up' | 'down'

type NavEvent = Pick<CalEvent, 'key' | 'startAt' | 'allDay' | 'firstDay' | 'lastDay'>

/** The day an event is drawn on first in the shown days. */
function shownDay<E extends NavEvent>(e: E, days: ISODate[]): ISODate {
  return e.firstDay < days[0] ? days[0] : e.firstDay
}

/** The shown events in time order: by the day they start on screen, all-day first, then by start. */
export function eventOrder<E extends NavEvent>(events: E[], days: ISODate[]): E[] {
  if (!days.length) return []
  const shown = events.filter((e) => days.some((d) => e.firstDay <= d && e.lastDay >= d))
  return shown
    .map((e, i) => ({ e, i, day: shownDay(e, days) }))
    .sort((a, b) =>
      a.day < b.day ? -1 : a.day > b.day ? 1
        : Number(a.e.allDay ? 0 : 1) - Number(b.e.allDay ? 0 : 1)
          || a.e.startAt.getTime() - b.e.startAt.getTime()
          || a.i - b.i)
    .map((x) => x.e)
}

/** The event an arrow key moves the focus to, or undefined to stay put. */
export function stepEvent<E extends NavEvent>(events: E[], days: ISODate[], view: CalendarView, focus: string | null, step: EventStep): E | undefined {
  const list = eventOrder(events, days)
  if (!list.length) return undefined
  const i = focus ? list.findIndex((e) => e.key === focus) : -1
  const forward = step === 'next' || step === 'down'
  if (i < 0) return forward ? list[0] : list[list.length - 1]
  const cur = list[i]
  const vertical = step === 'up' || step === 'down'
  if (!vertical || view === 'schedule') return list[i + (forward ? 1 : -1)]
  if (view === 'month') {
    const set = new Set(days)
    for (let d = addDaysISO(shownDay(cur, days), forward ? 7 : -7); set.has(d); d = addDaysISO(d, forward ? 7 : -7)) {
      const hit = list.find((e) => e.key !== cur.key && e.firstDay <= d && e.lastDay >= d)
      if (hit) return hit
    }
    return undefined
  }
  // Day and Week: the same column.
  const day = shownDay(cur, days)
  const column = list.filter((e) => shownDay(e, days) === day)
  return column[column.indexOf(cur) + (forward ? 1 : -1)]
}

/** Schedule and search results are lists: step through the rows on screen. */
export function stepRow(container: HTMLElement | null, step: EventStep): HTMLElement | undefined {
  if (!container) return undefined
  const rows = [...container.querySelectorAll<HTMLElement>('[data-testid="cal-event"]')]
  if (!rows.length) return undefined
  const i = rows.findIndex((r) => r === document.activeElement || r.contains(document.activeElement))
  const forward = step === 'next' || step === 'down'
  if (i < 0) return forward ? rows[0] : rows[rows.length - 1]
  return rows[i + (forward ? 1 : -1)]
}

/** The one Tab stop into the events: the focused event while it is shown, else the first. */
export function tabStopOf(list: { key: string }[], focus: string | null): string | null {
  if (focus && list.some((e) => e.key === focus)) return focus
  return list[0]?.key ?? null
}
