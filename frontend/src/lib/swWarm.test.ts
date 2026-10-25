// Which answers the page asks for again through a service worker that took over a first visit
// (lib/serviceWorker.ts warmCache): kept paths on this origin that started before the takeover,
// tab lists included, also the ones that finish after it.
import { describe, expect, it } from 'vitest'
import { bypassedUrls, KEPT } from './swWarm'

const O = 'http://localhost:4173'
const e = (path: string, startTime: number, origin = O) => ({ name: origin + path, startTime })

describe('bypassedUrls', () => {
  it('keeps the tabbed inbox and account lists with their whole query', () => {
    const got = bypassedUrls([
      e('/api/v1/conversations?view=inbox&layout=default&tab=primary&sec=primary&limit=50', 100),
      e('/api/v1/conversations?view=acct%3A2&limit=50', 120),
      e('/api/v1/layouts/default/counts?detail=true', 110),
    ], O, 500)
    expect(got).toEqual([
      '/api/v1/conversations?view=inbox&layout=default&tab=primary&sec=primary&limit=50',
      '/api/v1/conversations?view=acct%3A2&limit=50',
      '/api/v1/layouts/default/counts?detail=true',
    ])
  })

  it('leaves out requests made through the worker (started at or after the takeover)', () => {
    expect(bypassedUrls([e('/api/v1/conversations?view=inbox&limit=50', 500), e('/api/v1/labels', 900)], O, 500)).toEqual([])
  })

  it('counts a list still loading at the takeover (its entry arrives after it, its start is before)', () => {
    expect(bypassedUrls([e('/api/v1/conversations?view=inbox&tab=primary&limit=50', 499.9)], O, 500)).toEqual(['/api/v1/conversations?view=inbox&tab=primary&limit=50'])
  })

  it('leaves out other origins, scripts, the bulk action POST and answers the worker does not keep', () => {
    expect(bypassedUrls([
      e('/api/v1/conversations?view=inbox', 10, 'https://example.com'),
      e('/assets/boot-0123456789.js', 10),
      e('/api/v1/events', 10),
      e('/api/v1/conversations/actions', 10),
      { name: 'not a url', startTime: 10 },
    ], O, 500)).toEqual([])
  })

  it('matches the worker\'s kept kinds (public/sw.js KINDS)', () => {
    for (const p of ['/api/v1/auth/session', '/api/v1/labels', '/api/v1/counts', '/api/v1/layouts', '/api/v1/layouts/default/counts', '/api/v1/pictures/local', '/api/v1/conversations', '/api/v1/conversations/c1', '/api/v1/messages/m1/body']) expect(KEPT.test(p), p).toBe(true)
    for (const p of ['/api/v1/events', '/api/v1/changes', '/api/v1/messages/m1/raw']) expect(KEPT.test(p), p).toBe(false)
  })
})
