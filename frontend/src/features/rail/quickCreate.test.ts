// Quick add outside the apps (palette and panels): event.create and task.create bodies, the
// chips shown before Enter, and the palette's "New event: …" / "Go to …" commands.
import { describe, expect, it } from 'vitest'
import { createCommand, goToDate, parseLine, quickChips, quickEventBody, quickTaskBody, type QuickCtx } from './quickCreate'

// Thursday 1 October 2026, 10:12 in UTC.
const c: QuickCtx = { prefs: { zone: 'UTC', hour12: true }, now: new Date('2026-10-01T10:12:00Z'), defaultDuration: 60 }

describe('event.create from one line', () => {
  it('reads a day and a time', () => {
    const b = quickEventBody(parseLine('Lunch with the team fri 1pm', 'event', c), c)
    expect(b).toMatchObject({ summary: 'Lunch with the team', start: '2026-10-02T13:00:00+00:00', end: '2026-10-02T14:00:00+00:00', all_day: false, tz: 'UTC' })
  })
  it('starts an untimed event at the next half hour', () => {
    const b = quickEventBody(parseLine('Call the bank', 'event', c), c)
    expect(b.start).toBe('2026-10-01T10:30:00+00:00')
    expect(b.end).toBe('2026-10-01T11:30:00+00:00')
  })
  it('keeps the location and the repeat', () => {
    const b = quickEventBody(parseLine('Standup every weekday 9am at Room 4', 'event', c), c)
    expect(b.location).toBe('Room 4')
    expect(b.recurrence).toContain('FREQ=')
  })
  it('shows the parse as chips', () => {
    const chips = quickChips(parseLine('Lunch fri 1pm', 'event', c), 'event', c)
    expect(chips[0]).toMatch(/Fri 2 Oct/)
  })
})

describe('task.create from one line', () => {
  it('reads the due and the tag', () => {
    const r = parseLine('call Dana fri #phone', 'task', c)
    const b = quickTaskBody(r, c, 'list-1')!
    expect(b).toMatchObject({ summary: expect.stringMatching(/call Dana/), due: '2026-10-02', list: 'list-1' })
    expect(quickChips(r, 'task', c)[0]).toBe('Due Fri 2 Oct')
  })
  it('makes nothing from an empty title', () => {
    expect(quickTaskBody(parseLine('', 'task', c), c, null)).toBeNull()
  })
})

describe('palette commands', () => {
  it('recognises "New event:" and "New task:"', () => {
    expect(createCommand('New event: lunch fri')).toEqual({ mode: 'event', text: 'lunch fri' })
    expect(createCommand('new task call Dana fri')).toEqual({ mode: 'task', text: 'call Dana fri' })
    expect(createCommand('task:')).toEqual({ mode: 'task', text: '' })
    expect(createCommand('tasks panel')).toBeNull()
    expect(createCommand('Go to Calendar')).toBeNull()
  })
  it('reads "Go to" a date', () => {
    expect(goToDate('Go to 12 Dec', c)).toBe('2026-12-12')
    expect(goToDate('go to tomorrow', c)).toBe('2026-10-02')
    expect(goToDate('Go to Calendar', c)).toBeNull()
  })
})
