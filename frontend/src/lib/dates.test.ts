import { describe, expect, it } from 'vitest'
import {
  addDays, addDaysISO, addMonthsISO, canonicalOccurrence, dayRange, formatDate, formatHour, formatRange, formatTime, fromZoned,
  minutesOfDay, monthGrid, parseISO, parseOccurrence, resolveDatePrefs, startOfDay, startOfWeekISO, toISODate, weekRange,
  workWeekRange, zoneOffset, zoneParts,
} from './dates'

const CHI = 'America/Chicago'
const LON = 'Europe/London'
const H = 3_600_000

describe('zone parts and offsets', () => {
  it('reads the wall clock in a zone', () => {
    const p = zoneParts(new Date('2026-10-01T15:00:00Z'), CHI)
    expect([p.year, p.month, p.day, p.hour, p.minute, p.weekday]).toEqual([2026, 10, 1, 10, 0, 4])
  })
  it('offsets follow DST', () => {
    expect(zoneOffset(new Date('2026-10-31T12:00:00Z'), CHI)).toBe(-300)
    expect(zoneOffset(new Date('2026-11-02T12:00:00Z'), CHI)).toBe(-360)
    expect(zoneOffset(new Date('2026-10-24T12:00:00Z'), LON)).toBe(60)
    expect(zoneOffset(new Date('2026-10-26T12:00:00Z'), LON)).toBe(0)
  })
})

describe('fromZoned at DST edges', () => {
  it('plain wall times', () => {
    expect(fromZoned({ year: 2026, month: 10, day: 1, hour: 9 }, CHI).toISOString()).toBe('2026-10-01T14:00:00.000Z')
    expect(fromZoned({ year: 2026, month: 12, day: 1, hour: 9 }, CHI).toISOString()).toBe('2026-12-01T15:00:00.000Z')
  })
  it('the repeated hour (Chicago, 1 Nov 2026) gives the earlier instant unless asked', () => {
    const w = { year: 2026, month: 11, day: 1, hour: 1, minute: 30 }
    expect(fromZoned(w, CHI).toISOString()).toBe('2026-11-01T06:30:00.000Z')
    expect(fromZoned(w, CHI, 'later').toISOString()).toBe('2026-11-01T07:30:00.000Z')
  })
  it('the skipped hour (Chicago, 8 Mar 2026) moves forward', () => {
    const w = { year: 2026, month: 3, day: 8, hour: 2, minute: 30 }
    expect(zoneParts(fromZoned(w, CHI), CHI).hour).toBe(3)
    expect(zoneParts(fromZoned(w, CHI, 'earlier'), CHI).hour).toBe(1)
  })
  it('London falls back on 25 Oct 2026', () => {
    const w = { year: 2026, month: 10, day: 25, hour: 1, minute: 30 }
    expect(fromZoned(w, LON).toISOString()).toBe('2026-10-25T00:30:00.000Z')
    expect(fromZoned(w, LON, 'later').toISOString()).toBe('2026-10-25T01:30:00.000Z')
  })
})

describe('days in a zone', () => {
  it('startOfDay and dayRange: 25 hours on the fall-back day', () => {
    const r = dayRange('2026-11-01', CHI)
    expect(r.start.toISOString()).toBe('2026-11-01T05:00:00.000Z')
    expect(r.end.toISOString()).toBe('2026-11-02T06:00:00.000Z')
    expect((r.end.getTime() - r.start.getTime()) / H).toBe(25)
    const l = dayRange('2026-10-25', LON)
    expect((l.end.getTime() - l.start.getTime()) / H).toBe(25)
    const spring = dayRange('2026-03-08', CHI)
    expect((spring.end.getTime() - spring.start.getTime()) / H).toBe(23)
    expect(startOfDay(new Date('2026-11-01T20:00:00Z'), CHI).toISOString()).toBe('2026-11-01T05:00:00.000Z')
  })
  it('addDays keeps the wall time across DST', () => {
    const nine = fromZoned({ year: 2026, month: 10, day: 31, hour: 9 }, CHI)
    const next = addDays(nine, 1, CHI)
    expect(next.toISOString()).toBe('2026-11-01T15:00:00.000Z')
    expect(minutesOfDay(next, CHI)).toBe(540)
    expect((next.getTime() - nine.getTime()) / H).toBe(25)
    const lon = addDays(fromZoned({ year: 2026, month: 10, day: 24, hour: 9 }, LON), 7, LON)
    expect(lon.toISOString()).toBe('2026-10-31T09:00:00.000Z')
    expect(addDays(nine, -31, CHI).toISOString()).toBe('2026-09-30T14:00:00.000Z')
  })
  it('the calendar date of an instant depends on the zone', () => {
    const t = new Date('2026-10-02T03:00:00Z')
    expect(toISODate(t, CHI)).toBe('2026-10-01')
    expect(toISODate(t, LON)).toBe('2026-10-02')
  })
  it('plain date arithmetic', () => {
    expect(addDaysISO('2026-12-31', 1)).toBe('2027-01-01')
    expect(addDaysISO('2026-03-01', -1)).toBe('2026-02-28')
    expect(addMonthsISO('2026-01-31', 1)).toBe('2026-02-28')
    expect(addMonthsISO('2028-01-31', 1)).toBe('2028-02-29')
  })
})

describe('weeks', () => {
  it('week start follows the setting', () => {
    expect(startOfWeekISO('2026-10-01', 0)).toBe('2026-09-27')
    expect(startOfWeekISO('2026-10-01', 1)).toBe('2026-09-28')
    expect(startOfWeekISO('2026-10-01', 6)).toBe('2026-09-26')
    expect(startOfWeekISO('2026-09-27', 0)).toBe('2026-09-27')
  })
  it('the DST week in Chicago (Sun 1 Nov 2026) is 169 hours', () => {
    const w = weekRange('2026-11-04', CHI, 0)
    expect(w.days).toEqual(['2026-11-01', '2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06', '2026-11-07'])
    expect(w.start.toISOString()).toBe('2026-11-01T05:00:00.000Z')
    expect(w.end.toISOString()).toBe('2026-11-08T06:00:00.000Z')
    expect((w.end.getTime() - w.start.getTime()) / H).toBe(169)
  })
  it('the DST week in London (Sun 25 Oct 2026), Monday start', () => {
    const w = weekRange(new Date('2026-10-25T12:00:00Z'), LON, 1)
    expect(w.days[0]).toBe('2026-10-19')
    expect(w.start.toISOString()).toBe('2026-10-18T23:00:00.000Z')
    expect(w.end.toISOString()).toBe('2026-10-26T00:00:00.000Z')
    expect((w.end.getTime() - w.start.getTime()) / H).toBe(169)
  })
  it('the work week is Monday to Friday', () => {
    expect(workWeekRange('2026-11-01', CHI).days).toEqual(['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30'])
  })
  it('month grids hold whole weeks', () => {
    const g = monthGrid('2026-10-15', 0)
    expect(g[0][0]).toBe('2026-09-27')
    expect(g[g.length - 1][6]).toBe('2026-10-31')
    expect(g).toHaveLength(5)
    expect(monthGrid('2026-08-01', 0)).toHaveLength(6)
    expect(monthGrid('2026-02-01', 0)).toHaveLength(4)
  })
})

describe('parseISO', () => {
  it('offsets and Z', () => {
    expect(parseISO('2026-10-01T15:00:00+00:00')!.toISOString()).toBe('2026-10-01T15:00:00.000Z')
    expect(parseISO('2026-10-01T10:00:00-05:00')!.toISOString()).toBe('2026-10-01T15:00:00.000Z')
    expect(parseISO('2026-10-01T20:30:00+0530')!.toISOString()).toBe('2026-10-01T15:00:00.000Z')
    expect(parseISO('2026-10-01T15:00:00.250Z')!.toISOString()).toBe('2026-10-01T15:00:00.250Z')
    expect(parseISO('20261001T150000Z')!.toISOString()).toBe('2026-10-01T15:00:00.000Z')
  })
  it('no offset means the zone given; a bare date is its start', () => {
    expect(parseISO('2026-11-01T01:30:00', CHI)!.toISOString()).toBe('2026-11-01T06:30:00.000Z')
    expect(parseISO('2026-10-25', LON)!.toISOString()).toBe('2026-10-24T23:00:00.000Z')
  })
  it('rejects junk', () => {
    expect(parseISO('tomorrow')).toBeNull()
    expect(parseISO('2026-13-01T00:00:00Z')).toBeNull()
    expect(parseISO('')).toBeNull()
  })
})

describe('canonical occurrence', () => {
  it('timed: UTC isoformat with +00:00', () => {
    expect(canonicalOccurrence('2026-10-01T10:00:00-05:00', false)).toBe('2026-10-01T15:00:00+00:00')
    expect(canonicalOccurrence(new Date('2026-10-01T15:00:00.400Z'), false)).toBe('2026-10-01T15:00:00+00:00')
    expect(canonicalOccurrence('2026-11-01T01:30:00', false, CHI)).toBe('2026-11-01T06:30:00+00:00')
  })
  it('all-day: the date, never shifted', () => {
    expect(canonicalOccurrence('2026-10-25', true)).toBe('2026-10-25')
    expect(canonicalOccurrence('2026-10-25T00:00:00+00:00', true)).toBe('2026-10-25')
    expect(canonicalOccurrence('20261025', true)).toBe('2026-10-25')
    expect(canonicalOccurrence(new Date('2026-10-25T03:00:00Z'), true, CHI)).toBe('2026-10-24')
  })
  it('round-trips', () => {
    expect(parseOccurrence('2026-10-25')).toEqual({ allDay: true, date: '2026-10-25' })
    const t = parseOccurrence('2026-10-01T15:00:00+00:00')
    expect(t && !t.allDay && t.instant.toISOString()).toBe('2026-10-01T15:00:00.000Z')
  })
})

describe('formatting', () => {
  const p12 = { zone: CHI, hour12: true }
  const p24 = { zone: CHI, hour12: false }
  const t = new Date('2026-10-06T20:00:00Z') // 3 PM in Chicago
  it('times follow the 12/24-hour setting', () => {
    expect(formatTime(t, p12)).toBe('3:00 PM')
    expect(formatTime(t, p12, { compact: true })).toBe('3 PM')
    expect(formatTime(t, p24)).toBe('15:00')
    expect(formatTime(new Date('2026-10-06T05:05:00Z'), p12)).toBe('12:05 AM')
    expect(formatTime(t, { zone: LON, hour12: false })).toBe('21:00')
    expect(formatHour(0, true)).toBe('12 AM')
    expect(formatHour(13, false)).toBe('13:00')
  })
  it('dates as the spec writes them', () => {
    expect(formatDate('2026-10-06')).toBe('Tue 6 Oct')
    expect(formatDate('2026-10-06', 'long')).toBe('Tuesday, 6 October 2026')
    expect(formatDate('2026-10-06', 'monthYear')).toBe('October 2026')
    expect(formatDate(new Date('2026-10-07T03:00:00Z'), 'day', CHI)).toBe('Tue 6 Oct')
  })
  it('ranges', () => {
    expect(formatRange(t, new Date('2026-10-06T21:00:00Z'), false, p12)).toBe('Tue 6 Oct, 3:00 – 4:00 PM')
    expect(formatRange(new Date('2026-10-06T16:00:00Z'), t, false, p12)).toBe('Tue 6 Oct, 11:00 AM – 3:00 PM')
    expect(formatRange(t, new Date('2026-10-06T21:00:00Z'), false, p24)).toBe('Tue 6 Oct, 15:00 – 16:00')
    expect(formatRange('2026-10-06', '2026-10-07', true, p12)).toBe('Tue 6 Oct (all day)')
    expect(formatRange('2026-10-06', '2026-10-09', true, p12)).toBe('Tue 6 Oct – Thu 8 Oct')
  })
})

describe('preferences', () => {
  it('settings win over browser defaults', () => {
    expect(resolveDatePrefs({ timezone: LON, week_start: 'monday', time_format: '24h' })).toEqual({ zone: LON, weekStart: 1, hour12: false })
    expect(resolveDatePrefs({ week_start: 6, time_format: '12h' })).toMatchObject({ weekStart: 6, hour12: true })
    expect(resolveDatePrefs({ timezone: 'Not/AZone' }).zone).not.toBe('Not/AZone')
  })
})
