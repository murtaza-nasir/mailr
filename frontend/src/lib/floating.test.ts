import { describe, expect, it } from 'vitest'
import { alignMenuX, placeFloating } from './floating'

const vp = { width: 1000, height: 800 }
const size = { width: 260, height: 100 }

describe('placeFloating', () => {
  it('sits above the anchor, centred, when there is room', () => {
    const p = placeFloating({ top: 400, bottom: 420, left: 490, width: 20 }, size, vp)
    expect(p).toEqual({ side: 'top', top: 294, left: 370 })
  })
  it('flips below when the top has no room', () => {
    const p = placeFloating({ top: 30, bottom: 50, left: 490, width: 20 }, size, vp)
    expect(p.side).toBe('bottom')
    expect(p.top).toBe(56)
  })
  it('stays 8 px inside the right and left edges', () => {
    expect(placeFloating({ top: 400, bottom: 420, left: 985, width: 10 }, size, vp).left).toBe(1000 - 8 - 260)
    expect(placeFloating({ top: 400, bottom: 420, left: 2, width: 10 }, size, vp).left).toBe(8)
  })
  it('prefers below when asked (tooltips)', () => {
    expect(placeFloating({ top: 400, bottom: 420, left: 490, width: 20 }, size, vp, { prefer: 'bottom' }).side).toBe('bottom')
  })
})

describe('alignMenuX', () => {
  it('keeps the preferred side when the menu fits there', () => {
    expect(alignMenuX({ left: 600, right: 640 }, 260, 1440, 'end')).toBe(380)
    expect(alignMenuX({ left: 600, right: 640 }, 260, 1440, 'start')).toBe(600)
  })
  it('opens toward the side with room (a ⋮ near the left edge of a note)', () => {
    expect(alignMenuX({ left: 100, right: 140 }, 260, 1440, 'end')).toBe(100)
    expect(alignMenuX({ left: 1300, right: 1340 }, 260, 1440, 'start')).toBe(1080)
  })
  it('stays inside the window when neither side has room', () => {
    expect(alignMenuX({ left: 100, right: 140 }, 380, 400, 'end')).toBe(8)
    expect(alignMenuX({ left: 100, right: 140 }, 380, 400, 'start')).toBe(12)
  })
  it('in a right-to-left page, start means the right edges meet and end the left edges', () => {
    expect(alignMenuX({ left: 600, right: 640 }, 260, 1440, 'start', 8, true)).toBe(380)
    expect(alignMenuX({ left: 600, right: 640 }, 260, 1440, 'end', 8, true)).toBe(600)
    // A ⋮ near the right edge with 'end' (opening rightwards) has no room: it opens leftwards.
    expect(alignMenuX({ left: 1300, right: 1340 }, 260, 1440, 'end', 8, true)).toBe(1080)
    // Near the left edge with 'start' (opening leftwards): it opens rightwards.
    expect(alignMenuX({ left: 100, right: 140 }, 260, 1440, 'start', 8, true)).toBe(100)
  })
})
