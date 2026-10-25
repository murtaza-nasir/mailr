// ACT-SURFACES: web.settings
import { describe, expect, it, vi } from 'vitest'
import { bodyCsp, buildBodyDoc, imagesAllowedByDefault, isEmptyHtml, joinQuote, isTextAsHtml, looksStyled, reflowPlainText, splitQuote, textToHtml, trackersLabel } from './html'

describe('quote splitting', () => {
  it('splits at the quote marker', () => {
    const h = '<p>Hi</p><div class="mailr_quote"><div>On x wrote:</div><blockquote>old</blockquote></div>'
    const { body, quote } = splitQuote(h)
    expect(body).toBe('<p>Hi</p>')
    expect(quote.startsWith('<div class="mailr_quote"')).toBe(true)
    expect(joinQuote(body, quote)).toBe(`<p>Hi</p><br>${quote}`)
  })
  it('no quote', () => {
    expect(splitQuote('<p>x</p>')).toEqual({ body: '<p>x</p>', quote: '', hadSignature: false })
    expect(splitQuote(null)).toEqual({ body: '', quote: '', hadSignature: false })
  })
  it('signature goes between body and quote', () => {
    expect(joinQuote('<p>a</p>', '', 'Sig')).toContain('-- <br>Sig')
  })
})

describe('misc html', () => {
  it('detects empty html', () => {
    expect(isEmptyHtml('<p></p>')).toBe(true)
    expect(isEmptyHtml('<p>&nbsp;</p>')).toBe(true)
    expect(isEmptyHtml('<p>x</p>')).toBe(false)
    expect(isEmptyHtml('<img src="a">')).toBe(false)
  })
  it('detects styled emails', () => {
    expect(looksStyled('<td bgcolor="#fff">')).toBe(true)
    expect(looksStyled('<p style="color: red">')).toBe(true)
    expect(looksStyled('<p>plain</p>')).toBe(false)
  })
  it('text to html escapes and links', () => {
    expect(textToHtml('a < b\nhttps://x.io')).toBe('a &lt; b<br><a href="https://x.io">https://x.io</a>')
  })
  it('csp blocks remote images unless allowed', () => {
    expect(buildBodyDoc({ html: '', dark: false, paper: false, allowImages: false })).not.toContain('https: http:')
    expect(buildBodyDoc({ html: '', dark: false, paper: false, allowImages: true })).toContain('img-src data: cid: https:')
  })
  it('uses the server csp, never looser than the local choice', () => {
    const server = "default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src data: https: http:"
    expect(bodyCsp(true, server)).toBe(`${server}; media-src 'none'; base-uri 'none'; form-action 'none';`)
    expect(bodyCsp(false, server)).toContain('img-src data:')
    expect(bodyCsp(false, server)).not.toMatch(/https?:/)
    expect(bodyCsp(true, 'img-src data:')).toMatch(/^default-src 'none'; img-src data:; media-src 'none'; base-uri 'none'; form-action 'none';$/)
    expect(bodyCsp(true, 'img-src "x"><script>')).not.toMatch(/["<>]/)
    expect(buildBodyDoc({ html: '', dark: false, paper: false, allowImages: true, csp: 'img-src data:' })).toContain("content=\"default-src 'none'; img-src data:;")
  })
})

describe('splitQuote signature handling', () => {
  it('drops a signature that is already in the HTML (Undo Send reopening the sent message)', () => {
    const sent = joinQuote('<p>Hello</p>', '', '<p>Rowan</p>')
    const again = splitQuote(sent)
    expect(again.hadSignature).toBe(true)
    expect(again.body).toBe('<p>Hello</p>')
    // Sending again adds exactly one signature.
    expect(joinQuote(again.body, again.quote, '<p>Rowan</p>').match(/mailr_signature/g)).toHaveLength(1)
  })
  it('keeps the quote separate', () => {
    const sent = joinQuote('<p>Hi</p>', '<div class="mailr_quote">old</div>', '<p>Sig</p>')
    const r = splitQuote(sent)
    expect(r.body).toBe('<p>Hi</p>')
    expect(r.quote).toContain('mailr_quote')
  })
})

// ACT mail.show_images: proxied bodies load images only from mailr's own origin.
describe('image proxy', () => {
  const proxyCsp = "default-src 'none'; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'; img-src 'self' data:"
  it('keeps the server csp to self and data when images are allowed', () => {
    const csp = bodyCsp(true, proxyCsp, true)
    expect(csp).toContain("img-src 'self' data:")
    expect(csp).not.toMatch(/https?:/)
  })
  it('drops self as well when images are not allowed', () => {
    const csp = bodyCsp(false, proxyCsp, true)
    expect(csp).toContain('img-src data:')
    expect(csp).not.toContain("'self'")
  })
  it('never lets a proxied body reach a remote host, even if the server csp would', () => {
    expect(bodyCsp(true, 'img-src data: https: http: *', true)).toMatch(/img-src data:;/)
  })
  it('falls back to a same-origin policy without a server csp', () => {
    const doc = buildBodyDoc({ html: '<img src="/api/v1/messages/1/images/ab">', dark: false, paper: false, allowImages: true, proxied: true })
    expect(doc).toContain("img-src 'self' data: cid: blob:;")
    expect(doc).not.toContain('https:')
  })
  it('labels the trackers indicator', () => {
    expect(trackersLabel(1)).toBe('1 tracker blocked')
    expect(trackersLabel(4)).toBe('4 trackers blocked')
  })
  it('loads images by default except for spam, "never", and untrusted senders under "ask"', () => {
    expect(imagesAllowedByDefault('always', false, false)).toBe(true)
    expect(imagesAllowedByDefault(undefined, false, false)).toBe(true)
    expect(imagesAllowedByDefault('always', true, true)).toBe(false)
    expect(imagesAllowedByDefault('never', false, true)).toBe(false)
    expect(imagesAllowedByDefault('ask', false, false)).toBe(false)
    expect(imagesAllowedByDefault('ask', false, true)).toBe(true)
  })
})

describe('proxied images in the srcdoc frame', () => {
  it("names Mailr's own origin next to 'self' (some browsers read 'self' as about:srcdoc)", () => {
    vi.stubGlobal('window', { location: { origin: 'https://mail.example.org' } })
    const csp = bodyCsp(true, "default-src 'none'; img-src 'self' data:", true)
    expect(csp).toContain(`img-src 'self' ${window.location.origin} data:`)
    expect(bodyCsp(true, null, true)).toContain(`'self' ${window.location.origin}`)
    expect(bodyCsp(false, "img-src 'self' data:", true)).not.toContain(window.location.origin)
    vi.unstubAllGlobals()
  })
})

describe('message body wrapping', () => {
  // overflow-wrap:anywhere also lowers a table cell's minimum width, so a column of small icons
  // (GitHub's "Status") collapsed to one letter and its heading broke letter by letter.
  it('breaks only words that overflow, never shrinking table columns below a word', () => {
    const doc = buildBodyDoc({ html: '<table><tr><th>Status</th><th>Job</th></tr></table>', dark: false, paper: true, allowImages: false })
    expect(doc).toContain('overflow-wrap:break-word')
    expect(doc).not.toContain('anywhere')
  })
})

describe('reflowPlainText', () => {
  it('joins the lines of a hard-wrapped paragraph', () => {
    const text = 'Hi Alex,\n\nThanks for the brief. On Thursday 20 minutes is plenty, and it\nwould help us settle the van wrap before the board review.\n\nDana'
    expect(reflowPlainText(text)).toBe('Hi Alex,\n\nThanks for the brief. On Thursday 20 minutes is plenty, and it would help us settle the van wrap before the board review.\n\nDana')
  })
  it('joins a longer line cut mid-sentence when the next goes on in lower case', () => {
    const text = "Could you present the Lumen concepts at Thursday's studio review? 20 minutes is plenty, and it\nwould help the team see how we are handling the wholesale range."
    expect(reflowPlainText(text)).toBe("Could you present the Lumen concepts at Thursday's studio review? 20 minutes is plenty, and it would help the team see how we are handling the wholesale range.")
    const kept = 'Thanks so much for all of the help with the launch of the new catalogue this week\nDana'
    expect(reflowPlainText(kept)).toBe(kept)
  })
  it('reads CRLF line ends', () => {
    expect(reflowPlainText('Could you present the Lumen concepts at the review on Thursday, and it\r\nwould help.')).toBe('Could you present the Lumen concepts at the review on Thursday, and it would help.')
  })
  it('knows a text-only message the store dressed as HTML', () => {
    expect(isTextAsHtml("<div>Hi Alex,\n<br> <br>Could you, and it\n<br>would help.\n<br></div>")).toBe(true)
    expect(isTextAsHtml('<div><p>Hi</p></div>')).toBe(false)
    expect(isTextAsHtml('<p>Hi<br>there</p>')).toBe(false)
  })
  it('joins format=flowed lines (a trailing space)', () => {
    expect(reflowPlainText('short and \nflowed')).toBe('short and flowed')
  })
  it('keeps short lines, lists, quotes, indented text and the signature line', () => {
    const kept = 'Agenda:\n- one item that is long enough to look like a wrapped line of text\n- two\n> quoted line that is long enough to look like a wrapped line too\n> more\n    code that is long enough to look like a wrapped line of the text\n    more\n-- \nDana'
    expect(reflowPlainText(kept)).toBe(kept)
    expect(reflowPlainText('Best,\nDana')).toBe('Best,\nDana')
  })
})
