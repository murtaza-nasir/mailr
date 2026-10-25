// Ambrose wrote "today at 17:08" to someone whose app shows 5:08 PM (20 Oct 2026): every request,
// the assistant's chat stream included, says which clock this device shows (X-Mailr-Clock).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, postStream } from './client'
import { setClockSetting } from '../lib/dates'

function sentHeaders(): Record<string, string>[] {
  const seen: Record<string, string>[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
    seen.push(init.headers as Record<string, string>)
    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
  }))
  return seen
}

afterEach(() => {
  vi.unstubAllGlobals()
  setClockSetting(null)
})

describe('X-Mailr-Clock', () => {
  it('sends the clock the user chose, on plain requests and on the chat stream', async () => {
    const seen = sentHeaders()
    setClockSetting('12h')
    await api.get('/me')
    setClockSetting('24h')
    for await (const _ of postStream('/agent/chat', { message: 'hi' })) { /* no events */ }
    expect(seen[0]['X-Mailr-Clock']).toBe('12h')
    expect(seen[1]['X-Mailr-Clock']).toBe('24h')
    expect(seen[1]['X-Mailr-Zone']).toBeTruthy()
  })

  it('on Automatic, sends the locale clock as 12h or 24h', async () => {
    const seen = sentHeaders()
    setClockSetting('auto')
    await api.get('/me')
    expect(['12h', '24h']).toContain(seen[0]['X-Mailr-Clock'])
  })
})
