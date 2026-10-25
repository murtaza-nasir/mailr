import { describe, expect, it } from 'vitest'
import type { Connector } from '../../api/types'
import { calendarStepState } from './calendarStep'

function conn(id: number, extra: Partial<Connector> = {}): Connector {
  return {
    id, kind: 'caldav', name: `c${id}`, preset: null, mode: 'mirror', data_class: 'normal', config: {},
    retention_days: 14, delete_upstream: false, enabled: true, status: 'idle', status_detail: '', last_sync_at: null, ...extra,
  }
}

describe('the setup guide calendar step', () => {
  it('shows nothing connected and the prepared server with no accounts', () => {
    const s = calendarStepState([], 'https://cal.example.com/dav/')
    expect(s.connected).toEqual([])
    expect(s.server).toBe('https://cal.example.com/dav/')
  })

  it('counts the Mailr calendar as connected but keeps both choices on offer', () => {
    const b = conn(1, { builtin: true, name: 'Mailr calendar' })
    const s = calendarStepState([conn(9, { kind: 'imap' }), b], 'https://cal.example.com/dav/')
    expect(s.connected.map((c) => c.id)).toEqual([1])
    expect(s.builtin?.id).toBe(1)
    expect(s.external).toEqual([])
    expect(s.server).toBe('https://cal.example.com/dav/')
  })

  it('takes the prepared server off offer once a calendar on it is connected', () => {
    const b = conn(1, { builtin: true })
    const davis = conn(2, { config: { url: 'https://cal.example.com/dav/' } })
    const s = calendarStepState([davis, b], 'https://cal.example.com/dav/')
    expect(s.connected.map((c) => c.id)).toEqual([1, 2])
    expect(s.external.map((c) => c.id)).toEqual([2])
    expect(s.server).toBeNull()
  })
})
