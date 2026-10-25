// invite.view, invite.apply_update, invite.counter: the conflict line, the mini timeline, the
// update diff and the proposal line of InviteCard.
import { describe, expect, it } from 'vitest'
import { conflictText, diffLines, organizerTime, overlaps, previousLine, proposalText, timeline, type DayEvent, type Invite } from './inviteLogic'

const P = { zone: 'UTC', hour12: true }
const day: DayEvent[] = [
  { uid: 'a', summary: 'Team sync', start: '2026-10-06T14:30:00+00:00', end: '2026-10-06T15:30:00+00:00' },
  { uid: 'b', summary: 'Focus time', start: '2026-10-06T15:00:00+00:00', end: '2026-10-06T16:00:00+00:00', transparent: true },
  { uid: 'c', summary: 'Lunch', start: '2026-10-06T12:00:00+00:00', end: '2026-10-06T13:00:00+00:00' },
  { uid: 'd', summary: 'Declined thing', start: '2026-10-06T15:00:00+00:00', end: '2026-10-06T16:00:00+00:00', my_status: 'DECLINED' },
  { uid: 'inv', summary: 'The invite itself', start: '2026-10-06T15:00:00+00:00', end: '2026-10-06T16:00:00+00:00' },
]

describe('the conflict line', () => {
  it('names busy events that overlap, not free, declined or the invite itself', () => {
    const c = overlaps(day, '2026-10-06T15:00:00+00:00', '2026-10-06T16:00:00+00:00', 'inv', 'UTC')
    expect(c.map((x) => x.uid)).toEqual(['a'])
    expect(conflictText(c)).toBe('You have Team sync at that time')
    expect(conflictText([...c, day[2]])).toBe('You have Team sync and 1 more at that time')
    expect(conflictText([])).toBeNull()
  })
})

describe('the mini timeline', () => {
  it('spans the slot with room either side and marks the clash', () => {
    const t = timeline(day, '2026-10-06T15:00:00+00:00', '2026-10-06T16:00:00+00:00', 'inv', 'UTC')!
    expect(new Date(t.from).toISOString()).toBe('2026-10-06T13:00:00.000Z')
    expect(new Date(t.to).toISOString()).toBe('2026-10-06T19:00:00.000Z')
    expect(t.slot.left).toBeCloseTo(2 / 6)
    expect(t.blocks.find((b) => b.title === 'Team sync')!.clash).toBe(true)
    expect(t.blocks.some((b) => b.title === 'Focus time')).toBe(false)
  })
  it('has no timeline for an all-day invitation', () => {
    expect(timeline(day, '2026-10-06', '2026-10-07', 'inv', 'UTC')).toBeNull()
  })
})

describe('the update diff', () => {
  it('says what changed against my copy', () => {
    const inv = { start: '2026-10-06T16:00:00+00:00', end: '2026-10-06T17:00:00+00:00', location: 'Room 5', summary: 'Review' }
    const mine = { start: '2026-10-06T15:00:00+00:00', end: '2026-10-06T16:00:00+00:00', location: 'Room 4', summary: 'Review' }
    expect(diffLines(inv, mine, P)).toEqual(['Time changed: was Tue 6 Oct, 3 PM', 'Place changed: was Room 4'])
    expect(diffLines(inv, { ...inv }, P)).toEqual([])
  })
})

describe('a proposal', () => {
  it('reads "Dana proposes …" for a COUNTER on my event', () => {
    const inv = {
      method: 'COUNTER', uid: 'u', summary: 'Review', start: null, end: null, location: '', organizer: { email: 'me@x', name: 'Me' },
      attendees: [{ email: 'dana@x.org', name: 'Dana', status: 'NEEDS-ACTION' }], my_status: null, recurring: false, cancelled: false, in_calendar: true,
      itip: { applied: false, detail: 'Proposed a new time', method: 'COUNTER', counter: true, proposed: { start: '2026-10-08T15:00:00+00:00', end: '2026-10-08T16:00:00+00:00' } },
    } satisfies Invite
    expect(proposalText(inv, 'dana@x.org', P)).toBe('Dana proposes Thu 8 Oct, 3 PM')
    expect(proposalText({ ...inv, itip: { ...inv.itip, counter: false } }, 'dana@x.org', P)).toBeNull()
  })
})

// invite.apply_update: the "was" line from itip.previous and the organizer's zone.
describe('the update line from the ingest record', () => {
  const base = { recurring: false, itip: { applied: true, detail: 'Updated in your calendar', method: 'REQUEST', time_changed: true,
    previous: { start: '2026-10-06T15:00:00+00:00', end: '2026-10-06T16:00:00+00:00', recurrence_id: null }, organizer_tz: 'UTC' } }
  it('says where the event was, after the update is applied', () => {
    expect(previousLine(base, P)).toBe('Time changed: was Tue 6 Oct, 3 PM')
  })
  it('adds the organizer\'s time when their zone differs', () => {
    const tokyo = { ...base, itip: { ...base.itip, organizer_tz: 'Asia/Tokyo' } }
    expect(previousLine(tokyo, P)).toBe('Time changed: was Tue 6 Oct, 3 PM (12 AM Tokyo for the organiser)')
    expect(organizerTime('2026-10-06T15:00:00+00:00', 'Europe/London', P)).toBe('4 PM London')
    expect(organizerTime('2026-10-06T15:00:00+00:00', 'UTC', P)).toBeNull()
    // Outlook's Windows name from an old record: no second time, and no error.
    expect(organizerTime('2026-10-06T15:00:00+00:00', 'Central Standard Time', P)).toBeNull()
    expect(organizerTime('2026-10-06', 'Europe/London', P)).toBeNull()
  })
  it('names one moved occurrence of a series', () => {
    const occ = { recurring: true, itip: { ...base.itip, previous: { ...base.itip.previous, recurrence_id: '2026-10-06T15:00:00+00:00' } } }
    expect(previousLine(occ, P)).toBe('This occurrence moved: was Tue 6 Oct, 3 PM')
  })
  it('says nothing without a time change', () => {
    expect(previousLine({ ...base, itip: { ...base.itip, time_changed: false } }, P)).toBeNull()
    expect(previousLine({ recurring: false, itip: null }, P)).toBeNull()
  })
})
