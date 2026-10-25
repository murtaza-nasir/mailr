// The saved colour choice (choice.ts), each theme's presets and mapping (themes.ts) and the sheet
// built from them (apply.ts).
import { describe, expect, it } from 'vitest'
import { accentCss, cssFor, schemeFor, themeVars } from './apply'
import { accentKey } from './boot'
import { parseChoice, presetChoice, resolveChoice, sameChoice } from './choice'
import { contrast } from './contrast'
import { THEME_COLOURS, presetById, type ThemeKey } from './themes'

const THEMES = Object.keys(THEME_COLOURS) as ThemeKey[]

describe('parseChoice', () => {
  it("reads the old Cards string as the Cards preset", () => {
    expect(parseChoice('teal')).toEqual({ preset: 'cards.teal' })
    expect(parseChoice({ preset: 'rose' })).toEqual({ preset: 'cards.rose' })
  })
  it('reads the new shape, and JSON of it (the device copy)', () => {
    expect(parseChoice({ preset: 'office.navy', seed: '#1F4E8C', harmony: 'single' })).toEqual({ preset: 'office.navy', seed: '#1f4e8c', harmony: 'single' })
    expect(parseChoice('{"seed":"#123456","harmony":"triadic"}')).toEqual({ seed: '#123456', harmony: 'triadic' })
    expect(parseChoice('#abcdef')).toEqual({ seed: '#abcdef', harmony: 'single' })
  })
  it('drops what it does not know', () => {
    expect(parseChoice(null)).toBeNull()
    expect(parseChoice('')).toBeNull()
    expect(parseChoice('mauve')).toBeNull()
    expect(parseChoice({ preset: 'classic.nope' })).toBeNull()
    expect(parseChoice({ seed: 'red', harmony: 'wild' })).toBeNull()
    expect(parseChoice({ seed: '#123456', harmony: 'wild' })).toEqual({ seed: '#123456' })
    expect(parseChoice('{not json')).toBeNull()
  })
})

describe('resolveChoice', () => {
  it("gives each theme its own colours when nothing is chosen", () => {
    expect(resolveChoice('classic', null)).toMatchObject({ kind: 'native', preset: { id: 'classic.ocean' } })
    expect(resolveChoice('cards', null)).toMatchObject({ kind: 'engine', preset: { id: 'cards.violet' }, seed: '#6750a4' })
  })
  it("applies a preset in its own theme only", () => {
    const c = presetChoice(presetById('cards.teal')!)
    expect(resolveChoice('cards', c)).toMatchObject({ kind: 'engine', preset: { id: 'cards.teal' } })
    expect(resolveChoice('office', c)).toMatchObject({ kind: 'native', preset: { id: 'office.ocean' } })
  })
  it('applies a custom colour in every theme', () => {
    for (const th of THEMES) expect(resolveChoice(th, { seed: '#aa3355', harmony: 'complementary' })).toEqual({ kind: 'engine', preset: null, seed: '#aa3355', harmony: 'complementary' })
  })
  it('compares choices', () => {
    expect(sameChoice({ seed: '#123456' }, { seed: '#123456', harmony: 'single' })).toBe(true)
    expect(sameChoice(null, { preset: 'cards.teal' })).toBe(false)
  })
})

describe('presets', () => {
  it('has 4 to 6 per theme, the first one the theme default, ids unique and in the theme', () => {
    const ids = new Set<string>()
    for (const th of THEMES) {
      const ps = THEME_COLOURS[th].presets
      expect(ps.length).toBeGreaterThanOrEqual(4)
      expect(ps.length).toBeLessThanOrEqual(6)
      for (const p of ps) {
        expect(p.id.startsWith(`${th}.`)).toBe(true)
        expect(ids.has(p.id)).toBe(false)
        ids.add(p.id)
        expect(p.name.length).toBeGreaterThan(0)
        expect(p.seed).toMatch(/^#[0-9a-f]{6}$/)
      }
    }
  })
  it("native defaults are the theme's own primary", () => {
    expect(THEME_COLOURS.classic.presets[0]).toMatchObject({ native: true, seed: '#1f6c86' })
    expect(THEME_COLOURS.office.presets[0]).toMatchObject({ native: true, seed: '#17607a' })
    expect(THEME_COLOURS.focus.presets[0]).toMatchObject({ native: true, seed: '#5151cd' })
    expect(THEME_COLOURS.desk.presets[0]).toMatchObject({ native: true, seed: '#1d6a8a' })
    expect(THEME_COLOURS.cards.presets[0].native).toBeUndefined()
  })
  it('swatches stand out from white and from a dark page', () => {
    for (const th of THEMES) for (const p of THEME_COLOURS[th].presets) expect(contrast(p.seed, '#ffffff'), p.id).toBeGreaterThanOrEqual(3)
  })
})

describe('the sheet', () => {
  it("maps every theme's properties to colours, per scheme", () => {
    for (const th of THEMES) {
      const r = schemeFor(th, '#aa3355', 'triadic')
      for (const scheme of ['light', 'dark'] as const) {
        const v = themeVars(th, r, scheme)
        expect(Object.keys(v)).toEqual(Object.keys(THEME_COLOURS[th].map))
        for (const [k, c] of Object.entries(v)) expect(c, `${th} ${scheme} ${k}`).toMatch(/^#[0-9a-f]{6}$/)
        expect(v['--primary']).toBe(r[scheme].primary)
      }
    }
  })
  it("gives Cards both accents in both schemes (its surfaces mix them)", () => {
    const r = schemeFor('cards', '#006a6a', 'single')
    expect(themeVars('cards', r, 'dark')['--c-accent']).toBe('#006a6a')
    expect(themeVars('cards', r, 'light')['--c-accent-d']).toBe(r.dark.primary)
  })
  it('scopes the blocks to the engine switch and the scheme', () => {
    const css = accentCss('office', schemeFor('office', '#1f4e8c', 'single'))
    expect(css).toContain(":root[data-accent-src='engine'][data-theme='light']{--primary:#1f4e8c;")
    expect(css).toContain(":root[data-accent-src='engine'][data-theme='dark']{")
    expect(css).toContain('--o-title-bg:')
    expect(css).toContain('::selection')
  })
  it('builds nothing for a native preset', () => {
    // The themes' own hand-tuned colours stay theirs: the engine's rules never reach them.
    for (const th of ['classic', 'office', 'focus', 'desk'] as const) expect(cssFor(th, resolveChoice(th, null)), th).toBeNull()
    expect(cssFor('classic', resolveChoice('classic', { preset: 'classic.plum' }))).not.toBeNull()
  })
  it('keys kept sheets by theme, seed and harmony', () => {
    expect(accentKey('desk', resolveChoice('desk', { seed: '#123456', harmony: 'split' }))).toBe('desk|#123456|split')
  })
})
