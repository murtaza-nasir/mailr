// HTML helpers for compose (quote splitting) and message rendering (iframe documents).

import { t } from '../i18n'

const QUOTE_MARK = '<div class="mailr_quote"'

/** Split composed HTML into the editable body and the quoted history block. */
const SIGNATURE_MARK = '<div class="mailr_signature">'

/** Split composer HTML into the editable body and the quoted history. A signature block that
 *  is already in the HTML (a sent message reopened by Undo) is removed from the body, because
 *  the composer adds the identity's signature itself when sending. */
export function splitQuote(html: string | null | undefined): { body: string; quote: string; hadSignature: boolean } {
  const h = html ?? ''
  const i = h.indexOf(QUOTE_MARK)
  let body = i < 0 ? h : h.slice(0, i)
  const quote = i < 0 ? '' : h.slice(i)
  const s = body.lastIndexOf(SIGNATURE_MARK)
  const hadSignature = s >= 0
  if (hadSignature) body = body.slice(0, s).replace(/(<br\s*\/?>\s*)+$/i, '')
  if (quote && body.endsWith('<br>')) body = body.slice(0, -4)
  return { body, quote, hadSignature }
}

export function joinQuote(body: string, quote: string, signature?: string): string {
  const sig = signature ? `<div class="mailr_signature"><br>-- <br>${signature}</div>` : ''
  return `${body}${sig}${quote ? `<br>${quote}` : ''}`
}

/** True when the editor HTML has no meaningful text or media. */
export function isEmptyHtml(html: string | null | undefined): boolean {
  if (!html) return true
  if (/<img\b/i.test(html)) return false
  return html.replace(/<[^>]+>/g, '').replace(/&nbsp;/g, ' ').trim() === ''
}

/** Whether an email sets its own colours (then render it on a light "paper" in dark mode). */
export function looksStyled(html: string): boolean {
  return /(\bbgcolor\s*=|\bbackground(-color)?\s*:|(?<![-\w])color\s*:|<table\b[^>]*\bbackground)/i.test(html)
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)
}

// A line that starts a structure of its own: a list item, a quote, indented text (code, a table),
// a signature separator. Reflow never joins it to the line before or after.
const STRUCTURAL = /^(\s*([-*•+]|\d{1,3}[.)])\s|>|\s{2,}|\t|--\s*$|_{3,}|={3,}|-{3,})/

/**
 * Plain-text mail is often hard-wrapped at 72–78 characters or more; on a phone those breaks left
 * one-word lines ("…and it⏎would help…"). Lines of a paragraph are joined again when
 * the sender marked them as flowed (format=flowed: a trailing space), when a line has a common wrap
 * width (50–80 characters), or when a longer line stops mid-sentence and the next goes on in lower
 * case; never when either line is a list item, a quote, indented or a signature line. Blank lines
 * (paragraphs) and every other break stay.
 */
export function reflowPlainText(text: string): string {
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  // A wrap: a flowed line (trailing space); a line of a common wrap width (50–80); or a longer line
  // cut mid-sentence (no closing punctuation, and the next line goes on in lower case).
  const wrapped = (l: string, n: string) => {
    const len = l.trimEnd().length
    return (l.endsWith(' ') && l !== '-- ') || (len >= 50 && len <= 80) || (len > 80 && !/[.!?:;]$/.test(l.trimEnd()) && /^\p{Ll}/u.test(n.trimStart()))
  }
  const joins = (l: string, n: string | undefined) =>
    n !== undefined && n.trim() !== '' && l.trim() !== '' && !STRUCTURAL.test(n) && !STRUCTURAL.test(l) && wrapped(l, n)
  const out: string[] = []
  let join = false
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (join) out[out.length - 1] = `${out[out.length - 1].trimEnd()} ${line.trimStart()}`
    else out.push(line)
    // The next line continues this one when this one (as written by the sender) is a wrap.
    join = joins(line, lines[i + 1])
  }
  return out.join('\n')
}

/** HTML that is only plain text in a <div>, its lines kept with <br> (how the mail store serves a
 *  text-only message). */
export function isTextAsHtml(html: string): boolean {
  return /^\s*<div>(?:[^<]|<br\s*\/?>)*<\/div>\s*$/i.test(html)
}

export function textToHtml(text: string): string {
  return escapeHtml(text)
    .replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1">$1</a>')
    .replace(/\n/g, '<br>')
}

export interface BodyDocOptions {
  html: string
  dark: boolean
  paper: boolean
  allowImages: boolean
  /** The CSP the server sent with the body (GET /messages/{id}/body). */
  csp?: string | null
  /** Images come from mailr's image proxy: only same-origin images may load. */
  proxied?: boolean
}

/** A full HTML document for the sandboxed message iframe. Scripts never run (no allow-scripts). */
/**
 * The iframe's Content-Security-Policy. The server sends one with each body that matches its
 * own remote-content decision; it is used when present, never loosened past what the user allowed
 * here, and always keeps scripts, frames and media off.
 */
/** Adds mailr's own origin next to 'self'. In a srcdoc frame some browsers (Firefox among them)
 *  take 'self' to mean about:srcdoc, so proxied images from mailr's origin were refused. */
function withOrigin(directive: string): string {
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  if (!origin || !/'self'/.test(directive) || directive.includes(origin)) return directive
  return directive.replace("'self'", `'self' ${origin}`)
}

export function bodyCsp(allowImages: boolean, serverCsp?: string | null, proxied = false): string {
  if (serverCsp) {
    const parts = serverCsp
      .replace(/["<>]/g, '')
      .split(';')
      .map((d) => d.trim())
      .filter(Boolean)
      .map((d) => {
        if (!/^(img|font)-src\b/i.test(d)) return d
        // Not allowed: no remote or same-origin images. Through the proxy: never a remote host.
        if (!allowImages) return d.replace(/\s(https?:|\*|'self')(?=\s|$)/gi, '')
        return proxied ? withOrigin(d.replace(/\s(https?:|\*)(?=\s|$)/gi, '')) : d
      })
    if (!parts.some((d) => /^default-src\b/i.test(d))) parts.unshift("default-src 'none'")
    if (!parts.some((d) => /^media-src\b/i.test(d))) parts.push("media-src 'none'")
    // A mail body may never change the base URL for relative links or submit forms.
    if (!parts.some((d) => /^base-uri\b/i.test(d))) parts.push(t('lib.html.baseUriNone'))
    if (!parts.some((d) => /^form-action\b/i.test(d))) parts.push(t('lib.html.formActionNone'))
    return parts.join('; ') + ';'
  }
  // Remote images and fonts are both trackers: they load only after the user allows them, and
  // through the image proxy only from mailr's own origin.
  const img = !allowImages ? 'img-src data: cid: blob:;' : proxied ? `${withOrigin("img-src 'self'")} data: cid: blob:;` : 'img-src data: cid: https: http: blob:;'
  const font = allowImages && !proxied ? 'font-src data: https:;' : 'font-src data:;'
  return `default-src 'none'; ${img} style-src 'unsafe-inline'; ${font} media-src 'none'; base-uri 'none'; form-action 'none';`
}

/** color-scheme for the message iframe element; must equal the one buildBodyDoc writes. */
export function bodyColorScheme(dark: boolean, paper: boolean): 'dark' | 'light' {
  return dark && !paper ? 'dark' : 'light'
}

export function buildBodyDoc({ html, dark, paper, allowImages, csp: serverCsp, proxied }: BodyDocOptions): string {
  const csp = bodyCsp(allowImages, serverCsp, proxied)
  const fg = dark && !paper ? '#e3e3e3' : '#202124'
  const link = dark && !paper ? '#a8c7fa' : '#1a73e8'
  const bg = paper ? '#ffffff' : 'transparent'
  // The document's color-scheme must match the iframe element's (see MessageBody): when they
  // differ, browsers paint an opaque white canvas behind a transparent document.
  const scheme = dark && !paper ? 'dark' : 'light'
  // Each paragraph takes its own direction (unicode-bidi:plaintext). text-align:start is set on the
  // body only and inherits from there, so start follows each paragraph's direction while the
  // mail's own align="center" on a cell still centres what it holds (set on every td, it pushed
  // centred newsletter cards to the left).
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="color-scheme" content="${scheme}">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="referrer" content="no-referrer">
<base target="_blank">
<style>
html{color-scheme:${scheme}}
html,body{margin:0;padding:0;background:${bg};}
body{font-family:Roboto,'Segoe UI',Arial,sans-serif;font-size:14px;line-height:1.45;color:${fg};word-wrap:break-word;overflow-wrap:break-word;${paper ? 'padding:12px;border-radius:8px;' : ''}}
a{color:${link}}
img{max-width:100%;height:auto}
img[data-mailr-src]{visibility:hidden}
table{max-width:100%}
pre{white-space:pre-wrap}
blockquote{margin:0;margin-inline-start:.8ex;border-inline-start:1px solid #ccc;padding-inline-start:1ex}
:where(body,p,div,li,td,th,h1,h2,h3,h4,h5,h6,blockquote,dd,dt,span[data-mailr-text]):not([dir],[dir] *){unicode-bidi:plaintext}
body:not([dir]){text-align:start}
</style></head><body>${html}</body></html>`
}

/**
 * Fit a message body to its frame's width, as Gmail shows mail on a phone (a fixed-width mail cut off at the right, another cut off on both sides). A body
 * wider than the frame gets the CSS zoom that makes it fit; responsive mail stays at zoom 1.
 * The width counts what overflows on the left too (a wide block centred in a flex row), which no
 * scrolling can reach. Pinch-to-zoom still enlarges the page. Returns the zoom (1 when it fits).
 */
export function fitBodyZoom(doc: Document): number {
  const root = doc.documentElement
  if (!root || !doc.body) return 1
  root.style.removeProperty('zoom')
  const view = root.clientWidth
  if (view <= 0) return 1
  let left = 0
  let right = root.scrollWidth
  if (right <= view + 2) {
    // Nothing overflows on the right; a centred block may still overflow on both sides.
    for (const el of Array.from(doc.body.querySelectorAll('*'))) {
      const r = el.getBoundingClientRect()
      if (r.width === 0) continue
      if (r.left < left) left = r.left
      if (r.right > right) right = r.right
    }
  } else {
    for (const el of Array.from(doc.body.querySelectorAll('*'))) {
      const r = el.getBoundingClientRect()
      if (r.width > 0 && r.left < left) left = r.left
    }
  }
  const full = right - left
  if (full <= view + 2) return 1
  const z = Math.max(0.25, Math.floor((view / full) * 1000) / 1000)
  root.style.setProperty('zoom', String(z))
  // A centred block: after the zoom it sits inside the page, so no horizontal offset remains.
  root.scrollLeft = 0
  return z
}

/** Trimmed history in the message frame. It travels in a <template>, whose content the browser
 *  never renders or loads, whatever CSS the email carries (an email's own stylesheet could undo
 *  a display:none); setQuotedShown copies it into the page when the reader asks for it. */
export function quotedHtml(quoted: string): string {
  return quoted ? `<template data-mailr-quoted-src>${quoted}</template><div data-mailr-quoted></div>` : ''
}

export function setQuotedShown(doc: Document, show: boolean): void {
  const tpl = doc.querySelector('template[data-mailr-quoted-src]') as HTMLTemplateElement | null
  const holder = doc.querySelector('div[data-mailr-quoted]')
  if (!tpl || !holder) return
  if (!show) holder.replaceChildren()
  else if (!holder.hasChildNodes()) holder.append(tpl.content.cloneNode(true))
}

/**
 * Split quoted history off a rendered message body. The server marks quoted history with
 * data-mailr-quote="1" (Gmail/Outlook/Apple quote containers, cite blockquotes). The quote is
 * collapsed only when it trails the message; replies written between quoted parts stay
 * expanded, as in Gmail.
 */
export function splitMarkedQuote(html: string): { main: string; quoted: string } | null {
  if (typeof DOMParser === 'undefined' || !html.includes('data-mailr-quote')) return null
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${html}</body>`, 'text/html')
  const marked = [...doc.querySelectorAll('[data-mailr-quote]')].filter(
    (el) => !el.parentElement?.closest('[data-mailr-quote]'),
  )
  if (!marked.length) return null
  const first = marked[0]
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT)
  let after = ''
  while (walker.nextNode()) {
    const n = walker.currentNode
    const following = first.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING
    if (!following || first.contains(n)) continue
    if (n.parentElement?.closest('[data-mailr-quote]')) continue
    after += n.textContent ?? ''
  }
  if (after.replace(/\s+/g, '').length > 40) return null
  const quoted = marked.map((el) => {
    const h = el.outerHTML
    el.remove()
    return h
  })
  const main = doc.body.innerHTML
  if (!main.replace(/<[^>]*>|&nbsp;|\s/g, '')) return null // nothing but quote: show it all
  return { main, quoted: quoted.join('') }
}


/** Does text the user wrote mention an attachment ("attached", "enclosed", "see the file")?
 *  Used to ask before sending when nothing is attached. */
export function mentionsAttachment(html: string): boolean {
  const text = html.replace(/<(style|script)[^>]*>[\s\S]*?<\/\1>/gi, ' ').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ')
  return /\b(attached|attaching|attachments?|enclosed|see the (attached )?file|i('ve| have) attached|please find (the )?(attached|enclosed))\b/i.test(text)
}

/** "1 tracker blocked", "3 trackers blocked" (the reader's indicator). */
export function trackersLabel(count: number): string {
  return t('lib.html.trackersBlocked', { count })
}

/** Whether the reader loads a message's images without asking: never for Spam, never when the
 *  setting is "never", always with "always", and for trusted senders with "ask". */
export function imagesAllowedByDefault(setting: 'always' | 'ask' | 'never' | undefined, spam: boolean, trustedSender: boolean): boolean {
  if (spam || setting === 'never') return false
  return setting !== 'ask' || trustedSender
}
