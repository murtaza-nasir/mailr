import { describe, expect, it } from 'vitest'
import { formatWhen, fromLocalInput, hasInvite, plusHour, toLocalInput } from './calendarForm'

const att = (type: string, filename: string) => ({ part: 1, type, filename, size: 1, inline: true, cid: null })

describe('calendarForm', () => {
  it('detects invitations', () => {
    expect(hasInvite({ attachments: [att('text/calendar', 'invite.ics')] })).toBe(true)
    expect(hasInvite({ attachments: [att('application/octet-stream', 'Meeting.ICS')] })).toBe(true)
    expect(hasInvite({ attachments: [att('application/pdf', 'a.pdf')] })).toBe(false)
    // An attached email that is an invitation (a forward from Outlook): the card asks the server.
    const fwd = { ...att('message/rfc822', 'Ops Review.eml'), email: { subject: 'Ops Review', from_name: '', from_email: '', date: null, excerpt: '', attachments: 0, invite: true } }
    expect(hasInvite({ attachments: [fwd] })).toBe(true)
    expect(hasInvite({ attachments: [{ ...fwd, email: { ...fwd.email, invite: false } }] })).toBe(false)
  })
  it('round-trips datetime-local values', () => {
    const v = toLocalInput('2026-10-01T15:30:00Z')
    expect(v).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
    expect(new Date(fromLocalInput(v)!).toISOString()).toBe('2026-10-01T15:30:00.000Z')
    expect(toLocalInput('2026-10-01')).toBe('2026-10-01T09:00')
    expect(toLocalInput('garbage')).toBe('')
    expect(fromLocalInput('')).toBeNull()
  })
  it('adds an hour', () => {
    expect(plusHour('2026-10-01T23:30')).toBe('2026-10-02T00:30')
  })
  it('formats all-day and timed ranges', () => {
    expect(formatWhen('2026-10-01', null)).toContain('2026')
    expect(formatWhen('2026-10-01T15:00:00Z', '2026-10-01T16:00:00Z')).toContain('–')
    expect(formatWhen(null, null)).toBe('')
  })
})
