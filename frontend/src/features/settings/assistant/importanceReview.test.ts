// ACT-SURFACES: web.settings
// mail.review_importance: the queue's range, keys and the cursor after a choice.
import { describe, expect, it } from 'vitest'
import { afterChoice, rangeStart, reviewKey, type ReviewItem } from './importanceReview'

const item = (id: number): ReviewItem => ({
  message_id: id, conversation_id: id, from: { email: `${id}@example.org`, name: null }, subject: `S${id}`, snippet: '',
  date: null, important: false, judged_important: true, score: 0.6, borderline: true, reasons: [], source: 'heuristic',
})

describe('mail.review_importance', () => {
  it('starts each range at local midnight', () => {
    const now = new Date(2026, 9, 1, 15, 30)
    expect(rangeStart('today', now)).toEqual(new Date(2026, 9, 1))
    expect(rangeStart('3d', now)).toEqual(new Date(2026, 8, 29))
    expect(rangeStart('7d', now)).toEqual(new Date(2026, 8, 25))
  })

  it('maps one key per choice and leaves modified keys alone', () => {
    const k = (key: string, mod = false) => reviewKey({ key, ctrlKey: mod, metaKey: false, altKey: false })
    expect(k('i')).toBe('important')
    expect(k('n')).toBe('not_important')
    expect(k('j')).toBe('next')
    expect(k('k')).toBe('prev')
    expect(k('Enter')).toBe('open')
    expect(k('i', true)).toBeNull()
    expect(k('x')).toBeNull()
  })

  it('drops judged rows and keeps the cursor on the next one', () => {
    const items = [item(1), item(2), item(3)]
    expect(afterChoice(items, new Set([2]), 1)).toEqual({ items: [item(1), item(3)], cursor: 1 })
    expect(afterChoice(items, new Set([3]), 2)).toEqual({ items: [item(1), item(2)], cursor: 1 })
    expect(afterChoice(items, new Set([1, 2, 3]), 0)).toEqual({ items: [], cursor: 0 })
    // A row judged elsewhere: the cursor stays on its row.
    expect(afterChoice(items, new Set([1]), 2)).toEqual({ items: [item(2), item(3)], cursor: 1 })
  })
})
