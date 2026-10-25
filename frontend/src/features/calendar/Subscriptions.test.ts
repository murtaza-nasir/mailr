// ACT-SURFACES: web.calendar
// Calendar links (calendar.subscribe, calendar.unsubscribe): the refresh words and the link's server.
import { describe, expect, it } from 'vitest'
import { linkHost, refreshText } from './Subscriptions'

describe('calendar links', () => {
  it('names the refresh interval', () => {
    expect(refreshText(12)).toBe('every 12 hours')
    expect(refreshText(24)).toBe('every day')
    expect(refreshText(72)).toBe('every 3 days')
    expect(refreshText(168)).toBe('every week')
  })
  it('shows the server of the link', () => {
    expect(linkHost('https://calendar.example.com/x/holidays.ics')).toBe('calendar.example.com')
    expect(linkHost('not a url')).toBe('not a url')
  })
})
