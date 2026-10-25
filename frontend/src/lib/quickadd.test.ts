import { describe, expect, it } from 'vitest'
import casesText from '../../../spec/cases/quickadd-cases.yaml?raw'
import androidCopy from '../../../android/app/src/test/resources/interaction/quickadd-cases.yaml?raw'
import { readFlowYaml } from './flowYaml'
import { parse, zonedToInstant, type QuickOptions, type QuickResult } from './quickadd'

const file = readFlowYaml(casesText)
const base = {
  ...(file.options as object),
  calendars: file.calendars,
  lists: file.lists,
  people: file.people,
} as Partial<QuickOptions>

describe('quickadd-cases.yaml (quick add)', () => {
  it('has the vectors and the Android copy matches', () => {
    expect(file.cases.length).toBeGreaterThanOrEqual(120)
    expect(new Set(file.cases.map((c) => c.id)).size).toBe(file.cases.length)
    expect(androidCopy).toBe(casesText)
  })

  // Every prefix of the typing lines, a code point at a time (quick add crashed while typing, 3 Oct 2026).
  it('parses every prefix of the typing lines', () => {
    const tz = file.tz as string
    const now = zonedToInstant(file.now as string, tz)
    const lines = file.typing as string[]
    expect(lines.length).toBeGreaterThan(10)
    for (const mode of ['task', 'event'] as const) {
      for (const line of lines) {
        const points = Array.from(line)
        for (let n = 1; n <= points.length; n++) {
          const input = points.slice(0, n).join('')
          const r = parse(input, { ...base, mode, now, tz } as QuickOptions)
          for (const t of r.tokens) expect(input.slice(t.start, t.end), `${mode} ${input}`).toBe(t.text)
        }
      }
    }
  })

  for (const c of file.cases) {
    it(`${c.id}: ${c.in}`, () => {
      const tz = file.tz as string
      const now = zonedToInstant((c.now as string) ?? (file.now as string), tz)
      const opts = { ...base, ...(c.opts as object), mode: c.mode, now, tz } as QuickOptions
      const input = c.in as string
      const r = parse(input, opts)
      const want = c.expect as Record<string, unknown>
      for (const t of r.tokens) expect(input.slice(t.start, t.end)).toBe(t.text)
      expect(r.title).toBe(want.title)
      expect(r.tokens.map((t) => `${t.kind}:${t.text}`)).toEqual(want.tokens ?? [])
      for (const [k, v] of Object.entries(want)) {
        if (k === 'title' || k === 'tokens') continue
        expect(r[k as keyof QuickResult], k).toEqual(v)
      }
    })
  }
})
