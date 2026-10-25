import { describe, expect, it, vi, beforeEach } from 'vitest'

const toast = vi.fn()
vi.mock('../stores/ui', () => ({ toast: (t: unknown) => toast(t) }))

import { copyText, nameAndAddress } from './clipboard'
import { parseAddressList } from './format'

describe('nameAndAddress', () => {
  it('gives "Name <address>", the bare address without a name', () => {
    expect(nameAndAddress({ name: 'Bich Nguyen', email: 'bich@example.com' })).toBe('Bich Nguyen <bich@example.com>')
    expect(nameAndAddress({ name: '  ', email: 'bich@example.com' })).toBe('bich@example.com')
    expect(nameAndAddress({ email: 'bich@example.com' })).toBe('bich@example.com')
  })
  it('quotes a name with a comma or a quote, and a To field reads it back whole', () => {
    const a = { name: 'Nguyen, Bich', email: 'bich@example.com' }
    expect(nameAndAddress(a)).toBe('"Nguyen, Bich" <bich@example.com>')
    expect(parseAddressList(nameAndAddress(a))).toEqual([a])
    expect(nameAndAddress({ name: 'Bich "B" Nguyen', email: 'bich@example.com' })).toBe('"Bich \\"B\\" Nguyen" <bich@example.com>')
  })
})

describe('copyText', () => {
  beforeEach(() => toast.mockReset())
  it('copies and says so', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    expect(await copyText('bich@example.com', 'Copied')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('bich@example.com')
    expect(toast).toHaveBeenCalledWith({ message: 'Copied' })
  })
  it('without clipboard access shows the text, or the given error', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    expect(await copyText('bich@example.com', 'Copied')).toBe(false)
    expect(toast).toHaveBeenCalledWith({ message: 'bich@example.com' })
    vi.stubGlobal('navigator', {})
    await copyText('123456', 'Code copied', "Couldn't copy the code")
    expect(toast).toHaveBeenLastCalledWith({ message: "Couldn't copy the code", error: true })
  })
})

describe('parseAddressList reads what Copy name and address gives', () => {
  it('a quoted name with an escaped quote', () => {
    const a = { name: 'Bich "B" Nguyen', email: 'bich@example.com' }
    expect(parseAddressList(`${nameAndAddress(a)}, x@example.com`)).toEqual([a, { email: 'x@example.com' }])
  })
})
