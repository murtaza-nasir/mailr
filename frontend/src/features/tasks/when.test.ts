import { describe, expect, it } from 'vitest'
import { applyLocal, undoBody } from './actions'
import type { TaskItem } from './model'
import { describe as describeWhen, join, needsDueScope, parseTime, parseWhen, presets, ruleAfterMove, split } from './when'

const zone = 'America/Chicago'
// Thu 1 Oct 2026, 10:00 in Chicago.
const now = new Date('2026-10-01T15:00:00Z')
const prefs = { zone, hour12: true }

describe('when picker presets', () => {
  it('offers t/m/w/n/0 with the task\'s time kept, plus Later today and Tonight while ahead', () => {
    const p = presets(now, '2026-10-05T19:30:00.000Z', zone)
    expect(p.map((x) => [x.id, x.key ?? ''])).toEqual([
      ['later', ''], ['tonight', ''], ['today', 't'], ['tomorrow', 'm'], ['weekend', 'w'], ['nextweek', 'n'], ['none', '0'],
    ])
    expect(split(p.find((x) => x.id === 'tomorrow')!.value, zone)).toEqual({ day: '2026-10-02', hm: '14:30' })
    expect(p.find((x) => x.id === 'weekend')!.value).toBe('2026-10-03T19:30:00.000Z')
    expect(split(p.find((x) => x.id === 'nextweek')!.value, zone).day).toBe('2026-10-05')
    expect(p.find((x) => x.id === 'none')!.value).toBeNull()
  })
  it('keeps a date-only task date-only', () => {
    expect(presets(now, '2026-10-09', zone, { laterToday: false }).map((x) => x.value)).toEqual(['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-05', null])
  })
})

describe('typed dates', () => {
  it('reads the grammar\'s date and time words, and nothing else', () => {
    expect(parseWhen('fri 3p', now, zone)).toBe(join('2026-10-02', '15:00', zone))
    expect(parseWhen('tom', now, zone)).toBe('2026-10-02')
    expect(parseWhen('next week', now, zone)).toBe('2026-10-05')
    expect(parseWhen('no date', now, zone)).toBeNull()
    expect(parseWhen('call fri', now, zone)).toBeUndefined()
    expect(parseWhen('', now, zone)).toBeUndefined()
  })
  it('keeps the old time when only a day is typed', () => {
    expect(parseWhen('fri', now, zone, '09:15')).toBe(join('2026-10-02', '09:15', zone))
  })
  it('reads a time typed alone', () => {
    expect(['3p', '15', '1530', '3:30 pm', 'noon', '25', '13pm'].map(parseTime)).toEqual(['15:00', '15:00', '15:30', '15:30', '12:00', null, null])
  })
  it('describes the result', () => {
    expect(describeWhen('2026-10-02', now, prefs)).toBe('Fri 2 Oct · tomorrow')
    expect(describeWhen(join('2026-10-05', '15:00', zone), now, prefs)).toBe('Mon 5 Oct, 3:00 PM · in 4 days')
    expect(describeWhen(null, now, prefs)).toBe('No date')
  })
})

describe('optimistic copies and per-field undo', () => {
  const t: TaskItem = { uid: 'a', summary: 'A', status: 'needs-action', due: '2026-10-02', priority: 5, categories: ['x'], rrule: null }
  it('applies a PATCH body locally', () => {
    expect(applyLocal(t, { completed: true }).status).toBe('completed')
    expect(applyLocal(t, { clear_due: true }).due).toBeNull()
    expect(applyLocal(t, { due: '2026-10-05T15:00:00Z' }).due_all_day).toBe(false)
    expect(applyLocal(t, { recurrence: 'FREQ=DAILY' })).toMatchObject({ rrule: 'FREQ=DAILY', recurring: true })
  })
  it('builds the body that puts each field back', () => {
    expect(undoBody(t, { due: '2026-10-09' })).toEqual({ due: '2026-10-02' })
    expect(undoBody({ ...t, due: null }, { due: '2026-10-09' })).toEqual({ clear_due: true })
    expect(undoBody(t, { categories: ['x', 'y'], priority: 1 })).toEqual({ categories: ['x'], priority: 5 })
    expect(undoBody(t, { recurrence: 'FREQ=DAILY' })).toEqual({ recurrence: '' })
    expect(undoBody(t, { completed: true })).toEqual({ completed: false })
  })
})

describe('the series question for a repeating task (ruleAfterMove, as Android TaskQueries)', () => {
  // Mon 5 Oct 2026 → Wed 7 Oct.
  it('"This occurrence" pins a weekly rule to the old weekday; a rule with its own day stays', () => {
    expect(ruleAfterMove('FREQ=WEEKLY', '2026-10-05', '2026-10-07', 'this', zone)).toBe('FREQ=WEEKLY;BYDAY=MO')
    expect(ruleAfterMove('FREQ=WEEKLY;BYDAY=MO', '2026-10-05', '2026-10-07', 'this', zone)).toBeNull()
  })
  it('"All" moves a single BYDAY or BYMONTHDAY that matched the old day', () => {
    expect(ruleAfterMove('FREQ=WEEKLY;BYDAY=MO', '2026-10-05', '2026-10-07', 'all', zone)).toBe('FREQ=WEEKLY;BYDAY=WE')
    expect(ruleAfterMove('FREQ=WEEKLY;BYDAY=MO,TH', '2026-10-05', '2026-10-07', 'all', zone)).toBeNull()
    expect(ruleAfterMove('FREQ=MONTHLY;BYMONTHDAY=5', '2026-10-05', '2026-10-07', 'all', zone)).toBe('FREQ=MONTHLY;BYMONTHDAY=7')
    expect(ruleAfterMove('FREQ=MONTHLY', '2026-10-05', '2026-10-07', 'this', zone)).toBe('FREQ=MONTHLY;BYMONTHDAY=5')
    expect(ruleAfterMove('FREQ=DAILY', '2026-10-05', '2026-10-07', 'all', zone)).toBeNull()
  })
  it('reads timed dues in the zone and asks only when the due of a real series moves', () => {
    // 22:30 Chicago on Monday is Tuesday in UTC.
    expect(ruleAfterMove('FREQ=WEEKLY', '2026-10-06T03:30:00Z', '2026-10-07T03:30:00Z', 'this', zone)).toBe('FREQ=WEEKLY;BYDAY=MO')
    expect(needsDueScope({ rrule: 'FREQ=WEEKLY', due: '2026-10-05' }, '2026-10-07')).toBe(true)
    expect(needsDueScope({ rrule: 'FREQ=WEEKLY;COUNT=1', due: '2026-10-05' }, '2026-10-07')).toBe(false)
    expect(needsDueScope({ rrule: null, due: '2026-10-05' }, '2026-10-07')).toBe(false)
    expect(needsDueScope({ rrule: 'FREQ=WEEKLY', due: '2026-10-05' }, null)).toBe(false)
  })
})
