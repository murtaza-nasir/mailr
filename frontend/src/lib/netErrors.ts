// What a failed request means for the app. A signed-out session is the only
// failure that ends the session; a network error, a timeout or a 5xx keeps the app and its data
// on screen and retries.

export type ErrorKind = 'unauthorized' | 'forbidden' | 'not_found' | 'network' | 'timeout' | 'server' | 'client' | 'aborted' | 'unknown'

interface Shaped {
  status?: unknown
  name?: unknown
  message?: unknown
  kind?: unknown
}

export function classifyError(err: unknown): ErrorKind {
  if (!err || typeof err !== 'object') return 'unknown'
  const e = err as Shaped
  if (e.name === 'AbortError') return 'aborted'
  if (e.name === 'TimeoutError' || e.kind === 'timeout') return 'timeout'
  const status = typeof e.status === 'number' ? e.status : undefined
  if (status === undefined) {
    // fetch() itself rejects with a TypeError when the network is down.
    if (e.name === 'TypeError' && typeof e.message === 'string' && /fetch|network|load failed/i.test(e.message)) return 'network'
    return 'unknown'
  }
  if (status === 0) return e.kind === 'timeout' ? 'timeout' : 'network'
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404 || status === 410) return 'not_found'
  if (status === 408) return 'timeout'
  if (status >= 500) return 'server'
  if (status >= 400) return 'client'
  return 'unknown'
}

/** A failure that may succeed on a later try: retry it, and keep what is on screen. */
export function isTransient(err: unknown): boolean {
  const k = classifyError(err)
  return k === 'network' || k === 'timeout' || k === 'server'
}

/** A dynamic import (lazy chunk) that failed to load: after a deploy, the old hashed file is gone. */
export function isChunkLoadError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false
  const e = err as Shaped
  if (e.name === 'ChunkLoadError') return true
  const msg = typeof e.message === 'string' ? e.message : ''
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS|Loading (CSS )?chunk [\w-]+ failed/i.test(msg)
}

/** Delay before reconnect attempt `attempt` (0-based): 2 s doubling to a 60 s ceiling, with jitter. */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(60_000, 2000 * 2 ** Math.max(0, attempt))
  return Math.round(base * (0.8 + 0.4 * random()))
}
