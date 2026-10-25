// mailto: links (RFC 6068) and mailr's compose link, /compose?to=…&cc=…&bcc=…&subject=…&body=…
// or /compose?mailto=<a whole mailto: URL> (the browser's email-link handler sends that form).
// Pure parsing, no app state: features/compose/mailtoLinks.ts opens the composer with the result.
// The same cases run on Android (spec/cases/mailto-cases.yaml, ui/Mailto.kt).
//
// Rules:
// - The scheme is case-insensitive. The part before `?` is a comma-separated list of addresses.
// - Header fields are `name=value`, joined by `&`; names are case-insensitive. `to`, `cc` and `bcc`
//   add addresses (they may repeat); `subject` and `body` set the text (the last one wins).
//   Every other field (in-reply-to, attachments, from …) is ignored.
// - Everything is percent-decoded as UTF-8. `+` stays a plus sign (RFC 6068 §5): "a+b@example.org"
//   is an address, not "a b". A broken escape is kept as it is.
// - Encoded words (RFC 2047, "=?utf-8?B?…?=") in a subject or a display name are decoded.
// - An address needs an "@"; anything else in an address list is dropped. Addresses are kept once.
// - Addresses lose bidi controls, invisible characters and white space, and names lose bidi controls
//   (lib/safeText.ts): a link cannot show one address and fill another.
// - A link fills at most 50 recipients (To, Cc and Bcc together) and a body of 50,000 characters.
//  The rest is dropped.
// - Nothing in a link ever sends: it only fills a draft the user sends.

import type { Address } from '../api/types'
import { safeText } from './safeText'

export interface MailtoFields {
  to: Address[]
  cc: Address[]
  bcc: Address[]
  subject: string
  body: string
}

/** Recipients a link fills, To, Cc and Bcc together. */
export const MAX_RECIPIENTS = 50
const MAX_SUBJECT = 998
/** Characters of body text a link fills. */
export const MAX_BODY = 50_000

export function emptyFields(): MailtoFields {
  return { to: [], cc: [], bcc: [], subject: '', body: '' }
}

/** Percent-decoding as UTF-8; a sequence that does not decode stays as written. */
export function pctDecode(s: string): string {
  try {
    return decodeURIComponent(s)
  } catch {
    return s.replace(/(?:%[0-9A-Fa-f]{2})+/g, (m) => {
      try {
        return decodeURIComponent(m)
      } catch {
        return m
      }
    })
  }
}

function decodeBytes(bytes: Uint8Array, charset: string): string | null {
  try {
    return new TextDecoder(charset.toLowerCase(), { fatal: false }).decode(bytes)
  } catch {
    return null
  }
}

/** RFC 2047 encoded words ("=?utf-8?Q?Caf=C3=A9?="); whitespace between two words goes. */
export function decodeEncodedWords(s: string): string {
  if (!s.includes('=?')) return s
  const word = /=\?([^?\s]+)\?([bBqQ])\?([^?\s]*)\?=/g
  return s
    .replace(/(=\?[^?\s]+\?[bBqQ]\?[^?\s]*\?=)\s+(?==\?[^?\s]+\?[bBqQ]\?[^?\s]*\?=)/g, '$1')
    .replace(word, (whole, charset: string, enc: string, text: string) => {
      const cs = charset.split('*')[0]
      let bytes: Uint8Array
      if (enc.toUpperCase() === 'B') {
        try {
          const bin = atob(text)
          bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0))
        } catch {
          return whole
        }
      } else {
        const q = text.replace(/_/g, ' ')
        const out: number[] = []
        for (let i = 0; i < q.length; i++) {
          if (q[i] === '=' && /^[0-9A-Fa-f]{2}$/.test(q.slice(i + 1, i + 3))) {
            out.push(parseInt(q.slice(i + 1, i + 3), 16))
            i += 2
          } else out.push(q.charCodeAt(i) & 0xff)
        }
        bytes = Uint8Array.from(out)
      }
      return decodeBytes(bytes, cs) ?? whole
    })
}

/** Split an address list on commas (or semicolons) outside quotes and angle brackets. */
function splitList(s: string): string[] {
  const out: string[] = []
  let cur = ''
  let quoted = false
  let angle = 0
  for (const ch of s) {
    if (ch === '"') quoted = !quoted
    else if (!quoted && ch === '<') angle++
    else if (!quoted && ch === '>') angle = Math.max(0, angle - 1)
    if (!quoted && angle === 0 && (ch === ',' || ch === ';')) {
      out.push(cur)
      cur = ''
    } else cur += ch
  }
  out.push(cur)
  return out.map((x) => x.trim()).filter(Boolean)
}

const ADDR = /^[^\s@<>(),;:"]+@[^\s@<>(),;:"]+$/

/** An address without bidi controls and invisible characters (joiners too); a control character
 *  becomes a space, so the address is then refused. */
const cleanAddress = (s: string) => safeText(s).replace(/[\u200c\u200d]/g, '')

/** "Name <a@b>", "\"Last, First\" <a@b>" or "a@b" → an address; null for anything else. */
export function parseOneAddress(raw: string): Address | null {
  const s = decodeEncodedWords(raw.trim())
  const m = /^(.*?)<([^<>]+)>\s*$/.exec(s)
  const email = cleanAddress((m ? m[2] : s).trim())
  if (!ADDR.test(email)) return null
  const name = safeText((m ? m[1] : '').trim().replace(/^"(.*)"$/, '$1').replace(/\\(.)/g, '$1')).trim()
  return name ? { email, name } : { email }
}

export function parseAddresses(value: string): Address[] {
  return splitList(value).map(parseOneAddress).filter((a): a is Address => a !== null)
}

function addInto(f: MailtoFields, key: 'to' | 'cc' | 'bcc', more: Address[]): void {
  const list = f[key]
  for (const a of more) {
    if (f.to.length + f.cc.length + f.bcc.length >= MAX_RECIPIENTS) return
    if (!list.some((x) => x.email.toLowerCase() === a.email.toLowerCase())) list.push(a)
  }
}

function oneLine(s: string): string {
  return s.replace(/[\r\n]+/g, ' ').slice(0, MAX_SUBJECT)
}

function bodyText(s: string): string {
  return s.replace(/\r\n?/g, '\n').slice(0, MAX_BODY)
}

/** A mailto: URL → its fields, or null when it is not a mailto: URL. */
export function parseMailto(url: string): MailtoFields | null {
  const m = /^\s*mailto:(.*)$/is.exec(url)
  if (!m) return null
  const rest = m[1].replace(/#.*$/s, '')
  const q = rest.indexOf('?')
  const path = q < 0 ? rest : rest.slice(0, q)
  const query = q < 0 ? '' : rest.slice(q + 1)
  const f = emptyFields()
  addInto(f, 'to', parseAddresses(pctDecode(path)))
  for (const part of query.split('&')) {
    if (!part) continue
    const eq = part.indexOf('=')
    const name = pctDecode(eq < 0 ? part : part.slice(0, eq)).trim().toLowerCase()
    const value = pctDecode(eq < 0 ? '' : part.slice(eq + 1))
    if (name === 'to' || name === 'cc' || name === 'bcc') addInto(f, name, parseAddresses(value))
    else if (name === 'subject') f.subject = oneLine(decodeEncodedWords(value))
    else if (name === 'body') f.body = bodyText(value)
  }
  return f
}

/** The fields of a /compose link's query string ("?to=…"); null when it has none of the fields.
 *  `subject` and `body` are read as a form query (`+` is a space). Addresses keep a `+` (a+b@example.org),
 *  so they are read raw, percent-decoded only. */
export function parseComposeQuery(search: string): MailtoFields | null {
  // "?mailto=mailto:…" not encoded: the desktop's handler (Exec=xdg-open "…/compose?mailto=%u") puts
  // the link in as it is, so everything after "mailto=" is the link, & and = included.
  const raw = /^\??mailto=(mailto:.*)$/is.exec(search)
  if (raw) return parseMailto(raw[1])
  const p = new URLSearchParams(search)
  const keys = ['mailto', 'to', 'cc', 'bcc', 'subject', 'body']
  if (!keys.some((k) => p.has(k))) return null
  const f = (p.get('mailto') && parseMailto(p.get('mailto')!)) || emptyFields()
  for (const part of search.replace(/^\?/, '').split('&')) {
    const eq = part.indexOf('=')
    const k = pctDecode(eq < 0 ? part : part.slice(0, eq)).toLowerCase()
    if (k === 'to' || k === 'cc' || k === 'bcc') addInto(f, k, parseAddresses(pctDecode(eq < 0 ? '' : part.slice(eq + 1))))
  }
  if (p.has('subject')) f.subject = oneLine(decodeEncodedWords(p.get('subject') ?? ''))
  if (p.has('body')) f.body = bodyText(p.get('body') ?? '')
  return f
}

const addrText = (a: Address) => (a.name ? `"${a.name.replace(/(["\\])/g, '\\$1')}" <${a.email}>` : a.email)

/** mailr's own compose link for these fields: /compose?to=…&subject=…&body=… (encodeURIComponent,
 *  so a space is %20 and a plus sign %2B). */
export function composeHref(f: Partial<MailtoFields>): string {
  const parts: string[] = []
  for (const k of ['to', 'cc', 'bcc'] as const) if (f[k]?.length) parts.push(`${k}=${encodeURIComponent(f[k]!.map(addrText).join(', '))}`)
  if (f.subject) parts.push(`subject=${encodeURIComponent(f.subject)}`)
  if (f.body) parts.push(`body=${encodeURIComponent(f.body)}`)
  return `/compose${parts.length ? `?${parts.join('&')}` : ''}`
}

/** A mailto: URL for these fields (RFC 6068: percent-encoded, `+` never used for a space). */
export function mailtoHref(f: Partial<MailtoFields>): string {
  const enc = (s: string) => encodeURIComponent(s)
  const to = (f.to ?? []).map((a) => enc(a.email)).join(',')
  const hf: string[] = []
  if (f.cc?.length) hf.push(`cc=${f.cc.map((a) => enc(a.email)).join(',')}`)
  if (f.bcc?.length) hf.push(`bcc=${f.bcc.map((a) => enc(a.email)).join(',')}`)
  if (f.subject) hf.push(`subject=${enc(f.subject)}`)
  if (f.body) hf.push(`body=${enc(f.body.replace(/\r?\n/g, '\r\n'))}`)
  return `mailto:${to}${hf.length ? `?${hf.join('&')}` : ''}`
}

/** The link a browser's email-link handler opens: `%s` is the whole mailto: URL, encoded. */
export function handlerUrl(origin: string): string {
  return `${origin.replace(/\/$/, '')}/compose?mailto=%s`
}

/** True for an href that is a mailto: link. */
export function isMailto(href: string | null | undefined): href is string {
  return !!href && /^\s*mailto:/i.test(href)
}
