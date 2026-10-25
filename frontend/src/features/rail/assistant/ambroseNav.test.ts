import { describe, expect, it } from 'vitest'
import { appPathOf } from './ambroseNav'

const O = 'https://mail.example.org'

describe('appPathOf', () => {
  it('keeps links to Mailr pages', () => {
    expect(appPathOf('/calendar/week', O)).toBe('/calendar/week')
    expect(appPathOf('/notes/n/abc#i-1', O)).toBe('/notes/n/abc#i-1')
    expect(appPathOf(`${O}/tasks/today?x=1`, O)).toBe('/tasks/today?x=1')
    expect(appPathOf('/tables/t/9/r/3', O)).toBe('/tables/t/9/r/3')
  })
  it('leaves other links to the browser', () => {
    expect(appPathOf('https://example.com/calendar', O)).toBeNull()
    expect(appPathOf('//example.com/mail/inbox', O)).toBeNull()
    expect(appPathOf('/api/v1/me', O)).toBeNull()
    expect(appPathOf('mailto:a@example.org', O)).toBeNull()
    expect(appPathOf('', O)).toBeNull()
    expect(appPathOf(undefined, O)).toBeNull()
  })
})
