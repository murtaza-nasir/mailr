// A proxy's error page (HTML) while Mailr restarts must never become the shown message (14 Oct 2026:
// "Action failed: <html> <head><title>502 Bad Gateway</title>…").
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from './client'

function answer(status: number, body: string, type: string) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status, headers: { 'content-type': type } })))
}

afterEach(() => vi.unstubAllGlobals())

describe('error answers that are not JSON', () => {
  it('a 502 HTML page says Mailr is restarting, without the HTML', async () => {
    answer(502, '<html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center><hr><center>openresty</center></body></html>', 'text/html')
    const e = (await api.get('/labels').catch((x: unknown) => x)) as ApiError
    expect(e).toBeInstanceOf(ApiError)
    expect(e.status).toBe(502)
    expect(e.message).toBe('Mailr is restarting or cannot be reached. Try again in a moment.')
    expect(e.message).not.toMatch(/<|html|openresty/i)
  })

  it('another plain-text error gives its status only', async () => {
    answer(500, 'Internal Server Error', 'text/plain')
    const e = (await api.get('/labels').catch((x: unknown) => x)) as ApiError
    expect(e.message).toBe('The server could not do this (error 500).')
  })

  it('a JSON error keeps its own message', async () => {
    answer(400, JSON.stringify({ detail: 'add at least one recipient' }), 'application/json')
    const e = (await api.get('/labels').catch((x: unknown) => x)) as ApiError
    expect(e.message).toBe('add at least one recipient')
  })
})
