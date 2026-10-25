import { describe, expect, it } from 'vitest'

import { conferenceLink, joinTarget, meetingProvider } from './eventInfo'

const ev = (url: string | null, location = '', description = '') => ({ url, location, description })

describe('conference links', () => {
  it('refuses look-alike hosts and plain http', () => {
    for (const bad of ['https://zoom.us.evil.example/j/1', 'http://evil.example/zoom.us', 'https://phish.example/?meet.google.com',
      'http://zoom.us/j/1', 'https://evilzoom.us/j/1', 'https://user@zoom.us.evil.example/j/1']) {
      expect(meetingProvider(bad), bad).toBeNull()
      expect(conferenceLink(ev(null, `Room 3 ${bad}`, `Join: ${bad}`)), bad).toBeNull()
    }
  })

  it('takes the providers and their subdomains', () => {
    expect(conferenceLink(ev('https://us02web.zoom.us/j/123?pwd=x'))).toBe('https://us02web.zoom.us/j/123?pwd=x')
    expect(conferenceLink(ev(null, 'https://meet.google.com/abc-defg-hij'))).toBe('https://meet.google.com/abc-defg-hij')
    expect(conferenceLink(ev('https://example.org/page', 'x', 'see https://teams.microsoft.com/l/meetup-join/1 now')))
      .toBe('https://teams.microsoft.com/l/meetup-join/1')
  })

  it('names the host of any other link', () => {
    expect(joinTarget(ev('https://meet.google.com/abc-defg-hij'))).toEqual({ url: 'https://meet.google.com/abc-defg-hij', label: 'Join' })
    expect(joinTarget(ev('https://zoom.us.evil.example/j/1'))).toEqual({ url: 'https://zoom.us.evil.example/j/1', label: 'Join on zoom.us.evil.example' })
    expect(joinTarget(ev('https://www.example.org/call'))?.label).toBe('Join on example.org')
    expect(joinTarget(ev('http://zoom.us/j/1'))).toBeNull()
    expect(joinTarget(ev(null, 'https://zoom.us.evil.example/j/1'))).toBeNull()
  })
})
