/// <reference types="node" />
// With reduced motion asked for, scrolls jump instead of gliding and endless animations
// run once instead of restarting every frame.
import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { scrollBehavior } from './motion'

describe('reduced motion', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('scrolls jump when the user asked for less motion', () => {
    vi.stubGlobal('window', { matchMedia: (q: string) => ({ matches: q.includes('reduce') }) })
    expect(scrollBehavior()).toBe('auto')
    vi.stubGlobal('window', { matchMedia: () => ({ matches: false }) })
    expect(scrollBehavior()).toBe('smooth')
  })
  it('stops endless CSS animations and smooth scrolling', () => {
    const css = readFileSync(new URL('../styles/base.css', import.meta.url), 'utf8')
    const block = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'))
    expect(block).toMatch(/animation-iteration-count: 1 !important/)
    expect(block).toMatch(/scroll-behavior: auto !important/)
  })
  it('no smooth scroll is hard-coded in the app', () => {
    const src = import.meta.glob(['../**/*.ts', '../**/*.tsx', '!../**/*.test.ts'], { query: '?raw', import: 'default', eager: true }) as Record<string, string>
    const hard = Object.entries(src).filter(([, t]) => t.includes("behavior: 'smooth'")).map(([p]) => p)
    expect(hard).toEqual([])
  })
})
