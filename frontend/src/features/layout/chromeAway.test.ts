import { describe, expect, it } from 'vitest'
import { nextAway } from './chromeAway'

describe('nextAway', () => {
  it('a scroll down moves the bars away, a scroll up brings them back', () => {
    expect(nextAway(false, 200, 40)).toBe(true)
    expect(nextAway(true, 160, -40)).toBe(false)
  })
  it('small moves change nothing', () => {
    expect(nextAway(true, 200, 3)).toBe(true)
    expect(nextAway(false, 200, -3)).toBe(false)
  })
  it('near the top the bars always show', () => {
    expect(nextAway(true, 10, 40)).toBe(false)
  })
})
