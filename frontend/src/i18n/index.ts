// The web app's words. Every string a person reads lives in src/locales/<lang>.json
// under a key; code asks for it with t('mail.reader.reply') or, for a sentence with markup in it,
// tx('key', params, { b: (c) => <b>{c}</b> }).
//
// - English (en.json) is bundled and is the fallback for a key another language lacks.
// - Other languages are separate files, loaded only when chosen (import.meta.glob below).
// - {{name}} fills a parameter, as in Speakr. Plurals use the i18next suffixes chosen by
//   Intl.PluralRules: key_one, key_other (and key_zero, key_two, key_few, key_many where a
//   language has them), picked by params.count.
// - The language is the account setting `language` (Settings → You → Region). This browser keeps a
//   copy of it, and of that language's table, in localStorage, so the words are there before the
//   first paint. In a build the table is tied to the build: boot.ts, run from index.html, fetches
//   this build's file before the app starts when the kept one is older. No
//   top-level await: that splits the bundle into many small chunks. Changing the
//   language reloads the page, so strings read once when a module loads (menus, option lists)
//   change too.
// - en-XA is a pseudo-language for layout testing: accented and about 35% longer, in brackets.
//   ar-XB is its right-to-left twin, as on Android: the page reads right to left and every text is
//   shown mirrored, in brackets. Both are offered only in development or with ?lang=en-XA (ar-XB)
//   / localStorage mailr.i18n.pseudo=1.

import { createElement, Fragment, type ReactNode } from 'react'
import en from '../locales/en.json'

type Table = { [k: string]: string | Table }
export type Params = Record<string, string | number | boolean | null | undefined>

export const FALLBACK = 'en'
export const PSEUDO = 'en-XA'
/** The right-to-left pseudo-language. */
export const PSEUDO_RTL = 'ar-XB'
/** Whether a language code is one of the two pseudo-languages. */
export const isPseudo = (code: string | null | undefined): boolean => code === PSEUDO || code === PSEUDO_RTL
const STORE_KEY = 'mailr.language'
const PSEUDO_FLAG = 'mailr.i18n.pseudo'

/** Every locale file but English, loaded on demand (development and the tests). */
const files: Record<string, () => Promise<{ default: Table }>> = import.meta.env?.PROD
  ? {}
  : import.meta.glob<{ default: Table }>(['../locales/*.json', '!../locales/en.json'])
const codeOf = (path: string) => path.replace(/^.*\/([^/]+)\.json$/, '$1')

/** In a build: the locale files of this build, code → hashed URL, set in index.html with the boot
 *  script (src/i18n/boot.ts, vite.config.ts i18nKeys). Absent in development and the tests. */
function buildFiles(): Record<string, string> | null {
  return (globalThis as { __MAILR_LOCALES__?: Record<string, string> }).__MAILR_LOCALES__ ?? null
}

/** Which version of a language's table is current: its URL in this build, 'dev' otherwise. */
const versionOf = (code: string) => buildFiles()?.[code] ?? 'dev'

const tables: Record<string, Table> = { en: en as Table }

/** English text a module brings with it (the build splits en.json by the modules that use each key,
 *  vite.config.ts i18nKeys; development and the tests import en.json whole). */
export function registerText(table: Record<string, string>): void {
  Object.assign(tables[FALLBACK], table)
}

function storage(kind: 'local' | 'session'): Storage | null {
  try {
    return typeof window === 'undefined' ? null : kind === 'local' ? window.localStorage : window.sessionStorage
  } catch {
    return null
  }
}

const SESSION_KEY = 'mailr.language.tab'

/** ?lang=xx (testing, the pseudo-language): kept for this tab, so it survives navigation and reloads. */
function urlLang(): string | null {
  try {
    const fromUrl = typeof location === 'undefined' ? null : new URLSearchParams(location.search).get('lang')
    if (fromUrl) storage('session')?.setItem(SESSION_KEY, fromUrl)
    return fromUrl ?? storage('session')?.getItem(SESSION_KEY) ?? null
  } catch {
    return null
  }
}

/** Whether the pseudo-language may be chosen here (development, or the flag is set). */
export function pseudoAllowed(): boolean {
  return Boolean(import.meta.env?.DEV) || storage('local')?.getItem(PSEUDO_FLAG) === '1' || isPseudo(urlLang())
}

/** The languages a person can choose: English, every locale file, and en-XA and ar-XB where allowed. */
export function languages(): string[] {
  const built = buildFiles()
  const codes = [FALLBACK, ...(built ? Object.keys(built) : Object.keys(files).map(codeOf))]
  if (pseudoAllowed()) codes.push(PSEUDO, PSEUDO_RTL)
  return [...new Set(codes)]
}

// Traditional Chinese (Taiwan, Hong Kong, Macau) is not offered Simplified Chinese: it falls back.
const TRADITIONAL = /^zh-(hant|tw|hk|mo)\b/

/** The supported language closest to `want` (exact, then the same base language), else null. */
export function matchLanguage(want: string | null | undefined, list: string[] = languages()): string | null {
  if (!want) return null
  const w = want.toLowerCase()
  const exact = list.find((c) => c.toLowerCase() === w)
  if (exact) return exact
  if (TRADITIONAL.test(w)) return null
  const base = w.split('-')[0]
  return list.find((c) => c.toLowerCase() === base) ?? list.find((c) => c.split('-')[0].toLowerCase() === base && !isPseudo(c)) ?? null
}

function browserLanguages(): readonly string[] {
  if (typeof navigator === 'undefined') return []
  return navigator.languages?.length ? navigator.languages : navigator.language ? [navigator.language] : []
}

/** The language this browser would get with the setting on Automatic. */
export function browserLanguage(): string {
  for (const l of browserLanguages()) {
    const m = matchLanguage(l)
    if (m && !isPseudo(m)) return m
  }
  return FALLBACK
}

/** The language to start in: ?lang= (testing), the saved setting's copy, the browser's, English. */
function pickLanguage(): string {
  const fromUrl = matchLanguage(urlLang())
  if (fromUrl) return fromUrl
  const saved = matchLanguage(storage('local')?.getItem(STORE_KEY))
  if (saved) return saved
  return browserLanguage()
}

let current = FALLBACK

async function fetchTable(code: string): Promise<Table | null> {
  const built = buildFiles()
  if (built) {
    try {
      const r = await fetch(built[code])
      return r.ok ? ((await r.json()) as Table) : null
    } catch {
      return null
    }
  }
  const path = Object.keys(files).find((p) => codeOf(p) === code)
  if (!path) return null
  try {
    return (await files[path]()).default
  } catch {
    return null
  }
}

const RTL = new Set(['ar', 'arc', 'ckb', 'dv', 'fa', 'he', 'ku', 'ps', 'sd', 'ug', 'ur', 'yi'])

/** 'rtl' for right-to-left languages. */
export function directionOf(code: string): 'ltr' | 'rtl' {
  return RTL.has(code.split('-')[0].toLowerCase()) ? 'rtl' : 'ltr'
}

function applyToDocument(): void {
  if (typeof document === 'undefined') return
  document.documentElement.lang = current
  document.documentElement.dir = directionOf(current)
}

/** The language the app shows now. */
export function language(): string {
  return current
}

/** The locale for dates, numbers and money (Intl): the browser's own variant of the chosen language
 *  (en-GB stays en-GB with English chosen), else the chosen language. ar-XB formats as Arabic with
 *  Latin digits, so its dates and times read right to left as a real right-to-left language's do. */
export function appLocale(): string {
  if (current === PSEUDO_RTL) return 'ar-u-nu-latn'
  const base = (isPseudo(current) ? FALLBACK : current).split('-')[0].toLowerCase()
  for (const l of browserLanguages()) if (l.split('-')[0].toLowerCase() === base) return l
  return isPseudo(current) ? 'en-US' : current
}

/** For a display the app writes in one English style ("Sep 24" in en-US, "Tue 6 Oct" in en-GB):
 *  that style in English, the chosen language's own format otherwise. */
export function dateLocale(english: string): string {
  return current === FALLBACK || current === PSEUDO ? english : appLocale()
}

/** Choose a language: keep it for this browser and reload, so every string changes at once.
 *  `null` follows the browser. The account setting is saved by the caller (Settings → You → Region). */
export async function switchLanguage(code: string | null): Promise<void> {
  const s = storage('local')
  try {
    if (code) s?.setItem(STORE_KEY, code)
    else s?.removeItem(STORE_KEY)
  } catch {
    /* ignore */
  }
  await prepare(wantedLanguage(code))
  if (typeof location !== 'undefined') location.reload()
}

// ---- the kept table of a language other than English ----

const tableKey = (code: string) => `mailr.i18n.table.${code}`

/** This browser's copy of a language's table, and whether it is this build's. */
function keptTable(code: string): { table: Table; current: boolean } | null {
  try {
    const raw = storage('local')?.getItem(tableKey(code))
    return raw ? { table: JSON.parse(raw) as Table, current: storage('local')?.getItem(`${tableKey(code)}.v`) === versionOf(code) } : null
  } catch {
    return null
  }
}

/** Fetch a language's table and keep it on this browser, ready for the next page load. The table
 *  in use is left as it is: the words do not change under the reader. */
export async function prepare(code: string): Promise<boolean> {
  if (code === FALLBACK || isPseudo(code)) return true
  const table = await fetchTable(code)
  if (!table) return false
  try {
    storage('local')?.setItem(tableKey(code), JSON.stringify(table))
    storage('local')?.setItem(`${tableKey(code)}.v`, versionOf(code))
  } catch {
    /* full or blocked: the next load fetches it again */
  }
  if (code !== current) tables[code] = table
  return true
}

/** The language the account setting asks for on this browser (null: automatic). */
export function wantedLanguage(setting: string | null | undefined): string {
  return matchLanguage(setting) ?? browserLanguage()
}

/** Keep this browser's copy of the account setting; true when the page must reload to show it. */
export function syncLanguageSetting(setting: string | null | undefined): boolean {
  // A tab on ?lang=, and the pseudo-language chosen on this browser, are testing overrides.
  if (urlLang() || isPseudo(current)) return false
  const s = storage('local')
  const want = matchLanguage(setting)
  try {
    if (want) s?.setItem(STORE_KEY, want)
    else if (setting === null || setting === undefined || setting === '') s?.removeItem(STORE_KEY)
  } catch {
    /* ignore */
  }
  return wantedLanguage(setting) !== current
}

// ---- lookup ----

function lookup(table: Table | undefined, key: string): string | undefined {
  let v: string | Table | undefined = table
  for (const part of key.split('.')) {
    if (!v || typeof v === 'string') return undefined
    v = v[part]
  }
  return typeof v === 'string' ? v : undefined
}

const warned = new Set<string>()
const pluralRules = new Map<string, Intl.PluralRules>()

function pluralCategory(lang: string, n: number): string {
  let r = pluralRules.get(lang)
  if (!r) {
    try {
      r = new Intl.PluralRules(lang)
    } catch {
      r = new Intl.PluralRules(FALLBACK)
    }
    pluralRules.set(lang, r)
  }
  return r.select(n)
}

/** The raw message for `key` (plural form chosen), in the current language or English. */
function message(key: string, count: number | undefined): string | undefined {
  const lang = isPseudo(current) ? FALLBACK : current
  const pick = (table: Table | undefined, l: string): string | undefined => {
    if (count !== undefined) {
      if (count === 0) {
        const zero = lookup(table, `${key}_zero`)
        if (zero !== undefined) return zero
      }
      const form = lookup(table, `${key}_${pluralCategory(l, count)}`) ?? lookup(table, `${key}_other`)
      if (form !== undefined) return form
    }
    return lookup(table, key)
  }
  const v = pick(tables[lang], lang) ?? (lang === FALLBACK ? undefined : pick(tables[FALLBACK], FALLBACK))
  if (v === undefined && !warned.has(key)) {
    warned.add(key)
    if (import.meta.env?.DEV) console.warn(`i18n: no text for key ${key}`)
  }
  return v === undefined ? undefined : current === PSEUDO ? pseudo(v) : current === PSEUDO_RTL ? pseudoRtl(v) : v
}

function show(v: Params[string]): string {
  if (v === null || v === undefined || typeof v === 'boolean') return ''
  return String(v)
}

// A value filled into a sentence (a name, a subject) may be written in the other direction: an
// Arabic name in an English sentence, an English one in an Arabic sentence. Such a value is isolated
// (U+2068 FIRST STRONG ISOLATE … U+2069 POP DIRECTIONAL ISOLATE, the plain-text form of <bdi>), so it
// takes its own direction and the sentence's punctuation stays in place. A value of the sentence's
// own direction is left as it is.
const RTL_LETTER = /[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFC]/
const LTR_LETTER = /[A-Za-z\u00C0-\u024F\u0370-\u03FF\u0400-\u04FF]/

/** `v` wrapped in a directional isolate when it has letters of the other direction than the app's. */
export function isolate(v: string): string {
  if (!v) return v
  return (directionOf(current) === 'rtl' ? LTR_LETTER : RTL_LETTER).test(v) ? `\u2068${v}\u2069` : v
}

/** Fill {{name}} placeholders; an unknown one stays as it is. `count` is shown as a number of this locale.
 *  A value written in the other direction is isolated (see `isolate`). */
export function interpolate(text: string, params?: Params): string {
  if (!params) return text
  return text.replace(/\{\{\s*(\w+)\s*\}\}/g, (m, k: string) => {
    if (!(k in params)) return m
    const v = params[k]
    return k === 'count' && typeof v === 'number' ? formatCount(v) : isolate(show(v))
  })
}

const countFormats = new Map<string, Intl.NumberFormat>()
function formatCount(n: number): string {
  const loc = appLocale()
  let f = countFormats.get(loc)
  if (!f) {
    f = new Intl.NumberFormat(loc)
    countFormats.set(loc, f)
  }
  return f.format(n)
}

/** The text for `key`, with {{placeholders}} filled. A missing key shows the key itself. */
export function t(key: string, params?: Params): string {
  const count = typeof params?.count === 'number' ? params.count : undefined
  const m = message(key, count)
  return m === undefined ? key : interpolate(m, params)
}

/** Whether `key` has a text (in this language or English). */
export function hasText(key: string): boolean {
  return lookup(tables[current], key) !== undefined || lookup(tables[FALLBACK], key) !== undefined
    || lookup(tables[FALLBACK], `${key}_other`) !== undefined
}

type Tag = (chunks: ReactNode) => ReactNode

/**
 * A message with markup: `<b>…</b>` and `<br/>` style tags become the elements `tags` makes, and
 * {{name}} placeholders take any React node (a link, a chip). Tags do not nest.
 *   tx('mail.toolbar.allSelected', { count: n }, { b: (c) => <b>{c}</b> })
 */
export function tx(key: string, params?: Record<string, ReactNode>, tags?: Record<string, Tag>): ReactNode {
  const count = typeof params?.count === 'number' ? params.count : undefined
  const m = message(key, count)
  if (m === undefined) return key
  const out: ReactNode[] = []
  let k = 0
  const fill = (s: string) => {
    for (const part of s.split(/(\{\{\s*\w+\s*\}\})/)) {
      const ph = /^\{\{\s*(\w+)\s*\}\}$/.exec(part)
      if (!ph) {
        if (part) out.push(part)
        continue
      }
      const name = ph[1]
      if (!params || !(name in params)) out.push(part)
      else {
        const v = params[name]
        if (name === 'count' && typeof v === 'number') out.push(formatCount(v))
        else if (v !== null && v !== undefined && typeof v !== 'boolean') out.push(typeof v === 'object' ? createElement(Fragment, { key: `p${k++}` }, v) : isolate(String(v)))
      }
    }
  }
  const re = /<(\w+)\s*\/>|<(\w+)>([\s\S]*?)<\/\2>/g
  let last = 0
  for (let mm = re.exec(m); mm; mm = re.exec(m)) {
    fill(m.slice(last, mm.index))
    const name = mm[1] ?? mm[2]
    const make = tags?.[name]
    if (make) {
      const before = out.length
      fill(mm[3] ?? '')
      const inner = out.splice(before)
      out.push(createElement(Fragment, { key: `t${k++}` }, make(inner.length === 1 ? inner[0] : inner)))
    } else fill(mm[3] ?? '')
    last = mm.index + mm[0].length
  }
  fill(m.slice(last))
  return out.length === 1 && typeof out[0] === 'string' ? out[0] : createElement(Fragment, null, ...out)
}

// ---- pseudo-language ----

const ACCENTS: Record<string, string> = {
  a: 'á', b: 'ƀ', c: 'ç', d: 'ð', e: 'é', f: 'ƒ', g: 'ĝ', h: 'ĥ', i: 'í', j: 'ĵ', k: 'ķ', l: 'ļ', m: 'ɱ',
  n: 'ñ', o: 'ó', p: 'ƥ', q: 'ʠ', r: 'ŕ', s: 'š', t: 'ţ', u: 'ú', v: 'ṽ', w: 'ŵ', x: 'ẋ', y: 'ý', z: 'ž',
  A: 'Á', B: 'Ɓ', C: 'Ç', D: 'Ð', E: 'É', F: 'Ƒ', G: 'Ĝ', H: 'Ĥ', I: 'Í', J: 'Ĵ', K: 'Ķ', L: 'Ļ', M: 'Ṁ',
  N: 'Ñ', O: 'Ó', P: 'Ƥ', Q: 'Ǫ', R: 'Ŕ', S: 'Š', T: 'Ţ', U: 'Ú', V: 'Ṽ', W: 'Ŵ', X: 'Ẋ', Y: 'Ý', Z: 'Ž',
}

/** "Reply" → "[Ŕéƥļý ••]": accented, about 35% longer, in brackets; placeholders and tags kept. */
export function pseudo(s: string): string {
  let letters = 0
  const body = s.split(/(\{\{\s*\w+\s*\}\}|<\/?\w+\s*\/?>)/).map((part, i) => {
    if (i % 2) return part
    return part.replace(/[A-Za-z]/g, (c) => {
      letters++
      return ACCENTS[c] ?? c
    })
  }).join('')
  const pad = Math.max(1, Math.round(letters * 0.35))
  return `[${body} ${'•'.repeat(pad)}]`
}

/** ar-XB, as Android makes it: "Reply" → "[ylpeR]" read right to left. Each run of text is wrapped
 *  in a right-to-left override, so it shows mirrored in a right-to-left page; placeholders and tags
 *  are kept, so the values filled in (a name, a count) show as they are. */
export function pseudoRtl(s: string): string {
  const body = s.split(/(\{\{\s*\w+\s*\}\}|<\/?\w+\s*\/?>)/).map((part, i) => {
    if (i % 2) return part
    return part.replace(/[^\s]+/g, (w) => `\u200F\u202E${w}\u202C\u200F`)
  }).join('')
  return `[${body}]`
}

/** The text of a pseudo-language for an English text: en-XA or ar-XB. */
export function pseudoText(code: string, s: string): string {
  return code === PSEUDO_RTL ? pseudoRtl(s) : pseudo(s)
}

// ---- start ----

current = pickLanguage()
if (current !== FALLBACK && !isPseudo(current)) {
  const kept = keptTable(current)
  if (kept) {
    // In a build the boot script has already fetched this build's table when the kept one was
    // older. Still older here means that fetch failed: the old words now, a new try in the background.
    tables[current] = kept.table
    if (!kept.current) void prepare(current)
  } else if (buildFiles()) {
    // The boot script could not fetch the table and none is kept: English now; the next load tries again.
    current = FALLBACK
  } else {
    // First use on this browser (or the table was cleared): English now, the language after one reload.
    const want = current
    current = FALLBACK
    void prepare(want).then((ok) => {
      const mark = `mailr.i18n.fetched:${want}`
      if (ok && typeof location !== 'undefined' && !storage('session')?.getItem(mark)) {
        storage('session')?.setItem(mark, '1')
        location.reload()
      }
    })
  }
}
applyToDocument()
