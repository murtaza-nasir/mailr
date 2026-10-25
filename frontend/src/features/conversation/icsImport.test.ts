// event.import_ics: reading the events of a plain .ics file (not an invitation).
import { describe, expect, it } from 'vitest'
import { isInvitationMethod, parseIcs } from './icsImport'

const FILE = [
  'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Airline//EN',
  'BEGIN:VEVENT', 'UID:flight-1@air', 'SUMMARY:Flight BA 286 to London\\, Heathrow', 'DTSTART;TZID=America/Chicago:20261012T171500',
  'DTEND;TZID=Europe/London:20261013T081000', 'LOCATION:ORD Terminal 5', 'DESCRIPTION:Seat 34A\\nBooking XYZ',
  ' 123', 'BEGIN:VALARM', 'TRIGGER:-PT2H', 'DESCRIPTION:Reminder', 'END:VALARM', 'END:VEVENT',
  'BEGIN:VEVENT', 'UID:hotel@x', 'SUMMARY:Hotel', 'DTSTART;VALUE=DATE:20261013', 'DTEND;VALUE=DATE:20261016', 'END:VEVENT',
  'BEGIN:VEVENT', 'UID:talk@x', 'SUMMARY:Talk', 'DTSTART:20261014T090000Z', 'DURATION:PT1H30M', 'RRULE:FREQ=WEEKLY;COUNT=3', 'END:VEVENT',
  'BEGIN:VEVENT', 'UID:gone@x', 'SUMMARY:Gone', 'DTSTART:20261014T090000Z', 'STATUS:CANCELLED', 'END:VEVENT',
  'END:VCALENDAR',
].join('\r\n')

describe('parseIcs', () => {
  const evs = parseIcs(FILE)
  it('reads each VEVENT, leaving out cancelled ones', () => {
    expect(evs.map((e) => e.summary)).toEqual(['Flight BA 286 to London, Heathrow', 'Hotel', 'Talk'])
  })
  it('keeps the zone of a TZID time and unfolds and unescapes text', () => {
    expect(evs[0]).toMatchObject({ start: '2026-10-12T17:15:00', tz: 'America/Chicago', all_day: false, location: 'ORD Terminal 5', description: 'Seat 34A\nBooking XYZ123' })
  })
  it('reads all-day dates with an exclusive end', () => {
    expect(evs[1]).toMatchObject({ start: '2026-10-13', end: '2026-10-16', all_day: true })
  })
  it('turns a DURATION into an end and keeps the repeat', () => {
    expect(evs[2]).toMatchObject({ start: '2026-10-14T09:00:00+00:00', end: '2026-10-14T10:30:00+00:00', recurrence: 'FREQ=WEEKLY;COUNT=3' })
  })
  it('does not read the alarm description as the event text', () => {
    expect(evs[0].description).not.toContain('Reminder')
  })
})

describe('isInvitationMethod', () => {
  it('treats REQUEST, CANCEL, REPLY and COUNTER as invitations, PUBLISH and none as files', () => {
    expect(['REQUEST', 'CANCEL', 'REPLY', 'COUNTER'].every(isInvitationMethod)).toBe(true)
    expect(isInvitationMethod('PUBLISH')).toBe(false)
    expect(isInvitationMethod('')).toBe(false)
  })
})
