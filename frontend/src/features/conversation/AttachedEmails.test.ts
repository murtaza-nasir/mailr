// mail.open_attached_email: which attachments are cards, and the card's header line.
import { describe, expect, it } from 'vitest'
import type { Attachment } from '../../api/types'
import { attachedEmailLabel, attachedEmails } from './AttachedEmails'

const summary = { subject: 'New access cards', from_name: 'Harbor Property', from_email: 'office@harborproperty.example', date: null, excerpt: '', attachments: 0 }

describe('attached emails', () => {
  it('are the attachments with a summary; a broken one stays a file', () => {
    const atts: Attachment[] = [
      { part: 2, filename: 'quote.pdf', type: 'application/pdf', size: 10, inline: false, cid: null },
      { part: 3, filename: 'New access cards.eml', type: 'message/rfc822', size: 99, inline: false, cid: null, email: summary },
      { part: 5, filename: 'attached-email.eml', type: 'message/rfc822', size: 0, inline: false, cid: null },
    ]
    expect(attachedEmails({ attachments: atts }).map((a) => a.part)).toEqual([3])
  })
  it('read "Attached email: subject — sender", with fallbacks', () => {
    expect(attachedEmailLabel(summary)).toBe('Attached email: New access cards — Harbor Property')
    expect(attachedEmailLabel({ ...summary, subject: '', from_name: '' })).toBe('Attached email: (no subject) — office@harborproperty.example')
    expect(attachedEmailLabel({ ...summary, from_name: '', from_email: '' })).toBe('Attached email: New access cards — unknown sender')
  })
})
