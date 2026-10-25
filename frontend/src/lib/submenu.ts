// The pure parts of cascading submenus (components/Popover.tsx SubMenu): where the panel goes,
// the pointer's safe triangle, and what a key does. Kept apart so vitest can test them without a DOM.

export interface Box {
  left: number
  top: number
  right: number
  bottom: number
}

export interface SubmenuPlace {
  left: number
  top: number
  /** The side the panel opened on: right of the item, or flipped to its left. */
  side: 'right' | 'left'
  /** The most the panel may be tall before it scrolls inside. */
  maxHeight: number
}

/** Margin kept to the viewport's edges. */
export const EDGE = 8
/** The panel overlaps its menu by this much, so the pointer never crosses a gap. */
export const OVERLAP = 4
/** The panel's first item lines up with its parent item (the panel's top padding). */
export const PAD_TOP = 8

/**
 * Place a submenu beside the item that opens it. `menu` is the parent menu's box (the panel opens
 * past its edge, not over it); `size` the panel's natural size. It opens toward the end (the right,
 * or the left in a right-to-left page); with no room there, toward the start; with room on neither
 * side, on the side with more room, kept on screen.
 * Near the bottom it shifts up; taller than the viewport, it is capped and scrolls inside.
 */
export function placeSubmenu(item: Box, menu: Box, size: { width: number; height: number }, viewport: { width: number; height: number }, rtl = false): SubmenuPlace {
  const maxHeight = Math.max(0, viewport.height - 2 * EDGE)
  const h = Math.min(size.height, maxHeight)
  const w = size.width
  const rightLeft = menu.right - OVERLAP
  const leftLeft = menu.left - w + OVERLAP
  const roomRight = viewport.width - EDGE - rightLeft
  const roomLeft = menu.left + OVERLAP - EDGE
  const [first, second, roomFirst, roomSecond]: ['right' | 'left', 'right' | 'left', number, number] = rtl
    ? ['left', 'right', roomLeft, roomRight]
    : ['right', 'left', roomRight, roomLeft]
  let side: 'right' | 'left'
  if (w <= roomFirst) side = first
  else if (w <= roomSecond) side = second
  else side = roomSecond > roomFirst ? second : first
  let left = side === 'right' ? rightLeft : leftLeft
  left = Math.max(EDGE, Math.min(left, viewport.width - EDGE - w))
  let top = item.top - PAD_TOP
  if (top + h > viewport.height - EDGE) top = viewport.height - EDGE - h
  top = Math.max(EDGE, top)
  return { left, top, side, maxHeight }
}

export interface Point {
  x: number
  y: number
}

/** Whether p lies inside the triangle a-b-c (edges included). */
export function inTriangle(p: Point, a: Point, b: Point, c: Point): boolean {
  const d = (p1: Point, p2: Point, p3: Point) => (p1.x - p3.x) * (p2.y - p3.y) - (p2.x - p3.x) * (p1.y - p3.y)
  const d1 = d(p, a, b)
  const d2 = d(p, b, c)
  const d3 = d(p, c, a)
  const neg = d1 < 0 || d2 < 0 || d3 < 0
  const pos = d1 > 0 || d2 > 0 || d3 > 0
  return !(neg && pos)
}

/**
 * The safe triangle: from where the pointer left the item to the near edge of the open panel.
 * While the pointer moves inside it (heading for the panel), the items it crosses do not take
 * over. The apex is moved back a few pixels so the pointer's first move counts as inside.
 */
export function safeTriangle(from: Point, panel: Box, side: 'right' | 'left'): [Point, Point, Point] {
  const back = side === 'right' ? -4 : 4
  const x = side === 'right' ? panel.left : panel.right
  return [{ x: from.x + back, y: from.y }, { x, y: panel.top - 4 }, { x, y: panel.bottom + 4 }]
}

/** What a key does to a submenu. `at` is where focus is: on the item that opens one, or inside
 *  an open panel; `target` is what has focus: a menu item, or a field (a filter box, a colour grid)
 *  that keeps its own arrow keys. The arrow toward the end opens (ArrowRight, or ArrowLeft in a
 *  right-to-left page); the arrow toward the start closes. */
export type SubmenuKeyIntent = 'open' | 'close' | null

export function submenuKey(key: string, at: 'trigger' | 'panel', target: 'item' | 'field', rtl = false): SubmenuKeyIntent {
  const toEnd = rtl ? 'ArrowLeft' : 'ArrowRight'
  const toStart = rtl ? 'ArrowRight' : 'ArrowLeft'
  if (at === 'trigger') return key === toEnd || key === 'Enter' || key === ' ' ? 'open' : null
  if (key === 'Escape') return 'close'
  if (key === toStart && target === 'item') return 'close'
  return null
}

/** How long the pointer rests on an item before its submenu opens (hover intent), and how long
 *  it may travel through the safe triangle before the item under it takes over. */
export const OPEN_DELAY = 150
export const GRACE_MS = 400
