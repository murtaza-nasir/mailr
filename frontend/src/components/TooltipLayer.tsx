import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { placeFloating } from '../lib/floating'

/**
 * One tooltip for the whole app. Any element with `data-tip="..."` shows it on hover/focus.
 * Cheaper than per-element tooltip components in long lists. Only a mouse hover or keyboard
 * focus shows it: a tap on a touch screen never does (taps also fire pointerover and focus).
 * It sits below its element (above when there is no room) and stays inside the window (lib/floating).
 *
 * It goes, as on other sites, when attention moves: a click or a scroll, the pointer leaving the
 * window, the window losing focus or the tab being hidden, keyboard focus leaving its element, Esc,
 * or its element leaving the page (a click that changed the screen).
 */
export function TooltipLayer() {
  const [tip, setTip] = useState<{ text: string; anchor: DOMRect } | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  useLayoutEffect(() => {
    const el = box.current
    if (!tip || !el) return setPos(null)
    setPos(placeFloating(tip.anchor, { width: el.offsetWidth, height: el.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight }, { prefer: 'bottom' }))
  }, [tip])

  useEffect(() => {
    let current: HTMLElement | null = null
    let byFocus = false
    let watch: number | undefined
    const hide = () => {
      current = null
      byFocus = false
      window.clearInterval(watch)
      watch = undefined
      setTip(null)
    }
    const show = (el: HTMLElement, focus = false) => {
      const text = el.dataset.tip
      if (!text) return
      current = el
      byFocus = focus
      setTip({ text, anchor: el.getBoundingClientRect() })
      // An element removed under the tip (the screen changed) takes its tip with it.
      window.clearInterval(watch)
      watch = window.setInterval(() => {
        if (current && !current.isConnected) hide()
      }, 250)
    }
    const onOver = (e: Event) => {
      const el = (e.target as HTMLElement | null)?.closest?.('[data-tip]') as HTMLElement | null
      if (el === current) return
      if (el) show(el)
      else hide()
    }
    const onPointer = (e: PointerEvent) => {
      if (e.pointerType === 'mouse') onOver(e)
    }
    const onFocus = (e: FocusEvent) => {
      // Keyboard focus only: focus that follows a tap or click is not :focus-visible.
      const t = e.target as HTMLElement | null
      if (!t?.matches?.(':focus-visible')) return
      const el = t.closest?.('[data-tip]') as HTMLElement | null
      if (el && el !== current) show(el, true)
      else if (!el) hide()
    }
    const onFocusOut = (e: FocusEvent) => {
      // A tip shown for keyboard focus goes with the focus (unless it moves inside the same element).
      if (byFocus && current && !current.contains(e.relatedTarget as Node | null)) hide()
    }
    const onPointerOut = (e: PointerEvent) => {
      // The pointer left the window.
      if (e.pointerType === 'mouse' && !e.relatedTarget) hide()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') hide()
    }
    const onVisibility = () => {
      if (document.visibilityState !== 'visible') hide()
    }
    document.addEventListener('pointerover', onPointer)
    document.addEventListener('pointerout', onPointerOut)
    document.addEventListener('focusin', onFocus)
    document.addEventListener('focusout', onFocusOut)
    document.addEventListener('pointerdown', hide)
    document.addEventListener('scroll', hide, true)
    document.addEventListener('keydown', onKey, true)
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('blur', hide)
    window.addEventListener('resize', hide)
    return () => {
      window.clearInterval(watch)
      document.removeEventListener('pointerover', onPointer)
      document.removeEventListener('pointerout', onPointerOut)
      document.removeEventListener('focusin', onFocus)
      document.removeEventListener('focusout', onFocusOut)
      document.removeEventListener('pointerdown', hide)
      document.removeEventListener('scroll', hide, true)
      document.removeEventListener('keydown', onKey, true)
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('blur', hide)
      window.removeEventListener('resize', hide)
    }
  }, [])

  if (!tip) return null
  return createPortal(
    <div ref={box} className="tooltip" role="tooltip"
      style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: 'hidden' }}>
      {tip.text}
    </div>,
    document.body,
  )
}
