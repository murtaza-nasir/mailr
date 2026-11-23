// ACT-SURFACES: web.notification
// reminder.fire in the tab (notify.ts): a reminder from the SSE stream is handed to the service
// worker, which shows each delivery once for the browser; with no worker, or one that does not
// answer, the tab shows it, and then only the tab that claims the delivery.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Fn = (e: { data: unknown }) => void

function storage() {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    key: (i: number) => [...m.keys()][i] ?? null,
    get length() { return m.size },
  }
}

function fakeWorker(answer: boolean) {
  const listeners: Fn[] = []
  const posted: unknown[] = []
  const active = {
    postMessage: (m: { type: string; id: string }) => {
      posted.push(m)
      if (answer) setTimeout(() => listeners.forEach((fn) => fn({ data: { type: 'mailr:reminder-ack', id: m.id } })), 5)
    },
  }
  const reg = { active }
  vi.stubGlobal('navigator', {
    serviceWorker: {
      getRegistration: async () => reg,
      addEventListener: (_t: string, fn: Fn) => listeners.push(fn),
      removeEventListener: (_t: string, fn: Fn) => { const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1) },
    },
  })
  return { posted, listeners }
}

const p = { key: 't|uid-1|abs|20261115T091800Z', title: 'NIH biosketch', delivery: 'd1' }

describe('reminders from the SSE stream', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.stubGlobal('localStorage', storage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('go to the service worker, which answers', async () => {
    const w = fakeWorker(true)
    const { viaWorker } = await import('./notify')
    expect(await viaWorker(p)).toBe(true)
    expect(w.posted).toEqual([{ type: 'mailr:reminder', id: expect.any(String), d: p }])
    expect(w.listeners).toHaveLength(0)
  })

  it('are shown by the tab when the worker does not answer (an older worker)', async () => {
    vi.useFakeTimers()
    const w = fakeWorker(false)
    const { viaWorker } = await import('./notify')
    const got = viaWorker(p)
    await vi.advanceTimersByTimeAsync(1600)
    expect(await got).toBe(false)
    expect(w.listeners).toHaveLength(0)
  })

  it('are shown by the tab when there is no worker', async () => {
    vi.stubGlobal('navigator', {})
    const { viaWorker } = await import('./notify')
    expect(await viaWorker(p)).toBe(false)
  })

  it('without a worker, one tab claims a delivery', async () => {
    const { claim } = await import('./notify')
    expect(await claim('d1')).toBe(true)
    // Another tab (same storage) finds it taken.
    vi.resetModules()
    const other = await import('./notify')
    expect(await other.claim('d1')).toBe(false)
    expect(await other.claim('d2')).toBe(true)
  })
})
