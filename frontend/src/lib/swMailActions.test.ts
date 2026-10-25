/// <reference types="node" />
// ACT-SURFACES: web.notification
// The new-mail notification's buttons in the service worker (public/sw.js): Archive and Mark read
// (mail.archive, mail.read) post the action with the session's CSRF token, tell the open tabs and
// close the notification; signed out, the click opens the conversation instead. The worker runs
// here with a fake `self` and `fetch`.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

type Listener = (event: unknown) => void
interface Sent { url: string; init?: RequestInit }

function worker(session: { authenticated: boolean; csrf?: string } | null) {
  const listeners: Record<string, Listener> = {}
  const sent: Sent[] = []
  const posted: unknown[] = []
  const opened: string[] = []
  const tab = { focused: true, visibilityState: 'visible', focus: async () => tab, postMessage: (m: unknown) => posted.push(m) }
  const self = {
    addEventListener: (type: string, fn: Listener) => { listeners[type] = fn },
    location: { origin: 'https://mail.example' },
    navigator: {},
    registration: { getNotifications: async () => [] },
    clients: { matchAll: async () => [tab], openWindow: async (url: string) => { opened.push(url) } },
  }
  const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
  const fetch = async (url: string, init?: RequestInit) => {
    sent.push({ url, init })
    if (url === '/api/v1/auth/session') return session ? json(session) : { ok: false, status: 401, json: async () => ({}) }
    if (url === '/api/v1/counts') return json({ views: { inbox: { unread: 3 } } })
    return json({ ok: true })
  }
  const code = readFileSync(new URL('../../public/sw.js', import.meta.url), 'utf8')
  new Function('self', 'fetch', 'crypto', code)(self, fetch, { randomUUID: () => 'key-1' })
  const click = async (action: string) => {
    let closed = false
    let wait: Promise<unknown> = Promise.resolve()
    listeners.notificationclick({
      action,
      notification: { data: { type: 'mail', mail: true, conversation_id: 42, url: '/mail/inbox/42' }, close: () => { closed = true } },
      waitUntil: (p: Promise<unknown>) => { wait = p },
    })
    await wait
    return closed
  }
  return { click, sent, posted, opened }
}

describe('new-mail notification buttons', () => {
  for (const action of ['archive', 'read']) {
    it(`${action === 'archive' ? 'mail.archive' : 'mail.read'}: the button posts the action with the session's token`, async () => {
      const w = worker({ authenticated: true, csrf: 'csrf-1' })
      expect(await w.click(action)).toBe(true)
      const post = w.sent.find((s) => s.url === '/api/v1/conversations/actions')!
      expect(post.init?.method).toBe('POST')
      expect(JSON.parse(String(post.init?.body))).toEqual({ ids: [42], action })
      expect((post.init?.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf-1')
      expect(w.posted).toContainEqual({ type: 'mailr:mail-changed' })
      expect(w.opened).toEqual([])
    })
  }

  it('signed out, the button opens the conversation and changes nothing', async () => {
    const w = worker(null)
    await w.click('archive')
    expect(w.sent.some((s) => s.url === '/api/v1/conversations/actions')).toBe(false)
    expect(w.posted).toContainEqual({ type: 'mailr:navigate', url: '/mail/inbox/42' })
  })
})
