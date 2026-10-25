// ACT-SURFACES: web.editor
// event.email_guests: who each group reaches.
import { describe, expect, it } from 'vitest'
import { groupCounts, guestRecipients } from './emailGuests'

const ev = {
  organizer: 'me@example.com',
  organizer_name: 'Me',
  attendee_list: [
    { email: 'dana@example.org', name: 'Dana', partstat: 'ACCEPTED' },
    { email: 'evan@example.org', name: 'Evan', partstat: 'NEEDS-ACTION' },
    { email: 'fay@example.org', name: '', partstat: 'DECLINED' },
    { email: 'gus@example.org' },
    { email: 'ME@example.com', partstat: 'ACCEPTED' },
    { email: 'Dana@example.org', partstat: 'ACCEPTED' },
  ],
}

describe('event.email_guests', () => {
  it('all: every guest once, without me', () => {
    expect(guestRecipients(ev, 'all', ['me@example.com']).map((g) => g.email)).toEqual(['dana@example.org', 'evan@example.org', 'fay@example.org', 'gus@example.org'])
  })
  it('yes: only the guests who accepted', () => {
    expect(guestRecipients(ev, 'yes', ['me@example.com']).map((g) => g.name)).toEqual(['Dana'])
  })
  it('waiting: guests with no answer yet, a missing PARTSTAT included', () => {
    expect(guestRecipients(ev, 'waiting', ['me@example.com']).map((g) => g.email)).toEqual(['evan@example.org', 'gus@example.org'])
  })
  it('the organizer is one of "all" when I am a guest', () => {
    const theirs = { ...ev, organizer: 'boss@example.org', organizer_name: 'Boss' }
    expect(guestRecipients(theirs, 'all', ['me@example.com'])[0]).toEqual({ email: 'boss@example.org', name: 'Boss' })
    expect(groupCounts(theirs, ['me@example.com'])).toEqual({ all: 5, yes: 1, waiting: 2 })
  })
})
