// mail.schedule_meeting and event.insert_times: the title, guests, identity and texts.
import { describe, expect, it } from 'vitest'
import { isRobot, meetingTitle, participants, receivingIdentity, replyText, slotText, spreadSlots, timesHtml } from './meeting'

const P = { zone: 'UTC', hour12: true }
const a = (email: string, name = '') => ({ email, name })

describe('meetingTitle', () => {
  it('drops Re: and Fwd: prefixes', () => {
    expect(meetingTitle('Re: Fwd: RE: Budget for Q4')).toBe('Budget for Q4')
    expect(meetingTitle('AW: Termin')).toBe('Termin')
    expect(meetingTitle('Report')).toBe('Report')
  })
})

describe('participants', () => {
  const msgs = [
    { from: a('dana@acme.example', 'Dana'), to: [a('me@x.org')], cc: [a('bob@acme.example', 'Bob'), a('team@lists.acme.example')] },
    { from: a('me@x.org'), to: [a('dana@acme.example', 'Dana')], cc: [a('no-reply@acme.example')], sent_by_me: true },
    { from: a('news@shop.com'), to: [a('me@x.org')], cc: [], list_unsubscribe: '<mailto:u@shop.com>' },
    { from: a('Carol@Acme.example', 'Carol'), to: [a('ME@x.org')], cc: [], draft: false },
  ]
  it('keeps the people, leaving out me, lists and robots', () => {
    expect(participants(msgs, ['me@x.org']).map((p) => p.email)).toEqual(['dana@acme.example', 'bob@acme.example', 'Carol@Acme.example'])
  })
  it('knows robots', () => {
    expect(['noreply@x.example', 'notifications@github.com', 'bounces+123@mail.x.example', 'mailer-daemon@x.example'].every(isRobot)).toBe(true)
    expect(isRobot('dana@acme.example')).toBe(false)
  })
})

describe('receivingIdentity', () => {
  const ids = [{ id: 1, email: 'me@x.org' }, { id: 2, email: 'work@acme.example' }]
  it('takes the newest hint, else the address the mail went to', () => {
    expect(receivingIdentity([{ from: a('d@x'), to: [a('me@x.org')], cc: [], identity_hint: 2 }], ids)!.id).toBe(2)
    expect(receivingIdentity([{ from: a('d@x'), to: [a('WORK@acme.example')], cc: [] }], ids)!.id).toBe(2)
    expect(receivingIdentity([{ from: a('d@x'), to: [a('other@y')], cc: [] }], ids)).toBeNull()
  })
})

describe('texts', () => {
  const s = new Date('2026-10-06T15:00:00Z')
  const e = new Date('2026-10-06T15:30:00Z')
  it('writes a slot in my clock', () => {
    expect(slotText(s, e, P)).toBe('Tue 6 Oct, 3:00 – 3:30 PM')
    expect(slotText(s, e, { zone: 'UTC', hour12: false })).toBe('Tue 6 Oct, 15:00 – 15:30')
  })
  it('inserts three times as a list', () => {
    const html = timesHtml([{ start: s, end: e }, { start: new Date('2026-10-07T09:00:00Z'), end: new Date('2026-10-07T09:30:00Z') }], P)
    expect(html).toContain('Would any of these work?')
    expect(html).toContain('<li>Wed 7 Oct, 9:00 – 9:30 AM</li>')
  })
  it('offers a reply with the details', () => {
    expect(replyText('Budget', s, e, false, 'Room 4', P)).toBe("I've sent an invitation for Budget on Tue 6 Oct, 3:00 – 3:30 PM (UTC) at Room 4. Let me know if the time doesn't work.")
  })
})

describe('spreadSlots', () => {
  const at = (iso: string) => ({ start: new Date(iso) })
  it('takes the best slot of each day first, then fills, in time order', () => {
    const ranked = [at('2026-10-01T09:00:00Z'), at('2026-10-01T16:30:00Z'), at('2026-10-01T09:30:00Z'), at('2026-10-02T10:00:00Z'), at('2026-10-05T09:00:00Z')]
    expect(spreadSlots(ranked, 3, 'UTC').map((s) => s.start.toISOString().slice(0, 16))).toEqual(['2026-10-01T09:00', '2026-10-02T10:00', '2026-10-05T09:00'])
    expect(spreadSlots(ranked.slice(0, 3), 2, 'UTC').map((s) => s.start.toISOString().slice(11, 16))).toEqual(['09:00', '16:30'])
  })
})
