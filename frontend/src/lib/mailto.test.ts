// ACT-SURFACES: web.editor
// compose.deeplink: mailto: links and /compose links fill a draft (lib/mailto.ts). The cases in
// spec/cases/mailto-cases.yaml run here and on Android (MailtoTest).
import { describe, expect, it } from 'vitest'
import casesText from '../../../spec/cases/mailto-cases.yaml?raw'
import casesAndroid from '../../../android/app/src/test/resources/interaction/mailto-cases.yaml?raw'
import manifestText from '../../public/manifest.webmanifest?raw'
import { readFlowYaml } from './flowYaml'
import { composeHref, decodeEncodedWords, handlerUrl, isMailto, mailtoHref, parseComposeQuery, parseMailto, type MailtoFields } from './mailto'
import { handlerSupport } from '../features/settings/EmailLinks'

const show = (list: { email: string; name?: string }[]) => list.map((a) => (a.name ? `${a.name} <${a.email}>` : a.email))

describe('mailto-cases.yaml', () => {
  const file = readFlowYaml(casesText)
  it('the Android copy matches', () => {
    expect(casesAndroid).toBe(casesText)
  })
  for (const c of file.cases) {
    it(String(c.id), () => {
      const f = parseMailto(String(c.url))
      if (c.none) {
        expect(f).toBeNull()
        return
      }
      expect(f).not.toBeNull()
      expect(show(f!.to)).toEqual(c.to)
      expect(show(f!.cc)).toEqual(c.cc)
      expect(show(f!.bcc)).toEqual(c.bcc)
      expect(f!.subject).toBe(c.subject)
      expect(f!.body).toBe(c.body)
    })
  }
})

describe('/compose links (compose.deeplink)', () => {
  const fields: MailtoFields = {
    to: [{ email: 'dana+work@example.com', name: 'Lee, Dana' }, { email: 'bob@example.org' }],
    cc: [{ email: 'erin@example.com' }],
    bcc: [],
    subject: 'Plans & dates: 50% done',
    body: 'Line one\n\nLine two + more',
  }

  it('round-trips through composeHref', () => {
    const href = composeHref(fields)
    expect(href.startsWith('/compose?to=')).toBe(true)
    expect(parseComposeQuery(href.slice('/compose'.length))).toEqual(fields)
  })

  it('round-trips through a mailto: URL inside ?mailto= (what the browser handler opens)', () => {
    const m = mailtoHref(fields)
    expect(m).not.toContain('+')
    const handler = handlerUrl('https://mail.example.com/').replace('%s', encodeURIComponent(m))
    expect(handler.startsWith('https://mail.example.com/compose?mailto=mailto%3A')).toBe(true)
    const f = parseComposeQuery(new URL(handler).search)!
    expect(f.to.map((a) => a.email)).toEqual(['dana+work@example.com', 'bob@example.org'])
    expect(f.cc).toEqual([{ email: 'erin@example.com' }])
    expect(f.subject).toBe(fields.subject)
    expect(f.body).toBe(fields.body)
  })

  it('reads the plain fields, with a form query for the text and raw addresses', () => {
    const f = parseComposeQuery('?to=a+b@example.com&cc=c@example.com,d@example.com&subject=Hello+there&body=Hi%0Athere')!
    expect(f.to).toEqual([{ email: 'a+b@example.com' }])
    expect(f.cc.map((a) => a.email)).toEqual(['c@example.com', 'd@example.com'])
    expect(f.subject).toBe('Hello there')
    expect(f.body).toBe('Hi\nthere')
  })

  it('adds plain fields to a mailto= link; the plain subject wins', () => {
    const f = parseComposeQuery(`?mailto=${encodeURIComponent('mailto:a@example.com?subject=Old')}&to=b@example.com&subject=New`)!
    expect(f.to.map((a) => a.email)).toEqual(['a@example.com', 'b@example.com'])
    expect(f.subject).toBe('New')
  })

  it('reads a mailto= link that is not encoded (the desktop handler: xdg-open "…/compose?mailto=%u")', () => {
    // What the browser makes of it: it encodes only spaces, quotes and angle brackets.
    const f = parseComposeQuery('?mailto=mailto:a+b@example.com?subject=Tom%20%26%20Jerry&cc=c@example.com&body=1%2B1%0D%0A2')!
    expect(f.to).toEqual([{ email: 'a+b@example.com' }])
    expect(f.cc).toEqual([{ email: 'c@example.com' }])
    expect(f.subject).toBe('Tom & Jerry')
    expect(f.body).toBe('1+1\n2')
    expect(parseComposeQuery('?mailto=')).toEqual({ to: [], cc: [], bcc: [], subject: '', body: '' })
  })

  it('is null without any field, and a non-mailto value is ignored', () => {
    expect(parseComposeQuery('')).toBeNull()
    expect(parseComposeQuery('?x=1')).toBeNull()
    expect(parseComposeQuery('?mailto=javascript%3Aalert(1)')).toEqual({ to: [], cc: [], bcc: [], subject: '', body: '' })
  })

  it('knows a mailto: href', () => {
    expect(isMailto('MAILTO:a@b.c')).toBe(true)
    expect(isMailto(' mailto:')).toBe(true)
    expect(isMailto('https://example.com')).toBe(false)
    expect(isMailto(null)).toBe(false)
  })

  it('decodes encoded words, joining adjacent ones', () => {
    expect(decodeEncodedWords('=?utf-8?Q?Caf=C3=A9?= =?utf-8?Q?_au_lait?=')).toBe('Café au lait')
    expect(decodeEncodedWords('=?bogus-charset?Q?x?=')).toBe('=?bogus-charset?Q?x?=')
    expect(decodeEncodedWords('plain')).toBe('plain')
  })

  it('caps very long text', () => {
    const f = parseMailto(`mailto:?subject=${'a'.repeat(5000)}&body=${'b'.repeat(300_000)}`)!
    expect(f.subject.length).toBe(998)
    expect(f.body.length).toBe(50_000)
  })
})

describe('the installed app (manifest protocol_handlers)', () => {
  it('opens /compose?mailto=%s for mailto:', () => {
    const m = JSON.parse(manifestText) as { protocol_handlers?: { protocol: string; url: string }[] }
    expect(m.protocol_handlers).toEqual([{ protocol: 'mailto', url: '/compose?mailto=%s' }])
  })
})

describe('Email links support (settings.mail_handler)', () => {
  const chrome = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
  const firefox = 'Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0'
  const safari = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15'
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1'
  const android = 'Mozilla/5.0 (Linux; Android 16; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36'
  it('says what each browser can do', () => {
    expect(handlerSupport({ ua: chrome, secure: true, hasApi: true })).toBe('chromium')
    expect(handlerSupport({ ua: firefox, secure: true, hasApi: true })).toBe('firefox')
    expect(handlerSupport({ ua: safari, secure: true, hasApi: false })).toBe('safari')
    expect(handlerSupport({ ua: iphone, secure: true, hasApi: false })).toBe('ios')
    expect(handlerSupport({ ua: safari, touchPoints: 5, secure: true, hasApi: false })).toBe('ios')
    expect(handlerSupport({ ua: android, secure: true, hasApi: false })).toBe('android')
    expect(handlerSupport({ ua: chrome, secure: false, hasApi: true })).toBe('insecure')
    expect(handlerSupport({ ua: 'Other/1.0', secure: true, hasApi: false })).toBe('unsupported')
  })
})
