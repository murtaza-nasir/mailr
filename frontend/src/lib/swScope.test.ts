/// <reference types="node" />
// The service worker's reach (area 4): public/sw.js answers only GET requests to
// its own origin and leaves the help pages (/help, /help/…) to the server; the page registers it at
// /sw.js with the default scope "/" and asks for no wider one. The server sends no
// Service-Worker-Allowed header (backend/tests/test_response_headers_hardening.py).
import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'

const ORIGIN = 'https://mail.example'

function worker() {
  const listeners: Record<string, (event: unknown) => void> = {}
  const self = {
    addEventListener: (type: string, fn: (event: unknown) => void) => { listeners[type] = fn },
    location: { origin: ORIGIN },
    navigator: {},
    registration: { getNotifications: async () => [], showNotification: async () => undefined },
    clients: { matchAll: async () => [], openWindow: async () => undefined, claim: async () => undefined },
  }
  const cache = { match: async () => undefined, put: async () => undefined, delete: async () => true, keys: async () => [] }
  const caches = { open: async () => cache, match: async () => undefined, keys: async () => [], delete: async () => true }
  const fetch = async () => ({ ok: false, status: 503, headers: { get: () => null }, clone() { return this } })
  const code = readFileSync(new URL('../../public/sw.js', import.meta.url), 'utf8')
  new Function('self', 'fetch', 'crypto', 'caches', code)(self, fetch, { randomUUID: () => 'key-1' }, caches)
  // Whether the worker answers the request itself (respondWith) or leaves it to the network.
  return (url: string, init: { method?: string; mode?: string } = {}) => {
    let answered = false
    listeners.fetch({
      request: { url, method: init.method ?? 'GET', mode: init.mode ?? 'no-cors', headers: { get: () => null } },
      respondWith: (p: Promise<unknown>) => {
        answered = true
        void Promise.resolve(p).catch(() => undefined)
      },
      waitUntil: () => undefined,
    })
    return answered
  }
}

describe('what the service worker answers', () => {
  it('its own build files and page loads (the check below would pass on a worker that answers nothing)', () => {
    const answers = worker()
    expect(answers(`${ORIGIN}/assets/index-abc123.js`)).toBe(true)
    expect(answers(`${ORIGIN}/inbox`, { mode: 'navigate' })).toBe(true)
  })

  it('never the help pages, another origin or a request other than GET', () => {
    const answers = worker()
    expect(answers(`${ORIGIN}/help`, { mode: 'navigate' })).toBe(false)
    expect(answers(`${ORIGIN}/help/`, { mode: 'navigate' })).toBe(false)
    expect(answers(`${ORIGIN}/help/user-guide/inbox/`, { mode: 'navigate' })).toBe(false)
    expect(answers(`${ORIGIN}/help/assets/javascripts/bundle.min.js`)).toBe(false)
    expect(answers('https://cdn.example.org/assets/index-abc123.js')).toBe(false)
    expect(answers('https://evil.example/inbox', { mode: 'navigate' })).toBe(false)
    expect(answers(`${ORIGIN}/assets/index-abc123.js`, { method: 'POST' })).toBe(false)
    expect(answers(`${ORIGIN}/api/v1/auth/session`, { method: 'POST' })).toBe(false)
    // A path that only starts with "help" is the app's.
    expect(answers(`${ORIGIN}/helpdesk`, { mode: 'navigate' })).toBe(true)
  })
})

describe('registration', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.resetModules()
  })

  it('registers /sw.js with the default scope', async () => {
    const register = vi.fn(async () => ({ waiting: null, installing: null, addEventListener: () => undefined }))
    vi.stubGlobal('navigator', { serviceWorker: { register, addEventListener: () => undefined, controller: null } })
    vi.stubGlobal('document', { addEventListener: () => undefined, visibilityState: 'visible' })
    const { registerServiceWorker } = await import('./serviceWorker')
    await registerServiceWorker()
    expect(register).toHaveBeenCalledTimes(1)
    expect(register.mock.calls[0]).toEqual(['/sw.js'])
  })
})
