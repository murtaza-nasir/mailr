// URL form of conversation and approval ids (server: core/publicid.py). With public ids on, ids are large
// opaque numbers (≥ 2^52); the address bar shows them as "c" + base62 (/mail/inbox/cK7fQ2mXb9).
// Plain numbers are still read, for old links and for servers with public ids off.

const B62 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
const PUBLIC_MIN = 2 ** 52

function slug(prefix: string, id: number): string {
  if (id < PUBLIC_MIN) return String(id)
  let v = id
  let s = ''
  while (v > 0) {
    s = B62[v % 62] + s
    v = Math.floor(v / 62)
  }
  return `${prefix}${s}`
}

function fromSlug(prefix: string, raw: string | undefined | null): number | null {
  if (!raw) return null
  if (/^\d+$/.test(raw)) return Number.isSafeInteger(Number(raw)) ? Number(raw) || null : null
  if (raw[0] !== prefix || !/^.[0-9A-Za-z]{2,12}$/.test(raw)) return null
  let v = 0
  for (const ch of raw.slice(1)) v = v * 62 + B62.indexOf(ch)
  // The short form exists only for public ids ("abc" is a word, not an approval).
  return Number.isSafeInteger(v) && v >= PUBLIC_MIN ? v : null
}

export const convSlug = (id: number): string => slug('c', id)
export const convFromSlug = (raw: string | undefined | null): number | null => fromSlug('c', raw)

/** Approvals (pending actions): "a" + base62 in links (/approvals/aK7fQ2mXb9); plain numbers are still read. */
export const approvalSlug = (id: number): string => slug('a', id)
export const approvalFromSlug = (raw: string | undefined | null): number | null => fromSlug('a', raw)
