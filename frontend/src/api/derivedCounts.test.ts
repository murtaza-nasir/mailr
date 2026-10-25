import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import { linkDerivedCounts, qk } from './hooks'
import { liveCounts } from './liveState'

describe('linkDerivedCounts', () => {
  it('marks label and layout-tab counts stale whenever a conversation list is', async () => {
    const qc = new QueryClient()
    const stop = linkDerivedCounts(qc)
    qc.setQueryData(qk.conversationList({ view: 'inbox' } as never), { items: [], total: 0 })
    qc.setQueryData(qk.labels, [])
    qc.setQueryData(qk.layoutCounts('priority-later'), { later: 10 })
    qc.setQueryData(qk.layouts, [])
    await qc.invalidateQueries({ queryKey: qk.conversations })
    await new Promise((r) => setTimeout(r, 80))
    expect(qc.getQueryState(qk.labels)?.isInvalidated).toBe(true)
    expect(qc.getQueryState(qk.layoutCounts('priority-later'))?.isInvalidated).toBe(true)
    expect(qc.getQueryState(qk.layouts)?.isInvalidated).toBe(false) // the layouts themselves did not change
    stop()
  })
  it('with the live stream open only the tab badges refetch: the counts event brings the rest', async () => {
    const qc = new QueryClient()
    const stop = linkDerivedCounts(qc)
    liveCounts.set(true)
    try {
      qc.setQueryData(qk.conversationList({ view: 'inbox' } as never), { items: [], total: 0 })
      qc.setQueryData(qk.labels, [])
      qc.setQueryData(qk.layoutCounts('default'), { primary: 1 })
      await qc.invalidateQueries({ queryKey: qk.conversations })
      await new Promise((r) => setTimeout(r, 80))
      expect(qc.getQueryState(qk.labels)?.isInvalidated).toBe(false)
      expect(qc.getQueryState(qk.layoutCounts('default'))?.isInvalidated).toBe(true)
    } finally {
      liveCounts.set(false)
      stop()
    }
  })
})
