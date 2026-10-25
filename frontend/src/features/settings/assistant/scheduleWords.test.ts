import { describe, expect, it } from 'vitest'
import { scheduleWords } from './scheduleWords'

describe('scheduleWords', () => {
  it('says the usual schedules in words', () => {
    expect(scheduleWords('0 8 * * 1-5')).toMatch(/^Weekdays at 8:00/)
    expect(scheduleWords('30 6 * * *')).toMatch(/^Every day at 6:30/)
    expect(scheduleWords('0 9 * * 6,0')).toMatch(/^Weekends at 9:00/)
    expect(scheduleWords('0 9 * * 1,4')).toMatch(/^Monday and Thursday at 9:00/)
    expect(scheduleWords('0 9 1 * *')).toMatch(/^On day 1 of each month at 9:00/)
  })
  it('falls back for what the words cannot say', () => {
    expect(scheduleWords('*/15 * * * *')).toBe('Custom schedule')
    expect(scheduleWords('0 9 * 1 *')).toBe('Custom schedule')
  })
})
