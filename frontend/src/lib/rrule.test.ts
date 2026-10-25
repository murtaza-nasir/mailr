import { describe, expect, it } from 'vitest'
import { describeRepeat, formatRRule, isEditable, matchSmartOption, nextDates, parseRRule, smartOptions, summarizeRRule } from './rrule'

const TUE = '2026-10-06'

describe('summaries', () => {
  it('the spec examples', () => {
    expect(summarizeRRule('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH;UNTIL=20261215T235959Z', '2026-10-12')).toBe('Every 2 weeks on Mon and Thu, until 15 Dec')
    expect(summarizeRRule('FREQ=WEEKLY;BYDAY=TU', TUE)).toBe('Weekly on Tuesday')
    expect(summarizeRRule('FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR', TUE)).toBe('Every weekday (Mon–Fri)')
    expect(summarizeRRule('FREQ=MONTHLY;BYDAY=1TU', TUE)).toBe('Monthly on the first Tuesday')
    expect(summarizeRRule('FREQ=MONTHLY;BYMONTHDAY=6', TUE)).toBe('Monthly on day 6')
    expect(summarizeRRule('FREQ=YEARLY;BYMONTH=10;BYMONTHDAY=6', TUE)).toBe('Yearly on 6 October')
  })
  it('parts DTSTART implies', () => {
    expect(summarizeRRule('RRULE:FREQ=WEEKLY', TUE)).toBe('Weekly on Tuesday')
    expect(summarizeRRule('FREQ=MONTHLY', TUE)).toBe('Monthly on day 6')
    expect(summarizeRRule('FREQ=YEARLY', TUE)).toBe('Yearly on 6 October')
    expect(summarizeRRule('FREQ=DAILY;INTERVAL=3')).toBe('Every 3 days')
  })
  it('ends, positions and lists', () => {
    expect(summarizeRRule('FREQ=DAILY;COUNT=5')).toBe('Daily, 5 times')
    expect(summarizeRRule('FREQ=MONTHLY;BYDAY=-1FR')).toBe('Monthly on the last Friday')
    expect(summarizeRRule('FREQ=MONTHLY;BYMONTHDAY=-1')).toBe('Monthly on the last day')
    expect(summarizeRRule('FREQ=YEARLY;BYMONTH=11;BYDAY=4TH')).toBe('Yearly on the fourth Thursday of November')
    expect(summarizeRRule('FREQ=WEEKLY;BYDAY=FR,MO,WE', TUE)).toBe('Weekly on Mon, Wed and Fri')
    expect(summarizeRRule('FREQ=WEEKLY;UNTIL=20270110', TUE)).toBe('Weekly on Tuesday, until 10 Jan 2027')
    expect(summarizeRRule('FREQ=MONTHLY;BYDAY=TU;BYSETPOS=1')).toBe('Monthly on the first Tuesday')
    expect(summarizeRRule(null)).toBe('Does not repeat')
  })
  it('rules from other apps stay read-only', () => {
    for (const r of ['FREQ=DAILY;BYHOUR=9,17', 'FREQ=MONTHLY;BYDAY=MO,TU;BYSETPOS=-1', 'FREQ=HOURLY', 'FREQ=YEARLY;BYWEEKNO=20']) {
      expect(summarizeRRule(r)).toBe('Custom rule from another app')
      expect(isEditable(parseRRule(r))).toBe(false)
      expect(formatRRule(parseRRule(r)!)).toBe(r)
      expect(nextDates(r, TUE, 3)).toEqual([])
    }
  })
})

describe('smart options', () => {
  it('for Tue 6 Oct', () => {
    expect(smartOptions(TUE).map((o) => o.label)).toEqual([
      'Does not repeat', 'Daily', 'Weekly on Tuesday', 'Every weekday (Mon–Fri)', 'Monthly on the first Tuesday',
      'Monthly on day 6', 'Yearly on 6 October', 'Custom…',
    ])
  })
  it('a last-week date also offers "the last"; a fifth one only "the last"', () => {
    expect(smartOptions('2026-10-27').map((o) => o.id)).toContain('monthly-last') // fourth and last Tuesday
    expect(smartOptions('2026-10-27').map((o) => o.id)).toContain('monthly-nth')
    const fifth = smartOptions('2026-10-29').map((o) => o.label) // fifth Thursday
    expect(fifth).toContain('Monthly on the last Thursday')
    expect(fifth.some((l) => l.includes('fifth'))).toBe(false)
  })
  it('every option summarises to its own label', () => {
    for (const o of smartOptions(TUE)) if (o.rrule) expect(summarizeRRule(o.rrule, TUE)).toBe(o.label)
  })
  it('a stored rule finds its option', () => {
    expect(matchSmartOption('FREQ=WEEKLY;BYDAY=TU', TUE)).toBe('weekly')
    expect(matchSmartOption('FREQ=WEEKLY', TUE)).toBe('weekly')
    expect(matchSmartOption('FREQ=YEARLY', TUE)).toBe('yearly')
    expect(matchSmartOption('FREQ=WEEKLY;INTERVAL=2', TUE)).toBe('custom')
    expect(matchSmartOption(null, TUE)).toBe('none')
  })
})

describe('next dates', () => {
  it('the spec example', () => {
    expect(describeRepeat('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH;UNTIL=20261215T235959Z', '2026-10-12'))
      .toBe('Every 2 weeks on Mon and Thu, until 15 Dec · next: Mon 12 Oct, Thu 15 Oct, Mon 26 Oct')
  })
  it('daily, weekly and weekdays', () => {
    expect(nextDates('FREQ=DAILY', '2026-10-30', 3)).toEqual(['2026-10-30', '2026-10-31', '2026-11-01'])
    expect(nextDates('FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR', '2026-10-02', 3)).toEqual(['2026-10-02', '2026-10-05', '2026-10-06'])
    expect(nextDates('FREQ=WEEKLY', TUE, 3, { from: '2026-10-20' })).toEqual(['2026-10-20', '2026-10-27', '2026-11-03'])
  })
  it('monthly edge days are skipped, not moved', () => {
    expect(nextDates('FREQ=MONTHLY;BYMONTHDAY=31', '2026-01-31', 3)).toEqual(['2026-01-31', '2026-03-31', '2026-05-31'])
    expect(nextDates('FREQ=MONTHLY;BYDAY=-1FR', '2026-10-30', 3)).toEqual(['2026-10-30', '2026-11-27', '2026-12-25'])
    expect(nextDates('FREQ=MONTHLY;BYDAY=1TU', TUE, 2)).toEqual(['2026-10-06', '2026-11-03'])
    expect(nextDates('FREQ=YEARLY', '2028-02-29', 2)).toEqual(['2028-02-29', '2032-02-29'])
  })
  it('COUNT, UNTIL and EXDATE', () => {
    expect(nextDates('FREQ=DAILY;COUNT=2', TUE, 5)).toEqual(['2026-10-06', '2026-10-07'])
    expect(nextDates('FREQ=DAILY;UNTIL=20261008', TUE, 5)).toEqual(['2026-10-06', '2026-10-07', '2026-10-08'])
    expect(nextDates('FREQ=DAILY', TUE, 3, { exdates: ['2026-10-07'] })).toEqual(['2026-10-06', '2026-10-08', '2026-10-09'])
  })
})

describe('writing rules', () => {
  it('round-trips the editable subset', () => {
    expect(formatRRule({ freq: 'WEEKLY', interval: 2, byDay: [{ day: 'TH' }, { day: 'MO' }], until: '2026-12-15' })).toBe('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH;UNTIL=20261215')
    expect(formatRRule(parseRRule('FREQ=MONTHLY;BYDAY=TU;BYSETPOS=1')!)).toBe('FREQ=MONTHLY;BYDAY=1TU')
    expect(formatRRule(parseRRule('FREQ=YEARLY;BYMONTH=10;BYMONTHDAY=6;COUNT=3')!)).toBe('FREQ=YEARLY;BYMONTH=10;BYMONTHDAY=6;COUNT=3')
  })
})
