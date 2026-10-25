// The editor's warning for orders on a recipient address, and how the run log names duplicates.
import { describe, expect, it } from 'vitest'
import { actionText, matchesRecipient, needsSenderCondition } from './OrdersSection'
import { orderName } from './types'

const base = { trigger: 'message' as const, query: '', actions: {}, errand: '', from_me: false, from_trusted: false }

describe('orders on a recipient address', () => {
  it('sees a recipient condition in the search', () => {
    expect(matchesRecipient('to:memory@example.com')).toBe(true)
    expect(matchesRecipient('is:unread (cc:team@example.com)')).toBe(true)
    expect(matchesRecipient('deliveredto:alias@example.com')).toBe(true)
    expect(matchesRecipient('from:billing@example.com subject:to:do')).toBe(false)
  })
  it('warns only when the order writes and nothing limits the sender', () => {
    expect(needsSenderCondition({ ...base, query: 'to:memory@example.com', actions: { star: true } })).toBe(false)
    expect(needsSenderCondition({ ...base, query: 'to:memory@example.com', actions: { add_to_calendar: true } })).toBe(true)
    expect(needsSenderCondition({ ...base, query: 'to:memory@example.com', errand: 'Remember this' })).toBe(true)
    expect(needsSenderCondition({ ...base, query: 'to:memory@example.com', errand: 'x', from_me: true })).toBe(false)
    expect(needsSenderCondition({ ...base, query: 'to:memory@example.com', errand: 'x', from_trusted: true })).toBe(false)
    expect(needsSenderCondition({ ...base, query: 'to:memory@example.com from:me@example.com', errand: 'x' })).toBe(false)
    expect(needsSenderCondition({ ...base, trigger: 'schedule', query: 'to:memory@example.com', errand: 'x' })).toBe(false)
  })
})

describe('the run log', () => {
  // Actions by name ("Add to calendar", "Notify me"), not their keys.
  it('names the built-in order in the reader language and the order a duplicate came from', () => {
    expect(orderName({ name: 'Follow my forwarded notes', builtin: 'forwarded_notes' })).toBe('Follow my forwarded notes')
    expect(orderName({ name: 'Bills', builtin: null })).toBe('Bills')
    expect(actionText({ action: 'add_to_calendar', result: 'duplicate', by: { order_id: 1, name: 'Work invites' } }))
      .toBe('Add to calendar skipped: Work invites already did it')
    expect(actionText({ action: 'add_to_calendar', result: 'duplicate' })).toBe('Add to calendar skipped: an approval for it is waiting')
    expect(actionText({ action: 'notify', result: 'sent' })).toBe('Notify me')
  })
})
