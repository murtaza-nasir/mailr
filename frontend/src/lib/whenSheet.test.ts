// ACT-SURFACES: web.sheet
// The when sheet's presets: ACT task.snooze,
// ACT task.reschedule, ACT event.snooze, ACT event.reschedule, ACT event.remind_before.
;(globalThis as unknown as { process: { env: Record<string, string> } }).process.env.TZ = 'America/Chicago'

import { afterEach, describe, expect, it } from 'vitest'
import durationText from '../../../spec/cases/duration-cases.yaml?raw'
import durationAndroid from '../../../android/app/src/test/resources/interaction/duration-cases.yaml?raw'
import { readFlowYaml } from './flowYaml'
import {
  customResult, defaultBase, nearest, UNITS, hasBaseToggle, overdueText, relative, rememberBase, rememberedBase, resultLine, sheetGroups, verbHint,
  type SheetSpec,
} from './whenSheet'

// A small localStorage for the node environment (the base is remembered per verb).
if (typeof globalThis.localStorage === 'undefined' || typeof globalThis.localStorage?.clear !== 'function') {
  const m = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), clear: () => m.clear() },
  })
}

// 1 Oct 2026 is a Thursday. Local time in America/Chicago (DST ends Sun 1 Nov 2026).
const t = (mo: number, d: number, h: number, m = 0) => new Date(2026, mo - 1, d, h, m, 0, 0)
const ids = (s: SheetSpec) => sheetGroups(s).map((g) => `${g.id}: ${g.tiles.map((x) => x.id).join(' ')}`)
const tile = (s: SheetSpec, id: string) => sheetGroups(s).flatMap((g) => g.tiles).find((x) => x.id === id)

describe('the when sheet presets', () => {
  it('task.snooze: Later, Same time from the original due time, Next', () => {
    // "Record the class", due today 12:00 PM, now 4:00 PM (overdue 4 h).
    const s: SheetSpec = { verb: 'snooze', kind: 'task', now: t(10, 1, 16), anchor: t(10, 1, 12), morningHour: 8 }
    expect(ids(s)).toEqual([
      'later: 10m 60m 180m evening',
      'same: same-tomorrow same-week same-month',
      'next: morning monday weekend',
    ])
    expect(sheetGroups(s).map((g) => g.label)).toEqual(['Later', 'Same time', 'Next'])
    expect(tile(s, 'same-tomorrow')!.at).toEqual(t(10, 2, 12))
    expect(tile(s, 'same-week')!.at).toEqual(t(10, 8, 12))
    expect(tile(s, 'same-month')!.at).toEqual(t(11, 1, 12))
    expect(tile(s, 'morning')!.at).toEqual(t(10, 2, 8))
    expect(tile(s, 'monday')!.at).toEqual(t(10, 5, 8))
    expect(tile(s, 'weekend')!.label).toBe('This weekend')
    expect(tile(s, 'weekend')!.at).toEqual(t(10, 3, 8))
    expect(tile(s, '60m')!.label).toBe('In 1 hour')
    expect(tile(s, '180m')!.label).toBe('In 3 hours')
  })

  it('hides presets in the past and duplicates', () => {
    // 5:30 PM: This evening is gone; a due at 8:00 AM makes Same time tomorrow equal Tomorrow morning.
    const s: SheetSpec = { verb: 'snooze', kind: 'task', now: t(10, 1, 17, 30), anchor: t(10, 1, 8), morningHour: 8 }
    expect(ids(s)).toEqual(['later: 10m 60m 180m', 'same: same-tomorrow same-week same-month', 'next: monday weekend'])
    // Event snooze when the start is 1 minute away: At start time is left out.
    expect(ids({ verb: 'snooze', kind: 'event', now: t(10, 1, 10), anchor: t(10, 1, 10, 1) })).toEqual(['later: 5m 10m'])
  })

  it('a task without a due, or date-only when snoozing, has no Same time', () => {
    expect(ids({ verb: 'snooze', kind: 'task', now: t(10, 1, 10), anchor: null })).toEqual(['later: 10m 60m 180m evening', 'next: morning monday weekend'])
    expect(ids({ verb: 'snooze', kind: 'task', now: t(10, 1, 10), anchor: t(10, 1, 9), dateOnly: true })).not.toContain('same')
  })

  it('task.reschedule: date-only stays date-only for Same time and Next', () => {
    const s: SheetSpec = { verb: 'reschedule', kind: 'task', now: t(10, 1, 10), anchor: t(9, 29, 9), dateOnly: true, morningHour: 9 }
    expect(ids(s)).toEqual(['later: 60m 180m evening', 'same: same-tomorrow same-week same-month', 'next: monday weekend'])
    expect(tile(s, 'same-tomorrow')!.dateOnly).toBe(true)
    expect(tile(s, '60m')!.dateOnly).toBe(false)
  })

  it('weekends: Next weekend on Saturday and Sunday', () => {
    expect(tile({ verb: 'snooze', kind: 'task', now: t(10, 3, 10), anchor: null }, 'weekend')!.label).toBe('Next weekend')
    expect(tile({ verb: 'snooze', kind: 'task', now: t(10, 3, 10), anchor: null }, 'weekend')!.at).toEqual(t(10, 10, 9))
  })

  it('event.snooze and event.reschedule', () => {
    expect(ids({ verb: 'snooze', kind: 'event', now: t(10, 1, 10), anchor: t(10, 1, 10, 30) })).toEqual(['later: 5m 10m start'])
    expect(ids({ verb: 'snooze', kind: 'event', now: t(10, 1, 10), anchor: t(10, 1, 9) })).toEqual(['later: 5m 15m'])
    const r: SheetSpec = { verb: 'reschedule', kind: 'event', now: t(10, 1, 10), anchor: t(10, 1, 15) }
    expect(tile(r, 'p15')!.at).toEqual(t(10, 1, 15, 15))
    expect(tile(r, 'same-week')!.at).toEqual(t(10, 8, 15))
  })

  it('event.remind_before: only offsets ahead, with the minutes', () => {
    const g = sheetGroups({ verb: 'remind', kind: 'event', now: t(10, 1, 10), anchor: t(10, 1, 10, 40) })
    expect(g.map((x) => x.label)).toEqual(['Before it starts'])
    expect(g[0].tiles.map((x) => x.minutes)).toEqual([0, 5, 10, 15, 30])
  })

  it('Same time next month on the 31st is the last day of the next month', () => {
    expect(tile({ verb: 'snooze', kind: 'task', now: t(10, 31, 8), anchor: t(10, 30, 14) }, 'same-month')!.at).toEqual(t(11, 30, 14))
  })
})

describe('custom: From now | From due time', () => {
  afterEach(() => localStorage.clear())

  it('counts from now or from the due', () => {
    const s: SheetSpec = { verb: 'reschedule', kind: 'task', now: t(10, 1, 16, 7), anchor: t(10, 1, 12) }
    expect(customResult(s, 'due', 8, 'days').at).toEqual(t(10, 9, 12))
    expect(customResult(s, 'now', 8, 'days').at).toEqual(t(10, 9, 16, 7))
    expect(customResult(s, 'now', 3, 'hours').at).toEqual(t(10, 1, 19, 7))
    expect(resultLine(customResult(s, 'due', 8, 'days').at, s.now)).toMatch(/^→ Fri, Oct 9, 12:00 PM · in 8 days$|^→ Fri 9 Oct, 12:00 PM · in 8 days$/)
  })

  it('defaults: From now for a snooze, From due time for a reschedule; no toggle for event snooze or remind', () => {
    const base = { kind: 'task' as const, now: t(10, 1, 10), anchor: t(10, 1, 9) }
    expect(defaultBase({ ...base, verb: 'snooze' })).toBe('now')
    expect(defaultBase({ ...base, verb: 'reschedule' })).toBe('due')
    expect(hasBaseToggle({ ...base, verb: 'snooze', kind: 'event' })).toBe(false)
    expect(hasBaseToggle({ ...base, verb: 'remind', kind: 'event' })).toBe(false)
    expect(hasBaseToggle({ ...base, verb: 'reschedule', anchor: null })).toBe(false)
    expect(customResult({ ...base, verb: 'snooze', kind: 'event' }, 'due', 10, 'minutes').at).toEqual(t(10, 1, 10, 10))
  })

  it('remembers the last choice per verb', () => {
    const s: SheetSpec = { verb: 'snooze', kind: 'task', now: t(10, 1, 10), anchor: t(10, 1, 9) }
    expect(rememberedBase(s)).toBe('now')
    rememberBase('snooze', 'due')
    expect(rememberedBase(s)).toBe('due')
    expect(rememberedBase({ ...s, verb: 'reschedule' })).toBe('due')
    rememberBase('reschedule', 'now')
    expect(rememberedBase({ ...s, verb: 'reschedule' })).toBe('now')
  })

  it('date-only tasks stay date-only for days and weeks', () => {
    const s: SheetSpec = { verb: 'reschedule', kind: 'task', now: t(10, 1, 10), anchor: t(10, 1, 9), dateOnly: true }
    expect(customResult(s, 'due', 2, 'weeks')).toEqual({ at: t(10, 15, 9), dateOnly: true })
    expect(customResult(s, 'due', 2, 'hours').dateOnly).toBe(false)
  })

  it('remind before counts back from the start', () => {
    const s: SheetSpec = { verb: 'remind', kind: 'event', now: t(10, 1, 10), anchor: t(10, 2, 15) }
    expect(customResult(s, 'now', 2, 'hours').at).toEqual(t(10, 2, 13))
  })

  it('DST: days keep the time of day, hours are exact (fall back on 1 Nov 2026)', () => {
    const s: SheetSpec = { verb: 'reschedule', kind: 'task', now: t(10, 31, 12), anchor: t(10, 31, 12) }
    expect(customResult(s, 'due', 1, 'days').at).toEqual(t(11, 1, 12))
    expect(customResult(s, 'due', 24, 'hours').at).toEqual(t(11, 1, 11))
    expect(tile({ ...s, verb: 'snooze' }, 'same-tomorrow')!.at).toEqual(t(11, 1, 12))
  })

  it('a result in the past reads "ago"; the overdue badge', () => {
    expect(relative(t(9, 29, 12), t(10, 1, 16))).toBe('2 days ago')
    expect(relative(t(10, 1, 19, 20), t(10, 1, 16))).toBe('in 3 h 20 min')
    // The custom result counts from the current minute: 12 hours from 4:00:42 PM reads "in 12 h" (it read "in 11 h 59 min").
    expect(relative(t(10, 2, 4), new Date(2026, 9, 1, 16, 0, 42))).toBe('in 12 h')
    expect(overdueText(t(10, 1, 12), t(10, 1, 16))).toBe('Overdue 4 h')
    expect(overdueText(t(10, 1, 15, 35), t(10, 1, 16))).toBe('Overdue 25 min')
    expect(overdueText(t(9, 29, 0), t(10, 1, 16), true)).toBe('Overdue 2 d')
    expect(overdueText(t(10, 1, 17), t(10, 1, 16))).toBeNull()
  })

  it('says what the verb changes', () => {
    const s: SheetSpec = { verb: 'snooze', kind: 'task', now: t(10, 1, 16), anchor: t(10, 1, 12) }
    expect(verbHint(s, 'today 12:00 PM')).toBe('Only the reminder moves. The task stays due today 12:00 PM.')
    expect(verbHint({ ...s, verb: 'reschedule' }, null)).toBe('The due moves. Its reminders follow.')
  })
})

describe('duration-cases.yaml (durations)', () => {
  const file = readFlowYaml(durationText)
  it('has the unit lists, and the Android copy matches', () => {
    expect(durationAndroid).toBe(durationText)
    expect(Object.fromEntries(UNITS.map((u) => [u.id, u.amounts]))).toEqual(file.units)
    // Every hour from 1 to 23 is reachable on the hours list, 2 and 3 included.
    expect(UNITS.find((u) => u.id === 'hours')!.amounts).toEqual(Array.from({ length: 23 }, (_, i) => i + 1))
  })
  for (const c of file.cases) {
    it(`${c.id}`, () => {
      expect(nearest(c.to as 'minutes', c.amount as number)).toBe(c.expect)
    })
  }
})
