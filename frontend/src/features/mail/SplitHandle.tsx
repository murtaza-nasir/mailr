// The draggable divider between the message list and the reading pane. The size is kept per
// theme and direction in localStorage (a per-device convenience) and applied as a CSS
// variable on the split: --list-w beside, --list-h below. Double-click resets it.

import { useCallback, useEffect, useState, type KeyboardEvent, type PointerEvent, type RefObject } from 'react'
import { t } from '../../i18n'
import { dirSign, endKey, startKey } from '../../lib/rtl'

const MIN_LIST = { right: 280, below: 120 }
const MIN_READER = { right: 360, below: 160 }

function load(key: string): number | null {
  try {
    const v = Number(localStorage.getItem(key))
    return Number.isFinite(v) && v > 0 ? v : null
  } catch {
    return null
  }
}

function store(key: string, v: number | null) {
  try {
    if (v == null) localStorage.removeItem(key)
    else localStorage.setItem(key, String(Math.round(v)))
  } catch {
    /* private window: the size just is not remembered */
  }
}

/** The saved size for this theme and direction, and a setter that clamps and saves it. */
export function useSplitSize(themeId: string, pane: 'right' | 'below', splitRef: RefObject<HTMLDivElement | null>) {
  const key = `mailr.split.${themeId}.${pane}`
  const [size, setSize] = useState<number | null>(() => load(key))
  useEffect(() => setSize(load(key)), [key])

  const clamp = useCallback(
    (v: number) => {
      const el = splitRef.current
      const total = el ? (pane === 'right' ? el.clientWidth : el.clientHeight) : Infinity
      return Math.max(MIN_LIST[pane], Math.min(v, total - MIN_READER[pane]))
    },
    [pane, splitRef],
  )
  const set = useCallback(
    (v: number | null, save = true) => {
      const next = v == null ? null : clamp(v)
      setSize(next)
      if (save) store(key, next)
    },
    [clamp, key],
  )
  return { size, set }
}

export function SplitHandle({
  pane,
  splitRef,
  size,
  onSize,
}: {
  pane: 'right' | 'below'
  splitRef: RefObject<HTMLDivElement | null>
  size: number | null
  onSize: (v: number | null, save?: boolean) => void
}) {
  const [dragging, setDragging] = useState(false)
  // The numbers a separator must carry (aria-valuenow, -min, -max; axe flagged the handle): the
  // list's size in pixels and the range the handle allows, kept up to date as the
  // split resizes.
  const [range, setRange] = useState<{ now: number; max: number } | null>(null)
  useEffect(() => {
    const el = splitRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const read = () => {
      const list = el.firstElementChild as HTMLElement | null
      const total = pane === 'right' ? el.clientWidth : el.clientHeight
      const now = list ? (pane === 'right' ? list.offsetWidth : list.offsetHeight) : 0
      setRange({ now: Math.round(now), max: Math.max(MIN_LIST[pane], Math.round(total - MIN_READER[pane])) })
    }
    read()
    const ro = new ResizeObserver(read)
    ro.observe(el)
    if (el.firstElementChild) ro.observe(el.firstElementChild)
    return () => ro.disconnect()
  }, [splitRef, pane])
  const current = () => {
    const list = splitRef.current?.firstElementChild as HTMLElement | null
    return size ?? (list ? (pane === 'right' ? list.offsetWidth : list.offsetHeight) : 400)
  }

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    const start = pane === 'right' ? e.clientX : e.clientY
    const from = current()
    let last = from
    setDragging(true)
    // The mail body is an iframe, which would swallow the pointer while dragging over it.
    document.body.classList.add('split-dragging')
    const move = (ev: globalThis.PointerEvent) => {
      // The list is on the start side: in a right-to-left page it grows as the pointer moves left.
      last = from + (pane === 'right' ? (ev.clientX - start) * dirSign() : ev.clientY - start)
      onSize(last, false)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setDragging(false)
      document.body.classList.remove('split-dragging')
      onSize(last)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const grow = pane === 'right' ? endKey() : 'ArrowDown'
    const shrink = pane === 'right' ? startKey() : 'ArrowUp'
    if (e.key !== grow && e.key !== shrink) return
    e.preventDefault()
    onSize(current() + (e.key === grow ? 1 : -1) * (e.shiftKey ? 64 : 16))
  }

  return (
    <div
      className={`split-handle${dragging ? ' dragging' : ''}`}
      role="separator"
      aria-orientation={pane === 'right' ? 'vertical' : 'horizontal'}
      aria-label={t('mail.splitHandle.resizeTheMessageList')}
      aria-valuenow={Math.round(size ?? range?.now ?? MIN_LIST[pane])}
      aria-valuemin={MIN_LIST[pane]}
      aria-valuemax={range?.max ?? undefined}
      tabIndex={0}
      title={t('mail.splitHandle.dragToResizeDoubleClick')}
      onPointerDown={onPointerDown}
      onDoubleClick={() => onSize(null)}
      onKeyDown={onKeyDown}
    />
  )
}
