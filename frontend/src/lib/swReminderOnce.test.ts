/// <reference types="node" />
// ACT-SURFACES: web.notification
// reminder.fire in the service worker (public/sw.js): one alert reaches a browser as a Web Push and,
// through every open tab, as an SSE event the tab hands over ('mailr:reminder'). All carry the
// server's `delivery` id, and the worker shows it once. Firefox on a Linux desktop showed each copy
// as its own popup despite the shared tag (three popups per reminder, 15 Nov 2026).
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

type Listener = (event: unknown) => void

interface Shown { title: string; tag: string; data: Record<string, unknown>; closed: boolean; close: () => void }

function worker() {
  const listeners: Record<string, Listener> = {}
  const shown: Shown[] = []
  const self = {
    addEventListener: (type: string, fn: Listener) => { listeners[type] = fn },
    location: { origin: 'https://mail.example' },
    navigator: {},
    registration: {
      getNotifications: async (o?: { tag?: string }) => shown.filter((n) => !n.closed && (!o?.tag || n.tag === o.tag)),
      showNotification: async (title: string, opts: { tag: string; data?: Record<string, unknown> }) => {
        const n: Shown = { title, tag: opts.tag, data: opts.data ?? {}, closed: false, close: () => { n.closed = true } }
        // Same tag: the browser replaces it (what Chrome does; Firefox on Linux still pops up again).
        for (const o of shown) if (o.tag === n.tag) o.closed = true
        shown.push(n)
      },
    },
    clients: { matchAll: async () => [], openWindow: async () => undefined },
  }
  const fetch = async () => ({ ok: false, status: 503, json: async () => ({}) })
  const code = readFileSync(new URL('../../public/sw.js', import.meta.url), 'utf8')
  new Function('self', 'fetch', 'crypto', code)(self, fetch, { randomUUID: () => 'key-1' })
  const push = async (data: unknown) => {
    let wait: Promise<unknown> = Promise.resolve()
    listeners.push({ data: { json: () => data }, waitUntil: (p: Promise<unknown>) => { wait = p } })
    await wait
  }
  const acks: unknown[] = []
  const fromTab = async (d: unknown, id = 'm1') => {
    let wait: Promise<unknown> = Promise.resolve()
    listeners.message({ data: { type: 'mailr:reminder', id, d }, source: { postMessage: (m: unknown) => acks.push(m) },
                        waitUntil: (p: Promise<unknown>) => { wait = p } })
    await wait
  }
  return { push, fromTab, shown, acks }
}

const task = (delivery: string | undefined, extra: Record<string, unknown> = {}) => ({
  kind: 'reminder', key: 't|uid-1|abs|20261115T091800Z', title: 'NIH biosketch', body: 'Due yesterday, 18:00',
  item: { kind: 'task', uid: 'uid-1' }, ...(delivery ? { delivery } : {}), ...extra,
})

describe('a reminder is shown once per browser', () => {
  it('the push and two tabs with the same delivery show one notification', async () => {
    const w = worker()
    await w.fromTab(task('d1'), 'a')
    await w.push(task('d1'))
    await w.fromTab(task('d1'), 'b')
    expect(w.shown).toHaveLength(1)
    expect(w.shown[0].data.delivery).toBe('d1')
    // Each tab is told the worker took it, so no tab shows its own.
    expect(w.acks).toEqual([{ type: 'mailr:reminder-ack', id: 'a' }, { type: 'mailr:reminder-ack', id: 'b' }])
  })

  it('a repeat (a new delivery) alerts again under the same tag', async () => {
    const w = worker()
    await w.push(task('d1'))
    await w.push(task('d2', { again: true }))
    expect(w.shown).toHaveLength(2)
    expect(w.shown.filter((n) => !n.closed).map((n) => n.data.delivery)).toEqual(['d2'])
  })

  it('a copy is still skipped when the worker was restarted in between', async () => {
    const a = worker()
    await a.push(task('d1'))
    const b = worker()
    // The new worker sees the open notification's delivery.
    b.shown.push(...a.shown)
    await b.fromTab(task('d1'))
    expect(b.shown).toHaveLength(1)
  })

  it('missed copies count once in the missed notice', async () => {
    const w = worker()
    await w.push(task('m1', { missed: true }))
    await w.fromTab(task('m1', { missed: true }))
    await w.push(task('m2', { missed: true }))
    const open = w.shown.filter((n) => !n.closed)
    expect(open.map((n) => n.title)).toEqual(['2 missed reminders'])
  })

  it('an alert from an older server (no delivery) is shown as before', async () => {
    const w = worker()
    await w.push(task(undefined))
    await w.push(task(undefined))
    expect(w.shown).toHaveLength(2)
  })
})
