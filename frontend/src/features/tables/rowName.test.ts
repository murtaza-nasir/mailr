import { describe, expect, it } from 'vitest'
import { rowName } from './actions'

describe('rowName (one toast per row)', () => {
  it('names a row by its first column when that is short text', () => {
    expect(rowName({ primary_column: 'c1' }, { cells: { c1: 'Hotel' } })).toBe('Hotel')
    expect(rowName({ primary_column: 'c1' }, { cells: { c1: 'x'.repeat(41) } })).toBeNull()
    expect(rowName({ primary_column: null }, { cells: { c1: 'Hotel' } })).toBeNull()
    expect(rowName(undefined, { cells: {} })).toBeNull()
  })
})
