import { describe, expect, it } from 'vitest'
import { noteMode } from './palette'

describe('noteMode', () => {
  it('takes "Add to <list>:" only once the colon is typed', () => {
    expect(noteMode('Add to Shopping: milk')).toBe('add')
    expect(noteMode('add to shopping:')).toBe('add')
    // Mail's favorites commands and a half-typed list name stay ordinary palette queries.
    expect(noteMode('Add to Favorites')).toBeNull()
    expect(noteMode('Add to ')).toBeNull()
  })
  it('knows the create prefixes', () => {
    expect(noteMode('New note: x')).toBe('note')
    expect(noteMode('New table: x')).toBe('table')
    expect(noteMode('New list: x')).toBeNull()
  })
})
