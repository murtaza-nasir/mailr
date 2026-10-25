import { describe, expect, it } from 'vitest'
import { startingName } from './steps/Welcome'

describe('the setup guide starts the name field', () => {
  it('with the name the account has', () => {
    expect(startingName({ name: 'Dana Whitfield', email: 'dana@lumencoffee.example' })).toBe('Dana Whitfield')
  })
  it('empty when the name is only the address local part (password sign-in without a name)', () => {
    expect(startingName({ name: 'admin', email: 'admin@northwind.example' })).toBe('')
    expect(startingName({ name: 'Admin', email: 'admin@northwind.example' })).toBe('')
  })
  it('empty when there is no name', () => {
    expect(startingName({ name: null, email: 'sam@northwind.example' })).toBe('')
  })
})
