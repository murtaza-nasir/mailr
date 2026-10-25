// Follow/detach scrolling for the chat log.
//
// The log follows new output while the reader is at the bottom. Scrolling up (wheel, touch, keys or
// the scrollbar) detaches it; while detached nothing moves the log, so the view never snaps back.
// Scrolling back to the bottom, or `jump()` (the "Jump to latest" pill), attaches it again. The pill
// shows while the log is detached AND the latest output is out of sight (more than JUMP_DISTANCE below
// the view), also after the turn ends or while it waits on an approval card. A log too short to scroll
// never detaches, so an empty chat never shows the pill.

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'

export const BOTTOM_SLACK = 48
/** How far below the view the latest output must be before the pill offers to jump to it. */
export const JUMP_DISTANCE = 150

/** Whether a scroll position counts as "at the bottom". */
export function atBottom(scrollTop: number, scrollHeight: number, clientHeight: number, slack = BOTTOM_SLACK): boolean {
  return scrollHeight - scrollTop - clientHeight <= slack
}

export function useFollowScroll(ref: RefObject<HTMLElement | null>, content: unknown) {
  const [attached, setAttached] = useState(true)
  const [far, setFar] = useState(false)
  const attachedRef = useRef(true)
  const lastTop = useRef(0)
  const roRef = useRef<ResizeObserver | null>(null)
  const set = useCallback((v: boolean) => {
    attachedRef.current = v
    setAttached(v)
  }, [])

  const toBottom = useCallback(() => {
    const el = ref.current
    if (!el) return
    el.scrollTop = el.scrollHeight
    lastTop.current = el.scrollTop
  }, [ref])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const scrollable = () => el.scrollHeight - el.clientHeight > BOTTOM_SLACK
    const measure = () => setFar(!atBottom(el.scrollTop, el.scrollHeight, el.clientHeight, JUMP_DISTANCE))
    const onScroll = () => {
      measure()
      const bottom = atBottom(el.scrollTop, el.scrollHeight, el.clientHeight)
      // Moving up detaches; reaching the bottom attaches. Growth below (scrollTop unchanged) does not detach.
      if (el.scrollTop < lastTop.current - 2 && !bottom) set(false)
      else if (bottom) set(true)
      lastTop.current = el.scrollTop
    }
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0 && scrollable()) set(false)
    }
    let touchY = 0
    const onTouchStart = (e: TouchEvent) => {
      touchY = e.touches[0]?.clientY ?? 0
    }
    const onTouchMove = (e: TouchEvent) => {
      if ((e.touches[0]?.clientY ?? 0) > touchY + 4 && scrollable()) set(false)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('touchstart', onTouchStart, { passive: true })
    el.addEventListener('touchmove', onTouchMove, { passive: true })
    // Content that grows without a React update (images, a table measuring itself) is followed too.
    const ro = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => {
          if (attachedRef.current) toBottom()
          measure()
        })
      : null
    // The log itself too: it gets shorter when the composer grows or a card opens under it.
    ro?.observe(el)
    roRef.current = ro
    return () => {
      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('touchstart', onTouchStart)
      el.removeEventListener('touchmove', onTouchMove)
      ro?.disconnect()
      roRef.current = null
    }
  }, [ref, set, toBottom])

  // Follow every update while attached.
  useLayoutEffect(() => {
    const el = ref.current
    if (el && roRef.current) for (const child of Array.from(el.children)) roRef.current.observe(child)
    if (attachedRef.current) toBottom()
  }, [content, ref, toBottom])

  /** Attach and go to the latest output (the pill, sending a message). */
  const jump = useCallback(() => {
    set(true)
    toBottom()
  }, [set, toBottom])

  /** Stop following, so a turn brought into view stays there (a memory's "From a chat" link). */
  const detach = useCallback(() => set(false), [set])

  /** Show "Jump to latest": detached, and the latest output is well out of sight. */
  const showJump = !attached && far
  return { attached, jump, detach, showJump }
}
