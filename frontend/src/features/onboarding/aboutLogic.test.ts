import { describe, expect, it } from 'vitest'
import { draftFromSummary, draftToSave, EMPTY_FIELDS, fieldsToSave, parseSignoff, writingText } from './aboutLogic'
import { progress, setupCardMode, setupCardPlace } from './SetupCard'
import { STEP_ORDER } from './steps'

describe('sign-offs', () => {
  it('splits "Love, Rosa" into lines and finds the name', () => {
    expect(parseSignoff('Love, Rosa')).toEqual({ signature: 'Love,\nRosa', name: 'Rosa' })
    expect(parseSignoff('Best wishes,\nRosa Lind')).toEqual({ signature: 'Best wishes,\nRosa Lind', name: 'Rosa Lind' })
    expect(parseSignoff('Mary-Jane')).toEqual({ signature: 'Mary-Jane', name: 'Mary-Jane' })
    expect(parseSignoff('Thanks so much for everything')).toEqual({ signature: 'Thanks so much for everything', name: '' })
    expect(parseSignoff('  ')).toEqual({ signature: '', name: '' })
  })
})

describe('the fields form', () => {
  it('leaves empty fields out, so nothing is cleared', () => {
    expect(fieldsToSave(EMPTY_FIELDS)).toEqual({ profile: {}, memories: [], signature: null })
  })
  it('turns tones and her own words into writing preferences', () => {
    expect(writingText(['Warm', 'Short and to the point'], 'Start with Hi.')).toBe('Warm, Short and to the point. Start with Hi.')
    const s = fieldsToSave({ name: 'Rosa', role: 'Teacher', tones: ['Friendly'], writing: '', signoff: 'Love, Rosa', notes: 'Sam is my husband.' })
    expect(s).toEqual({
      profile: { preferred_name: 'Rosa', role: 'Teacher', writing: 'Friendly.', signoff_name: 'Rosa', about: 'Sam is my husband.' },
      memories: [],
      signature: 'Love,\nRosa',
    })
  })
})

describe('the chat summary', () => {
  const summary = {
    profile: { preferred_name: 'Rosa', role: 'Teacher', about: 'I teach.', writing: 'Warm.', signoff_name: 'Rosa', languages: '' },
    signature: 'Love,\nRosa',
    memories: [{ text: 'No Sunday mornings', kind: 'preference' }, { text: 'Sam is my husband', kind: 'relationship' }],
  }
  it('saves only what she kept, as she edited it', () => {
    const d = draftFromSummary(summary)
    d.role = 'Teacher (year 3)'
    d.memories[1].keep = false
    expect(draftToSave(d)).toEqual({
      profile: { preferred_name: 'Rosa', role: 'Teacher (year 3)', about: 'I teach.', writing: 'Warm.', signoff_name: 'Rosa' },
      memories: [{ text: 'No Sunday mornings', kind: 'preference' }],
      signature: 'Love,\nRosa',
    })
  })
})

describe('the Continue setup card', () => {
  it('shows while no mail account is connected, and for older accounts with no mail yet', () => {
    expect(setupCardMode({ status: 'active' }, false)).toBe('continue')
    expect(setupCardMode({ status: 'pending' }, false)).toBe('start')
    expect(setupCardMode(undefined, false)).toBe('start')
    expect(setupCardMode(undefined, true)).toBeNull()
    expect(setupCardMode({ status: 'done' }, false)).toBeNull()
    expect(setupCardMode({ status: 'dismissed' }, false)).toBeNull()
  })
  it('hides on an account with mail connected, whatever the guide marked', () => {
    expect(setupCardMode({ status: 'active' }, true)).toBeNull()
    expect(setupCardMode({ status: 'active', steps: {} }, true)).toBeNull()
  })
  it('counts what the account already has: the guide opened, a mail account', () => {
    expect(progress({ status: 'active' }, false)).toEqual({ done: 1, total: STEP_ORDER.length - 1 })
    expect(progress({ status: 'active', steps: { look: 'done' } }, true).done).toBe(3)
    expect(progress({ status: 'active', steps: { accounts: 'done' } }, true).done).toBe(2)
  })
  it('never says "8 of 8 steps done": with every step marked it hides', () => {
    const all = Object.fromEntries(STEP_ORDER.filter((s) => s !== 'done').map((s) => [s, 'done' as const]))
    expect(setupCardMode({ status: 'active', steps: all }, false)).toBeNull()
    const { [STEP_ORDER[2]]: _third, ...allButOne } = all
    expect(setupCardMode({ status: 'active', steps: allButOne }, false)).toBe('continue')
  })
  it('shows on mail pages only', () => {
    expect(setupCardPlace('/mail/inbox')).toBe(true)
    expect(setupCardPlace('/calendar/week')).toBe(false)
    expect(setupCardPlace('/tasks')).toBe(false)
    expect(setupCardPlace('/notes')).toBe(false)
    expect(setupCardPlace('/settings/you')).toBe(false)
  })
})
