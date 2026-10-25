// Gmail search syntax helpers: advanced-search form ↔ query string, and search chips.

import { t as tr } from '../i18n'

export interface AdvancedSearch {
  from: string
  to: string
  subject: string
  hasWords: string
  doesntHave: string
  sizeOp: 'larger' | 'smaller'
  size: string
  sizeUnit: 'MB' | 'KB' | 'bytes'
  dateWithin: '' | '1d' | '3d' | '1w' | '2w' | '1m' | '2m' | '6m' | '1y'
  dateAnchor: string // yyyy-mm-dd; empty = today
  searchIn: string // 'all' | 'inbox' | 'starred' | ... | 'label:<name>'
  hasAttachment: boolean
}

export const EMPTY_SEARCH: AdvancedSearch = {
  from: '',
  to: '',
  subject: '',
  hasWords: '',
  doesntHave: '',
  sizeOp: 'larger',
  size: '',
  sizeUnit: 'MB',
  dateWithin: '',
  dateAnchor: '',
  searchIn: 'all',
  hasAttachment: false,
}

export const DATE_WITHIN_OPTIONS: { value: AdvancedSearch['dateWithin']; label: string; days: number }[] = [
  { value: '1d', label: tr('lib.search.n1Day'), days: 1 },
  { value: '3d', label: tr('common.n3Days'), days: 3 },
  { value: '1w', label: tr('lib.search.n1Week'), days: 7 },
  { value: '2w', label: tr('lib.search.n2Weeks'), days: 14 },
  { value: '1m', label: tr('lib.search.n1Month'), days: 30 },
  { value: '2m', label: tr('lib.search.n2Months'), days: 60 },
  { value: '6m', label: tr('lib.search.n6Months'), days: 182 },
  { value: '1y', label: tr('lib.search.n1Year'), days: 365 },
]

/** Quote a value if it has spaces or special characters. */
export function quoteValue(v: string): string {
  const t = v.trim()
  if (!t) return ''
  if (/^".*"$/.test(t)) return t
  return /[\s(){}]/.test(t) ? `"${t.replace(/"/g, '')}"` : t
}

function ymd(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())}`
}

export function buildQuery(f: AdvancedSearch): string {
  const parts: string[] = []
  if (f.from.trim()) parts.push(`from:${quoteValue(f.from)}`)
  if (f.to.trim()) parts.push(`to:${quoteValue(f.to)}`)
  if (f.subject.trim()) parts.push(`subject:${quoteValue(f.subject)}`)
  if (f.hasWords.trim()) parts.push(f.hasWords.trim())
  if (f.doesntHave.trim()) {
    const words = f.doesntHave.trim().split(/\s+/)
    parts.push(words.length > 1 ? `-{${words.join(' ')}}` : `-${words[0]}`)
  }
  if (f.size.trim() && Number(f.size) > 0) {
    const unit = f.sizeUnit === 'MB' ? 'M' : f.sizeUnit === 'KB' ? 'K' : ''
    parts.push(`${f.sizeOp}:${Number(f.size)}${unit}`)
  }
  if (f.dateWithin) {
    const opt = DATE_WITHIN_OPTIONS.find((o) => o.value === f.dateWithin)
    if (opt) {
      if (f.dateAnchor) {
        const anchor = new Date(`${f.dateAnchor}T12:00:00`)
        const after = new Date(anchor.getTime() - opt.days * 86_400_000)
        const before = new Date(anchor.getTime() + (opt.days + 1) * 86_400_000)
        parts.push(`after:${ymd(after)}`, `before:${ymd(before)}`)
      } else {
        parts.push(`newer_than:${opt.days}d`)
      }
    }
  }
  if (f.searchIn && f.searchIn !== 'all') {
    if (f.searchIn.startsWith('label:')) parts.push(`label:${quoteValue(f.searchIn.slice(6))}`)
    else parts.push(`in:${f.searchIn}`)
  }
  if (f.hasAttachment) parts.push('has:attachment')
  return parts.join(' ')
}

const TOKEN_RE = /-?\{[^}]*\}|-?"[^"]*"|-?[^\s"]+:"[^"]*"|\S+/g

export function tokenize(q: string): string[] {
  return q.match(TOKEN_RE) ?? []
}

function unquote(v: string): string {
  return v.replace(/^"(.*)"$/, '$1')
}

/** Best-effort inverse of buildQuery for pre-filling the advanced search form. */
export function parseQuery(q: string): AdvancedSearch {
  const f: AdvancedSearch = { ...EMPTY_SEARCH }
  const words: string[] = []
  for (const tok of tokenize(q)) {
    const m = /^([a-z_]+):(.*)$/i.exec(tok)
    if (tok.startsWith('-{')) {
      f.doesntHave = tok.slice(2, -1)
      continue
    }
    if (tok.startsWith('-') && !tok.includes(':')) {
      f.doesntHave = [f.doesntHave, tok.slice(1)].filter(Boolean).join(' ')
      continue
    }
    if (!m) {
      words.push(tok)
      continue
    }
    const [, op, raw] = m
    const v = unquote(raw)
    switch (op.toLowerCase()) {
      case 'from':
        f.from = v
        break
      case 'to':
        f.to = v
        break
      case 'subject':
        f.subject = v
        break
      case 'larger':
      case 'smaller': {
        f.sizeOp = op.toLowerCase() as 'larger' | 'smaller'
        const sm = /^(\d+)([KkMm]?)/.exec(v)
        if (sm) {
          f.size = sm[1]
          f.sizeUnit = sm[2].toUpperCase() === 'M' ? 'MB' : sm[2].toUpperCase() === 'K' ? 'KB' : 'bytes'
        }
        break
      }
      case 'newer_than': {
        const dm = /^(\d+)d$/.exec(v)
        const opt = dm ? DATE_WITHIN_OPTIONS.find((o) => o.days === Number(dm[1])) : undefined
        if (opt) f.dateWithin = opt.value
        else words.push(tok)
        break
      }
      case 'in':
        f.searchIn = v.toLowerCase()
        break
      case 'label':
        f.searchIn = `label:${v}`
        break
      case 'has':
        if (v.toLowerCase().startsWith('attachment')) f.hasAttachment = true
        else words.push(tok)
        break
      default:
        words.push(tok)
    }
  }
  f.hasWords = words.join(' ')
  return f
}

export interface SearchChip {
  id: string
  label: string
  token: string
}

export const SEARCH_CHIPS: SearchChip[] = [
  { id: 'attachment', label: tr('common.hasAttachment'), token: 'has:attachment' },
  { id: '7d', label: tr('lib.search.last7Days'), token: 'newer_than:7d' },
  { id: 'unread', label: tr('common.unread'), token: 'is:unread' },
  { id: 'starred', label: tr('common.starred'), token: 'is:starred' },
  { id: 'fromme', label: tr('lib.search.fromMe'), token: 'from:me' },
  { id: 'important', label: tr('common.important'), token: 'is:important' },
]

export function hasToken(q: string, token: string): boolean {
  return tokenize(q).some((t) => t.toLowerCase() === token.toLowerCase())
}

/** Toggle a chip token on/off in a query, preserving the rest. */
export function toggleToken(q: string, token: string): string {
  const toks = tokenize(q)
  const lower = token.toLowerCase()
  if (toks.some((t) => t.toLowerCase() === lower)) {
    return toks.filter((t) => t.toLowerCase() !== lower).join(' ')
  }
  return [...toks, token].join(' ')
}

/** Replace any existing operator (e.g. from:) value, or add it. */
export function setOperator(q: string, op: string, value: string): string {
  const toks = tokenize(q).filter((t) => !t.toLowerCase().startsWith(`${op.toLowerCase()}:`))
  if (value.trim()) toks.push(`${op}:${quoteValue(value)}`)
  return toks.join(' ')
}

/** Query that finds messages "like this one" for filter creation. */
export function filterQueryForSender(email: string): string {
  return `from:${quoteValue(email)}`
}

/** The values of the `account:` tokens in a query (backend core/search.py: an account's address or id). */
export function accountTokens(q: string): string[] {
  return tokenize(q)
    .filter((t) => /^account:/i.test(t))
    .map((t) => t.slice(8).replace(/^"|"$/g, ''))
    .filter(Boolean)
}

/** A search typed inside an account's own folder stays in that account: `account:<address>` goes in
 *  front, unless the query names an account already. */
export function scopeToAccount(q: string, address: string | null | undefined): string {
  if (!address || !q.trim() || accountTokens(q).length) return q
  return `account:${address} ${q}` // i18n-ignore: search syntax
}
