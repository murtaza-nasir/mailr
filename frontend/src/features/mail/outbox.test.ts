import { describe, expect, it } from 'vitest'
import type { Message } from '../../api/types'
import { inOutbox, notSent, plainSendError, sendStateFor, type OutboxRow } from './outbox'

const row = (o: Partial<OutboxRow>): OutboxRow => ({
  id: 1, status: 'queued', send_at: '', recipients: ['marco@example.org'], subject: 'Quote', identity_id: 1, token_name: null,
  approval_reason: '', error: '', ...o,
})
const msg = (o: Partial<Message>): Message => ({ id: 5, draft: false, sent_by_me: false, message_id: null, ...o } as Message)

describe('send states', () => {
  it('names a draft that is on its way, failed or waiting for approval', () => {
    expect(sendStateFor(msg({ draft: true }), [row({ draft_id: 5 })])?.kind).toBe('sending')
    expect(sendStateFor(msg({ draft: true }), [row({ draft_id: 5, status: 'failed' })])?.kind).toBe('not_sent')
    expect(sendStateFor(msg({ draft: true }), [row({ draft_id: 5, status: 'awaiting_approval' })])?.kind).toBe('approval')
    expect(sendStateFor(msg({ draft: true }), [row({ draft_id: 6 })])).toBeNull()
  })
  it('marks a message that just left as Sent, by its Message-ID', () => {
    const sent = row({ status: 'sent', sent_message_id: '<ABC@example.com>' })
    expect(sendStateFor(msg({ sent_by_me: true, message_id: '<abc@example.com>' }), [sent])?.kind).toBe('sent')
    expect(sendStateFor(msg({ sent_by_me: true, message_id: '<other@example.com>' }), [sent])).toBeNull()
  })
  it('says send errors in plain words', () => {
    expect(plainSendError('SMTPRecipientsRefused: 550 5.1.1 no such user')).toMatch(/address was refused/)
    expect(plainSendError('SMTPAuthenticationError: 535 bad credentials')).toMatch(/Sign-in refused/)
    expect(plainSendError('TimeoutError: timed out')).toMatch(/Couldn't reach the mail server/)
    expect(plainSendError('Sending was interrupted. The message may have been sent')).toMatch(/Check Sent/)
    expect(plainSendError('KeyError: x')).toBe('Not accepted by the mail server.')
  })
  it('lists a send that may have gone out with the ones not sent, so the user decides', () => {
    const maybe = row({ draft_id: 5, status: 'uncertain', error: 'Sending was interrupted after the message was handed to the mail server.' })
    expect(notSent(maybe) && inOutbox(maybe)).toBe(true)
    expect(sendStateFor(msg({ draft: true }), [maybe])?.kind).toBe('not_sent')
    expect(plainSendError(maybe.error)).toMatch(/Check Sent/)
  })
})
