// ACT-SURFACES: web.calendar
// Arrow keys between events: the order and the steps in Day/Week, Month and Schedule.
import { describe, expect, it } from 'vitest'
import { eventOrder, stepEvent, tabStopOf } from './eventNav'

const ev = (key: string, day: string, hour: number | null, lastDay = day) => ({
  key, firstDay: day, lastDay, allDay: hour === null, startAt: new Date(`${day}T${String(hour ?? 0).padStart(2, '0')}:00:00`),
})
const WEEK = ['2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13', '2026-11-14', '2026-11-15']
const offsite = ev('offsite', '2026-11-10', null)
const alpha = ev('alpha', '2026-11-10', 9)
const beta = ev('beta', '2026-11-10', 11)
const delta = ev('delta', '2026-11-11', 10)
const trip = ev('trip', '2026-11-05', null, '2026-11-09') // starts before the week
const EVENTS = [beta, delta, alpha, offsite, trip]

describe('event order', () => {
  it('sorts by the day shown, all-day first, then by start', () => {
    expect(eventOrder(EVENTS, WEEK).map((e) => e.key)).toEqual(['trip', 'offsite', 'alpha', 'beta', 'delta'])
  })
  it('the Tab stop is the focused event, else the first', () => {
    const list = eventOrder(EVENTS, WEEK)
    expect(tabStopOf(list, 'beta')).toBe('beta')
    expect(tabStopOf(list, 'gone')).toBe('trip')
    expect(tabStopOf([], null)).toBeNull()
  })
})

describe('calendar.nextEvent, calendar.prevEvent, calendar.eventUp, calendar.eventDown', () => {
  it('← and → follow time order in every view; nothing focused starts at an end', () => {
    for (const view of ['week', 'month', 'day', 'schedule'] as const) {
      expect(stepEvent(EVENTS, WEEK, view, 'beta', 'next')?.key).toBe('delta')
      expect(stepEvent(EVENTS, WEEK, view, 'beta', 'prev')?.key).toBe('alpha')
    }
    expect(stepEvent(EVENTS, WEEK, 'week', null, 'next')?.key).toBe('trip')
    expect(stepEvent(EVENTS, WEEK, 'week', null, 'up')?.key).toBe('delta')
    expect(stepEvent(EVENTS, WEEK, 'week', 'delta', 'next')).toBeUndefined()
  })
  it('↑ and ↓ stay in the day column in the time grid', () => {
    expect(stepEvent(EVENTS, WEEK, 'week', 'alpha', 'up')?.key).toBe('offsite')
    expect(stepEvent(EVENTS, WEEK, 'week', 'alpha', 'down')?.key).toBe('beta')
    expect(stepEvent(EVENTS, WEEK, 'week', 'beta', 'down')).toBeUndefined()
    expect(stepEvent(EVENTS, WEEK, 'week', 'delta', 'up')).toBeUndefined()
  })
  it('↑ and ↓ move a week in Month, to the first event that day', () => {
    const month = Array.from({ length: 21 }, (_, i) => {
      const d = new Date(2026, 10, 9 + i)
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    })
    const gamma = ev('gamma', '2026-11-17', 9)
    const late = ev('late', '2026-11-24', 9)
    const all = [...EVENTS, gamma, late]
    expect(stepEvent(all, month, 'month', 'alpha', 'down')?.key).toBe('gamma')
    expect(stepEvent(all, month, 'month', 'gamma', 'down')?.key).toBe('late')
    expect(stepEvent(all, month, 'month', 'gamma', 'up')?.key).toBe('offsite')
    expect(stepEvent(all, month, 'month', 'delta', 'down')).toBeUndefined()
  })
  it('↑ and ↓ are previous and next in Schedule', () => {
    expect(stepEvent(EVENTS, WEEK, 'schedule', 'beta', 'down')?.key).toBe('delta')
    expect(stepEvent(EVENTS, WEEK, 'schedule', 'beta', 'up')?.key).toBe('alpha')
  })
})
