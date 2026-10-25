// ACT-SURFACES: web.calendar web.editor
// The Today view's words and day line (today.ts, calendar.today_view), and Find a time with
// household members (household.ts, event.find_time_household): guests who share their free/busy
// are chosen at once, and the chosen people's busy times count in the local ranking.
import { describe, expect, it } from 'vitest'
import { chosenBusy, guestsInHousehold, type HouseholdPerson } from './household'
import { dayPart, greeting, lineSpan, summary } from './today'

describe('Today', () => {
  it('greets by the time of day', () => {
    expect(greeting(3)).toBe('Up late')
    expect(greeting(8)).toBe('Good morning')
    expect(greeting(13)).toBe('Good afternoon')
    expect(greeting(20)).toBe('Good evening')
    expect([dayPart(2), dayPart(9), dayPart(15), dayPart(19), dayPart(23)]).toEqual(['night', 'morning', 'afternoon', 'evening', 'night'])
  })

  it('says what the day holds in one sentence', () => {
    const zero = { events: 0, tasks: 0, followups: 0, approvals: 0, reminders: 0 }
    expect(summary(zero, true)).toBe('Nothing planned or waiting.')
    expect(summary(zero, false)).toBe('Nothing planned for this day.')
    expect(summary({ ...zero, events: 1 }, true)).toBe('1 event.')
    expect(summary({ ...zero, events: 3, tasks: 2, followups: 1 }, true)).toBe('3 events, 2 tasks due and 1 reply to wait for.')
    expect(summary({ ...zero, approvals: 2, reminders: 1 }, true)).toBe('2 requests to decide and 1 missed reminder.')
  })

  it('spans the working hours, at least 8 to 20, and every event', () => {
    expect(lineSpan({ start: 540, end: 1020 }, [])).toEqual({ start: 480, end: 1200 })
    expect(lineSpan({ start: 540, end: 1020 }, [{ start: 390, end: 420 }, { start: 1290, end: 1320 }])).toEqual({ start: 360, end: 1320 })
  })
})

describe('event.find_time_household', () => {
  const people: HouseholdPerson[] = [
    { id: 2, name: 'Dana', email: 'Dana@example.org', busy: [{ start: '2026-10-07T14:00:00Z', end: '2026-10-07T15:00:00Z' }], tentative: [] },
    { id: 3, name: 'Sam', email: 'sam@example.org', busy: [], tentative: [{ start: '2026-10-07T16:00:00Z', end: '2026-10-07T17:00:00Z' }] },
  ]

  it('chooses the guests who share their free/busy', () => {
    expect(guestsInHousehold(people, [{ email: 'dana@example.org' }, { email: 'kim@example.com' }])).toEqual([2])
    expect(guestsInHousehold(people, [])).toEqual([])
  })

  it('counts only the chosen people, tentative times too', () => {
    expect(chosenBusy(people, [3]).map((b) => b.start.toISOString())).toEqual(['2026-10-07T16:00:00.000Z'])
    expect(chosenBusy(people, [2, 3])).toHaveLength(2)
    expect(chosenBusy(people, [])).toEqual([])
  })
})
