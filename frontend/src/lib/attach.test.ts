import { describe, expect, it } from 'vitest'
import { mentionsAttachment } from './html'

describe('mentionsAttachment', () => {
  it('spots a promised attachment in what the user wrote', () => {
    expect(mentionsAttachment('<p>Please find attached the report.</p>')).toBe(true)
    expect(mentionsAttachment('<p>I have attached the slides</p>')).toBe(true)
    expect(mentionsAttachment('<p>The contract is enclosed.</p>')).toBe(true)
    expect(mentionsAttachment('<p>See you on Friday.</p>')).toBe(false)
    expect(mentionsAttachment('<p>No attachment needed, thanks</p>')).toBe(true) // errs on asking
  })
})
