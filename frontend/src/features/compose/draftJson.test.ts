// "Draft saved" compares what the form shows with what the server holds (draftJson).
import { describe, expect, it } from 'vitest'
import { draftJson } from './useDraftLocalCopy'

describe('draftJson', () => {
  it('is the same for the form payload and the server\'s answer to a merged save', () => {
    // Taken from a failing e2e run (live-edits.spec.ts, 13 Oct 2026): the form's payload and the
    // server's payload for the same draft, keys in another order, empty fields on one side only.
    const form = {
      identity_id: 599, to: [], cc: [], bcc: [], subject: 'Order E2E',
      html: '<p dir="auto">Hi Dana,</p><p dir="auto">See you Friday.</p><p dir="auto">Best, Alex and Sam</p>',
      in_reply_to_message_id: null, forward_of_message_id: null, attachments: [], headers: undefined,
    }
    const server = {
      cc: [], to: [], bcc: [],
      html: '<p dir="auto">Hi Dana,</p><p dir="auto">See you Friday.</p><p dir="auto">Best, Alex and Sam</p>',
      subject: 'Order E2E', attachments: [], identity_id: 599, forward_of_message_id: null, in_reply_to_message_id: null,
    }
    expect(JSON.stringify(form)).not.toBe(JSON.stringify(server))
    expect(draftJson(form)).toBe(draftJson(server))
  })

  it('tells different content apart', () => {
    expect(draftJson({ subject: 'a', html: '<p>x</p>' })).not.toBe(draftJson({ subject: 'a', html: '<p>y</p>' }))
    expect(draftJson({ to: [{ email: 'a@x' }] })).not.toBe(draftJson({ to: [] }))
    expect(draftJson({ subject: 'a' })).not.toBe(draftJson({ subject: 'a', html: '<p>x</p>' }))
  })
})
