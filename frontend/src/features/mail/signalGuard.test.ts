import { describe, expect, it } from 'vitest'
import { inSpamOrTrash, shownSignals } from './signalGuard'

const labels = [
  { id: 1, role: 'inbox' as const },
  { id: 2, role: 'junk' as const },
  { id: 3, role: 'trash' as const },
  { id: 10, role: null },
]
const due = { name: 'deadline', reason: '', date: '2026-10-06' }

describe('signals in Spam and Trash', () => {
  it('hides every chip of a conversation that is all in Spam or Trash', () => {
    expect(shownSignals({ signals: [due, { name: 'needs_reply', reason: '' }], label_ids: [2] }, labels)).toEqual([])
    expect(shownSignals({ signals: [due], label_ids: [3] }, labels)).toEqual([])
    expect(shownSignals({ signals: [due], label_ids: [2, 3] }, labels)).toEqual([])
  })
  it('keeps them elsewhere, and when one message is still in the inbox or carries a label', () => {
    expect(shownSignals({ signals: [due], label_ids: [1] }, labels)).toEqual([due])
    expect(shownSignals({ signals: [due], label_ids: [1, 3] }, labels)).toEqual([due])
    expect(shownSignals({ signals: [due], label_ids: [10, 3] }, labels)).toEqual([due])
  })
  it('does nothing before the labels have loaded', () => {
    expect(inSpamOrTrash([2], [])).toBe(false)
    expect(shownSignals({ signals: [due], label_ids: [2] }, [])).toEqual([due])
    expect(shownSignals({ label_ids: [1] }, labels)).toEqual([])
  })
})
