import { describe, expect, it } from 'vitest'
import { dueUrgent } from './SignalChips'

describe('dueUrgent', () => {
  const now = new Date(2026, 9, 1, 15, 0) // Thu 1 Oct 2026, afternoon
  it('marks deadlines today, tomorrow, in two days and past ones', () => {
    for (const d of ['2026-09-28', '2026-10-01', '2026-10-02', '2026-10-03']) expect(dueUrgent(d, now)).toBe(true)
  })
  it('leaves later deadlines and missing dates alone', () => {
    expect(dueUrgent('2026-10-04', now)).toBe(false)
    expect(dueUrgent(null, now)).toBe(false)
  })
})
