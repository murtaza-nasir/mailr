// The web renderers on the golden fixtures that the Android renderers also run
// (spec/fixtures/agent-blocks-fixtures.json, spec/fixtures/agent-markdown-fixtures.json; produced by
// backend/mailr/agent/present.py). ACT approval.approve, ACT approval.deny

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import blocksText from '../../../../../../spec/fixtures/agent-blocks-fixtures.json?raw'
import blocksAndroid from '../../../../../../android/app/src/test/resources/design/agent-blocks-fixtures.json?raw'
import mdText from '../../../../../../spec/fixtures/agent-markdown-fixtures.json?raw'
import mdAndroid from '../../../../../../android/app/src/test/resources/design/agent-markdown-fixtures.json?raw'
import { ApprovalCard } from '../ApprovalCard'
import { Markdown } from '../Markdown'
import type { ApprovalRef } from '../useAssistant'
import { ApprovalDisplay } from './ApprovalDisplay'
import { cellChars, tableLayout } from './tableLayout'
import type { Display, Outcome } from './types'

interface ApprovalFixture { name: string; action: string; display: Display; outcomes: Outcome[]; expect: string[] }
interface MdSample {
  name: string
  md: string
  normalized: string
  expect: { headings: number[]; tables: { cols: number; rows: number; layout: Record<string, string> }[]; text: string[] }
}

const blocks = JSON.parse(blocksText) as { version: number; approvals: ApprovalFixture[]; steps: unknown[] }
const markdown = JSON.parse(mdText) as { version: number; samples: MdSample[] }

/** The visible text of rendered markup. */
function textOf(html: string): string {
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#x27;/g, "'")
    .replace(/\s+/g, ' ')
    .trim()
}

const noop = () => undefined

function card(f: ApprovalFixture, status = 'pending', outcome?: Outcome): string {
  const a: ApprovalRef = { id: 1, action: f.action, title: f.display.title, status, display: f.display, outcome: outcome ?? null }
  return renderToStaticMarkup(createElement(ApprovalCard, { a, onStatus: noop, onDecided: noop }))
}

describe('agent-blocks-fixtures.json', () => {
  it('is version 1 and the Android copy matches', () => {
    expect(blocks.version).toBe(1)
    expect(blocks.approvals.length).toBeGreaterThanOrEqual(10)
    expect(blocksAndroid).toBe(blocksText)
  })

  for (const f of blocks.approvals) {
    it(`renders ${f.name}`, () => {
      const text = textOf(card(f))
      for (const s of f.expect) expect(text, `${f.name}: “${s}”`).toContain(s.replace(/\s+/g, ' ').trim())
      // Raw machine text never reaches the card.
      expect(text).not.toMatch(/add_labels=|\[\d+\]$|Do: /)
    })

    it(`shows the outcomes of ${f.name} in place`, () => {
      for (const o of f.outcomes.filter((x) => x.state !== 'pending')) {
        const html = card(f, o.state, o)
        expect(textOf(html)).toContain(o.text)
        expect(html).toContain(`data-status="${o.state}"`)
      }
    })
  }

  it('draws label chips in their colours', () => {
    const f = blocks.approvals.find((x) => x.name === 'filter-query-mix')!
    const html = renderToStaticMarkup(createElement(ApprovalDisplay, { d: f.display }))
    const chips = [...html.matchAll(/<span class="label-chip"([^>]*)>([^<]*)</g)]
    const shopping = chips.find((m) => m[2] === 'Shopping')!
    expect(shopping[1]).toContain('style=')
    expect(chips.some((m) => m[2] === 'Receipts' && m[1].includes('style='))).toBe(true)
  })

  it('shows the readable query with its raw syntax tucked away', () => {
    const f = blocks.approvals.find((x) => x.name === 'filter-create')!
    const html = renderToStaticMarkup(createElement(ApprovalDisplay, { d: f.display }))
    expect(textOf(html)).toContain('From any of: paypal.com, amex.com, chase.com, … (6)')
    expect(html).toContain('<summary>Search syntax</summary>')
    expect(textOf(html)).toContain('Show all 6')
  })

  it('shows the text of a value type it does not know', () => {
    const d: Display = { v: 1, icon: 'action', title: 'T', summary: null, danger: false, reason: null, text: 'T',
      fields: [{ label: 'Future', value: { t: 'hologram', text: 'shown as text' } }] }
    expect(textOf(renderToStaticMarkup(createElement(ApprovalDisplay, { d })))).toContain('Future shown as text')
  })
})

describe('agent-markdown-fixtures.json', () => {
  it('the Android copy matches', () => {
    expect(mdAndroid).toBe(mdText)
    expect(markdown.samples.map((s) => s.name)).toContain('categories-table')
  })

  for (const s of markdown.samples) {
    for (const width of [360, 800]) {
      it(`renders ${s.name} at ${width}px`, () => {
        const html = renderToStaticMarkup(createElement(Markdown, { text: s.normalized, width }))
        const levels = [...html.matchAll(/<h([1-6])[ >]/g)].map((m) => Number(m[1]))
        expect(levels.filter((l) => l < 3)).toEqual([]) // nothing larger than ###
        expect([...new Set(levels)]).toEqual([...new Set(s.expect.headings)])
        const tables = [...html.matchAll(/<div class="md-table [a-z]+" data-layout="([a-z]+)" data-cols="(\d+)"[^>]*><table>([\s\S]*?)<\/table>/g)]
        expect(tables.length).toBe(s.expect.tables.length)
        tables.forEach((m, i) => {
          const want = s.expect.tables[i]
          expect(m[1]).toBe(want.layout[String(width)])
          expect(Number(m[2])).toBe(want.cols)
          const body = m[3].split('<tbody>')[1] ?? ''
          expect((body.match(/<tr>/g) ?? []).length).toBe(want.rows)
          if (m[1] === 'cards') expect(body).toContain('data-label=')
        })
        const text = textOf(html)
        for (const t of s.expect.text) expect(text).toContain(t)
      })
    }
  }

  it('tableLayout counts visible characters', () => {
    expect(cellChars('**Bold** `code` [3](mailr-cite:3) [site](https://x.example)')).toBe('Bold code [3] site'.length)
    expect(tableLayout(['A', 'B'], [['x', 'y']], 360)).toBe('table')
    expect(tableLayout(['A', 'B', 'C'], [['x'.repeat(50), 'y', 'z']], 300)).toBe('cards')
    expect(tableLayout(['A', 'B'], [['x'.repeat(50), 'y']], 300)).toBe('scroll')
  })
})
