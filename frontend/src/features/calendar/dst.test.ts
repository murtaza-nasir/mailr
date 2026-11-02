// ACT-SURFACES: web.calendar web.panel web.editor
// Daylight-saving days on the time grid, the Today line, the editor and the calendar panel, with
// fixed instants (nothing here is compared with the real now). The autumn case is the one seen on
// 1 Nov 2026: 06:42Z–07:27Z in America/Chicago reads 01:42 CDT → 01:27 CST. vitest.config.ts runs the
// process in America/Chicago, so nextHalfHour (the browser's own clock) is tested in that zone.
import { describe, expect, it } from 'vitest'
import { isLaterPass, minutesOfDay, minutesOnDay } from '../../lib/dates'
import { layoutDay } from '../../lib/eventLayout'
import { normalizeEvent } from './data'
import { formTimes, initialForm } from './EventEditor'
import { joinable, nextMeeting } from './eventInfo'
import { daySegments, gridInstant, spanInstants } from './geometry'
import { nextHalfHour } from './halfHour'
import type { EditorInit } from './store'
import { dayLineBlocks } from './today'
import type { ApiEvent, CalEvent } from './types'

const CHI = 'America/Chicago'
const LON = 'Europe/London'
const at = (iso: string) => new Date(iso)
const span = (s: string, e: string, day: string, zone: string) => minutesOnDay(at(s), at(e), day, zone)

// The cases each consumer is checked with: [start, end, day, zone, minutes on that day].
const CASES: Array<[string, string, string, string, string, { start: number; end: number }]> = [
  ['autumn, the repeated hour (Chicago)', '2026-11-01T06:42:00Z', '2026-11-01T07:27:00Z', '2026-11-01', CHI, { start: 102, end: 147 }],
  ['spring 2026, across the gap (Chicago)', '2026-03-08T07:30:00Z', '2026-03-08T08:30:00Z', '2026-03-08', CHI, { start: 90, end: 210 }],
  ['spring 2027, across the gap (Chicago)', '2027-03-14T07:45:00Z', '2027-03-14T08:15:00Z', '2027-03-14', CHI, { start: 105, end: 195 }],
  ['a normal day (Chicago)', '2026-10-14T14:00:00Z', '2026-10-14T15:00:00Z', '2026-10-14', CHI, { start: 540, end: 600 }],
  ['autumn, the repeated hour (London)', '2026-10-25T00:30:00Z', '2026-10-25T01:10:00Z', '2026-10-25', LON, { start: 90, end: 130 }],
  ['spring, across the gap (London)', '2026-03-29T00:30:00Z', '2026-03-29T01:30:00Z', '2026-03-29', LON, { start: 30, end: 150 }],
  ['a normal day (London)', '2026-10-14T08:00:00Z', '2026-10-14T09:15:00Z', '2026-10-14', LON, { start: 540, end: 615 }],
]

describe('minutesOnDay', () => {
  it.each(CASES)('%s', (_name, s, e, day, zone, want) => {
    expect(span(s, e, day, zone)).toEqual(want)
  })

  it('the clock end of the autumn case comes before its start; the helper keeps the 45 minutes', () => {
    expect(minutesOfDay(at('2026-11-01T06:42:00Z'), CHI)).toBe(102)
    expect(minutesOfDay(at('2026-11-01T07:27:00Z'), CHI)).toBe(87)
    const m = span('2026-11-01T06:42:00Z', '2026-11-01T07:27:00Z', '2026-11-01', CHI)!
    expect(m.end - m.start).toBe(45)
  })

  it('a span inside the spring gap ends on its named hour line and is never negative', () => {
    // 01:50 CST → 03:10 CDT: 20 minutes pass, the clock jumps 80.
    expect(span('2026-03-08T07:50:00Z', '2026-03-08T08:10:00Z', '2026-03-08', CHI)).toEqual({ start: 110, end: 190 })
  })

  it('a span that began the day before starts at 0 and keeps its elapsed minutes across the repeated hour', () => {
    // 23:00 CDT on 31 Oct to 01:30 CST on 1 Nov: 150 minutes of 1 Nov pass (the clock says 90).
    expect(span('2026-11-01T04:00:00Z', '2026-11-01T07:30:00Z', '2026-11-01', CHI)).toEqual({ start: 0, end: 150 })
    expect(span('2026-11-01T04:00:00Z', '2026-11-01T07:30:00Z', '2026-10-31', CHI)).toEqual({ start: 1380, end: 1440 })
  })

  it('a span past the day ends at 1440; one that ends at midnight stays on its first day', () => {
    expect(span('2026-10-15T04:00:00Z', '2026-10-15T05:00:00Z', '2026-10-14', CHI)).toEqual({ start: 1380, end: 1440 })
    expect(span('2026-10-15T04:00:00Z', '2026-10-15T05:00:00Z', '2026-10-15', CHI)).toBeNull()
    // The 25-hour autumn day still ends at 1440 on a 24-hour grid.
    expect(span('2026-11-01T05:00:00Z', '2026-11-02T06:00:00Z', '2026-11-01', CHI)).toEqual({ start: 0, end: 1440 })
  })

  it('an empty span belongs to the day it is on; a span on another day is null', () => {
    expect(span('2026-10-15T05:00:00Z', '2026-10-15T05:00:00Z', '2026-10-15', CHI)).toEqual({ start: 0, end: 0 })
    expect(span('2026-10-15T05:00:00Z', '2026-10-15T05:00:00Z', '2026-10-14', CHI)).toBeNull()
    expect(span('2026-10-14T14:00:00Z', '2026-10-14T15:00:00Z', '2026-10-13', CHI)).toBeNull()
  })

  it('an end before the start counts as an empty span', () => {
    expect(span('2026-10-14T15:00:00Z', '2026-10-14T14:00:00Z', '2026-10-14', CHI)).toEqual({ start: 600, end: 600 })
  })
})

describe('the second pass of a repeated wall time', () => {
  it('isLaterPass tells 01:27 CST from 01:27 CDT, and gridInstant gives either', () => {
    expect(isLaterPass(at('2026-11-01T07:27:00Z'), CHI)).toBe(true)
    expect(isLaterPass(at('2026-11-01T06:27:00Z'), CHI)).toBe(false)
    expect(isLaterPass(at('2026-11-01T06:42:00Z'), CHI)).toBe(false)
    expect(isLaterPass(at('2026-03-08T08:30:00Z'), CHI)).toBe(false)
    expect(isLaterPass(at('2026-10-25T01:10:00Z'), LON)).toBe(true)
    expect(isLaterPass(at('2026-10-14T14:00:00Z'), CHI)).toBe(false)
    expect(gridInstant('2026-11-01', 87, CHI).toISOString()).toBe('2026-11-01T06:27:00.000Z')
    expect(gridInstant('2026-11-01', 87, CHI, true).toISOString()).toBe('2026-11-01T07:27:00.000Z')
    // Outside a repeated hour `later` changes nothing; in the spring gap both move forward.
    expect(gridInstant('2026-10-14', 540, CHI, true).toISOString()).toBe('2026-10-14T14:00:00.000Z')
    expect(gridInstant('2026-03-08', 150, CHI, true).toISOString()).toBe(gridInstant('2026-03-08', 150, CHI).toISOString())
  })
})

describe('the time grid (daySegments, layoutDay)', () => {
  it.each(CASES)('%s', (_name, s, e, day, zone, want) => {
    const segs = daySegments([{ id: 'x', start: at(s), end: at(e) }], [day], zone)
    expect(segs).toEqual([{ id: 'x', date: day, ...want, continued: false, continues: false }])
    const placed = layoutDay(segs.map((g) => ({ id: g.id, start: g.start, end: g.end }))).placed[0]
    expect([placed.top, placed.bottom]).toEqual([want.start, want.end])
    expect(placed.compact).toBe(false)
  })

  it('cuts a span across midnight into a continued part', () => {
    const segs = daySegments([{ id: 'x', start: at('2026-11-01T04:00:00Z'), end: at('2026-11-01T07:30:00Z') }], ['2026-10-31', '2026-11-01'], CHI)
    expect(segs).toEqual([
      { id: 'x', date: '2026-10-31', start: 1380, end: 1440, continued: false, continues: true },
      { id: 'x', date: '2026-11-01', start: 0, end: 150, continued: true, continues: false },
    ])
  })
})

describe('a drag lands (spanInstants)', () => {
  it('a move keeps the real length on the autumn day; a resize takes the clock time of the end line', () => {
    const move = spanInstants('2026-11-01', { start: 102, end: 147 }, CHI, true)
    expect([move.start.toISOString(), move.end.toISOString()]).toEqual(['2026-11-01T06:42:00.000Z', '2026-11-01T07:27:00.000Z'])
    const resize = spanInstants('2026-11-01', { start: 102, end: 150 }, CHI, false)
    expect(resize.end.toISOString()).toBe('2026-11-01T08:30:00.000Z') // 02:30 CST
  })
  it('a move and a resize land on the same end on a normal day', () => {
    for (const zone of [CHI, LON]) {
      const a = spanInstants('2026-10-14', { start: 540, end: 615 }, zone, true)
      const b = spanInstants('2026-10-14', { start: 540, end: 615 }, zone, false)
      expect(a.end.toISOString()).toBe(b.end.toISOString())
    }
  })
})

describe('the Today line (dayLineBlocks)', () => {
  it.each(CASES)('%s', (_name, s, e, day, zone, want) => {
    const ev = { startAt: at(s), endAt: at(e) }
    expect(dayLineBlocks([ev], day, zone, at(s), true)).toEqual([{ e: ev, ...want, past: false }])
  })

  it('"past" compares instants: 01:43 CST is after the 01:42 CDT start, before the 01:27 CST end', () => {
    const ev = { startAt: at('2026-11-01T06:42:00Z'), endAt: at('2026-11-01T07:27:00Z') }
    // 01:43 CST (07:43Z) is later on the clock than 01:27 CST, and later as an instant: past.
    expect(dayLineBlocks([ev], '2026-11-01', CHI, at('2026-11-01T07:43:00Z'), true)[0].past).toBe(true)
    // 01:43 CDT (06:43Z): not past, although its clock minute (103) is past the clock end (87).
    expect(dayLineBlocks([ev], '2026-11-01', CHI, at('2026-11-01T06:43:00Z'), true)[0].past).toBe(false)
    // Another day's line dims nothing.
    expect(dayLineBlocks([ev], '2026-11-01', CHI, at('2026-11-02T12:00:00Z'), false)[0].past).toBe(false)
  })
})

describe('the editor (initialForm, formTimes)', () => {
  const defaults = { calendar: '/cal/', duration: 60, now: at('2026-10-05T14:00:00Z') }
  const ev = (start: string, end: string, zone: string) =>
    normalizeEvent({ uid: 'u1', summary: 'Sync', start, end } as ApiEvent, { zone, collections: new Map() })!

  it.each(CASES)('%s: the form gives the same instants back', (_name, s, e, _day, zone) => {
    const f = initialForm({ mode: 'edit', event: ev(s, e, zone) } as EditorInit, zone, defaults)
    const t = formTimes(f)
    expect([(t.start as Date).toISOString(), (t.end as Date).toISOString()]).toEqual([at(s).toISOString(), at(e).toISOString()])
    expect(t.gap).toBeNull()
  })

  it('marks the autumn end as the second pass, so the duration stays 45 minutes', () => {
    const f = initialForm({ mode: 'edit', event: ev('2026-11-01T06:42:00Z', '2026-11-01T07:27:00Z', CHI) } as EditorInit, CHI, defaults)
    expect([f.startMin, f.endMin, f.startLater, f.endLater]).toEqual([102, 87, false, true])
    const t = formTimes(f)
    expect(((t.end as Date).getTime() - (t.start as Date).getTime()) / 60_000).toBe(45)
  })

  it('a new event seeded in the second pass keeps it', () => {
    const f = initialForm({ mode: 'new', seed: { start: at('2026-11-01T07:10:00Z'), end: at('2026-11-01T07:40:00Z') } } as EditorInit, CHI, defaults)
    const t = formTimes(f)
    expect([(t.start as Date).toISOString(), (t.end as Date).toISOString()]).toEqual(['2026-11-01T07:10:00.000Z', '2026-11-01T07:40:00.000Z'])
  })
})

describe('now and next compare instants (joinable, nextMeeting)', () => {
  const ev = { startAt: at('2026-11-01T06:42:00Z'), endAt: at('2026-11-01T07:27:00Z'), status: 'CONFIRMED', url: 'https://meet.jit.si/x' } as unknown as CalEvent

  it('the autumn event is on and joinable at 01:43 CDT, and over at 01:30 CST', () => {
    expect(joinable(ev, at('2026-11-01T06:43:00Z'))).toBe(true)
    expect(nextMeeting([ev], at('2026-11-01T06:43:00Z'))).toBe(ev)
    expect(joinable(ev, at('2026-11-01T07:30:00Z'))).toBe(false)
    expect(nextMeeting([ev], at('2026-11-01T07:30:00Z'))).toBeUndefined()
  })

  it.each(CASES)('%s: on from its start until its end', (_name, s, e) => {
    const x = { ...ev, startAt: at(s), endAt: at(e) } as CalEvent
    const mid = new Date((at(s).getTime() + at(e).getTime()) / 2)
    expect(joinable(x, mid)).toBe(true)
    expect(nextMeeting([x], mid)).toBe(x)
    expect(nextMeeting([x], at(e))).toBeUndefined()
  })

  it('skips a cancelled meeting', () => {
    const c = { ...ev, status: 'CANCELLED' } as CalEvent
    expect(nextMeeting([c, ev], at('2026-11-01T06:43:00Z'))).toBe(ev)
  })
})

describe('the next round half-hour (nextHalfHour, the new event without a slot)', () => {
  it.each([
    ['01:10 CST, the second pass: 01:30 CST, not 01:30 CDT in the past', '2026-11-01T07:10:00Z', '2026-11-01T07:30:00Z'],
    ['01:40:15 CDT, the first pass: 01:00 CST', '2026-11-01T06:40:15Z', '2026-11-01T07:00:00Z'],
    ['01:50 CST before the spring gap: 03:00 CDT', '2026-03-08T07:50:00Z', '2026-03-08T08:00:00Z'],
    ['a normal day, first half', '2026-10-14T14:10:30Z', '2026-10-14T14:30:00Z'],
    ['a normal day, second half', '2026-10-14T14:45:00Z', '2026-10-14T15:00:00Z'],
  ])('%s', (_name, now, want) => {
    const got = nextHalfHour(at(now))
    expect(got.toISOString()).toBe(at(want).toISOString())
    expect(got.getTime()).toBeGreaterThan(at(now).getTime())
  })
})
