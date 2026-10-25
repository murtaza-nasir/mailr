import { ChevronLeft, ChevronRight } from 'lucide-react'
import './ScrollTabs.css'
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { scrollBehavior } from '../lib/motion'
import { arrowStep, isRtl, scrollFromStart, scrollLeftFor } from '../lib/rtl'
import { t } from '../i18n'

/** The current item of a strip: a selected tab, the page's link or a pressed chip. */
const CURRENT = '[aria-selected="true"], [aria-current="page"], [aria-pressed="true"]'

interface Props {
  /** The strip's own class (cat-tabs, settings-tabs, …); the wrapper gets `<className>-wrap`. */
  className: string
  label: string
  /** "tablist": role="tablist" over role="tab" children, with one tab in the Tab order.
   *  "nav": a <nav> of links or buttons, each in the Tab order. */
  kind: 'tablist' | 'nav'
  /** Changes when the current item changes or may have moved; it then scrolls into view. */
  revealKey?: unknown
  /** Rendered inside the wrapper after the strip (not one of its items): e.g. a hidden copy of
   *  the items for measuring (InboxTabs). */
  aside?: ReactNode
  children: ReactNode
}

/**
 * A row of tabs or chips that scrolls sideways when it does not fit. A side with hidden items
 * fades out and gets a ‹ or › button (touch screens swipe instead); a vertical mouse wheel
 * scrolls the row. The current item scrolls into view on load and when it changes. Arrow
 * keys, Home and End move between items; Enter or Space opens one. Start and end follow the
 * page's direction: in a right-to-left page the row starts on the right.
 */
export function ScrollTabs({ className, label, kind, revealKey, aside, children }: Props) {
  const strip = useRef<HTMLDivElement & HTMLElement>(null)
  const [edges, setEdges] = useState({ start: false, end: false })

  const measure = useCallback(() => {
    const el = strip.current
    if (!el) return
    const pos = scrollFromStart(el)
    const start = pos > 1
    const end = pos + el.clientWidth < el.scrollWidth - 1
    setEdges((e) => (e.start === start && e.end === end ? e : { start, end }))
  }, [])

  const items = () => Array.from(strip.current?.children ?? []).filter((c): c is HTMLElement => c instanceof HTMLElement)

  // The row's width changes with the pane, its content with the items and their counts.
  useEffect(() => {
    const el = strip.current
    if (!el) return
    const ro = new ResizeObserver(measure)
    const watch = () => {
      ro.disconnect()
      ro.observe(el)
      for (const c of items()) ro.observe(c)
      measure()
    }
    watch()
    const mo = new MutationObserver(watch)
    mo.observe(el, { childList: true })
    return () => {
      ro.disconnect()
      mo.disconnect()
    }
  }, [measure])

  // A vertical wheel scrolls the row sideways while it has hidden items (a mouse cannot swipe).
  useEffect(() => {
    const el = strip.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return
      const pos = scrollFromStart(el)
      const next = Math.max(0, Math.min(el.scrollWidth - el.clientWidth, pos + e.deltaY))
      if (next === pos) return
      e.preventDefault()
      el.scrollLeft = scrollLeftFor(next)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  // One tab of a tablist is in the Tab order: the selected one, else the first.
  useLayoutEffect(() => {
    if (kind !== 'tablist') return
    const all = items()
    const cur = all.find((c) => c.matches(CURRENT)) ?? all[0]
    for (const c of all) c.tabIndex = c === cur ? 0 : -1
  })

  // The current item comes into view, clear of the fades and buttons.
  const reveal = useCallback(() => {
    const el = strip.current
    const cur = el?.querySelector<HTMLElement>(`:scope > :is(${CURRENT})`)
    if (!el || !cur) return
    const pad = 40
    const rtl = isRtl()
    const s = el.getBoundingClientRect()
    const b = cur.getBoundingClientRect()
    // Distances from the strip's start edge (its left, or its right in a right-to-left page).
    const itemStart = rtl ? s.right - b.right : b.left - s.left
    const itemEnd = rtl ? s.right - b.left : b.right - s.left
    const pos = scrollFromStart(el, rtl)
    let delta = 0
    if (itemStart < pad) delta = Math.max(itemStart - pad, -pos)
    // Past the end: bring its start in at the strip's start, where a snap point of the strip lies
    // (the strip snaps to item starts, so a scroll that stops in between can end short of it).
    else if (itemEnd > s.width - pad) delta = itemStart - pad
    if (delta) el.scrollLeft = scrollLeftFor(pos + delta, rtl)
    measure()
  }, [measure])
  useLayoutEffect(() => {
    reveal()
  }, [revealKey, reveal])

  // A page is most of the visible width; near an end the row goes all the way there.
  const page = (dir: 1 | -1) => {
    const el = strip.current
    if (!el) return
    const max = el.scrollWidth - el.clientWidth
    let pos = scrollFromStart(el) + dir * Math.max(120, el.clientWidth * 0.7)
    if (pos < 48) pos = 0
    if (pos > max - 48) pos = max
    el.scrollTo({ left: scrollLeftFor(pos), behavior: scrollBehavior() })
  }

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const all = items()
    const i = all.indexOf(document.activeElement as HTMLElement)
    if (i < 0 || e.altKey || e.ctrlKey || e.metaKey) return
    // Enter and Space open the item here, so the app's shortcuts (Enter opens a conversation)
    // see a handled key.
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      all[i].click()
      return
    }
    const step = arrowStep(e.key)
    const j = step ? i + step : ({ Home: 0, End: all.length - 1 } as Record<string, number>)[e.key]
    if (j === undefined) return
    e.preventDefault()
    const next = all[(j + all.length) % all.length]
    next.focus()
    next.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }

  const Strip = kind === 'nav' ? 'nav' : 'div'
  return (
    <div className={`scroll-tabs ${className}-wrap`} data-start={edges.start || undefined} data-end={edges.end || undefined}>
      <Strip
        ref={strip}
        className={`scroll-tabs-strip ${className}`}
        role={kind === 'tablist' ? 'tablist' : undefined}
        aria-label={label}
        onScroll={measure}
        onKeyDown={onKeyDown}
      >
        {children}
      </Strip>
      {aside}
      {edges.start && (
        <button type="button" className="scroll-tabs-btn start" tabIndex={-1} aria-label={t('components.scrollTabs.scrollLeft', { label })} onClick={() => page(-1)}>
          <ChevronLeft />
        </button>
      )}
      {edges.end && (
        <button type="button" className="scroll-tabs-btn end" tabIndex={-1} aria-label={t('components.scrollTabs.scrollRight', { label })} onClick={() => page(1)}>
          <ChevronRight />
        </button>
      )}
    </div>
  )
}
