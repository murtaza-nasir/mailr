/// <reference types="node" />
// ACT-SURFACES: web.notification
// approval.sync in the service worker (public/sw.js): the server's approval.decided push closes
// the notifications of the approvals it names (tag a<id>) and leaves every other notification;
// it shows nothing of its own. An approval push still shows its notification under that tag.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

type Listener = (event: unknown) => void

interface Shown { title: string; tag: string; closed: boolean; close: () => void }

function worker() {
  const listeners: Record<string, Listener> = {}
  const shown: Shown[] = []
  const show = (title: string, tag: string) => {
    const n: Shown = { title, tag, closed: false, close: () => { n.closed = true } }
    shown.push(n)
  }
  const self = {
    addEventListener: (type: string, fn: Listener) => { listeners[type] = fn },
    location: { origin: 'https://mail.example' },
    navigator: {},
    registration: {
      getNotifications: async () => shown.filter((n) => !n.closed),
      showNotification: async (title: string, opts: { tag: string }) => show(title, opts.tag),
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
  return { push, shown, show }
}

describe('approval notifications in the service worker', () => {
  it('an approval push shows its notification under the tag a<id>', async () => {
    const w = worker()
    await w.push({ type: 'approval', title: 'Claude Code needs your approval', body: 'Create event', pending_id: 4503599627370501 })
    expect(w.shown.map((n) => n.tag)).toEqual(['a4503599627370501'])
  })

  it('approval.sync: approval.decided closes the named approvals and nothing else', async () => {
    const w = worker()
    for (const id of [11, 12, 13]) await w.push({ type: 'approval', title: `Approval ${id}`, body: '', pending_id: id })
    w.show('New mail from Dana', 'c7')
    w.show('Event reminder', 'e|uid|20261002T150000Z|10')
    await w.push({ type: 'approval.decided', title: '', body: '', outcome: 'approved', ids: [11, 13] })
    expect(w.shown.filter((n) => !n.closed).map((n) => n.tag)).toEqual(['a12', 'c7', 'e|uid|20261002T150000Z|10'])
    // It shows nothing of its own.
    expect(w.shown).toHaveLength(5)
  })

  it('approval.sync: a decided push for approvals not showing changes nothing', async () => {
    const w = worker()
    await w.push({ type: 'approval', title: 'Approval 21', body: '', pending_id: 21 })
    await w.push({ type: 'approval.decided', outcome: 'expired', ids: [99] })
    await w.push({ type: 'approval.decided', outcome: 'expired' })
    expect(w.shown.map((n) => [n.tag, n.closed])).toEqual([['a21', false]])
  })
})
