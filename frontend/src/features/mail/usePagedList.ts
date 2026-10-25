import { useCallback, useMemo, useRef, useState } from 'react'
import { useConversations, type ListParams } from '../../api/hooks'
import { formatRange } from '../../lib/format'

function readStack(storageKey: string | undefined, key: string): (string | null)[] {
  if (!storageKey) return [null]
  try {
    const raw = sessionStorage.getItem(storageKey)
    if (!raw) return [null]
    const saved = JSON.parse(raw) as { key: string; stack: (string | null)[] }
    return saved.key === key && Array.isArray(saved.stack) && saved.stack.length ? saved.stack : [null]
  } catch {
    return [null]
  }
}

function writeStack(storageKey: string | undefined, key: string, stack: (string | null)[]) {
  if (!storageKey) return
  try {
    sessionStorage.setItem(storageKey, JSON.stringify({ key, stack }))
  } catch {
    /* private mode */
  }
}

/** Keyset pagination with a cursor stack so "newer" goes back a page.

  With `storageKey`, the page position survives leaving the list (opening a conversation and
  coming back), for the browser session. While a different list's data is still showing as a
  placeholder (a view or search change that is loading or failed), `items` is empty and
  `loading` is true, so rows from another view never appear under the new one. */
export function usePagedList(params: Omit<ListParams, 'cursor'>, enabled = true, storageKey?: string) {
  const key = JSON.stringify(params)
  const [state, setState] = useState<{ key: string; stack: (string | null)[] }>(() => ({ key, stack: readStack(storageKey, key) }))
  // Reset when the params change (derived state instead of an effect).
  const stack = useMemo(() => (state.key === key ? state.stack : readStack(storageKey, key)), [state, key, storageKey])
  if (state.key !== key) setState({ key, stack })

  const set = useCallback(
    (next: (string | null)[]) => {
      setState({ key, stack: next })
      writeStack(storageKey, key, next)
    },
    [key, storageKey],
  )

  const cursor = stack[stack.length - 1]
  const query = useConversations({ ...params, cursor }, enabled)
  // Which list the data on screen belongs to (placeholder data may be another view's).
  const shownKey = useRef<string | null>(null)
  if (query.data && !query.isPlaceholderData) shownKey.current = key
  const foreign = query.isPlaceholderData && shownKey.current !== key
  const data = foreign ? undefined : query.data
  const items = useMemo(() => data?.items ?? [], [data])
  const page = stack.length - 1
  const limit = params.limit ?? 50
  const start = page * limit
  const total = data?.total ?? 0

  const next = useCallback(() => {
    if (data?.next_cursor) set([...stack, data.next_cursor])
  }, [data?.next_cursor, stack, set])
  const prev = useCallback(() => {
    if (stack.length > 1) set(stack.slice(0, -1))
  }, [stack, set])
  const reset = useCallback(() => set([null]), [set])

  return {
    query,
    items,
    total,
    /** The total stopped at the server's cap (the list holds more). */
    capped: !!data?.total_capped,
    page,
    start,
    /** True while nothing for these params is known yet (first load, or another view's data). */
    loading: query.isLoading || (foreign && query.isFetching),
    /** Nothing for these params is known and the request waits for the network (offline). */
    waiting: !data && query.fetchStatus === 'paused',
    error: query.isError ? (query.error as Error) : null,
    rangeText: formatRange(start, items.length, Math.max(total, start + items.length), !!data?.total_capped),
    canPrev: stack.length > 1,
    canNext: !!data?.next_cursor,
    next,
    prev,
    reset,
  }
}

export type PagedList = ReturnType<typeof usePagedList>
