// ACT-SURFACES: web.note web.table web.calendar web.pane web.recording web.reader
// Links: the link picker of a note or a table (note.link, table.link),
// the chips' targets, and the lookup key of the links panels where note.link, note.unlink,
// table.link and table.unlink are offered on an email, an event, a task and a recording.

import { describe, expect, it } from 'vitest'
import { linkedRef, linkTarget, parseLink, pickerOptions } from './LinkPicker'

describe('parseLink', () => {
  it('reads the address of an email, an event, a task and a recording', () => {
    expect(parseLink('https://mail.example.org/mail/inbox/c123')).toEqual({ kind: 'conversation', ref: { conversation_id: 123 } })
    expect(parseLink('/calendar/event/abc%40x/2026-10-02')).toEqual({ kind: 'event', ref: { uid: 'abc@x', occurrence: '2026-10-02' } })
    expect(parseLink('/tasks/today/task/t-1')).toEqual({ kind: 'task', ref: { uid: 't-1' } })
    expect(parseLink('https://mail.example.org/recordings/2/41')).toEqual({ kind: 'recording', ref: { connector_id: 2, recording_id: 41 } })
    expect(parseLink('budget review')).toBeNull()
    expect(parseLink('https://example.org/elsewhere')).toBeNull()
  })
})

describe('linkTarget', () => {
  it('opens the email, the occurrence, the task and the recording', () => {
    expect(linkTarget({ target_kind: 'event', target_ref: { uid: 'e1', occurrence: '2026-10-06T09:00:00+00:00' } })).toBe('/calendar/event/e1/2026-10-06T09%3A00%3A00%2B00%3A00')
    expect(linkTarget({ target_kind: 'task', target_ref: { uid: 't1' } })).toBe('/tasks/task/t1')
    expect(linkTarget({ target_kind: 'recording', target_ref: { connector_id: 3, recording_id: 42 } })).toBe('/recordings/3/42')
    expect(linkTarget({ target_kind: 'conversation', target_ref: { conversation_id: 7 } })).toMatch(/^\/mail\/inbox\//)
  })
})

describe('linkedRef', () => {
  it('is the key GET /notes/linked and /tables/linked look a target up by', () => {
    expect(linkedRef('conversation', { conversation_id: 9 })).toBe(9)
    expect(linkedRef('event', { uid: 'e1', occurrence: 'x' })).toBe('e1')
    expect(linkedRef('task', { uid: 't1' })).toBe('t1')
    expect(linkedRef('recording', { connector_id: 3, recording_id: 42 })).toBe('3:42')
    expect(linkedRef('recording', { recording_id: 42 })).toBeNull()
  })
})

describe('pickerOptions', () => {
  const data = {
    mail: [{ id: 5, subject: 'Q4 plan', last_date: '2026-10-01T10:00:00Z' }],
    events: [{ uid: 'e1', summary: 'Team sync', start: '2026-10-06T09:00:00Z', occurrence: '2026-10-06T09:00:00+00:00' }],
    tasks: [{ uid: 't1', summary: 'Call Dana', due: '2026-10-07' }],
    recordings: [{ connector_id: 3, recording_id: 42, title: 'Weekly sync', meeting_date: '2026-10-02T15:00:00Z' }],
  }
  it('groups emails, events, tasks and recordings in that order', () => {
    const o = pickerOptions('sync', data)
    expect(o.map((x) => x.group)).toEqual(['mail', 'event', 'task', 'recording'])
    expect(o[1]).toMatchObject({ kind: 'event', ref: { uid: 'e1', occurrence: '2026-10-06T09:00:00+00:00' }, title: 'Team sync' })
    expect(o[3]).toMatchObject({ kind: 'recording', ref: { connector_id: 3, recording_id: 42 } })
  })
  it('offers a pasted address first, and leaves out excluded kinds and a failed group', () => {
    const o = pickerOptions('/tasks/task/t9', { ...data, recordings: null }, ['conversation'])
    expect(o[0]).toMatchObject({ group: 'address', kind: 'task', ref: { uid: 't9' }, title: 'Link this address' })
    expect(o.some((x) => x.group === 'mail' || x.group === 'recording')).toBe(false)
  })
})
