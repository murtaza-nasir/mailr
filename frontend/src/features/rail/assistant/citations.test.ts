import { describe, expect, it } from 'vitest'
import { viewOfLocation, citationRoute, citeNumber, citeWhen, shortDate, sourceRows, streamingText, usedCitations, type Citation } from './citations'
import sourceCasesText from '../../../../../spec/cases/source-cases.yaml?raw'
import sourceCasesAndroid from '../../../../../android/app/src/test/resources/interaction/source-cases.yaml?raw'
import { readFlowYaml } from '../../../lib/flowYaml'
import { copyText } from './timeline'

const c = (n: number, extra: Partial<Citation> = {}): Citation => ({
  n, kind: 'message', conversation_id: 10, message_id: 20, subject: 's', from: 'f', date: null, quote: 'the deadline',
  verified: true, start: 0, end: 5, url: null, ...extra,
})

describe('streamingText', () => {
  it('hides complete and partial citation markers while streaming', () => {
    expect(streamingText('Due Sep 30 [[m:12 "nominations close"]]. Next')).toBe('Due Sep 30. Next')
    expect(streamingText('See [[c:3065]] and [c:77].')).toBe('See and.')
    expect(streamingText('Partial [[m:12 "nomin')).toBe('Partial')
  })
  it('leaves normal brackets alone', () => {
    expect(streamingText('An [example](https://x.org) link')).toBe('An [example](https://x.org) link')
  })
})

describe('citeNumber', () => {
  it('reads mailr-cite links only', () => {
    expect(citeNumber('mailr-cite:3')).toBe(3)
    expect(citeNumber('mailr-cite:0')).toBeNull()
    expect(citeNumber('https://example.org')).toBeNull()
    expect(citeNumber(undefined)).toBeNull()
  })
})

describe('citationRoute', () => {
  it('opens the conversation at the message with the verified quote', () => {
    expect(citationRoute(c(1))).toBe('/mail/all/10?m=20&hl=the+deadline')
  })
  it('drops unverified quotes and handles conversation citations', () => {
    expect(citationRoute(c(1, { verified: false }))).toBe('/mail/all/10?m=20')
    expect(citationRoute(c(1, { message_id: null, quote: null }))).toBe('/mail/all/10')
    expect(citationRoute(c(1, { conversation_id: null }))).toBeNull()
    // In a mail view the citation opens there, so the list beside it stays; searches are kept.
    expect(citationRoute(c(1), { view: 'inbox' })).toBe('/mail/inbox/10?m=20&hl=the+deadline')
    expect(citationRoute(c(1), { view: 'label:7', q: 'from:x' })).toBe('/mail/label%3A7/10?q=from%3Ax&m=20&hl=the+deadline')
    expect(viewOfLocation('/mail/label%3A7/55', '?q=a')).toEqual({ view: 'label:7', q: 'a' })
    expect(viewOfLocation('/settings/general', '')).toEqual({ view: null, q: null })
  })
})

describe('usedCitations', () => {
  it('keeps the citations the text refers to', () => {
    const text = 'A [2](mailr-cite:2) and B [1](mailr-cite:1).'
    expect(usedCitations(text, [c(1), c(2), c(3)]).map((x) => x.n)).toEqual([1, 2])
  })
})

describe('shortDate', () => {
  it('formats or returns empty', () => {
    expect(shortDate(null)).toBe('')
    expect(shortDate('not a date')).toBe('')
    expect(shortDate(new Date().toISOString())).not.toBe('')
  })
})

describe('event and task citations', () => {
  it('hides their markers while streaming', () => {
    expect(streamingText('Standup [[event:abc@x.org@2026-10-02T15:00:00+00:00]] and [[task:t-1]].')).toBe('Standup and.')
  })
  it('open the event occurrence or the task', () => {
    expect(citationRoute(c(1, { kind: 'event', uid: 'abc@x', occurrence: '2026-10-02T15:00:00+00:00', conversation_id: null })))
      .toBe('/calendar/event/abc%40x/2026-10-02T15%3A00%3A00%2B00%3A00')
    expect(citationRoute(c(2, { kind: 'task', uid: 't 1', conversation_id: null }))).toBe('/tasks/task/t%201')
  })
  it('say when', () => {
    expect(citeWhen({ kind: 'task', due: null })).toBe('No due date')
    expect(citeWhen({ kind: 'task', due: '2026-10-02' })).toMatch(/^Due /)
    expect(citeWhen({ kind: 'event', starts_at: '2026-10-02', all_day: true })).toMatch(/2/)
    expect(citeWhen({ kind: 'event', starts_at: 'nope' })).toBe('')
    // An event at 23:00 on Sunday in Chicago (04:00Z Monday) is Sunday 11:00 PM in the user's zone.
    const sun = citeWhen({ kind: 'event', starts_at: '2026-10-05T04:00:00+00:00' }, 'America/Chicago')
    expect(sun).toMatch(/Sun/)
    expect(sun).toMatch(/11:00/)
    expect(sun).toMatch(/PM/)
  })
})

describe('recording citations', () => {
  it('hides r: markers while streaming, with or without a moment and a quote', () => {
    expect(streamingText('We ship Friday [[r:12@754 "ship the budget on Friday"]]. See [[r:12]] and [[ r: 5 @ 12:34 ]].'))
      .toBe('We ship Friday. See and.')
    expect(streamingText('Partial [[r:12@75')).toBe('Partial')
  })
  it('open the recording sheet at the moment', () => {
    expect(citationRoute(c(1, { kind: 'recording', recording_id: 12, connector_id: 3, t: 754, conversation_id: null })))
      .toBe('/recordings/3/12?t=754')
    expect(citationRoute(c(1, { kind: 'recording', recording_id: 12, connector_id: 3, t: null, conversation_id: null })))
      .toBe('/recordings/3/12')
  })
})

describe('source-cases.yaml (the Sources list)', () => {
  const file = readFlowYaml(sourceCasesText)
  it('has the vectors and the Android copy matches', () => {
    expect(file.cases.length).toBeGreaterThanOrEqual(5)
    expect(sourceCasesAndroid).toBe(sourceCasesText)
  })
  for (const c of file.cases) {
    it(`${c.id}`, () => {
      const rows = sourceRows(c.cites as unknown as Citation[])
      expect(rows.map((r) => r.all.map((x) => x.n))).toEqual(c.rows)
    })
  }
  it('a copied answer lists each source once, with all its numbers', () => {
    const cites = [
      { n: 1, kind: 'conversation', conversation_id: 7, message_id: null, subject: 'Propuesta', from: 'Lucía', date: null },
      { n: 2, kind: 'message', conversation_id: 7, message_id: 70, subject: 'Propuesta', from: 'Lucía', date: null },
    ] as unknown as Citation[]
    expect(copyText('She asks [1](mailr-cite:1) for [2](mailr-cite:2).', cites)).toBe('She asks [1] for [2].\n\nSources:\n[1][2] Propuesta — Lucía')
  })
})
