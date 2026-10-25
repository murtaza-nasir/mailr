// mailto: links (proofs of the holes, inverted): the parser cleans
// addresses and a
// link fills at most 50 recipients and 50,000 characters of body. Android runs the same rules
// (MailtoTest; spec/cases/mailto-cases.yaml holds the shared cases).
import { describe, expect, it } from 'vitest'
import { MAX_BODY, MAX_RECIPIENTS, parseComposeQuery, parseMailto } from './mailto'

const RLO = '‮'

describe('a mailto: link only fills a draft', () => {
  it('ignores every header but to, cc, bcc, subject and body', () => {
    const f = parseMailto('mailto:a@example.com?from=boss@example.com&attach=/etc/passwd&attachment=x&in-reply-to=%3Cx%40y%3E&reply-to=evil@evil.test&x-mailer=1')!
    expect(f).toEqual({ to: [{ email: 'a@example.com' }], cc: [], bcc: [], subject: '', body: '' })
  })
  it('keeps the subject on one line and never makes an address of CR/LF tricks', () => {
    const f = parseMailto('mailto:a@example.com?subject=Hi%0D%0ABcc:%20evil@evil.test&cc=b@example.com%0D%0ABcc:evil@evil.test')!
    expect(f.subject).toBe('Hi Bcc: evil@evil.test')
    expect(f.bcc).toEqual([])
    expect(f.cc).toEqual([])
  })
})

describe('limits', () => {
  it('fills at most 50 recipients, To, Cc and Bcc together', () => {
    const many = (p: string, n: number) => Array.from({ length: n }, (_, i) => `${p}${i}@evil.test`).join(',')
    const f = parseComposeQuery(`?to=${many('t', 30)}&cc=${many('c', 30)}&bcc=${many('b', 300)}`)!
    expect(f.to.length + f.cc.length + f.bcc.length).toBe(MAX_RECIPIENTS)
    expect([f.to.length, f.cc.length, f.bcc.length]).toEqual([30, 20, 0])
    const g = parseMailto(`mailto:?bcc=${many('b', 300)}`)!
    expect(g.bcc).toHaveLength(MAX_RECIPIENTS)
  })
  it('fills at most 50,000 characters of body', () => {
    expect(MAX_BODY).toBe(50_000)
    expect(parseMailto(`mailto:a@example.com?body=${'x'.repeat(200_000)}`)!.body).toHaveLength(MAX_BODY)
    expect(parseComposeQuery(`?to=a@example.com&body=${'y'.repeat(200_000)}`)!.body).toHaveLength(MAX_BODY)
  })
})

describe('bidi and invisible characters in addresses', () => {
  it('the mailto parser removes a right-to-left override from an address', () => {
    const f = parseMailto(`mailto:${encodeURIComponent(`${RLO}moc.ynapmoc`)}@evil.test`)!
    expect(f.to[0].email).toBe('moc.ynapmoc@evil.test')
  })
  it('removes zero-width characters and joiners, and refuses control characters', () => {
    const f = parseMailto('mailto:ceo%E2%80%8B@example.com,ann%E2%80%8D@example.com,a%07b@example.com')!
    expect(f.to.map((a) => a.email)).toEqual(['ceo@example.com', 'ann@example.com'])
  })
  it('removes bidi controls from a display name', () => {
    const f = parseMailto('mailto:%22Service%E2%80%AE%20Desk%22%20%3Cdesk@example.com%3E')!
    expect(f.to[0]).toEqual({ email: 'desk@example.com', name: 'Service Desk' })
  })
})
