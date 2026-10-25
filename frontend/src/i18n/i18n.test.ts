/// <reference types="node" />
// The web app's words: the loader, and the locale files against the code.
// Like Speakr's tests/test_localization.py: a key the code asks for and en.json lacks would show
// the raw key on screen, and nothing else would fail.
import { readdirSync, readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { directionOf, interpolate, matchLanguage, pseudo, pseudoRtl, t, tx } from '.'

const ROOT = path.resolve(__dirname, '../..')
const LOCALES = path.join(ROOT, 'src/locales')

type Table = { [k: string]: string | Table }
function flatten(o: Table, prefix = '', out: Record<string, string> = {}): Record<string, string> {
  for (const [k, v] of Object.entries(o)) {
    if (typeof v === 'string') out[prefix + k] = v
    else flatten(v, `${prefix}${k}.`, out)
  }
  return out
}
const load = (lang: string) => flatten(JSON.parse(readFileSync(path.join(LOCALES, `${lang}.json`), 'utf8')))
const languages = readdirSync(LOCALES).filter((f) => f.endsWith('.json')).map((f) => f.slice(0, -5))
const en = load('en')
const PLURAL = /_(zero|one|two|few|many|other)$/
const base = (k: string) => k.replace(PLURAL, '')

const sources = execSync(`git ls-files --cached --others --exclude-standard 'src/*.ts' 'src/*.tsx' 'src/**/*.ts' 'src/**/*.tsx'`, { cwd: ROOT })
  .toString().split('\n').filter((f) => f && !/\.test\.tsx?$/.test(f) && !f.startsWith('src/i18n/'))
  .map((f) => ({ f, src: readFileSync(path.join(ROOT, f), 'utf8') }))

/** Keys passed to t()/tx() (or their aliases tr, i18nT, txr) as a literal. */
function calledKeys(): Map<string, string[]> {
  const out = new Map<string, string[]>()
  const re = /\b(?:t|tr|tx|txr|i18nT)\(\s*'([a-z][\w]*(?:\.[\w]+)+)'/g
  for (const { f, src } of sources) for (const m of src.matchAll(re)) out.set(m[1], [...(out.get(m[1]) ?? []), f])
  return out
}

/** Every quoted string in the code that names a key (a key may sit in a map before it reaches t()). */
function mentionedKeys(): Set<string> {
  const out = new Set<string>()
  for (const { src } of sources) for (const m of src.matchAll(/'([a-z][\w]*(?:\.[\w]+)+)'/g)) out.add(m[1])
  return out
}

describe('en.json and the code', () => {
  it('the scan finds the keys (guards the tests below from passing on nothing)', () => {
    expect(calledKeys().size).toBeGreaterThan(3000)
  })

  it('every key the code asks for exists in en.json', () => {
    const missing = [...calledKeys()].filter(([k]) => !(k in en) && !(`${k}_other` in en))
    expect(missing.map(([k, fs]) => `${k} <- ${fs[0]}`)).toEqual([])
  })

  it('every key in en.json is used', () => {
    const used = mentionedKeys()
    const unused = Object.keys(en).filter((k) => !used.has(k) && !used.has(base(k)))
    expect(unused).toEqual([])
  })

  it('every plural has an _other form, the one i18next and every language need', () => {
    const forms = Object.keys(en).filter((k) => PLURAL.test(k))
    expect(forms.filter((k) => !(`${base(k)}_other` in en))).toEqual([])
  })
})

describe.each(languages)('%s.json', (lang) => {
  const table = load(lang)

  it('has no empty text', () => {
    expect(Object.entries(table).filter(([, v]) => !v.trim()).map(([k]) => k)).toEqual([])
  })

  it('uses {{name}} placeholders, the only kind t() fills', () => {
    const single = /(?<!\{)\{[A-Za-z_]\w*\}(?!\})/
    // Text that shows a literal {word} to the person (template variables, a URL pattern).
    const literal = new Set(['settings.templatesTab.textYouUseOftenIn', 'settings.writingSections.videoCallLinkTemplate'])
    expect(Object.entries(table).filter(([k, v]) => single.test(v) && !literal.has(k)).map(([k, v]) => `${k}: ${v}`)).toEqual([])
  })

  if (lang !== 'en') {
    it('has every English key and no other', () => {
      const ours = new Set(Object.keys(table).map(base))
      const theirs = new Set(Object.keys(en).map(base))
      expect([...theirs].filter((k) => !ours.has(k))).toEqual([])
      expect([...ours].filter((k) => !theirs.has(k))).toEqual([])
    })

    it('keeps the placeholders and tags of the English text', () => {
      // {{count}} is left out: a plural form may leave it out ("One message" for _one) or add it
      // (Russian _one is also 21, 31…, so it shows the number).
      const marks = (s: string) => [...s.matchAll(/\{\{\s*(\w+)\s*\}\}|<\/?(\w+)\s*\/?>/g)].map((m) => m[0].replace(/\s/g, '')).filter((m) => m !== '{{count}}').sort().join(' ')
      const bad = Object.entries(table).filter(([k, v]) => {
        const e = en[k] ?? en[`${base(k)}_other`]
        if (e === undefined) return false
        return marks(v) !== marks(e)
      })
      expect(bad.map(([k]) => k)).toEqual([])
    })
  }
})

// The plural forms each language needs (CLDR): Russian one (1, 21…), few (2–4, 22–24…), many (0,
// 5–20…) and other (fractions); Chinese other only; the others one and other (French and Portuguese
// also say "many" for a million, which falls back to other).
const FORMS: Record<string, string[]> = { ru: ['one', 'few', 'many', 'other'], 'zh-Hans': ['other'] }

describe.each(languages.filter((l) => l !== 'en'))('%s.json plurals', (lang) => {
  const table = load(lang)
  const groups = [...new Set(Object.keys(en).filter((k) => PLURAL.test(k)).map(base))]

  it('has the forms the language needs for every plural', () => {
    const want = FORMS[lang] ?? ['one', 'other']
    const bad = groups.filter((g) => want.some((f) => !(`${g}_${f}` in table)))
    expect(bad).toEqual([])
  })

  it('shows the number in every form that covers more than one number', () => {
    // Russian "one" is also 21 and 101; French and Portuguese "one" is also 0.
    const many = lang === 'ru' ? ['one', 'few', 'many', 'other'] : ['fr', 'pt-BR'].includes(lang) ? ['one', 'other'] : ['other']
    const bad = groups.filter((g) => en[`${g}_other`]?.includes('{{count}}'))
      .flatMap((g) => many.filter((f) => `${g}_${f}` in table && !table[`${g}_${f}`].includes('{{count}}')).map((f) => `${g}_${f}`))
    expect(bad).toEqual([])
  })

  if (lang === 'ru') {
    it('picks the Russian form by CLDR rules', () => {
      const r = new Intl.PluralRules('ru')
      expect([1, 21, 2, 24, 5, 11, 12, 0, 1.5].map((n) => r.select(n))).toEqual(['one', 'one', 'few', 'few', 'many', 'many', 'many', 'many', 'other'])
    })
  }
})

describe('t()', () => {
  it('fills {{name}} and leaves an unknown placeholder alone', () => {
    expect(interpolate('Hello {{name}}, {{other}}', { name: 'Ambrose' })).toBe('Hello Ambrose, {{other}}')
    expect(interpolate('{{ name }}!', { name: 'x' })).toBe('x!')
  })

  it('shows nothing for null, undefined and booleans, as React does', () => {
    expect(interpolate('[{{a}}{{b}}{{c}}]', { a: null, b: undefined, c: false })).toBe('[]')
  })

  it('picks the plural form by count and formats the count', () => {
    const k = Object.keys(en).find((x) => x.endsWith('_one') && en[x].includes('{{count}}'))!
    const key = base(k)
    expect(t(key, { count: 1 })).toBe(interpolate(en[`${key}_one`], { count: 1 }))
    expect(t(key, { count: 2 })).toBe(interpolate(en[`${key}_other`], { count: 2 }))
    expect(interpolate('{{count}}', { count: 12345 })).toBe('12,345')
  })

  it('shows the key itself for a key that does not exist', () => {
    expect(t('no.such.key')).toBe('no.such.key')
  })

  it('matches a browser language to a supported one', () => {
    expect(matchLanguage('en-GB', ['en'])).toBe('en')
    expect(matchLanguage('de-DE', ['en', 'de'])).toBe('de')
    expect(matchLanguage('pt-BR', ['en', 'pt', 'pt-BR'])).toBe('pt-BR')
    expect(matchLanguage('fr', ['en'])).toBe(null)
    expect(matchLanguage('zh-CN', ['en', 'zh-Hans'])).toBe('zh-Hans')
    expect(matchLanguage('zh', ['en', 'zh-Hans'])).toBe('zh-Hans')
    expect(matchLanguage('zh-TW', ['en', 'zh-Hans'])).toBe(null)
    expect(matchLanguage('zh-Hant-HK', ['en', 'zh-Hans'])).toBe(null)
    expect(matchLanguage('pt', ['en', 'pt-BR'])).toBe('pt-BR')
  })
})

describe('tx()', () => {
  it('turns tags into the elements given and keeps the text between them', () => {
    const key = Object.keys(en).find((k) => /<b>\{\{\w+\}\}<\/b>/.test(en[k]))!
    const node = tx(key, new Proxy({}, { get: () => 'X', has: () => true }), { b: (c) => `[${String(c)}]` }) as unknown as { props: { children: unknown[] } }
    const flat = (n: unknown): string => (n === null || n === undefined ? '' : typeof n === 'string' ? n : Array.isArray(n) ? n.map(flat).join('') : flat((n as { props: { children: unknown } }).props.children))
    expect(flat(node)).toContain('[X]')
  })
})

describe('the pseudo-language', () => {
  it('accents the letters, keeps placeholders and tags, and is about a third longer', () => {
    const p = pseudo('Reply to {{name}} <b>now</b>')
    expect(p).toMatch(/^\[Ŕéƥļý ţó \{\{name\}\} <b>ñóŵ<\/b> •+\]$/)
    expect(pseudo('Archive').length).toBeGreaterThanOrEqual(Math.round('Archive'.length * 1.35))
  })
})

describe('the right-to-left pseudo-language (ar-XB)', () => {
  it('wraps each word in a right-to-left override, in brackets, and keeps placeholders and tags', () => {
    const rlo = (w: string) => `\u200F\u202E${w}\u202C\u200F`
    expect(pseudoRtl('Reply to {{name}} <b>now</b>')).toBe(`[${rlo('Reply')} ${rlo('to')} {{name}} <b>${rlo('now')}</b>]`)
  })
  it('reads right to left, and an Arabic browser is never given it', () => {
    expect(directionOf('ar-XB')).toBe('rtl')
    expect(directionOf('en-XA')).toBe('ltr')
    expect(matchLanguage('ar', ['en', 'en-XA', 'ar-XB'])).toBe(null)
    expect(matchLanguage('ar-XB', ['en', 'en-XA', 'ar-XB'])).toBe('ar-XB')
  })
})
