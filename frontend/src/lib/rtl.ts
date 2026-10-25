// Right to left. <html dir> follows the language; these helpers
// let code that works in screen coordinates (placement, scrolling, keys, swipes) think in start and
// end. Start is the left in a left-to-right page and the right in a right-to-left one.

/** Whether the page reads right to left. */
export function isRtl(): boolean {
  return typeof document !== 'undefined' && document.documentElement.dir === 'rtl'
}

/** 1 in a left-to-right page, -1 in a right-to-left one: multiply a sideways screen move by it to
 *  get a move toward the end. */
export function dirSign(rtl = isRtl()): 1 | -1 {
  return rtl ? -1 : 1
}

/** How far a box is scrolled from its start (0 at the start, growing toward the end). In a
 *  right-to-left box, browsers count scrollLeft from 0 down to negative numbers. */
export function scrollFromStart(el: HTMLElement, rtl = isRtl()): number {
  return rtl ? -el.scrollLeft : el.scrollLeft
}

/** The scrollLeft that puts a box `pos` px from its start. */
export function scrollLeftFor(pos: number, rtl = isRtl()): number {
  return rtl ? -pos : pos
}

/** A horizontal arrow key as a step through a row: -1 to the previous item, 1 to the next,
 *  0 for any other key. The previous item is on the right in a right-to-left page. */
export function arrowStep(key: string, rtl = isRtl()): -1 | 0 | 1 {
  if (key === 'ArrowRight') return rtl ? -1 : 1
  if (key === 'ArrowLeft') return rtl ? 1 : -1
  return 0
}

/** The arrow key that moves toward the end of a row (ArrowRight left to right, ArrowLeft right to left). */
export function endKey(rtl = isRtl()): 'ArrowLeft' | 'ArrowRight' {
  return rtl ? 'ArrowLeft' : 'ArrowRight'
}

/** The arrow key that moves toward the start of a row. */
export function startKey(rtl = isRtl()): 'ArrowLeft' | 'ArrowRight' {
  return rtl ? 'ArrowRight' : 'ArrowLeft'
}

/** How far a screen x lies from a box's start edge (its left, or its right in a right-to-left page). */
export function xFromStart(x: number, box: { left: number; right: number }, rtl = isRtl()): number {
  return rtl ? box.right - x : x - box.left
}
