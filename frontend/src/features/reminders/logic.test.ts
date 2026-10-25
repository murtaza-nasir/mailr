// ACT-SURFACES: web.settings web.notification
// Reminders on the web: when lines, snooze presets, the triage order, notification buttons and
// the summary rule (reminder.fire, notification.open_stack,
// reminder.sync, task.snooze, event.snooze).
import { describe, expect, it } from 'vitest'
import {
  clickUrl, itemFromPayload, joinedWhileOpen, newMissed, notificationButtons, planBurst, recordPath, snoozeChoices, stackOrder, staleTags, started,
  REPEAT_CHOICES, repeatMinutes, repeatSummary, summaryTitle, SUMMARY_TAG, whenLine, type ReminderItem,
} from './logic'

const UTC = { zone: 'UTC', hour12: true }
const now = new Date('2026-10-01T14:00:00Z')

function item(p: Partial<ReminderItem>): ReminderItem {
  return {
    key: 'e|u1|2026-10-01T15:00:00+00:00|10', kind: 'event', uid: 'u1', occurrence: '2026-10-01T15:00:00+00:00', minutes: 10,
    title: 'Budget review', detail: '', start: '2026-10-01T15:00:00+00:00', fire_at: '2026-10-01T14:50:00+00:00',
    fired_at: '2026-10-01T14:50:00+00:00', missed: false, state: 'fired', actions: ['snooze', 'dismiss'], ...p,
  }
}

describe('whenLine', () => {
  it('counts down to an event within 90 minutes', () => {
    expect(whenLine(item({ start: '2026-10-01T14:25:00+00:00' }), now, UTC)).toBe('Starts in 25 min')
  })
  it('says how long ago a running event started', () => {
    expect(whenLine(item({ start: '2026-10-01T13:55:00+00:00' }), now, UTC)).toBe('Started 5 min ago')
  })
  it('names the day and time of a later event', () => {
    expect(whenLine(item({ start: '2026-10-02T09:00:00+00:00' }), now, UTC)).toBe('Starts tomorrow 9:00 AM')
  })
  it('shows an all-day event by its day', () => {
    expect(whenLine(item({ start: '2026-10-02' }), now, UTC)).toBe('Tomorrow · all day')
  })
  it('shows a task due with how overdue it is', () => {
    expect(whenLine(item({ kind: 'task', start: '2026-10-01T12:00:00+00:00' }), now, UTC)).toBe('Due today 12:00 PM · overdue 2 h')
    expect(whenLine(item({ kind: 'task', start: '2026-09-29' }), now, UTC)).toBe('Due Tue 29 Sep · overdue 2 days')
    expect(whenLine(item({ kind: 'task', start: '2026-10-01' }), now, UTC)).toBe('Due today')
  })
  it('reads the user zone, not UTC', () => {
    expect(whenLine(item({ start: '2026-10-02T03:00:00+00:00' }), now, { zone: 'America/Chicago', hour12: false })).toBe('Starts today 22:00')
  })
})

describe('started', () => {
  it('is true only for an event whose start has passed', () => {
    expect(started(item({ start: '2026-10-01T13:00:00+00:00' }), now, 'UTC')).toBe(true)
    expect(started(item({}), now, 'UTC')).toBe(false)
    expect(started(item({ kind: 'task', start: '2026-10-01T13:00:00+00:00' }), now, 'UTC')).toBe(false)
  })
})

describe('snoozeChoices', () => {
  it('offers 5 min, 10 min and At start time before an event', () => {
    const c = snoozeChoices('event', now, new Date('2026-10-01T14:30:00Z'))
    expect(c.map((x) => x.label)).toEqual(['5 min', '10 min', 'At start time'])
    expect(c[2].at.toISOString()).toBe('2026-10-01T14:30:00.000Z')
  })
  it('drops At start time with two minutes or less left', () => {
    expect(snoozeChoices('event', now, new Date('2026-10-01T14:02:00Z')).map((x) => x.id)).toEqual(['5m', '10m'])
  })
  it('offers 5 and 15 min once the event has started', () => {
    expect(snoozeChoices('event', now, new Date('2026-10-01T13:00:00Z')).map((x) => x.label)).toEqual(['5 min', '15 min'])
  })
  it('offers the task presets, with This evening only before 17:00', () => {
    const morning = new Date(2026, 9, 1, 10, 0)
    expect(snoozeChoices('task', morning, null).map((x) => x.id)).toEqual(['10m', '1h', '3h', 'evening', 'tomorrow', 'nextweek'])
    const late = new Date(2026, 9, 1, 17, 30)
    expect(snoozeChoices('task', late, null).map((x) => x.id)).not.toContain('evening')
    const next = snoozeChoices('task', morning, null).find((x) => x.id === 'nextweek')!.at
    expect(next.getDay()).toBe(1)
    expect(next.getHours()).toBe(9)
  })
})

describe('stackOrder (notification.open_stack)', () => {
  it('puts missed first, then fired oldest first, then snoozed', () => {
    const a = item({ key: 'a', fired_at: '2026-10-01T13:00:00Z' })
    const b = item({ key: 'b', fired_at: '2026-10-01T12:00:00Z' })
    const m = item({ key: 'm', missed: true, fired_at: '2026-10-01T13:30:00Z' })
    const s = item({ key: 's', state: 'snoozed', fired_at: '2026-10-01T11:00:00Z' })
    expect(stackOrder([a, s, b, m]).map((r) => r.key)).toEqual(['m', 'b', 'a', 's'])
  })
})

describe('notificationButtons (reminder.fire)', () => {
  it('gives tasks Done and Snooze 1 h', () => {
    expect(notificationButtons('task', null, now).map((b) => b.title)).toEqual(['Done', 'Snooze 1 h'])
  })
  it('gives events Snooze 5 min, Remind before… and Dismiss while the start is more than 5 minutes away (event.remind_before)', () => {
    expect(notificationButtons('event', new Date('2026-10-01T15:00:00Z'), now, 'https://meet').map((b) => b.action)).toEqual(['snooze', 'before', 'dismiss'])
    expect(notificationButtons('event', new Date('2026-10-01T15:00:00Z'), now).map((b) => b.title)).toEqual(['Snooze 5 min', 'Remind before…', 'Dismiss'])
  })
  it('gives events Snooze 5 min and Dismiss once they are about to start or have started', () => {
    expect(notificationButtons('event', new Date('2026-10-01T14:03:00Z'), now).map((b) => b.action)).toEqual(['snooze', 'dismiss'])
    expect(notificationButtons('event', new Date('2026-10-01T13:30:00Z'), now).map((b) => b.action)).toEqual(['snooze', 'dismiss'])
    expect(notificationButtons('event', null, now).map((b) => b.action)).toEqual(['snooze', 'dismiss'])
  })
  it('opens the sheet on the offsets from Remind before…', () => {
    expect(clickUrl('/calendar', 'e|u|2026-10-01T15:00:00+00:00|10', 'before')).toBe('/calendar?reminder=e%7Cu%7C2026-10-01T15%3A00%3A00%2B00%3A00%7C10&expand=before')
  })
  // ACT event.join
  it('puts Join in place of Dismiss within 5 minutes of a start with a link', () => {
    expect(notificationButtons('event', new Date('2026-10-01T14:04:00Z'), now, 'https://meet').map((b) => b.action)).toEqual(['snooze', 'join'])
    expect(notificationButtons('event', new Date('2026-10-01T14:04:00Z'), now, null).map((b) => b.action)).toEqual(['snooze', 'dismiss'])
  })
})

describe('the summary rule', () => {
  it('stacks more than three into one summary', () => {
    expect(planBurst(['a', 'b'], ['c'])).toEqual({ summary: false, total: 3 })
    expect(planBurst(['a', 'b', 'c'], ['d'])).toEqual({ summary: true, total: 4 })
    expect(planBurst([], ['a', 'b', 'c', 'd'])).toEqual({ summary: true, total: 4 })
    expect(planBurst(['a'], ['a'])).toEqual({ summary: false, total: 1 })
  })
  it('names the kind when all are the same', () => {
    expect(summaryTitle(['task', 'task', 'task'])).toBe('3 task reminders')
    expect(summaryTitle(['task', 'event', 'task', 'event'])).toBe('4 reminders')
  })
})

describe('reminder.sync: handled elsewhere', () => {
  it('closes notifications whose reminder left the tray, never the summaries', () => {
    expect(staleTags(['a', 'b', SUMMARY_TAG], [item({ key: 'b' })])).toEqual(['a'])
  })
  it('announces each missed reminder once', () => {
    const items = [item({ key: 'm1', missed: true }), item({ key: 'm2', missed: true }), item({ key: 'f' })]
    expect(newMissed(items, new Set(['m1'])).map((r) => r.key)).toEqual(['m2'])
  })
})

describe('links', () => {
  it('opens the sheet or the triage queue from a click', () => {
    expect(clickUrl('/calendar/event/u1/x', 'e|u1|x|10')).toBe('/calendar/event/u1/x?reminder=e%7Cu1%7Cx%7C10')
    expect(clickUrl(undefined, null)).toBe('/mail/inbox?reminders=review')
  })
  it('leads to the record', () => {
    expect(recordPath({ kind: 'task', uid: 't 1', occurrence: '' })).toBe('/tasks/task/t%201')
    expect(recordPath({ kind: 'event', uid: 'u1', occurrence: '2026-10-02' })).toBe('/calendar/event/u1/2026-10-02')
  })
  it('reads a payload as a tray item', () => {
    const r = itemFromPayload({ key: 't|abc|2026-10-02|0', title: 'Pay rent' }, now)
    expect(r).toMatchObject({ kind: 'task', uid: 'abc', occurrence: '2026-10-02', title: 'Pay rent', state: 'fired' })
  })
})

describe('joinedWhileOpen (reminder.fire on web.triage)', () => {
  const at = (min: number) => new Date(now.getTime() + min * 60_000).toISOString()
  const r = (key: string, fired: string | null, state: 'fired' | 'snoozed' = 'fired') =>
    ({ ...itemFromPayload({ key }), fired_at: fired, state }) as ReminderItem
  it('adds reminders that fired after the queue opened, in firing order', () => {
    const items = [r('a', at(-10)), r('c', at(3)), r('b', at(2)), r('d', at(4), 'snoozed')]
    expect(joinedWhileOpen(items, ['a'], now.getTime())).toEqual(['b', 'c'])
  })
  it('leaves out what the queue holds and what fired before it opened', () => {
    expect(joinedWhileOpen([r('a', at(1)), r('x', at(-5))], ['a'], now.getTime())).toEqual([])
  })
})

describe('Remind again (reminder.repeat)', () => {
  it('words the choice as Android does', () => {
    expect(REPEAT_CHOICES.map(([, l]) => l)).toEqual(['Off', 'Every 5 minutes', 'Every 10 minutes', 'Every 15 minutes', 'Every 30 minutes'])
    expect(repeatSummary({ enabled: true, every_min: 10 })).toBe('Every 10 minutes, up to three times, until you act on it')
    expect(repeatSummary({ enabled: false, every_min: 10 })).toBe('Off')
    expect(repeatSummary(undefined)).toBe('Off')
  })
  it('shows 0 (Off) unless enabled', () => {
    expect(repeatMinutes({ enabled: true, every_min: 15 })).toBe(15)
    expect(repeatMinutes({ enabled: false, every_min: 15 })).toBe(0)
    expect(repeatMinutes(null)).toBe(0)
  })
  it('a repeat of a showing reminder replaces it and never makes a summary', () => {
    const keys = ['e|a|x|10', 'e|b|x|10', 't|c|x|0']
    expect(planBurst(keys, ['e|a|x|10']).summary).toBe(false)
  })
})

describe('note reminders (n|<note id>|abs|<instant>)', () => {
  const note = item({
    key: 'n|n7abc|abs|2026-10-01T15:00:00+00:00', kind: 'note', uid: 'n7abc', occurrence: '2026-10-01T15:00:00+00:00', minutes: 0,
    title: 'Call the plumber', start: '2026-10-01T15:00:00+00:00',
  })

  it('reads the kind from the key and opens the note', () => {
    expect(itemFromPayload({ key: note.key }, now)).toMatchObject({ kind: 'note', uid: 'n7abc', occurrence: '2026-10-01T15:00:00+00:00', minutes: 0, title: 'Note reminder' })
    expect(recordPath(note)).toBe('/notes/n/n7abc')
    expect(clickUrl('/notes/n/n7abc', note.key)).toBe(`/notes/n/n7abc?reminder=${encodeURIComponent(note.key)}`)
  })

  it('says when it was set for, snoozes with the task presets and has Snooze and Dismiss buttons', () => {
    expect(whenLine(note, now, UTC)).toBe('Reminder for today 3:00 PM')
    expect(snoozeChoices('note', now, null).map((c) => c.id)).toEqual(snoozeChoices('task', now, null).map((c) => c.id))
    expect(notificationButtons('note', null, now).map((b) => b.action)).toEqual(['snooze', 'dismiss'])
    expect(started(note, now, 'UTC')).toBe(false)
    expect(summaryTitle(['note', 'note'])).toBe('2 note reminders')
  })
})
