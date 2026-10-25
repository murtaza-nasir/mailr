// Text a sender chose (display names, subjects, file names) as the page shows it. Bidirectional
// controls can reverse the text around them ("Bob <RLO>moc.lapyap@..." reads as an address it is
// not), and zero-width spaces hide inside words; both are removed (zero-width joiners stay: emoji
// and Persian and Indic scripts need them). The server removes them at ingest too (backend
// core/textsafe.py); this covers mail stored before that.

/** API fields that carry text a sender chose; the API client cleans them as it parses. */
export const SENDER_TEXT_KEYS = new Set(['name', 'from_name', 'subject', 'filename', 'snippet'])
/** API fields that carry an address a sender chose: they lose bidi, invisible and control characters
 *  and white space, which an address never needs (the server does the
 *  same at ingest, backend core/textsafe.clean_address; this covers mail stored before that). */
export const SENDER_ADDRESS_KEYS = new Set(['email', 'from_email'])

export function senderTextReviver(key: string, value: unknown): unknown {
  if (typeof value !== 'string') return value
  if (SENDER_TEXT_KEYS.has(key)) return safeText(value)
  if (SENDER_ADDRESS_KEYS.has(key)) return safeAddress(value)
  return value
}

// eslint-disable-next-line no-misleading-character-class -- U+034F (combining grapheme joiner) is removed on purpose
const DROP = /[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c\u200b\u2060-\u2064\ufeff\u180e\u00ad\u034f\u115f\u1160\u3164\uffa0]/g
// eslint-disable-next-line no-control-regex
const CTRL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/g

export function safeText(s: string | null | undefined): string {
  if (!s) return s ?? ''
  return s.replace(DROP, '').replace(CTRL, ' ')
}

// eslint-disable-next-line no-control-regex
const ADDR_DROP = /[\u0000-\u001f\u007f-\u009f\u200c\u200d\s]/g

/** An email address without bidi controls, invisible characters, joiners, control characters or
 *  white space: what the reader shows and Copy address copies. */
export function safeAddress(s: string | null | undefined): string {
  if (!s) return s ?? ''
  return s.replace(DROP, '').replace(ADDR_DROP, '')
}

const EMAIL = /[^\s<>()"',;:]+@[^\s<>()"',;:]+\.[a-z]{2,}/gi

/** An address the display name shows that is not the sender's own ("ceo@hale.example" <x@evil.test>). */
export function nameAddressMismatch(name: string | null | undefined, email: string): string | null {
  const shown = safeText(name).match(EMAIL)
  if (!shown) return null
  const real = (email ?? '').toLowerCase()
  const other = shown.find((a) => a.toLowerCase() !== real)
  return other ?? null
}

/** A word that mixes Latin letters with Cyrillic or Greek look-alikes ("PаyPal"). */
export function mixedScript(s: string | null | undefined): boolean {
  for (const word of safeText(s).split(/[^\p{L}\p{N}]+/u)) {
    const latin = /\p{Script=Latin}/u.test(word)
    if (latin && /[\p{Script=Cyrillic}\p{Script=Greek}]/u.test(word)) return true
  }
  return false
}

/** Executable and script types a mail attachment should not be opened as ("invoice.pdf.exe"). */
const RISKY_EXT = /\.(exe|scr|com|bat|cmd|pif|cpl|msi|msp|msc|jar|js|jse|vbs|vbe|wsf|wsh|ps1|psm1|hta|lnk|reg|iso|img|vhd|vhdx|dll|apk|app|dmg|pkg|sh|html?|svg|xhtml|mht|mhtml|url|scf|chm|appx|msix)$/i

export function riskyAttachment(filename: string | null | undefined): boolean {
  return RISKY_EXT.test(safeText(filename).trim().replace(/[.\s]+$/, ''))
}

/** What an attachment's icon shows. The type a sender declared counts only when the file name's
 *  extension agrees ("report.exe" sent as application/pdf is a program, not a PDF). */
export function attachmentKind(filename: string | null | undefined, type: string): 'risky' | 'image' | 'pdf' | 'text' | 'file' {
  const name = safeText(filename).trim().toLowerCase()
  if (riskyAttachment(name)) return 'risky'
  const ext = /\.([a-z0-9]{1,8})$/.exec(name)?.[1] ?? ''
  if (type.startsWith('image/') && (!ext || /^(png|jpe?g|gif|webp|avif|bmp|ico|heic|heif|tiff?)$/.test(ext))) return 'image'
  if (type === 'application/pdf' && (!ext || ext === 'pdf')) return 'pdf'
  if (type.startsWith('text/') && (!ext || /^(txt|csv|log|md|ics|vcf|eml)$/.test(ext))) return 'text'
  return 'file'
}
