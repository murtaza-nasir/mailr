// ACT-SURFACES: web.notification
// Meeting prep (event.prep): which events offer it, the collapsed line, the empty rule, the Prep
// link, and the Prep button on an event reminder's notification (second place before the start).
import { describe, expect, it } from 'vitest'
import { notificationButtons } from '../reminders/logic'
import { guestName, offersPrep, prepCounts, prepEmpty, prepPath, type PrepPack } from './prep'

const pack = (over: Partial<PrepPack> = {}): PrepPack => ({
  event: { uid: 'u', occurrence: 'o', title: 'Lumen review', start: null, end: null, guests: [{ email: 'dana@x.example', name: 'Dana' }], url: '/calendar/event/u/o', local_only: false },
  upcoming: true, has_guests: true, recording: null, threads: [], tasks: [], notes: [], attachments: [],
  open_items: { questions: [], waiting_on: [], promised: [] }, hidden: 0, local_only: false, ...over,
})

describe('meeting prep', () => {
  it('is offered for events with guests until half an hour after they end', () => {
    const now = new Date('2026-10-05T12:00:00Z')
    const end = (iso: string) => new Date(iso)
    expect(offersPrep({ attendee_list: [{}], endAt: end('2026-10-05T13:00:00Z') }, now)).toBe(true)
    expect(offersPrep({ attendee_list: [], endAt: end('2026-10-05T13:00:00Z') }, now)).toBe(false)
    expect(offersPrep({ attendee_list: [{}], endAt: end('2026-10-05T11:45:00Z') }, now)).toBe(true)
    expect(offersPrep({ attendee_list: [{}], endAt: end('2026-10-05T11:00:00Z') }, now)).toBe(false)
  })

  it('counts what the pack holds, and knows when it is empty', () => {
    expect(prepEmpty(pack())).toBe(true)
    const full = pack({
      recording: { connector_id: 1, recording_id: 2, title: 'r', meeting_date: null, created_at: null, summary_line: null, why: 'series', local_only: false, action_items: { open: [], done: 0 } },
      threads: [{ conversation_id: 1, message_id: 1, subject: 's', snippet: '', last_date: null, messages: 1, people: [], signals: [], from_me: false },
        { conversation_id: 2, message_id: 2, subject: 't', snippet: '', last_date: null, messages: 1, people: [], signals: [], from_me: true }],
      open_items: { questions: [{ conversation_id: 1, message_id: 1, from: 'dana@x.example', date: null, question: 'q?' }], waiting_on: [], promised: [] },
      tasks: [{ uid: 't', summary: 'x', due: null }],
    })
    expect(prepEmpty(full)).toBe(false)
    expect(prepCounts(full)).toBe('Last recording · 2 threads · 1 open question · 1 task')
    expect(guestName(full, 'dana@x.example')).toBe('Dana')
    expect(guestName(full, 'zoe@x.example')).toBe('zoe@x.example')
  })

  it('links to the event with its prep open', () => {
    expect(prepPath('a b', '2026-10-05T15:00:00+00:00')).toBe('/calendar/event/a%20b/2026-10-05T15%3A00%3A00%2B00%3A00?prep=1')
  })

  it('puts Prep on an event reminder (event.prep), keeping Snooze first', () => {
    const now = new Date('2026-10-05T12:00:00Z')
    const later = new Date('2026-10-05T12:30:00Z')
    const soon = new Date('2026-10-05T12:03:00Z')
    const acts = (b: { action: string }[]) => b.map((x) => x.action)
    expect(acts(notificationButtons('event', later, now, null, '/p'))).toEqual(['snooze', 'prep', 'before'])
    expect(acts(notificationButtons('event', later, now, null, null))).toEqual(['snooze', 'before', 'dismiss'])
    expect(acts(notificationButtons('event', soon, now, 'https://meet.example', '/p'))).toEqual(['snooze', 'join', 'prep'])
    expect(acts(notificationButtons('event', soon, now, null, '/p'))).toEqual(['snooze', 'prep', 'dismiss'])
    expect(acts(notificationButtons('task', later, now, null, '/p'))).toEqual(['done', 'snooze'])
  })
})
