import { describe, expect, it } from 'vitest'
import { approvalFromSlug, approvalSlug, convFromSlug, convSlug } from './pubid'

describe('conversation URL form', () => {
  it('round-trips public ids and keeps small ids as numbers', () => {
    for (const id of [2 ** 52, 2 ** 52 + 123456789, 2 ** 53 - 1, 4815062280917733]) {
      const s = convSlug(id)
      expect(s).toMatch(/^c[0-9A-Za-z]+$/)
      expect(convFromSlug(s)).toBe(id)
    }
    expect(convSlug(19846)).toBe('19846')
    expect(convFromSlug('19846')).toBe(19846)
    expect(convFromSlug('4815062280917733')).toBe(4815062280917733)
    expect(convFromSlug('x!')).toBeNull()
    expect(convFromSlug(undefined)).toBeNull()
  })
})

describe('approval URL form', () => {
  it('a + base62 for public ids; plain numbers still read', () => {
    for (const id of [2 ** 52, 2 ** 52 + 987654321, 2 ** 53 - 1]) {
      const s = approvalSlug(id)
      expect(s).toMatch(/^a[0-9A-Za-z]+$/)
      expect(approvalFromSlug(s)).toBe(id)
      // A conversation's prefix is not an approval's.
      expect(convFromSlug(s)).toBeNull()
      expect(approvalFromSlug(convSlug(id))).toBeNull()
    }
    expect(approvalSlug(20)).toBe('20')
    expect(approvalFromSlug('20')).toBe(20)
    expect(approvalFromSlug('99999999999999999999')).toBeNull()
    expect(approvalFromSlug('a!')).toBeNull()
    expect(approvalFromSlug('')).toBeNull()
  })
})
