import { describe, expect, it } from 'vitest'
import { typedGuests } from './fields'

describe('typedGuests: what Save does with guest text still in the field', () => {
  it('nothing typed adds nobody', () => expect(typedGuests('  ')).toEqual([]))
  it('an address is a guest', () => expect(typedGuests(' priya@example.org ')).toEqual([{ email: 'priya@example.org' }]))
  it('several addresses, as Enter takes them', () =>
    expect(typedGuests('a@x.example, Bo <b@y.example>')).toEqual([{ email: 'a@x.example' }, { email: 'b@y.example' }]))
  it('a name without an address stops Save', () => expect(typedGuests('priya')).toBeNull())
})
