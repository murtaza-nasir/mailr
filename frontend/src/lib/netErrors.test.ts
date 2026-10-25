import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import { backoffDelay, classifyError, isChunkLoadError, isTransient } from './netErrors'

describe('classifyError', () => {
  it('tells a signed-out session from everything else', () => {
    expect(classifyError(new ApiError(401, 'not signed in'))).toBe('unauthorized')
    expect(classifyError(new ApiError(403, 'no'))).toBe('forbidden')
  })
  it('treats network failures and timeouts as transient', () => {
    expect(classifyError(new ApiError(0, 'Network error', undefined, 'network'))).toBe('network')
    expect(classifyError(new ApiError(0, 'Network error'))).toBe('network')
    expect(classifyError(new ApiError(0, 'slow', undefined, 'timeout'))).toBe('timeout')
    expect(classifyError(new TypeError('Failed to fetch'))).toBe('network')
    expect(classifyError(Object.assign(new Error('t'), { name: 'TimeoutError' }))).toBe('timeout')
    expect(classifyError(new ApiError(408, 'Request Timeout'))).toBe('timeout')
    for (const e of [new ApiError(0, 'x'), new ApiError(0, 'x', undefined, 'timeout'), new TypeError('NetworkError when attempting to fetch resource.')])
      expect(isTransient(e)).toBe(true)
  })
  it('treats 5xx as transient and 4xx as final', () => {
    for (const s of [500, 502, 503, 504]) {
      expect(classifyError(new ApiError(s, 'x'))).toBe('server')
      expect(isTransient(new ApiError(s, 'x'))).toBe(true)
    }
    expect(classifyError(new ApiError(404, 'Not Found'))).toBe('not_found')
    expect(classifyError(new ApiError(410, 'Gone'))).toBe('not_found')
    expect(classifyError(new ApiError(422, 'bad'))).toBe('client')
    for (const s of [400, 401, 403, 404, 422]) expect(isTransient(new ApiError(s, 'x'))).toBe(false)
  })
  it('keeps aborts and odd values apart', () => {
    expect(classifyError(new DOMException('aborted', 'AbortError'))).toBe('aborted')
    expect(isTransient(new DOMException('aborted', 'AbortError'))).toBe(false)
    expect(classifyError(null)).toBe('unknown')
    expect(classifyError('boom')).toBe('unknown')
    expect(classifyError(new Error('render bug'))).toBe('unknown')
  })
})

describe('isChunkLoadError', () => {
  it('recognises failed dynamic imports in each browser', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: http://x/assets/TasksApp-abc.js'))).toBe(true)
    expect(isChunkLoadError(new TypeError('error loading dynamically imported module: http://x/assets/a.js'))).toBe(true)
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true)
    expect(isChunkLoadError(new Error('Unable to preload CSS for /assets/a.css'))).toBe(true)
    expect(isChunkLoadError(Object.assign(new Error('Loading chunk 7 failed'), { name: 'ChunkLoadError' }))).toBe(true)
  })
  it('ignores other errors', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch'))).toBe(false)
    expect(isChunkLoadError(new Error("Cannot read properties of undefined (reading 'id')"))).toBe(false)
    expect(isChunkLoadError(null)).toBe(false)
  })
})

describe('backoffDelay', () => {
  it('doubles from 2 s to a 60 s ceiling', () => {
    const mid = () => 0.5
    expect([0, 1, 2, 3, 4, 5, 9].map((a) => backoffDelay(a, mid))).toEqual([2000, 4000, 8000, 16000, 32000, 60000, 60000])
  })
  it('jitters by at most 20 percent', () => {
    expect(backoffDelay(0, () => 0)).toBe(1600)
    expect(backoffDelay(0, () => 1)).toBe(2400)
  })
})
