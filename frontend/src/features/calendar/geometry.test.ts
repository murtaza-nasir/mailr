import { describe, expect, it } from 'vitest'
import { contrast, displayColour, normalizeColour, swatchFor, textOn, SWATCHES } from './colour'
import {
  dragResult, formatDuration, gridInstant, isoWeek, keyboardDrag, layoutBars, moveDays, needsScope, overlapping, parseTimeText,
  periodTitle, resolveScope, rubberBand, shiftDate, singleOccurrence, snap, viewDays, viewSpan, wallString,
  type ViewOptions,
} from './geometry'

const o: ViewOptions = { weekStart: 1, ndays: 3, hideWeekends: false, workDays: [1, 2, 3, 4, 5] }

describe('view days and periods', () => {
  it('week, work week, n days and day', () => {
    expect(viewDays('week', '2026-10-01', o)).toEqual(['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04'])
    expect(viewDays('week', '2026-10-01', { ...o, weekStart: 0 })[0]).toBe('2026-09-27')
    expect(viewDays('workweek', '2026-10-01', o)).toEqual(['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02'])
    expect(viewDays('ndays', '2026-10-01', o)).toEqual(['2026-10-01', '2026-10-02', '2026-10-03'])
    expect(viewDays('day', '2026-10-01', o)).toEqual(['2026-10-01'])
  })

  it('hide weekends drops Saturday and Sunday but loads the whole week', () => {
    const days = viewDays('week', '2026-10-01', { ...o, hideWeekends: true })
    expect(days).toHaveLength(5)
    expect(viewSpan('week', '2026-10-01', { ...o, hideWeekends: true })).toEqual({ first: '2026-09-28', last: '2026-10-04' })
  })

  it('a month grid is whole weeks: 5 rows when the month fits, else 6', () => {
    expect(viewDays('month', '2026-10-15', o)).toHaveLength(35)
    expect(viewDays('month', '2026-08-15', o)).toHaveLength(42)
    expect(viewDays('month', '2026-02-15', { ...o, weekStart: 0 })).toHaveLength(28)
  })

  it('moves by the period', () => {
    expect(shiftDate('week', '2026-10-01', 1, o)).toBe('2026-10-08')
    expect(shiftDate('ndays', '2026-10-01', -1, o)).toBe('2026-09-28')
    expect(shiftDate('month', '2026-01-31', 1, o)).toBe('2026-02-01')
    expect(shiftDate('day', '2026-10-31', 1, o)).toBe('2026-11-01')
  })

  it('titles', () => {
    expect(periodTitle('week', '2026-10-01', viewDays('week', '2026-10-01', o))).toBe('28 Sep – 4 Oct 2026')
    expect(periodTitle('ndays', '2026-10-05', viewDays('ndays', '2026-10-05', o))).toBe('5 – 7 Oct 2026')
    expect(periodTitle('day', '2026-10-01', ['2026-10-01'])).toBe('Thu 1 Oct 2026')
    expect(periodTitle('month', '2026-10-01', [])).toBe('October 2026')
    expect(periodTitle('week', '2026-12-30', viewDays('week', '2026-12-30', o))).toBe('28 Dec 2026 – 3 Jan 2027')
  })

  it('ISO week numbers', () => {
    expect(isoWeek('2026-10-01')).toBe(40)
    expect(isoWeek('2027-01-01')).toBe(53)
    expect(isoWeek('2026-01-01')).toBe(1)
  })
})

describe('drag snapping', () => {
  it('snaps to the step', () => {
    expect(snap(37, 15)).toBe(30)
    expect(snap(38, 15)).toBe(45)
    expect(snap(38, 5)).toBe(40)
  })

  it('a rubber band covers press to release, at least one step', () => {
    expect(rubberBand(9 * 60 + 7, 10 * 60 + 20, 15)).toEqual({ start: 540, end: 615 })
    expect(rubberBand(600, 602, 15)).toEqual({ start: 600, end: 615 })
    // Dragging upwards.
    expect(rubberBand(600, 530, 15)).toEqual({ start: 525, end: 615 })
  })

  it('a move keeps the duration and snaps the start; columns switch day', () => {
    expect(dragResult('move', { day: 1, start: 540, end: 600 }, 22, 1, 15, 7)).toEqual({ day: 2, start: 555, end: 615 })
    expect(dragResult('move', { day: 0, start: 60, end: 120 }, -200, -1, 15, 7)).toEqual({ day: 0, start: 0, end: 60 })
  })

  it('a resize keeps at least five minutes', () => {
    expect(dragResult('resize-end', { day: 0, start: 540, end: 600 }, 31, 0, 15, 1)).toEqual({ day: 0, start: 540, end: 630 })
    expect(dragResult('resize-end', { day: 0, start: 540, end: 600 }, -120, 0, 15, 1).end).toBe(545)
    expect(dragResult('resize-start', { day: 0, start: 540, end: 600 }, 120, 0, 15, 1).start).toBe(595)
  })

  it('keyboard drag: Shift+arrows move by the snap or a day; Alt+Shift resizes', () => {
    const s = { day: 2, start: 540, end: 600 }
    expect(keyboardDrag(s, 'down', false, 15, 7)).toEqual({ day: 2, start: 555, end: 615 })
    expect(keyboardDrag(s, 'left', false, 15, 7)).toEqual({ day: 1, start: 540, end: 600 })
    expect(keyboardDrag(s, 'right', false, 15, 3)).toEqual({ day: 2, start: 540, end: 600 })
    expect(keyboardDrag(s, 'up', true, 15, 7)).toEqual({ day: 2, start: 540, end: 585 })
  })

  it('grid instants follow the wall clock across DST', () => {
    // 9:00 New York on the day after the November change is 14:00 UTC (EST).
    expect(gridInstant('2026-11-02', 540, 'America/New_York').toISOString()).toBe('2026-11-02T14:00:00.000Z')
    expect(gridInstant('2026-10-30', 540, 'America/New_York').toISOString()).toBe('2026-10-30T13:00:00.000Z')
    expect(gridInstant('2026-10-01', 1500, 'UTC').toISOString()).toBe('2026-10-02T01:00:00.000Z')
    expect(wallString(new Date('2026-10-01T15:30:00Z'), 'Europe/Lisbon')).toBe('2026-10-01T16:30')
  })
})

describe('the series question (this event, this and following, all)', () => {
  it('asks only for a series with more than one occurrence and a non-rule change', () => {
    expect(needsScope({ recurring: false })).toBe(false)
    expect(needsScope({ recurring: true })).toBe(true)
    expect(needsScope({ recurring: true, single: true })).toBe(false)
    expect(needsScope({ recurring: true, ruleChange: true })).toBe(false)
    expect(singleOccurrence('FREQ=DAILY;COUNT=1')).toBe(true)
    expect(singleOccurrence('FREQ=DAILY;COUNT=10')).toBe(false)
  })

  it('"following" from the first occurrence means all', () => {
    expect(resolveScope('following', true)).toBe('all')
    expect(resolveScope('following', false)).toBe('following')
    expect(resolveScope('this', true)).toBe('this')
  })
})

describe('typed times (the when picker)', () => {
  it.each([
    ['3p', 900], ['3pm', 900], ['3:30 pm', 930], ['15', 900], ['1530', 930], ['930', 570], ['noon', 720],
    ['12am', 0], ['12pm', 720], ['9a', 540], ['midnight', 0], ['15:45', 945],
  ])('%s', (text, want) => expect(parseTimeText(text)).toBe(want))

  it.each(['25', '13pm', 'abc', '9:75', ''])('rejects %s', (t) => expect(parseTimeText(t)).toBeNull())

  it('durations read as the spec writes them', () => {
    expect(formatDuration(90)).toBe('1 h 30 min')
    expect(formatDuration(45)).toBe('45 min')
    expect(formatDuration(120)).toBe('2 h')
    expect(formatDuration(1440)).toBe('1 day')
  })
})

describe('overlaps and bars', () => {
  const at = (h: number) => new Date(Date.UTC(2026, 9, 1, h))
  it('busy, non-declined, timed events overlap', () => {
    const items = [
      { key: 'a', summary: 'A', start: at(14), end: at(15) },
      { key: 'b', summary: 'B', start: at(15), end: at(16) },
      { key: 'c', summary: 'C', start: at(14), end: at(16), declined: true },
      { key: 'd', summary: 'D', start: at(14), end: at(16), transparent: true },
      { key: 'e', summary: 'E', start: at(0), end: at(23), allDay: true },
    ]
    expect(overlapping(items, at(14), at(15)).map((b) => b.key)).toEqual(['a'])
    expect(overlapping(items, at(14), at(16), 'a').map((b) => b.key)).toEqual(['b'])
  })

  it('bars pack into lanes and clip at the row edges', () => {
    const days = ['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']
    const bars = layoutBars([
      { key: 'trip', first: '2026-09-26', last: '2026-09-30', allDay: true, start: 0 },
      { key: 'one', first: '2026-09-29', last: '2026-09-29', allDay: true, start: 0 },
      { key: 'two', first: '2026-10-01', last: '2026-10-06', allDay: true, start: 0 },
    ], days)
    expect(bars.find((b) => b.key === 'trip')).toMatchObject({ lane: 0, from: 0, to: 2, clippedStart: true, clippedEnd: false })
    expect(bars.find((b) => b.key === 'one')).toMatchObject({ lane: 1, from: 1, to: 1 })
    expect(bars.find((b) => b.key === 'two')).toMatchObject({ lane: 0, from: 3, to: 6, clippedEnd: true })
  })
})

describe('colours', () => {
  it('twelve named swatches, each found by either variant', () => {
    expect(SWATCHES).toHaveLength(12)
    expect(swatchFor('#F6BF26')?.name).toBe('Banana')
    expect(swatchFor('#7986cb')?.name).toBe('Lavender')
    expect(displayColour('#0b8043', true)).toBe('#3fa36d')
    expect(normalizeColour('teal')).toBe('#008080')
    expect(normalizeColour('#abc')).toBe('#aabbcc')
  })

  it('text contrast is computed: dark on light fills, white on dark ones', () => {
    expect(textOn('#f6bf26')).toBe('#1f1f1f')
    expect(textOn('#0b8043')).toBe('#ffffff')
    expect(textOn('#3f51b5')).toBe('#ffffff')
    for (const s of SWATCHES) for (const c of [s.light, s.dark]) expect(contrast(c, textOn(c))).toBeGreaterThanOrEqual(3)
  })
})

describe('moving a timed event by days', () => {
  it('keeps the wall-clock time and the length across a daylight-saving change', () => {
    const zone = 'America/Chicago'
    // 9:00 CDT on 29 Oct 2026 (14:00 UTC), one hour; a week later it is CST.
    const start = new Date('2026-10-29T14:00:00Z')
    const end = new Date('2026-10-29T15:00:00Z')
    const to = moveDays(start, end, '2026-10-29', 7, zone)
    expect(wallString(to.start, zone)).toBe('2026-11-05T09:00')
    expect(to.end.getTime() - to.start.getTime()).toBe(3_600_000)
    const back = moveDays(to.start, to.end, '2026-11-05', -7, zone)
    expect(back.start.toISOString()).toBe(start.toISOString())
  })
})
