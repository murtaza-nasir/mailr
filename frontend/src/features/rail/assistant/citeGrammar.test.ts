// The citation marker grammar: spec/cases/citation-cases.yaml, shared with the backend and Android.
// The cases: a raw [[event:… "Chinatown walk (until ~11 AM)"]] in an answer, and raw markers
// while an answer streamed.
import { describe, expect, it } from 'vitest'
import casesText from '../../../../../spec/cases/citation-cases.yaml?raw'
import androidCopy from '../../../../../android/app/src/test/resources/interaction/citation-cases.yaml?raw'
import { readFlowYaml } from '../../../lib/flowYaml'
import { cleanMarkers, scanMarkers } from './citeGrammar'
import { applyEvent, type TimelineTurn } from './timeline'
import { finalText, streamingText, type Citation } from './citations'

const file = readFlowYaml(casesText)

describe('citation-cases.yaml', () => {
  it('has the vectors and the Android copy matches', () => {
    expect(file.cases.length).toBeGreaterThanOrEqual(25)
    expect(new Set(file.cases.map((c) => c.id)).size).toBe(file.cases.length)
    expect(androidCopy).toBe(casesText)
  })
  for (const c of file.cases) {
    it(`${c.id}`, () => {
      const input = c.in as string
      expect(scanMarkers(input)).toEqual(c.markers)
      expect(cleanMarkers(input)).toBe(c.clean)
      expect(cleanMarkers(input, true)).toBe(c.stream)
    })
  }
})

describe('streaming citations', () => {
  it('a cite event adds the citation before its link arrives, so the chip works at once', () => {
    const empty: TimelineTurn = { items: [], citations: [] }
    const cite = { n: 1, kind: 'event', uid: 'u1', title: 'Lunch' } as unknown as Citation
    let t = applyEvent(empty, 'cite', { citation: cite, segment: 0 })
    t = applyEvent(t, 'token', { text: 'Lunch [1](mailr-cite:1) then [[m:1', segment: 0 })
    expect(t.citations.map((c) => c.n)).toEqual([1])
    const item = t.items[0] as { raw: string }
    expect(streamingText(item.raw)).toBe('Lunch [1](mailr-cite:1) then')
    // The same citation again (in the final event) is not doubled.
    t = applyEvent(t, 'final', { content: 'Lunch [1](mailr-cite:1).', citations: [cite], segment: 0 })
    expect(t.citations).toHaveLength(1)
  })

  it('an older answer with a raw titled marker shows no markup', () => {
    expect(finalText('A walk [[event:abc@2026-05-20T13:00:00+00:00 "Chinatown walk"]], then lunch.')).toBe('A walk, then lunch.')
  })
})
