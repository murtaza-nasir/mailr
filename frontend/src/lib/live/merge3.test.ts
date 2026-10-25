/// <reference types="node" />
// The three-way merge of live editing: the cases shared with the server (backend/tests/test_merge3.py)
// and Android (LiveMergeTest.kt) give the same results here.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { diff, key, mergeFields, mergeText, same, tokens, type MergeMode } from './merge3'

interface TextCase { name: string; mode: MergeMode; base: string; mine: string; theirs: string; ok: boolean; result: string; conflicts: { base: string; mine: string; theirs: string }[] }
interface FieldCase { name: string; base: Record<string, unknown>; mine: Record<string, unknown>; theirs: Record<string, unknown>; text_fields: Record<string, MergeMode>; ok: boolean; merged: Record<string, unknown>; conflicts: string[] }
interface LongCase { name: string; lines: number; line: string; mine: [number, string]; theirs: [number, string]; ok: boolean }

const cases = JSON.parse(readFileSync(new URL('../../../../backend/tests/fixtures/merge3_cases.json', import.meta.url), 'utf8')) as {
  text: TextCase[]
  fields: FieldCase[]
  long: LongCase[]
}

describe('merge3: shared text cases', () => {
  for (const c of cases.text) {
    it(c.name, () => {
      const r = mergeText(c.base, c.mine, c.theirs, c.mode)
      expect(r.ok).toBe(c.ok)
      expect(r.text).toBe(c.result)
      expect(r.conflicts).toEqual(c.conflicts)
    })
  }
})

describe('merge3: shared field cases', () => {
  for (const c of cases.fields) {
    it(c.name, () => {
      const r = mergeFields(c.base, c.mine, c.theirs, c.text_fields)
      expect(r.ok).toBe(c.ok)
      expect(r.merged).toEqual(c.merged)
      expect(r.conflicts).toEqual(c.conflicts)
    })
  }
})

describe('merge3: long texts', () => {
  for (const c of cases.long) {
    const make = (sides: ('mine' | 'theirs')[]) => {
      const lines = Array.from({ length: c.lines }, (_, i) => c.line.replace('{n}', String(i + 1)))
      for (const s of sides) lines[c[s][0] - 1] = c[s][1]
      return lines.join('')
    }
    it(c.name, () => {
      const t0 = Date.now()
      const r = mergeText(make([]), make(['mine']), make(['theirs']), 'lines')
      expect(Date.now() - t0).toBeLessThan(2000)
      expect(r.ok).toBe(c.ok)
      if (c.ok) expect(r.text).toBe(make(['mine', 'theirs']))
      else expect(r.text).toBe(make(['mine']))
    })
  }
})

describe('merge3: units', () => {
  it('give the text back', () => {
    for (const [t, m] of [['a\nb\n\nc', 'lines'], ['<p>a</p><div>b<br>c</div>tail', 'html'], ['', 'lines'], ['<P>Up</P><BR/>x', 'html']] as const) {
      expect(tokens(t, m).join('')).toBe(t)
    }
  })
  it('compare by a normalised key in html', () => {
    expect(key('<div class="a" dir="ltr">Hi&nbsp; there<br/></div>', 'html')).toBe('<p>Hi there<br></p>')
  })
  it('diff reports the changed range', () => {
    expect(diff(['a', 'b', 'c'], ['a', 'x', 'c'])).toEqual([[1, 2, 1, 2]])
  })
  it('treat empty as missing', () => {
    expect(same(null, '')).toBe(true)
    expect(same([], undefined)).toBe(true)
    expect(same('a', null)).toBe(false)
    expect(same({ b: 1, a: 2 }, { a: 2, b: 1 })).toBe(true)
  })
})
