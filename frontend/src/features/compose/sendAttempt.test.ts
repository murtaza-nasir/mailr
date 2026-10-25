import { describe, expect, it } from 'vitest'
import { ApiError } from '../../api/client'
import { sendAttempt } from './sendAttempt'

describe('send attempts', () => {
  it('keeps the key after a lost answer and starts a new one after a definite answer', () => {
    const a = sendAttempt()
    const first = a.key()
    a.settle(new ApiError(0, 'offline', undefined, 'network'))
    expect(a.key()).toBe(first)
    a.settle(new ApiError(400, 'unknown draft'))
    const second = a.key()
    expect(second).not.toBe(first)
    a.settle()
    expect(a.key()).not.toBe(second)
  })
})
