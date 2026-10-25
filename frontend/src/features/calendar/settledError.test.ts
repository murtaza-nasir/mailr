// The calendar's load-error banner follows settledError: it stays while requests keep
// failing and clears only after a success.
import { QueryClient, QueryObserver, keepPreviousData } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import { settledError } from './data'

describe('settledError', () => {
  it('keeps the error through a refetch of a query without data, and clears on success', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let fail = true
    let release: () => void = () => {}
    const obs = new QueryObserver(client, {
      queryKey: ['range'],
      queryFn: () => new Promise<number>((resolve, reject) => {
        release = () => (fail ? reject(new Error('503')) : resolve(1))
      }),
      placeholderData: keepPreviousData,
    })
    let shown: unknown = null
    const seen: { raw: unknown; shown: unknown }[] = []
    const unsub = obs.subscribe((r) => {
      shown = settledError(shown, r)
      seen.push({ raw: r.error, shown })
    })
    await Promise.resolve()
    release()
    await expect.poll(() => obs.getCurrentResult().isError).toBe(true)
    expect(shown).toBeInstanceOf(Error)

    // Retry while the server still fails. React Query itself drops the error at the start.
    seen.length = 0
    const refetch = obs.refetch()
    await Promise.resolve()
    expect(obs.getCurrentResult().error).toBeNull()
    expect(shown).toBeInstanceOf(Error)
    release()
    await refetch
    expect(seen.some((s) => s.raw == null)).toBe(true)
    expect(seen.every((s) => s.shown instanceof Error)).toBe(true)

    // A success clears it.
    fail = false
    const ok = obs.refetch()
    await Promise.resolve()
    release()
    await ok
    expect(shown).toBeNull()
    unsub()
    client.clear()
  })

  it('does not count placeholder data from another range as a success', () => {
    const err = new Error('503')
    expect(settledError(err, { error: null, isSuccess: true, isPlaceholderData: true })).toBe(err)
    expect(settledError(err, { error: null, isSuccess: false, isPlaceholderData: false })).toBe(err)
    expect(settledError(err, { error: null, isSuccess: true, isPlaceholderData: false })).toBeNull()
  })
})
