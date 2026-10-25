// Focus's key hints come from the binding tables. Letters show as the key to press (r, not
// R, which reads as Shift+r, a different action), and every hint names a bound key.
import { describe, expect, it } from 'vitest'
import { CALENDAR, CONV, LIST, RECORDINGS, SELECTED, TASKS, hintKeys, hintsFor } from './hints'

describe('Focus key hints', () => {
  it('show reply and reply all as r and a, not R and A', () => {
    const conv = Object.fromEntries(hintsFor('mail', CONV).map(([k, l]) => [l, k]))
    expect(conv['reply']).toEqual(['r'])
    expect(conv['reply all']).toEqual(['a'])
    expect(conv['next · prev']).toEqual(['j', 'k'])
  })
  it('spell Shift for an upper-case binding', () => {
    expect(hintKeys('mail', 'markRead')).toEqual(['Shift', 'i'])
  })
  it('have a bound key for every hint in every scope', () => {
    for (const [scope, hints] of [['mail', LIST], ['mail', CONV], ['mail', SELECTED], ['calendar', CALENDAR], ['tasks', TASKS], ['recordings', RECORDINGS]] as const)
      for (const [actions, label] of hints)
        for (const a of actions) expect(hintKeys(scope, a), `${scope} ${label} ${a}`).not.toEqual([])
  })
})
