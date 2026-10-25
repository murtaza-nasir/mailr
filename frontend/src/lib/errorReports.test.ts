import { afterEach, describe, expect, it, vi } from 'vitest'
import { scrubEvent, startErrorReports, stripUrl } from './errorReports'

const init = vi.fn()
vi.mock('./sentryParts', () => ({
  init: (o: unknown) => init(o),
  dedupeIntegration: () => ({}),
  eventFiltersIntegration: () => ({}),
  functionToStringIntegration: () => ({}),
  globalHandlersIntegration: () => ({}),
  linkedErrorsIntegration: () => ({}),
}))

describe('error reports', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('strips query strings and fragments', () => {
    expect(stripUrl('https://m.example/inbox?q=tax#c=1')).toBe('https://m.example/inbox')
    expect(stripUrl('https://m.example/a/b')).toBe('https://m.example/a/b')
  })

  it('scrubs a report', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ev: any = scrubEvent({
      request: { url: 'https://m.example/search?q=refund', headers: { Cookie: 's=1' } },
      breadcrumbs: [{ message: 'clicked' }],
      user: { id: '1', email: 'a@b.example' },
      exception: { values: [{ value: 'bad dave@example.com', stacktrace: { frames: [{ filename: 'https://m.example/assets/x.js?v=1', vars: { a: 1 } }] } }] },
    })
    expect(ev.request).toEqual({ url: 'https://m.example/search' })
    expect(ev.breadcrumbs).toBeUndefined()
    expect(ev.user).toBeUndefined()
    expect(JSON.stringify(ev)).not.toContain('dave@')
    expect(ev.exception.values[0].stacktrace.frames[0]).toEqual({ filename: 'https://m.example/assets/x.js', abs_path: undefined })
  })

  it('stays off without a DSN and never loads the SDK', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ dsn: null }) })
    vi.stubGlobal('fetch', fetchMock)
    expect(await startErrorReports()).toBe(false)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/errors/config', { credentials: 'same-origin' })
  })

  it('posts its reports to the tunnel the server names, with its key', async () => {
    const tunnel = '/api/v1/errors/envelope?k=signed-key'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ dsn: 'http://k@errors.example/3', tunnel }) }))
    expect(await startErrorReports()).toBe(true)
    expect(init).toHaveBeenCalledWith(expect.objectContaining({ tunnel }))
  })
})
