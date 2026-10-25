// Where a floating card goes next to its anchor, in viewport coordinates (position: fixed). The card
// prefers one side of the anchor and flips to the other when that side has less room; it is centred
// on the anchor and then shifted so it stays `margin` px inside the window on every edge.

export interface FloatingPlace {
  top: number
  left: number
  side: 'top' | 'bottom'
}

export function placeFloating(
  anchor: { top: number; bottom: number; left: number; width: number },
  size: { width: number; height: number },
  viewport: { width: number; height: number },
  opts: { prefer?: 'top' | 'bottom'; gap?: number; margin?: number } = {},
): FloatingPlace {
  const { prefer = 'top', gap = 6, margin = 8 } = opts
  const above = anchor.top - gap - margin
  const below = viewport.height - anchor.bottom - gap - margin
  const fitsPreferred = (prefer === 'top' ? above : below) >= size.height
  const side: 'top' | 'bottom' = fitsPreferred ? prefer : above >= below ? 'top' : 'bottom'
  const top = side === 'top' ? anchor.top - gap - size.height : anchor.bottom + gap
  const centred = anchor.left + anchor.width / 2 - size.width / 2
  const left = Math.min(Math.max(centred, margin), Math.max(margin, viewport.width - margin - size.width))
  return { top: Math.max(margin, Math.min(top, viewport.height - margin - size.height)), left, side }
}

/** The left edge of a menu aligned to one side of its anchor ('start': the start edges meet, 'end':
 *  the end edges meet; start is the left, or the right in a right-to-left page). When the preferred
 *  side has no room for it (the menu would be pushed against the window's edge) and the other side
 *  has, it opens toward the other side; the result always stays `margin` px inside the window. A ⋮
 *  near the left edge of a note opened its menu leftwards, out of the note and against the window's
 *  edge. */
export function alignMenuX(anchor: { left: number; right: number }, width: number, viewportWidth: number,
  prefer: 'start' | 'end', margin = 8, rtl = false): number {
  const atStart = rtl ? anchor.right - width : anchor.left
  const atEnd = rtl ? anchor.left : anchor.right - width
  // A menu at anchor.left opens rightwards; one ending at anchor.right opens leftwards.
  const fitsRightwards = (x: number) => x + width <= viewportWidth - margin
  const fitsLeftwards = (x: number) => x >= margin
  const fitsStart = rtl ? fitsLeftwards(atStart) : fitsRightwards(atStart)
  const fitsEnd = rtl ? fitsRightwards(atEnd) : fitsLeftwards(atEnd)
  const left = prefer === 'end' ? (fitsEnd || !fitsStart ? atEnd : atStart) : (fitsStart || !fitsEnd ? atStart : atEnd)
  return Math.max(margin, Math.min(left, viewportWidth - width - margin))
}
