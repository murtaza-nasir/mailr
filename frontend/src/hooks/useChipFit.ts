import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react'

/**
 * Folds a chip's second part when the chip is squeezed, so its label is never cut to "Track pac…".
 *
 * A chip that carries `data-chip-fold` has a label (`data-chip-main`) and a part that can go
 * (`data-chip-extra`, such as an event's "Tomorrow 3 PM"). When the label shows narrower than it
 * would be on its own (its text, or its max-width when the text is longer), the chip gets
 * `data-chip-folded` and the extra part leaves the line (conversation.css keeps it for screen readers;
 * the chip's tooltip holds the full text). A label longer than its max-width keeps its ellipsis: that
 * is a long chip, and the tooltip says the rest.
 *
 * The chips can arrive after the first render (their queries load in child components), and the
 * box can be replaced, so the hook checks the box on every render and watches it for size and
 * content changes.
 */
export function useChipFit(ref: RefObject<HTMLElement | null>): void {
  const watched = useRef<{ el: HTMLElement; stop: () => void } | null>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (watched.current?.el === el) return
    watched.current?.stop()
    watched.current = null
    if (!el) return
    let frame = 0
    const fit = () => {
      for (const chip of el.querySelectorAll<HTMLElement>('[data-chip-fold]')) {
        chip.removeAttribute('data-chip-folded')
        const main = chip.querySelector<HTMLElement>('[data-chip-main]')
        if (!main || !chip.querySelector('[data-chip-extra]')) continue
        const cap = parseFloat(getComputedStyle(main).maxWidth)
        const want = Math.min(main.scrollWidth, Number.isFinite(cap) ? cap : Infinity)
        if (main.clientWidth < want - 1) chip.setAttribute('data-chip-folded', '')
      }
    }
    const later = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(fit)
    }
    fit()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(later) : null
    ro?.observe(el)
    // Not attributes: fit itself toggles data-chip-folded.
    const mo = new MutationObserver(later)
    mo.observe(el, { childList: true, subtree: true, characterData: true })
    void document.fonts?.ready.then(later)
    watched.current = {
      el,
      stop: () => {
        cancelAnimationFrame(frame)
        ro?.disconnect()
        mo.disconnect()
      },
    }
  })

  useEffect(() => () => {
    watched.current?.stop()
    watched.current = null
  }, [])
}
