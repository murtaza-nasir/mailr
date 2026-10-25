// The Screener's words and rules (features/mail/screener.ts): who a decision covers, the card's
// counts, the toast after a decision, a past decision in Settings, and when the nav shows it.
import { describe, expect, it } from 'vitest'
import { decisionText, doneText, heldText, senderScope, senderTitle, showScreenerNav } from './screener'

describe('screener', () => {
  it('says who a decision covers', () => {
    expect(senderScope({ kind: 'domain', key: 'acme.example' })).toBe('Everyone at acme.example')
    expect(senderScope({ kind: 'address', key: 'ann@gmail.com' })).toBe('This address only')
  })

  it('names the sender and counts the held mail', () => {
    expect(senderTitle({ name: '  ', email: 'x@y.example' })).toBe('x@y.example')
    expect(senderTitle({ name: 'Ann', email: 'x@y.example' })).toBe('Ann')
    expect(heldText({ messages: 1, conversations: 1 })).toBe('1 message')
    expect(heldText({ messages: 3, conversations: 2 })).toBe('3 messages in 2 conversations')
  })

  it('says what a decision did', () => {
    expect(doneText({ decision: 'allow' }, 'Ann')).toBe('Allowed Ann: their mail is in your Inbox')
    expect(doneText({ decision: 'block' }, 'Ann')).toBe('Blocked Ann: their mail goes to Spam')
    expect(doneText({ decision: 'block', block_to: 'trash' }, 'Ann')).toBe('Blocked Ann: their mail goes to Trash')
    expect(doneText({ decision: 'label', feed: true }, 'Ann')).toBe('Ann goes to Feed, out of your Inbox')
    expect(doneText({ decision: 'label', label_id: 4 }, 'Ann', 'Shopping')).toBe('Ann goes to Shopping, out of your Inbox')
  })

  it('describes past decisions', () => {
    expect(decisionText({ status: 'allowed', label: '', block_to: 'spam' })).toBe('Allowed')
    expect(decisionText({ status: 'blocked', label: '', block_to: 'trash' })).toBe('Blocked, to Trash')
    expect(decisionText({ status: 'label', label: 'Feed', block_to: 'spam' })).toBe('To Feed')
    expect(decisionText({ status: 'label', label: '', block_to: 'spam' })).toBe('To a deleted label')
  })

  it('shows the nav entry while the Screener is on', () => {
    expect(showScreenerNav(false)).toBe(false)
    expect(showScreenerNav(undefined)).toBe(false)
    expect(showScreenerNav(true)).toBe(true)
  })
})
