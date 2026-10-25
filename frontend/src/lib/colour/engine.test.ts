/// <reference types="node" />
// The colour engine (engine.ts): tones, harmony angles, and the contrast guarantee (APCA for text on
// fills, WCAG AA for the rest) over a sweep of hues, chromas, tones, harmonies and every theme's surfaces. cases.json is shared with Android
// (SeedSchemeTest.kt, a byte-for-byte copy in its test resources): UPDATE_COLOUR_CASES=1 rewrites it.
import { readFileSync, writeFileSync } from 'node:fs'
import { Hct } from '@material/material-color-utilities'
import { describe, expect, it } from 'vitest'
import { apca, contrast, hexFromArgb, mixOklab } from './contrast'
import { FILL, HARMONIES, PAIRS, ROLES, buildScheme, harmonySeeds, hctOf, verify, wrapHue, NEUTRAL_CONTEXT, type Harmony } from './engine'
import { THEME_COLOURS, type ThemeKey } from './themes'

const THEMES = Object.keys(THEME_COLOURS) as ThemeKey[]

describe('contrast', () => {
  it('matches the WCAG examples', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 5)
    expect(contrast('#777777', '#ffffff')).toBeCloseTo(4.48, 2)
    expect(contrast('#ffffff', '#ffffff')).toBe(1)
  })
  it('matches the published APCA-W3 0.0.98G-4g values', () => {
    // The apca-w3 package's examples: text first, background second; the sign gives the polarity.
    expect(apca('#888888', '#ffffff')).toBeCloseTo(63.056469930209424, 9)
    expect(apca('#ffffff', '#888888')).toBeCloseTo(-68.54146436644962, 9)
    expect(apca('#000000', '#aaaaaa')).toBeCloseTo(58.146262578561334, 9)
    expect(apca('#aaaaaa', '#000000')).toBeCloseTo(-56.24113336839742, 9)
    expect(apca('#112233', '#ddeeff')).toBeCloseTo(91.66830811481631, 9)
    expect(apca('#ddeeff', '#112233')).toBeCloseTo(-93.06770049484275, 9)
    // Same colour, and the low clip: 0.
    expect(apca('#777777', '#777777')).toBe(0)
    expect(apca('#7a7a7a', '#777777')).toBe(0)
  })
  it('ranks a mid blue as APCA does and WCAG 2 does not', () => {
    // White on #009ccd: WCAG 3.16, Lc -63.5. Black: WCAG 6.6, Lc 46.
    expect(contrast('#ffffff', '#009ccd')).toBeLessThan(contrast('#000000', '#009ccd'))
    expect(Math.abs(apca('#ffffff', '#009ccd'))).toBeGreaterThan(Math.abs(apca('#000000', '#009ccd')))
    expect(apca('#ffffff', '#009ccd')).toBeCloseTo(-63.49, 1)
  })
  it('mixes in oklab like CSS color-mix', () => {
    expect(mixOklab('#ff0000', 100, '#0000ff')).toBe('#ff0000')
    expect(mixOklab('#ff0000', 0, '#0000ff')).toBe('#0000ff')
    // color-mix(in oklab, black 50%, white) is rgb(99 99 99) in browsers.
    expect(mixOklab('#000000', 50, '#ffffff')).toBe('#636363')
  })
})

describe('tones', () => {
  it('keeps a preset seed as the light primary and mirrors its tone for dark', () => {
    const r = buildScheme('#6750a4', 'single')
    expect(r.light.primary).toBe('#6750a4')
    expect(Math.round(hctOf(r.dark.primary).tone)).toBe(80)
    expect(Math.round(hctOf(r.light.primaryContainer).tone)).toBe(90)
    expect(Math.round(hctOf(r.dark.primaryContainer).tone)).toBe(30)
  })
  it("reproduces Cards' five accents (light exactly, dark within one step)", () => {
    const today: Record<string, [string, string]> = {
      'cards.violet': ['#6750a4', '#d0bcff'],
      'cards.teal': ['#006a6a', '#4cdada'],
      'cards.rose': ['#984061', '#ffb0c8'],
      'cards.green': ['#3b6a1f', '#a0d57f'],
      'cards.blue': ['#00629e', '#9ccaff'],
    }
    for (const p of THEME_COLOURS.cards.presets) {
      const r = buildScheme(p.seed, p.harmony, THEME_COLOURS.cards.context)
      expect(r.light.primary, p.id).toBe(today[p.id][0])
      const [want, got] = [hctOf(today[p.id][1]), hctOf(r.dark.primary)]
      expect(Math.abs(want.tone - got.tone), p.id).toBeLessThan(1.5)
      expect(Math.abs(want.chroma - got.chroma), p.id).toBeLessThan(3)
    }
  })
  it('darkens a light seed until white text reads on it', () => {
    const r = buildScheme('#ffd400', 'single')
    expect(contrast(r.light.primary, '#ffffff')).toBeGreaterThanOrEqual(4.5)
    expect(hctOf(r.light.primary).tone).toBeLessThan(50)
  })
  it("gives a mid blue white text in dark, with the fill at the seed (#009ccd, complementary)", () => {
    const r = buildScheme('#009ccd', 'complementary', THEME_COLOURS.classic.context)
    expect(r.dark.onPrimary).toBe('#ffffff')
    expect(Math.abs(hctOf(r.dark.primary).tone - hctOf('#009ccd').tone)).toBeLessThan(1)
    expect(Math.abs(apca(r.dark.onPrimary, r.dark.primary))).toBeGreaterThanOrEqual(60)
    expect(contrast(r.dark.onPrimary, r.dark.primary)).toBeGreaterThanOrEqual(3)
    // Still a link on Classic's dark surfaces.
    for (const s of ['#282828', '#1b1b1b', '#1f1f1f']) expect(contrast(r.dark.primary, s)).toBeGreaterThanOrEqual(4.5)
    // A light dark-scheme fill keeps dark text, moved lighter where needed (not the text darker).
    expect(r.dark.onSecondary).not.toBe('#ffffff')
    expect(Math.abs(apca(r.dark.onSecondary, r.dark.secondary))).toBeGreaterThanOrEqual(60)
  })
  it('keeps a grey seed grey', () => {
    const r = buildScheme('#808080', 'single')
    expect(hctOf(r.light.primary).chroma).toBeLessThan(4)
  })
})

describe('harmony', () => {
  const p = { hue: 100, chroma: 40 }
  const at = (h: Harmony) => harmonySeeds(p, h)
  it('places the partners at the documented angles', () => {
    expect(at('single')).toEqual({ secondary: { hue: 100, chroma: 16 }, tertiary: { hue: 160, chroma: 24 } })
    expect(at('complementary').tertiary.hue).toBe(280)
    expect([at('analogous').secondary.hue, at('analogous').tertiary.hue]).toEqual([130, 70])
    expect([at('triadic').secondary.hue, at('triadic').tertiary.hue]).toEqual([220, 340])
    expect([at('split').secondary.hue, at('split').tertiary.hue]).toEqual([250, 310])
  })
  it('wraps hues', () => {
    expect(wrapHue(-30)).toBe(330)
    expect(wrapHue(400)).toBe(40)
    expect(harmonySeeds({ hue: 350, chroma: 40 }, 'analogous').secondary.hue).toBe(20)
  })
  it('gives a grey seed visible partners, and calms a loud one', () => {
    expect(harmonySeeds({ hue: 10, chroma: 2 }, 'triadic').secondary.chroma).toBe(24)
    expect(harmonySeeds({ hue: 10, chroma: 110 }, 'triadic').secondary.chroma).toBe(64)
  })
  it('shows in the built tones', () => {
    const r = buildScheme('#1f6c86', 'complementary')
    // The angle between tertiary and primary, folded into -180…180.
    const d = Math.abs(((hctOf(r.light.tertiary).hue - hctOf(r.light.primary).hue + 540) % 360) - 180)
    expect(d).toBeGreaterThan(168)
  })
})

describe('contrast guarantee', () => {
  it('puts the APCA rule on the fills and WCAG 4.5 on the rest', () => {
    const fills = PAIRS.filter(([, , rule]) => rule === FILL).map(([fg]) => fg)
    expect(fills).toEqual(['onPrimary', 'onSecondary', 'onTertiary', 'onFab', 'onSolid', 'onBar'])
    for (const [, , rule] of PAIRS) if (rule !== FILL) expect(rule).toBe(4.5)
  })
  it('passes every pair over a sweep of hues, chromas, tones, harmonies and themes', () => {
    let built = 0
    const fails: string[] = []
    for (const theme of THEMES) {
      const ctx = THEME_COLOURS[theme].context
      for (let h = 0; h < 360; h += 15) {
        for (const c of [0, 12, 30, 60, 100]) {
          for (const t of [10, 25, 40, 55, 70, 90]) {
            const seed = hexFromArgb(Hct.from(h, c, t).toInt())
            for (const hm of HARMONIES) {
              const r = buildScheme(seed, hm, ctx)
              built++
              for (const f of verify(r, ctx)) fails.push(`${theme} ${seed} ${hm}: ${f.scheme} ${f.fg}/${f.bg} ${f.min === FILL ? 'Lc' : 'ratio'} ${f.ratio.toFixed(2)}`)
            }
          }
        }
      }
    }
    expect(built).toBe(5 * 24 * 5 * 6 * 5)
    expect(fails).toEqual([])
  }, 60_000) // 18,000 builds: about 3 s alone, far more on a busy machine (the release gate timed out at 5 s)
  it('passes for every preset of every theme', () => {
    for (const theme of THEMES) {
      const ctx = THEME_COLOURS[theme].context
      for (const p of THEME_COLOURS[theme].presets) expect(verify(buildScheme(p.seed, p.harmony, ctx), ctx), p.id).toEqual([])
    }
  })
  it('fills every role with a hex colour', () => {
    const r = buildScheme('#5151cd', 'split')
    for (const s of ['light', 'dark'] as const) for (const role of ROLES) expect(r[s][role], `${s} ${role}`).toMatch(/^#[0-9a-f]{6}$/)
  })
})

describe('shared cases (Android)', () => {
  const url = new URL('./cases.json', import.meta.url)
  const seeds = ['#6750a4', '#006a6a', '#1f6c86', '#5151cd', '#ff0000', '#ffd400', '#00c853', '#808080', '#0b0b0b', '#f5f5f5']
  const fresh = {
    note: 'Generated by frontend/src/lib/colour/engine.test.ts (UPDATE_COLOUR_CASES=1); copied byte for byte to android/app/src/test/resources/colour_cases.json.',
    cases: seeds.flatMap((seed) => HARMONIES.map((harmony) => ({ seed, harmony, ...(({ light, dark }) => ({ light, dark }))(buildScheme(seed, harmony, NEUTRAL_CONTEXT)) }))),
  }
  const text = JSON.stringify(fresh, null, 0).replace(/\},\{"seed"/g, '},\n{"seed"') + '\n'
  if (process.env.UPDATE_COLOUR_CASES) writeFileSync(url, text)
  it('the engine still gives the shared cases', () => {
    expect(readFileSync(url, 'utf8')).toBe(text)
  })
  it("Android's copy is the same file", () => {
    const android = new URL('../../../../android/app/src/test/resources/colour_cases.json', import.meta.url)
    expect(readFileSync(android, 'utf8')).toBe(readFileSync(url, 'utf8'))
  })
})
