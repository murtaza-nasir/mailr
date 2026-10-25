import { describe, expect, it } from 'vitest'
import type { Connector } from '../../api/types'
import { importSummary, importText } from './importProgress'

const conn = (over: Partial<Connector>): Connector => ({
  id: 1, kind: 'gmail', name: 'rosa@gmail.com', preset: 'gmail', mode: 'mirror', data_class: 'normal', config: {}, retention_days: 14,
  delete_upstream: false, enabled: true, status: 'idle', status_detail: '', last_sync_at: null, ...over,
})

describe('import progress', () => {
  it('is quiet when nothing is coming in', () => {
    expect(importSummary([]).active).toBe(false)
    expect(importSummary([conn({ progress: { initial_done: true } })]).active).toBe(false)
    expect(importSummary([conn({ kind: 'caldav', progress: { initial_done: false } })]).active).toBe(false)
    expect(importSummary([conn({ enabled: false, progress: { initial_done: false } })]).active).toBe(false)
  })
  it('first brings in the recent mail', () => {
    const s = importSummary([conn({ progress: { initial_done: false } })])
    expect(s).toMatchObject({ active: true, phase: 'recent', fraction: null })
    expect(importText(s)).toBe('Importing recent mail…')
  })
  it('then counts the older mail across accounts', () => {
    const s = importSummary([
      conn({ progress: { initial_done: true, backfill: { done: false, total: 3000, imported: 1000 } } }),
      conn({ id: 2, name: 'b@gmail.com', progress: { initial_done: true, backfill: { done: false, total: 1000, imported: 1000 } } }),
      conn({ id: 3, progress: { initial_done: true, backfill: { done: true, total: 50, imported: 50 } } }),
    ])
    expect(s).toMatchObject({ phase: 'older', imported: 2000, total: 4000, fraction: 0.5, accounts: ['rosa@gmail.com', 'b@gmail.com'] })
    expect(importText(s)).toBe('Importing older mail: 2,000 of 4,000')
  })
  it('says so while the older mail is still being counted', () => {
    const s = importSummary([conn({ progress: { initial_done: true, backfill: { done: false, total: null, imported: 0 } } })])
    expect(s.total).toBeNull()
    expect(importText(s)).toBe('Importing older mail…')
  })
})
