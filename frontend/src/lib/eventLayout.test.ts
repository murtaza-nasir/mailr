import { describe, expect, it } from 'vitest'
import casesText from '../../../spec/cases/layout-cases.yaml?raw'
import androidCopy from '../../../android/app/src/test/resources/interaction/layout-cases.yaml?raw'
import { readFlowYaml } from './flowYaml'
import { layoutDay, splitDays, type LayoutItem } from './eventLayout'

const file = readFlowYaml(casesText)
const min = (hhmm: string) => +hhmm.slice(0, 2) * 60 + +hhmm.slice(3)
const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`

interface Ev { id: string; start: string; end: string; partstat?: string; continued?: boolean }

describe('layout-cases.yaml (the overlap layout)', () => {
  it('the Android copy matches', () => {
    expect(androidCopy).toBe(casesText)
    expect(new Set(file.cases.map((c) => c.id)).size).toBe(file.cases.length)
  })

  for (const c of file.cases) {
    it(String(c.id), () => {
      if (c.split) {
        const segs = splitDays(c.split as Ev[]).map((s) => ({ ...s, start: hhmm(s.start), end: hhmm(s.end) }))
        expect(segs).toEqual(c.segments)
        return
      }
      const items: LayoutItem[] = (c.events as Ev[]).map((e) => ({ ...e, start: min(e.start), end: min(e.end) }))
      const { placed, chips } = layoutDay(items)
      const byId = new Map(placed.map((p) => [p.id, { ...p, top: hhmm(p.top), bottom: hhmm(p.bottom) }]))
      const hidden = (c.hidden as string[] | undefined) ?? []
      expect(placed.length + chips.reduce((n, ch) => n + ch.ids.length, 0)).toBe(items.length)
      for (const h of hidden) expect(byId.has(h), h).toBe(false)
      for (const [id, want] of Object.entries(c.expect as Record<string, Record<string, unknown>>)) {
        const got = byId.get(id)
        expect(got, id).toBeDefined()
        for (const [k, v] of Object.entries(want)) expect(got![k as keyof typeof got], `${id}.${k}`).toEqual(v)
      }
      expect(chips.map((ch) => ({ ids: ch.ids, top: hhmm(ch.top), bottom: hhmm(ch.bottom) }))).toEqual(c.chips ?? [])
      for (const ch of chips) expect([ch.col, ch.cols]).toEqual([3, 4])
    })
  }
})
