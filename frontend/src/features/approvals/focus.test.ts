import { describe, expect, it } from 'vitest'
import { approvalSlug } from '../../lib/pubid'
import { parseApprovalId } from './focus'

describe('parseApprovalId', () => {
  it('reads the id of an approval link', () => {
    expect(parseApprovalId('41')).toBe(41)
    // The public short form (a + base62) and the public number.
    expect(parseApprovalId(approvalSlug(2 ** 52 + 5))).toBe(2 ** 52 + 5)
    expect(parseApprovalId(String(2 ** 52 + 5))).toBe(2 ** 52 + 5)
    expect(parseApprovalId(undefined)).toBeNull()
    expect(parseApprovalId('')).toBeNull()
  })
  it('marks anything else as not a valid id', () => {
    expect(parseApprovalId('abc')).toBeUndefined()
    expect(parseApprovalId('0')).toBeUndefined()
    expect(parseApprovalId('4.1')).toBeUndefined()
    expect(parseApprovalId('cK7fQ2mXb9')).toBeUndefined() // a conversation's form
  })
})
