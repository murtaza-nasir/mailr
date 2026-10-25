import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'

/**
 * Folds a toolbar step by step until it fits its width, so a label is never cut to "Com…".
 *
 * Items inside the toolbar carry step numbers. At step N, an item with `data-fold` up to N gets
 * `data-folded`: its labels go and it shows icons only (the tooltip and aria-label keep the name;
 * components.css). An item with `data-fold-hide` up to N gets `data-fold-gone` and leaves the bar;
 * the component lists it in its More menu instead. The steps run from 0 (nothing folded) up,
 * before paint, and stop at the first that fits.
 * A toolbar fits when nothing in it is wider than its box, so its buttons and labels must not
 * shrink (flex: none) and it must not wrap.
 *
 * Returns the step reached, so the component can list the folded buttons in its More menu.
 */
export function useToolbarFit(ref: RefObject<HTMLElement | null>): number {
  const [level, setLevel] = useState(0)
  const fit = useCallback(() => {
    const el = ref.current
    if (!el) return
    const items = [...el.querySelectorAll<HTMLElement>('[data-fold], [data-fold-hide]')]
    const at = (v: string | undefined) => Number(v) || 0
    const steps = [...new Set(items.flatMap((i) => [at(i.dataset.fold), at(i.dataset.foldHide)]))].filter((n) => n > 0).sort((a, b) => a - b)
    const apply = (n: number) => {
      for (const i of items) {
        const f = at(i.dataset.fold)
        const h = at(i.dataset.foldHide)
        i.toggleAttribute('data-folded', f > 0 && f <= n)
        i.toggleAttribute('data-fold-gone', h > 0 && h <= n)
      }
      el.dataset.fit = String(n)
    }
    const overflows = () => el.scrollWidth > el.clientWidth + 1
    let reached = 0
    apply(0)
    for (const n of steps) {
      if (!overflows()) break
      reached = n
      apply(n)
    }
    setLevel(reached)
  }, [ref])

  // The observed toolbar: it can mount after the first render (a loading state draws none).
  const watched = useRef<{ el: HTMLElement; ro: ResizeObserver } | null>(null)
  const frame = useRef(0)

  // Every render: React may have added or replaced buttons (a command became available).
  useLayoutEffect(() => {
    const el = ref.current
    if (watched.current?.el !== el) {
      watched.current?.ro.disconnect()
      watched.current = null
      if (el && typeof ResizeObserver !== 'undefined') {
        const ro = new ResizeObserver(() => {
          cancelAnimationFrame(frame.current)
          frame.current = requestAnimationFrame(fit)
        })
        ro.observe(el)
        watched.current = { el, ro }
      }
    }
    fit()
  })

  useEffect(() => {
    // Labels measured in a fallback font are narrower or wider than the real ones.
    void document.fonts?.ready.then(() => fit())
    return () => {
      cancelAnimationFrame(frame.current)
      watched.current?.ro.disconnect()
      watched.current = null
    }
  }, [fit])

  return level
}
