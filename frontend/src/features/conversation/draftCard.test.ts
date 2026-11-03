import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import { qk } from '../../api/hooks'
import { draftRevision, invalidateDraftBody } from './draftCard'

const msg = { draft: true, date: '2026-10-01T10:00:00Z', subject: 'Re: Venue', snippet: 'See you Thursday.' }

describe('draft card freshness', () => {
  it('marks only that draft\'s cached bodies stale, with and without images', () => {
    const qc = new QueryClient()
    qc.setQueryData(qk.body(7, false), { html: '<p>old</p>' })
    qc.setQueryData(qk.body(7, true), { html: '<p>old</p>' })
    qc.setQueryData(['body-highlight', 7, 'old', false], { html: '<p>old</p>' })
    qc.setQueryData(qk.body(8, false), { html: '<p>other</p>' })
    invalidateDraftBody(qc, 7)
    expect(qc.getQueryState(qk.body(7, false))?.isInvalidated).toBe(true)
    expect(qc.getQueryState(qk.body(7, true))?.isInvalidated).toBe(true)
    expect(qc.getQueryState(['body-highlight', 7, 'old', false])?.isInvalidated).toBe(true)
    expect(qc.getQueryState(qk.body(8, false))?.isInvalidated).toBe(false)
  })

  it('takes the id a live event carries, as a number or a string', () => {
    const qc = new QueryClient()
    qc.setQueryData(qk.body(4503599627370497, false), { html: '' })
    invalidateDraftBody(qc, '4503599627370497')
    expect(qc.getQueryState(qk.body(4503599627370497, false))?.isInvalidated).toBe(true)
    invalidateDraftBody(qc, 'not an id') // ignored
  })

  it('a draft\'s revision changes with its snippet, subject or date; other messages have none', () => {
    const r = draftRevision(msg)
    expect(r).not.toBeNull()
    expect(draftRevision({ ...msg })).toBe(r)
    expect(draftRevision({ ...msg, snippet: 'See you Friday.' })).not.toBe(r)
    expect(draftRevision({ ...msg, date: '2026-10-01T10:00:41Z' })).not.toBe(r)
    expect(draftRevision({ ...msg, subject: 'Re: Venue (new)' })).not.toBe(r)
    expect(draftRevision({ ...msg, draft: false })).toBeNull()
  })
})
