// The item grammar against spec/cases/notes-quickadd-cases.yaml, the vectors the Python
// twin (backend/tests/test_itemadd.py) runs too.
// ACT-SURFACES: web.note
import { describe, expect, it } from 'vitest'
import casesText from '../../../spec/cases/notes-quickadd-cases.yaml?raw'
import androidCopy from '../../../android/app/src/test/resources/interaction/notes-quickadd-cases.yaml?raw'
import { readFlowYaml } from './flowYaml'
import { itemKey, parseItems, quickNote, type ItemList, type ItemMode } from './itemadd'

const file = readFlowYaml(casesText)
const lists = file.lists as ItemList[]
const byId = (id: unknown) => lists.find((l) => l.id === id) ?? null
const defaultList = byId(file.defaultList)

function run(input: string, mode: ItemMode, listId?: unknown, keep?: string[], prefixCreates?: boolean) {
  return parseItems(input, { mode, list: byId(listId), lists, defaultList, keep, prefixCreates })
}

describe('notes-quickadd-cases.yaml (adding items to a list)', () => {
  it('has the vectors and the Android copy matches', () => {
    expect(file.cases.length).toBeGreaterThanOrEqual(100)
    expect(new Set(file.cases.map((c) => c.id)).size).toBe(file.cases.length)
    expect(androidCopy).toBe(casesText)
  })

  it('parses every prefix of the typing lines', () => {
    const lines = file.typing as string[]
    for (const mode of ['list', 'capture'] as const) {
      for (const line of lines) {
        const points = Array.from(line)
        for (let n = 1; n <= points.length; n++) {
          const input = points.slice(0, n).join('')
          const r = run(input, mode, 'n-shop')
          for (const t of r.tokens) expect(input.slice(t.start, t.end), `${mode} ${input}`).toBe(t.text)
        }
      }
    }
  })

  for (const c of file.cases) {
    it(`${c.id}: ${JSON.stringify(c.in)}`, () => {
      const want = c.expect as Record<string, unknown>
      const input = c.in as string
      if (c.mode === 'key') {
        expect(itemKey(input)).toBe(want.key)
        return
      }
      if (c.mode === 'quick') {
        const q = quickNote(input, lists)
        expect(q.kind).toBe(want.kind)
        if (q.kind === 'note') {
          expect({ title: q.title, body: q.body }).toEqual(want.note)
          return
        }
        expect(q.list).toEqual(want.list)
        expect(q.tokens.map((t) => `${t.kind}:${t.text}`)).toEqual(want.tokens ?? [])
        expect(q.items.map((i) => ({ text: i.text, qty: i.qty ? { amount: i.qty.amount, unit: i.qty.unit } : null, section: i.section, checked: i.checked })))
          .toEqual((want.items as Array<Record<string, unknown>>).map((i) => ({ text: i.text, qty: i.qty ?? null, section: i.section ?? null, checked: i.checked ?? false })))
        return
      }
      const opts = (c.opts ?? {}) as { keep?: string[]; prefixCreates?: boolean }
      const r = run(input, c.mode as ItemMode, c.list, opts.keep, opts.prefixCreates)
      for (const t of r.tokens) expect(input.slice(t.start, t.end)).toBe(t.text)
      expect(r.tokens.map((t) => `${t.kind}:${t.text}`)).toEqual(want.tokens ?? [])
      expect(r.new_sections).toEqual(want.new_sections ?? [])
      if (c.mode === 'capture') expect(r.list).toEqual(want.list)
      const items = r.items.map((i) => ({
        text: i.text,
        qty: i.qty ? { amount: i.qty.amount, unit: i.qty.unit } : null,
        section: i.section,
        checked: i.checked,
      }))
      const expected = (want.items as Array<Record<string, unknown>>).map((i) => ({
        text: i.text, qty: i.qty ?? null, section: i.section ?? null, checked: i.checked ?? false,
      }))
      expect(items).toEqual(expected)
    })
  }
})
