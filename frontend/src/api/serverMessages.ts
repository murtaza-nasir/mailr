// The server's error texts in this app's language. An API error answers
// {detail, code, params}: `detail` is English, `code` keys the server's catalogue, which
// GET /i18n/messages serves per language. In English the detail is already the text, so nothing is
// fetched; in another language the catalogue is fetched once, on the first coded error, and that
// error and every later one show the translated text. An older server (404) leaves details as they are.
// In the pseudo-languages (en-XA, ar-XB) a coded error shows its detail as pseudo text, so an error
// the server still sends without a code stands out in plain English.

import { interpolate, isPseudo, language, pseudoText, type Params } from '../i18n'

let lang: string | null = null
let table: Record<string, string> = {}
let loading: Promise<void> | null = null

/** Fetch the catalogue for the current language (once per language). Never throws. */
export function loadServerMessages(): Promise<void> {
  const want = language()
  if (want === lang || want === 'en' || want.startsWith('en-') || isPseudo(want)) return loading ?? Promise.resolve()
  lang = want
  loading = fetch(`/api/v1/i18n/messages?lang=${encodeURIComponent(want)}`, { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : null))
    .then((body: { messages?: Record<string, string> } | null) => { table = body?.messages ?? {} })
    .catch(() => { table = {} })
  return loading
}

/** The catalogue text for an error code with its params, or null. `detail` is the server's English
 *  text, shown as pseudo text in en-XA and ar-XB. */
export function serverText(code: unknown, params: unknown, detail?: unknown): string | null {
  if (typeof code !== 'string') return null
  const m = table[code]
  if (m) return interpolate(m, (params && typeof params === 'object' ? params : {}) as Params)
  return isPseudo(language()) && typeof detail === 'string' ? pseudoText(language(), detail) : null
}

/** The text of a failed answer's coded error (`{detail, code, params}`) in this app's language, fetching
 *  the catalogue first when needed; null without a code or a text. For the paths that read an error
 *  outside `request` (uploads, the event stream, the tasks client). */
export async function codedText(data: unknown): Promise<string | null> {
  const d = data as { code?: unknown; params?: unknown; detail?: unknown } | null
  if (!d || typeof d !== 'object' || !d.code) return null
  await loadServerMessages()
  return serverText(d.code, d.params, d.detail)
}

/** For tests. */
export function setServerMessages(t: Record<string, string>, code = 'xx'): void {
  table = t
  lang = code
  loading = Promise.resolve()
}
