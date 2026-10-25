// Submenus (components/Popover.tsx SubMenu): placement with flip and shift, the safe
// triangle, and the keys.
import { describe, expect, it } from 'vitest'
import { EDGE, OVERLAP, PAD_TOP, inTriangle, placeSubmenu, safeTriangle, submenuKey } from './submenu'

const vp = { width: 1440, height: 900 }
const box = (left: number, top: number, w: number, h: number) => ({ left, top, right: left + w, bottom: top + h })

describe('placeSubmenu', () => {
  const menu = box(100, 100, 240, 500)
  const item = box(100, 300, 240, 36)

  it('opens to the right of the menu, its first item level with the parent item', () => {
    const p = placeSubmenu(item, menu, { width: 260, height: 300 }, vp)
    expect(p.side).toBe('right')
    expect(p.left).toBe(menu.right - OVERLAP)
    expect(p.top).toBe(item.top - PAD_TOP)
  })

  it('flips to the left when there is no room on the right', () => {
    const m = box(1200, 100, 230, 500)
    const p = placeSubmenu(box(1200, 300, 230, 36), m, { width: 260, height: 300 }, vp)
    expect(p.side).toBe('left')
    expect(p.left).toBe(m.left - 260 + OVERLAP)
  })

  it('with room on neither side, takes the wider side and stays on screen', () => {
    const narrow = { width: 400, height: 800 }
    const m = box(60, 100, 240, 300)
    const p = placeSubmenu(box(60, 150, 240, 36), m, { width: 300, height: 200 }, narrow)
    expect(p.side).toBe('right')
    expect(p.left).toBeGreaterThanOrEqual(EDGE)
    expect(p.left + 300).toBeLessThanOrEqual(narrow.width - EDGE)
  })

  it('shifts up near the bottom', () => {
    const p = placeSubmenu(box(100, 800, 240, 36), menu, { width: 260, height: 300 }, vp)
    expect(p.top).toBe(vp.height - EDGE - 300)
  })

  it('caps a tall panel at the viewport, which then scrolls inside', () => {
    const p = placeSubmenu(item, menu, { width: 260, height: 2000 }, vp)
    expect(p.maxHeight).toBe(vp.height - 2 * EDGE)
    expect(p.top).toBe(EDGE)
  })
})

describe('safe triangle', () => {
  const panel = box(336, 292, 260, 300)

  it('covers a diagonal path from the item to the panel', () => {
    const tri = safeTriangle({ x: 300, y: 318 }, panel, 'right')
    expect(inTriangle({ x: 310, y: 340 }, ...tri)).toBe(true) // down and right, toward the panel
    expect(inTriangle({ x: 330, y: 500 }, ...tri)).toBe(true)
  })

  it('does not cover moving straight down the menu', () => {
    const tri = safeTriangle({ x: 300, y: 318 }, panel, 'right')
    expect(inTriangle({ x: 290, y: 360 }, ...tri)).toBe(false)
  })

  it('points left when the panel flipped', () => {
    const left = box(40, 292, 260, 300)
    const tri = safeTriangle({ x: 320, y: 318 }, left, 'left')
    expect(inTriangle({ x: 310, y: 330 }, ...tri)).toBe(true)
    expect(inTriangle({ x: 340, y: 330 }, ...tri)).toBe(false)
  })
})

describe('submenu keys', () => {
  it('→, Enter and Space open from the item', () => {
    expect(submenuKey('ArrowRight', 'trigger', 'item')).toBe('open')
    expect(submenuKey('Enter', 'trigger', 'item')).toBe('open')
    expect(submenuKey(' ', 'trigger', 'item')).toBe('open')
    expect(submenuKey('ArrowDown', 'trigger', 'item')).toBeNull()
  })

  it('← and Escape close back to the item; ← in a filter box moves the caret instead', () => {
    expect(submenuKey('ArrowLeft', 'panel', 'item')).toBe('close')
    expect(submenuKey('Escape', 'panel', 'item')).toBe('close')
    expect(submenuKey('Escape', 'panel', 'field')).toBe('close')
    expect(submenuKey('ArrowLeft', 'panel', 'field')).toBeNull()
    expect(submenuKey('ArrowRight', 'panel', 'item')).toBeNull()
  })
})

describe('right to left', () => {
  it('a submenu opens to the left first', () => {
    const menu = box(800, 100, 240, 500)
    const p = placeSubmenu(box(800, 300, 240, 36), menu, { width: 260, height: 300 }, vp, true)
    expect(p.side).toBe('left')
    expect(p.left).toBe(800 - 260 + OVERLAP)
  })
  it('with no room on the left it opens to the right', () => {
    const menu = box(20, 100, 240, 500)
    expect(placeSubmenu(box(20, 300, 240, 36), menu, { width: 260, height: 300 }, vp, true).side).toBe('right')
  })
  it('ArrowLeft opens and ArrowRight closes', () => {
    expect(submenuKey('ArrowLeft', 'trigger', 'item', true)).toBe('open')
    expect(submenuKey('ArrowRight', 'trigger', 'item', true)).toBe(null)
    expect(submenuKey('ArrowRight', 'panel', 'item', true)).toBe('close')
    expect(submenuKey('ArrowLeft', 'panel', 'item', true)).toBe(null)
  })
})
