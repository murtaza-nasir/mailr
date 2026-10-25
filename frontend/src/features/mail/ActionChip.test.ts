import { describe, expect, it } from 'vitest'
import { factFor } from './ActionChip'

const pay = (fact: string) => ({ kind: 'pay', label: 'Make a Payment', url: 'https://bank.example/pay', fact })

describe('factFor', () => {
  it('drops a due-date fact when the row shows a deadline signal', () => {
    expect(factFor({ primary_action: pay('due on 10/04/2026'), signals: [{ name: 'deadline' }] })).toBe(false)
    expect(factFor({ primary_action: pay('Payment due date: Oct 12'), signals: [{ name: 'deadline' }] })).toBe(false)
  })
  it('keeps the fact otherwise', () => {
    expect(factFor({ primary_action: pay('due on 10/04/2026'), signals: [] })).toBe(true)
    expect(factFor({ primary_action: pay('Arriving today 1:30 PM - 4:30 PM'), signals: [{ name: 'deadline' }] })).toBe(true)
    expect(factFor({ primary_action: pay('Minimum payment due: $35.00'), signals: [{ name: 'deadline' }] })).toBe(true)
  })
})
