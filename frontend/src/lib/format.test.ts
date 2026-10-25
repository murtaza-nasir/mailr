import { describe, expect, it } from 'vitest'
import {
  formatListDate, formatParticipants, formatRelative, formatSize, initials, parseAddressList,
  formatRange, isValidEmail, labelLeaf, avatarColor, displayName, personName,
} from './format'
import { setClockSetting } from './dates'

const now = new Date('2026-09-27T15:00:00')

describe('formatListDate', () => {
  it('shows time for today', () => {
    expect(formatListDate(new Date('2026-09-27T02:52:00').toISOString(), now)).toBe('2:52 AM')
  })
  it('shows month and day this year', () => {
    expect(formatListDate(new Date('2026-09-24T10:00:00').toISOString(), now)).toBe('Sep 24')
  })
  it('shows numeric date for older years', () => {
    expect(formatListDate(new Date('2025-09-24T10:00:00').toISOString(), now)).toBe('9/24/25')
  })
  it("follows the account's clock (Settings → Region), not the locale's", () => {
    const iso = new Date('2026-09-27T14:52:00').toISOString()
    try {
      setClockSetting('24h')
      expect(formatListDate(iso, now)).toBe('14:52')
      setClockSetting('12h')
      expect(formatListDate(iso, now, 'en-GB')).toBe('2:52 pm')
    } finally {
      setClockSetting(null)
    }
    expect(formatListDate(iso, now)).toBe('2:52 PM')
  })
  it('handles empty and invalid', () => {
    expect(formatListDate(null, now)).toBe('')
    expect(formatListDate('nope', now)).toBe('')
  })
})

describe('formatRelative', () => {
  it('formats past and future', () => {
    expect(formatRelative(new Date(now.getTime() - 3 * 60_000).toISOString(), now)).toBe('3 minutes ago')
    expect(formatRelative(new Date(now.getTime() - 26 * 3600_000).toISOString(), now)).toBe('1 day ago')
    expect(formatRelative(new Date(now.getTime() + 2 * 3600_000).toISOString(), now)).toBe('in 2 hours')
    expect(formatRelative(new Date(now.getTime() - 10_000).toISOString(), now)).toBe('just now')
  })
})

describe('formatParticipants', () => {
  it('single sender without count', () => {
    expect(formatParticipants([{ email: 'a@x.example', name: 'Alice Smith' }], 1)).toBe('Alice Smith')
  })
  it('me and another with count', () => {
    expect(formatParticipants([{ email: 'm@x.example', me: true }, { email: 'a@x.example', name: 'Alice Smith' }], 3)).toBe('me, Alice 3')
  })
  it('many senders collapse with ..', () => {
    const p = ['Ann A', 'Bob B', 'Cid C', 'Dee D'].map((n, i) => ({ email: `${i}@x.example`, name: n }))
    expect(formatParticipants(p, 5)).toBe('Ann .. Cid, Dee 5')
  })
  it('falls back to local part', () => {
    expect(formatParticipants([{ email: 'noreply@github.com' }], 1)).toBe('noreply')
  })
  it('Last, First names', () => {
    expect(formatParticipants([{ email: 'a@x', name: 'Smith, John' }, { email: 'b@x', name: 'Jo' }], 2)).toBe('John, Jo 2')
  })
})

describe('personName', () => {
  it('turns "Last, First" around and leaves other names alone', () => {
    expect(personName('Turner, Ross')).toBe('Ross Turner')
    expect(personName('van der Berg, Anna Maria')).toBe('Anna Maria van der Berg')
    expect(personName('Ross Turner')).toBe('Ross Turner')
    expect(personName('Smith, Jr.')).toBe('Smith, Jr.')
    expect(personName('Acme, Inc')).toBe('Acme, Inc')
    expect(personName('a, b, c')).toBe('a, b, c')
    expect(displayName({ email: 'r@x.example', name: 'Turner, Ross' })).toBe('Ross Turner')
  })
})

describe('misc', () => {
  it('sizes', () => {
    expect(formatSize(500)).toBe('500 B')
    expect(formatSize(2048)).toBe('2 KB')
    expect(formatSize(5 * 1024 * 1024)).toBe('5 MB')
    expect(formatSize(1.5 * 1024 * 1024)).toBe('1.5 MB')
  })
  it('initials', () => {
    expect(initials({ email: 'bob@x.example', name: 'Bob' })).toBe('B')
    expect(initials({ email: 'zed@x.example' })).toBe('Z')
  })
  it('parses address lists', () => {
    expect(parseAddressList('Alice <A@x.example>, b@y.org; "C D" <c@z.io>')).toEqual([
      { email: 'a@x.example', name: 'Alice' },
      { email: 'b@y.org' },
      { email: 'c@z.io', name: 'C D' },
    ])
  })
  it('range and validation', () => {
    expect(formatRange(0, 50, 1234)).toBe('1–50 of 1,234')
    expect(formatRange(0, 0, 0)).toBe('0 of 0')
    expect(isValidEmail('a@b.co')).toBe(true)
    expect(isValidEmail('a@b')).toBe(false)
    expect(labelLeaf('Research/2026')).toBe('2026')
    expect(avatarColor('a@x.example')).toBe(avatarColor('A@X.example'))
  })
})

import { followUpOptions, scheduleOptions, snoozeOptions } from './snooze'
describe('time presets', () => {
  it('skips Monday morning on Sunday', () => {
    const sun = new Date('2026-09-27T10:00:00')
    expect(scheduleOptions(sun).map((o) => o.id)).toEqual(['tomorrow-am', 'tomorrow-pm'])
    expect(scheduleOptions(new Date('2026-09-29T10:00:00')).map((o) => o.id)).toContain('monday')
  })
  it('snooze offers later today before 5pm and weekend midweek', () => {
    const tue = new Date('2026-09-29T10:00:00')
    expect(snoozeOptions(tue).map((o) => o.id)).toEqual(['later', 'tomorrow', 'weekend', 'nextweek'])
    expect(snoozeOptions(new Date('2026-09-29T18:00:00')).map((o) => o.id)).not.toContain('later')
  })
  it('follow-up reminders: 1, 2, 3 and 7 days out at 9:00', () => {
    const wed = new Date('2026-09-30T19:00:00')
    const opts = followUpOptions(wed)
    expect(opts.map((o) => o.at.getDate())).toEqual([1, 2, 3, 7])
    expect(opts.every((o) => o.at.getHours() === 9 && o.at.getMinutes() === 0)).toBe(true)
  })
})
