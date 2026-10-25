// ACT-SURFACES: web.settings
// mail.label on web.settings: the three "how sure" levels of Learn from my choices.
import { describe, expect, it } from 'vitest'
import { DEFAULT_SETTINGS } from '../../../api/types'
import { LEARNING_LEVELS, learningLevel, learningOn, levelPatch } from './learningLevels'

describe('learning levels (mail.label)', () => {
  it('maps each level to its precision and minimum examples', () => {
    expect(levelPatch('cautious')).toEqual({ auto_label_precision: 0.95, auto_label_min_examples: 50 })
    expect(levelPatch('balanced')).toEqual({ auto_label_precision: 0.9, auto_label_min_examples: 30 })
    expect(levelPatch('eager')).toEqual({ auto_label_precision: 0.8, auto_label_min_examples: 15 })
  })
  it('reads each level back, and Balanced from the defaults', () => {
    for (const l of LEARNING_LEVELS) expect(learningLevel(levelPatch(l.id))).toBe(l.id)
    expect(learningLevel(undefined)).toBe('balanced')
    expect(learningLevel(DEFAULT_SETTINGS)).toBe('balanced')
  })
  it('calls any other pair custom', () => {
    expect(learningLevel({ auto_label_precision: 0.9, auto_label_min_examples: 31 })).toBe('custom')
    expect(learningLevel({ auto_label_precision: 0.7, auto_label_min_examples: 30 })).toBe('custom')
  })
  it('is on unless turned off', () => {
    expect(learningOn(undefined)).toBe(true)
    expect(learningOn({ auto_label: true })).toBe(true)
    expect(learningOn({ auto_label: false })).toBe(false)
  })
})
