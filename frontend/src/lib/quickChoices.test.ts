// ACT-SURFACES: web.sheet
import { describe, expect, it } from 'vitest'
import { eventReschedule, offsetLabel, offsets, roundUp5, taskReschedule } from './quickChoices'

// 1 Oct 2026 is a Thursday. Local time (the presets are in the user's time zone).
const t = (d: number, h: number, m = 0) => new Date(2026, 9, d, h, m, 0, 0)

describe('quick-choice presets (web)', () => {
  it('task.reschedule keeps the time and follows the day rules', () => {
    const c = taskReschedule(t(1, 10, 7), t(1, 16, 30), false)
    expect(c.map((x) => x.label)).toEqual(['Later today', 'Tonight', 'Tomorrow', 'This weekend', 'Next Monday', 'In a week'])
    expect(c.find((x) => x.id === 'tomorrow')!.at).toEqual(t(2, 16, 30))
    expect(c.find((x) => x.id === 'weekend')!.at).toEqual(t(3, 16, 30))
    expect(c.find((x) => x.id === 'later')!.at).toEqual(t(1, 13, 10))
    const fri = taskReschedule(t(2, 20, 30), null, true)
    expect(fri.map((x) => x.label)).toEqual(['Tomorrow', 'Next Monday', 'In a week'])
    expect(fri[0].at).toEqual(t(3, 9))
  })

  it('event.reschedule keeps the time of day', () => {
    expect(eventReschedule(t(1, 15)).map((c) => c.at)).toEqual([t(1, 15, 15), t(1, 16), t(2, 15), t(8, 15)])
  })

  it('event.remind_before offers only what is ahead', () => {
    expect(offsets(t(1, 10), t(1, 10, 40))).toEqual([0, 5, 10, 15, 30])
    expect(offsets(t(1, 10), t(2, 12))).toEqual([0, 5, 10, 15, 30, 60, 120, 1440])
    expect(offsetLabel(120)).toBe('2 hours before')
    expect(roundUp5(t(1, 10, 1))).toEqual(t(1, 10, 5))
  })
})
