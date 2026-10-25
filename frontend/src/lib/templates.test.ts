// ACT-SURFACES: web.editor
import { describe, expect, it } from 'vitest'
import { shortcutBefore } from './templates'

// template.insert: which text before the cursor is a `;shortcut` trigger.
describe('shortcutBefore', () => {
  it('finds a shortcut at the start or after a space', () => {
    expect(shortcutBefore(';thx')).toBe('thx')
    expect(shortcutBefore('Hello ;Intro-2')).toBe('intro-2')
    expect(shortcutBefore('Hello ;thx')).toBe('thx')
  })
  it('ignores semicolons inside text', () => {
    expect(shortcutBefore('a;thx')).toBeNull()
    expect(shortcutBefore(';;thx')).toBeNull()
    expect(shortcutBefore('; thx')).toBeNull()
    expect(shortcutBefore(';')).toBeNull()
    expect(shortcutBefore(';thx more')).toBeNull()
  })
})
