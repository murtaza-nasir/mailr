import { describe, expect, it } from 'vitest'
import { findSlots, rangeDays } from './findtime'

const at = (d: string, hm: string) => new Date(`${d}T${hm}:00Z`)

describe('find a time (client ranking)', () => {
  const base = {
    zone: 'UTC', workStart: 9 * 60, workEnd: 17 * 60, workDays: [1, 2, 3, 4, 5], duration: 60, buffer: 0,
    now: at('2026-10-05', '00:00'),
  }

  it('skips busy time and ranks the nearest first, avoiding the first hour', () => {
    const slots = findSlots({ ...base, days: ['2026-10-05'], busy: [{ start: at('2026-10-05', '10:00'), end: at('2026-10-05', '12:00') }] })
    expect(slots[0].start.toISOString()).toBe('2026-10-05T12:00:00.000Z')
    expect(slots.every((s) => s.start < at('2026-10-05', '10:00') ? s.end <= at('2026-10-05', '10:00') : s.start >= at('2026-10-05', '12:00'))).toBe(true)
  })

  it('spreads over days and stops at the limit', () => {
    const slots = findSlots({ ...base, days: ['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09'], busy: [] })
    expect(slots).toHaveLength(8)
    expect(new Set(slots.slice(0, 5).map((s) => s.start.toISOString().slice(0, 10))).size).toBe(3)
    expect(slots.filter((s) => s.start.toISOString().startsWith('2026-10-05'))).toHaveLength(2)
  })

  it('a buffer widens busy blocks; weekends and the past are skipped', () => {
    const slots = findSlots({ ...base, buffer: 15, days: ['2026-10-05'], now: at('2026-10-05', '13:00'), busy: [{ start: at('2026-10-05', '14:00'), end: at('2026-10-05', '15:00') }] })
    for (const s of slots) {
      expect(s.start >= at('2026-10-05', '13:00')).toBe(true)
      expect(s.end <= at('2026-10-05', '13:45') || s.start >= at('2026-10-05', '15:15')).toBe(true)
    }
    expect(findSlots({ ...base, days: ['2026-10-10'], busy: [] })).toEqual([])
  })

  it('ranges', () => {
    expect(rangeDays('next5', '2026-10-02', 1, [1, 2, 3, 4, 5])).toEqual(['2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08'])
    expect(rangeDays('this', '2026-10-01', 1, [1, 2, 3, 4, 5])[0]).toBe('2026-10-01')
    expect(rangeDays('next', '2026-10-01', 1, [1, 2, 3, 4, 5])[0]).toBe('2026-10-05')
  })
})
