// English plurals: no text reads "1 events" or "Delete 1 tasks?". A count before a plural noun
// lives in `_one` / `_other` forms, and every `_one` form, shown at a count of
// one, reads as a singular. The same rules hold for Android (PluralFormsTest).
import { describe, expect, it } from 'vitest'
import en from '../locales/en.json'
import { t } from './index'

type Tree = { [k: string]: string | Tree }
const flat: Record<string, string> = {}
const walk = (o: Tree, p = '') => {
  for (const [k, v] of Object.entries(o)) {
    if (typeof v === 'string') flat[p + k] = v
    else walk(v, `${p}${k}.`)
  }
}
walk(en as unknown as Tree)

const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/
// Words ending in s that are not plural nouns here.
const NOT_PLURAL = /^(as|is|was|has|its|this|always|across|status|address|yes|less|ago|plus|process|progress|needs|works|counts|comes|goes|makes|takes|shows|stays|keeps|waits|opens|runs|does)$/
// "{{count}} events", "{{n}} more events", "{{idsLength}} rows deleted": a number, at most two words, a plural noun.
const COUNTED = /\{\{(\w+)\}\}\+?\s+(?:[a-z]+\s+){0,2}?([a-z]+s)\b/g
const NUMERIC_PARAM = /^(count|n|total|.*(Length|Count|count|Days|days)|days|weeks|hours|minutes)$/

function pluralNouns(text: string, param?: RegExp): string[] {
  const out: string[] = []
  for (const m of text.matchAll(COUNTED)) {
    if (param && !param.test(m[1])) continue
    if (!NOT_PLURAL.test(m[2]) && !m[2].endsWith('ss')) out.push(m[0])
  }
  return out
}

describe('English plurals', () => {
  it('every _one form reads as a singular at a count of one', () => {
    const bad = Object.keys(flat).filter((k) => k.endsWith('_one')).flatMap((k) => {
      const base = k.replace(/_one$/, '')
      const text = t(base, { count: 1 })
      return /\b1 (?:[a-z]+ ){0,2}?[a-z]+(?<!s)s\b/.test(text) && pluralNouns(flat[k]).length ? [`${base}: ${text}`] : []
    })
    expect(bad).toEqual([])
  })
  it('a count before a plural noun has plural forms', () => {
    const bad = Object.entries(flat)
      .filter(([k]) => !PLURAL_SUFFIX.test(k))
      .flatMap(([k, v]) => pluralNouns(v, NUMERIC_PARAM).map((hit) => `${k}: ${hit}`))
    expect(bad).toEqual([])
  })
})
