import { describe, expect, it } from 'vitest'
import { startsOpen, summaryShown } from './aiCards'

const plain = { attachments: [], event_change: undefined }
const invite = { attachments: [{ type: 'text/calendar', filename: 'invite.ics' }], event_change: undefined } as never

describe('Ambrose and Summary cards', () => {
  it('no summary for a single message or an invitation', () => {
    expect(summaryShown({ messageCount: 1, hasBrief: false, newest: plain })).toBe(false)
    expect(summaryShown({ messageCount: 3, hasBrief: false, newest: invite })).toBe(false)
    expect(summaryShown({ messageCount: 3, hasBrief: false, newest: plain })).toBe(true)
  })
  it('a long thread starts with both folded', () => {
    const long = { messageCount: 3, hasBrief: true }
    expect(startsOpen(null, long, 'brief')).toBe(false)
    expect(startsOpen(null, long, 'summary')).toBe(false)
  })
  it('a short thread: the brief open, the summary folded under it', () => {
    expect(startsOpen(null, { messageCount: 2, hasBrief: true }, 'brief')).toBe(true)
    expect(startsOpen(null, { messageCount: 2, hasBrief: true }, 'summary')).toBe(false)
    expect(startsOpen(null, { messageCount: 2, hasBrief: false }, 'summary')).toBe(true)
  })
  it('the user\'s choice wins', () => {
    expect(startsOpen(true, { messageCount: 9, hasBrief: true }, 'summary')).toBe(true)
    expect(startsOpen(false, { messageCount: 2, hasBrief: false }, 'summary')).toBe(false)
  })
})
