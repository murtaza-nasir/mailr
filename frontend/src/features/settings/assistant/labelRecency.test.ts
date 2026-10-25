// ACT-SURFACES: web.settings
// mail.label on web.settings: the label recency setting read with its defaults.
import { describe, expect, it } from 'vitest'
import { HALF_LIVES, labelRecency, weightAfter } from './labelRecency'

describe('labelRecency (mail.label)', () => {
  it('defaults to on with a 12-month half-life', () => {
    expect(labelRecency(undefined)).toEqual({ enabled: true, half_life_months: 12 })
    expect(labelRecency({})).toEqual({ enabled: true, half_life_months: 12 })
  })
  it('keeps a valid choice and drops an unknown half-life', () => {
    expect(labelRecency({ label_recency: { enabled: false, half_life_months: 24 } })).toEqual({ enabled: false, half_life_months: 24 })
    expect(labelRecency({ label_recency: { enabled: true, half_life_months: 9 as never } })).toEqual({ enabled: true, half_life_months: 12 })
    expect(HALF_LIVES).toEqual([6, 12, 24])
  })
  it('halves a weight every half-life', () => {
    expect(weightAfter(0, 12)).toBe(1)
    expect(weightAfter(12, 12)).toBeCloseTo(0.5)
    expect(weightAfter(24, 6)).toBeCloseTo(0.0625)
  })
})
