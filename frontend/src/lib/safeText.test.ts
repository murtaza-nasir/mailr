import { describe, expect, it } from 'vitest'
import { attachmentKind, mixedScript, nameAddressMismatch, riskyAttachment, safeAddress, safeText, senderTextReviver } from './safeText'
import { displayName, firstName } from './format'

describe('sender text', () => {
  it('removes bidi overrides and zero-width spaces, keeps joiners', () => {
    expect(safeText('Bob ‮moc.lapyap@ecivres')).toBe('Bob moc.lapyap@ecivres')
    expect(safeText('Pay​Pal')).toBe('PayPal')
    expect(safeText('\u{1F468}‍\u{1F469}')).toBe('\u{1F468}‍\u{1F469}')
    const parsed = JSON.parse('{"from_name":"A\\u202eB","subject":"x\\u2066y","filename":"inv\\u202efdp.exe","body":"\\u202e"}', senderTextReviver)
    expect(parsed).toEqual({ from_name: 'AB', subject: 'xy', filename: 'invfdp.exe', body: '‮' })
  })

  it('flags a display name that shows another address, and look-alike alphabets', () => {
    expect(nameAddressMismatch('ceo@hale.example', 'x@evil.test')).toBe('ceo@hale.example')
    expect(nameAddressMismatch('Bob <bob@example.org>', 'bob@example.org')).toBeNull()
    expect(displayName({ name: 'ceo@hale.example', email: 'x@evil.test' })).toBe('ceo@hale.example (x@evil.test)')
    expect(firstName({ name: 'ceo@hale.example', email: 'x@evil.test' })).toBe('x@evil.test')
    expect(mixedScript('PаyPal')).toBe(true)
    expect(mixedScript('Иван')).toBe(false)
    expect(mixedScript('PayPal')).toBe(false)
  })

  it('marks programs and scripts whatever type the sender declared', () => {
    expect(riskyAttachment('invoice‮fdp.exe')).toBe(true)
    expect(riskyAttachment('report.pdf')).toBe(false)
    expect(attachmentKind('invoicefdp.exe', 'application/pdf')).toBe('risky')
    expect(attachmentKind('photo.jpg', 'image/jpeg')).toBe('image')
    expect(attachmentKind('photo.zip', 'image/jpeg')).toBe('file')
    expect(attachmentKind('a.pdf', 'application/pdf')).toBe('pdf')
  })
})

describe('sender addresses', () => {
  it('drop bidi, invisible, joiner, control characters and white space', () => {
    expect(safeAddress('"‮moc.lapyap@ecivres"@evil.test')).toBe('"moc.lapyap@ecivres"@evil.test')
    expect(safeAddress('ceo​@evil.test')).toBe('ceo@evil.test')
    expect(safeAddress('service⁦@evil.test ')).toBe('service@evil.test')
    expect(safeAddress('a‍@x.example')).toBe('a@x.example')
  })
  it('are cleaned as the API client parses', () => {
    const parsed = JSON.parse(JSON.stringify({ from: { name: 'S', email: 'x‮@evil.test' }, from_email: 'y​@evil.test', note: 'a‮b' }), senderTextReviver)
    expect(parsed.from.email).toBe('x@evil.test')
    expect(parsed.from_email).toBe('y@evil.test')
    expect(parsed.note).toBe('a‮b') // other fields as they are
  })
})
