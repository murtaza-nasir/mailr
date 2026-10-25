// The colour engine: one seed colour, a harmony mode, and the theme's
// own surfaces in; a light and a dark set of accent colours out. Tones come from Google's HCT
// colour space (@material/material-color-utilities), the one Material 3 and Android use, so the
// same seed gives the same tones on the web and on the phone (android/app …/ui/theme/SeedScheme.kt
// runs the shared cases in cases.json).
//
// Two rules for text (PAIRS). Text on a filled colour (a pill, a badge, a button label, a title bar)
// is white or the fill's dark partner, whichever APCA rates higher on it; the fill's tone then moves
// away from that text until it reaches |Lc| 60 and, as a floor, WCAG 3:1. Everything else (text on
// containers and surfaces, links) keeps WCAG AA, 4.5:1 (3:1 for the focus ring): the accent side
// moves away from its background, one step at a time, until it passes. Pure functions; nothing here
// touches the page.

import { Hct } from '@material/material-color-utilities'
import { AA_TEXT, AA_UI, APCA_FILL, apca, argbFromHex, contrast, hexFromArgb } from './contrast'

import type { Harmony } from './harmony'

export { HARMONIES, isHarmony, type Harmony } from './harmony'

export const ROLES = [
  'primary', 'onPrimary', 'primaryContainer', 'onPrimaryContainer',
  'secondary', 'onSecondary', 'secondaryContainer', 'onSecondaryContainer',
  'tertiary', 'onTertiary', 'tertiaryContainer', 'onTertiaryContainer',
  // Where the app shows them: the active folder, focus ring, links, chips and badges, the selected row.
  'navActive', 'navActiveFg', 'focusRing', 'link', 'chip', 'onChip', 'badge', 'onBadge', 'selection',
  // Theme-specific spots: a soft compose button (Classic), a solid fill that stays dark in both schemes
  // (Workspace's and Desk's compose, Desk's selected row), a title bar and the field on it (Workspace),
  // the action text on a toast.
  'fab', 'onFab', 'solid', 'onSolid', 'bar', 'onBar', 'barField', 'inversePrimary',
] as const
export type Role = (typeof ROLES)[number]
export type Tokens = Record<Role, string>

/** What the theme puts under the accent colours, per scheme. The engine never changes these. */
export interface SchemeContext {
  /** Backgrounds that accent text (links, primary text) and the focus ring sit on. A function for a
   *  theme whose surfaces are tinted by the accent itself (Cards); it gets the scheme's primary. */
  surfaces: string[] | ((primary: string) => string[])
  /** The theme's body text: it sits on the selection colour and on the title-bar field. */
  text: string
  /** The toast background: the inverse primary is the toast action's text on it. */
  toast: string
}
export interface EngineContext {
  light: SchemeContext
  dark: SchemeContext
}

export type Scheme = 'light' | 'dark'

/** A point in HCT: hue (degrees), chroma (colourfulness, 0 = grey). */
export interface HueChroma {
  hue: number
  chroma: number
}

export interface EngineResult {
  light: Tokens
  dark: Tokens
  seed: { hue: number; chroma: number; tone: number }
  primary: HueChroma
  secondary: HueChroma
  tertiary: HueChroma
}

/** Harmony offsets in degrees for the secondary and tertiary hues. Single is Material's own: the
 *  same hue, quieter, and a tertiary 60 degrees on. Complementary keeps that secondary. */
export const HARMONY_OFFSETS: Record<Harmony, [number, number]> = {
  single: [0, 60],
  complementary: [0, 180],
  analogous: [30, -30],
  triadic: [120, -120],
  split: [150, 210],
}

export function wrapHue(h: number): number {
  const x = h % 360
  return x < 0 ? x + 360 : x
}

/** The secondary and tertiary seeds a harmony gives for a primary hue and chroma. */
export function harmonySeeds(p: HueChroma, harmony: Harmony): { secondary: HueChroma; tertiary: HueChroma } {
  const [a, b] = HARMONY_OFFSETS[harmony]
  // A placed colour keeps the seed's colourfulness, within limits: a grey seed still gets visible
  // partners, and a vivid one does not shout.
  const placed = Math.max(24, Math.min(p.chroma, 64))
  if (harmony === 'single') return { secondary: { hue: p.hue, chroma: 16 }, tertiary: { hue: wrapHue(p.hue + 60), chroma: 24 } }
  if (harmony === 'complementary') return { secondary: { hue: p.hue, chroma: 16 }, tertiary: { hue: wrapHue(p.hue + 180), chroma: placed } }
  return { secondary: { hue: wrapHue(p.hue + a), chroma: placed }, tertiary: { hue: wrapHue(p.hue + b), chroma: placed } }
}

export function tone(p: HueChroma, t: number): string {
  return hexFromArgb(Hct.from(p.hue, p.chroma, Math.max(0, Math.min(100, t))).toInt())
}

export function hctOf(hex: string): { hue: number; chroma: number; tone: number } {
  const h = Hct.fromInt(argbFromHex(hex))
  return { hue: h.hue, chroma: h.chroma, tone: h.tone }
}

/** The light-scheme primary tone the seed asks for. The seed's own tone, kept where text on white
 *  can still read it. */
export function lightPrimaryTone(seedTone: number): number {
  return Math.round(Math.max(20, Math.min(60, seedTone)))
}
/** Its dark-scheme partner: the mirror around 60 (tone 40 gives 80, as in Material). */
export function darkPrimaryTone(lightTone: number): number {
  return Math.max(60, Math.min(92, 120 - lightTone))
}

/** Move tone `t` of `p` by `dir` until `ok` holds or the end of the scale. */
function push(p: HueChroma, t: number, dir: 1 | -1, ok: (hex: string) => boolean): number {
  let x = t
  while (!ok(tone(p, x)) && x + dir >= 0 && x + dir <= 100) x += dir
  return x
}

/** A fill and the text on it: the text moves away first; when it reaches the end of the scale the
 *  fill moves the other way. `dir` is the direction the text moves (-1: darker). */
function pair(fill: HueChroma, fillT: number, fg: HueChroma, fgT: number, dir: 1 | -1, min = AA_TEXT): [string, string] {
  const ok = (f: number, g: number) => contrast(tone(fill, f), tone(fg, g)) >= min
  let g = fgT
  while (!ok(fillT, g) && g + dir >= 0 && g + dir <= 100) g += dir
  let f = fillT
  while (!ok(f, g) && f - dir >= 0 && f - dir <= 100) f -= dir
  return [tone(fill, f), tone(fg, g)]
}

const WHITE = '#ffffff'

/** Text on a fill reads when APCA gives it |Lc| 60 (small bold UI text) and WCAG gives at least 3:1. */
export function fillReads(text: string, fill: string): boolean {
  return Math.abs(apca(text, fill)) >= APCA_FILL && contrast(text, fill) >= AA_UI
}

/** A filled colour and the text on it. The text is white or `dark` (the fill's tone 10 or 20 partner),
 *  whichever has the larger |Lc| on the fill at tone `t`; then the fill moves away from the text (darker
 *  under white, lighter under dark text) until `fillReads`. `keep` is what the fill must still satisfy
 *  for its other uses (the primary is also link text on the surfaces): when the first text cannot
 *  reach the level without breaking it, the other text takes the fill. `start` is the colour at tone
 *  `t` when it is not exactly a tone of `p` (a preset seed). */
function fill(p: HueChroma, t: number, dark: string, keep: (hex: string) => boolean = () => true, start = tone(p, t)): [string, string] {
  const at = (x: number) => (x === t ? start : tone(p, x))
  const texts = Math.abs(apca(WHITE, start)) >= Math.abs(apca(dark, start)) ? [WHITE, dark] : [dark, WHITE]
  for (const text of texts) {
    const dir = text === WHITE ? -1 : 1
    for (let x = t; x >= 0 && x <= 100; x += dir) {
      if (!keep(at(x))) break
      if (fillReads(text, at(x))) return [at(x), text]
    }
  }
  // Nothing keeps both (not seen in the sweep): the better text reads, the other use gives way.
  const text = texts[0]
  const dir = text === WHITE ? -1 : 1
  let x = t
  while (!fillReads(text, at(x)) && x + dir >= 0 && x + dir <= 100) x += dir
  return [at(x), text]
}

/** The selected row stays calm: at most this colourful. */
const SELECTION_CHROMA = 24
/** Containers (tone 90 / 30) stay soft: a vivid seed would make them loud. */
const CONTAINER_CHROMA = 32
const soft = (p: HueChroma): HueChroma => ({ hue: p.hue, chroma: Math.min(p.chroma, CONTAINER_CHROMA) })

function surfacesOf(ctx: SchemeContext, primary: string): string[] {
  return typeof ctx.surfaces === 'function' ? ctx.surfaces(primary) : ctx.surfaces
}

/** Primary text on every surface; a tinted theme's surfaces move with the candidate. */
const onSurfaces = (hex: string, ctx: SchemeContext) => surfacesOf(ctx, hex).every((b) => contrast(hex, b) >= AA_TEXT)

function buildLight(P: HueChroma, S: HueChroma, T: HueChroma, t0: number, seedHex: string, ctx: SchemeContext): Tokens {
  const okPrimary = (h: string) => contrast(h, '#ffffff') >= AA_TEXT && onSurfaces(h, ctx)
  // The seed itself when it already reads well at its own tone (a preset keeps its exact colour).
  const seedKept = Math.round(hctOf(seedHex).tone) === t0 && okPrimary(seedHex)
  const pt = seedKept ? t0 : push(P, t0, -1, okPrimary)
  const [primary, onPrimary] = fill(P, pt, tone(P, 10), okPrimary, seedKept ? seedHex : tone(P, pt))
  const [primaryContainer, onPrimaryContainer] = pair(soft(P), 90, P, 10, -1)
  const [secondary, onSecondary] = fill(S, 40, tone(S, 10))
  const [secondaryContainer, onSecondaryContainer] = pair(soft(S), 90, S, 10, -1)
  const [tertiary, onTertiary] = fill(T, 40, tone(T, 10))
  const [tertiaryContainer, onTertiaryContainer] = pair(soft(T), 90, T, 10, -1)
  const sel = { hue: P.hue, chroma: Math.min(P.chroma, SELECTION_CHROMA) }
  const selection = tone(sel, push(sel, 88, 1, (h) => contrast(h, ctx.text) >= AA_TEXT))
  const [fab, onFab] = fill(soft(P), 90, tone(P, 10))
  const barField = '#ffffff'
  const inversePrimary = tone(P, push(P, 80, 1, (h) => contrast(h, ctx.toast) >= AA_TEXT))
  return {
    primary, onPrimary, primaryContainer, onPrimaryContainer,
    secondary, onSecondary, secondaryContainer, onSecondaryContainer,
    tertiary, onTertiary, tertiaryContainer, onTertiaryContainer,
    navActive: secondaryContainer, navActiveFg: onSecondaryContainer, focusRing: primary, link: primary,
    chip: secondaryContainer, onChip: onSecondaryContainer, badge: tertiaryContainer, onBadge: onTertiaryContainer,
    selection, fab, onFab, solid: primary, onSolid: onPrimary, bar: primary, onBar: onPrimary, barField, inversePrimary,
  }
}

function buildDark(P: HueChroma, S: HueChroma, T: HueChroma, t0: number, light: Tokens, ctx: SchemeContext): Tokens {
  const d0 = darkPrimaryTone(t0)
  const okPrimary = (h: string) => onSurfaces(h, ctx)
  const dt = push(P, d0, 1, okPrimary)
  // A mid-tone primary (a seed of tone 55 or more mirrors to 60–65) usually takes white text.
  const [primary, onPrimary] = fill(P, dt, tone(P, 20), okPrimary)
  const [primaryContainer, onPrimaryContainer] = pair(soft(P), 30, P, 90, 1)
  const [secondary, onSecondary] = fill(S, 80, tone(S, 20))
  const [secondaryContainer, onSecondaryContainer] = pair(soft(S), 30, S, 90, 1)
  const [tertiary, onTertiary] = fill(T, 80, tone(T, 20))
  const [tertiaryContainer, onTertiaryContainer] = pair(soft(T), 30, T, 90, 1)
  const sel = { hue: P.hue, chroma: Math.min(P.chroma, SELECTION_CHROMA) }
  const selection = tone(sel, push(sel, 28, -1, (h) => contrast(h, ctx.text) >= AA_TEXT))
  const [fab, onFab] = fill(soft(P), 90, tone(P, 10))
  // The solid fill keeps the light scheme's primary and its text, as Workspace and Desk do today.
  const [solid, onSolid] = [light.primary, light.onPrimary]
  const [bar, onBar] = fill(P, 20, tone(P, 10))
  const barField = tone(P, push(P, 28, -1, (h) => contrast(h, ctx.text) >= AA_TEXT))
  const inversePrimary = tone(P, push(P, 40, -1, (h) => contrast(h, ctx.toast) >= AA_TEXT))
  return {
    primary, onPrimary, primaryContainer, onPrimaryContainer,
    secondary, onSecondary, secondaryContainer, onSecondaryContainer,
    tertiary, onTertiary, tertiaryContainer, onTertiaryContainer,
    navActive: secondaryContainer, navActiveFg: onSecondaryContainer, focusRing: primary, link: primary,
    chip: secondaryContainer, onChip: onSecondaryContainer, badge: tertiaryContainer, onBadge: onTertiaryContainer,
    selection, fab, onFab, solid, onSolid, bar, onBar, barField, inversePrimary,
  }
}

/** Neutral surfaces for a context-free build (the picker swatches, Android's cases): Material's. */
export const NEUTRAL_CONTEXT: EngineContext = {
  light: { surfaces: ['#ffffff', '#f8f9fa'], text: '#1f1f1f', toast: '#303030' },
  dark: { surfaces: ['#1b1b1b', '#282828'], text: '#e3e3e3', toast: '#e3e3e3' },
}

/** Build the light and dark accent tokens for a seed colour. */
export function buildScheme(seedHex: string, harmony: Harmony, ctx: EngineContext = NEUTRAL_CONTEXT): EngineResult {
  const seed = hctOf(seedHex)
  // A seed as colourful as its tone allows (a deep teal) asks for that colourfulness at every
  // tone: its palette gets at least chroma 48, so its dark-scheme tones are not washed out.
  const limited = seed.chroma >= 16 && Hct.from(seed.hue, seed.chroma + 10, seed.tone).chroma - seed.chroma < 1
  const P = { hue: seed.hue, chroma: limited ? Math.max(seed.chroma, 48) : seed.chroma }
  const { secondary, tertiary } = harmonySeeds(P, harmony)
  const t0 = lightPrimaryTone(seed.tone)
  const light = buildLight(P, secondary, tertiary, t0, seedHex.toLowerCase(), ctx.light)
  const dark = buildDark(P, secondary, tertiary, t0, light, ctx.dark)
  return { light, dark, seed, primary: P, secondary, tertiary }
}

/** Text on a fill: APCA picks it and |Lc| 60 holds (plus WCAG 3:1). */
export const FILL = 'fill'
/** The pairs the engine guarantees, as [text role, background role, rule]: FILL, or a WCAG minimum.
 *  The chip and badge are containers (tone 90 / 30): they keep the body-text rule. */
export const PAIRS: [Role, Role, number | typeof FILL][] = [
  ['onPrimary', 'primary', FILL],
  ['onPrimaryContainer', 'primaryContainer', AA_TEXT],
  ['onSecondary', 'secondary', FILL],
  ['onSecondaryContainer', 'secondaryContainer', AA_TEXT],
  ['onTertiary', 'tertiary', FILL],
  ['onTertiaryContainer', 'tertiaryContainer', AA_TEXT],
  ['navActiveFg', 'navActive', AA_TEXT],
  ['onChip', 'chip', AA_TEXT],
  ['onBadge', 'badge', AA_TEXT],
  ['onFab', 'fab', FILL],
  ['onSolid', 'solid', FILL],
  ['onBar', 'bar', FILL],
]

export interface Failure {
  scheme: Scheme
  fg: string
  bg: string
  /** The WCAG ratio, or for a fill the APCA Lc. */
  ratio: number
  min: number | typeof FILL
}

/** Every AA failure of a result against its context (empty when all pass). */
export function verify(r: EngineResult, ctx: EngineContext = NEUTRAL_CONTEXT): Failure[] {
  const out: Failure[] = []
  for (const scheme of ['light', 'dark'] as const) {
    const k = r[scheme]
    const c = ctx[scheme]
    const check = (fg: string, bg: string, min: number, fgName: string, bgName: string) => {
      const ratio = contrast(fg, bg)
      if (ratio < min) out.push({ scheme, fg: fgName, bg: bgName, ratio, min })
    }
    for (const [fg, bg, rule] of PAIRS) {
      if (rule !== FILL) {
        check(k[fg], k[bg], rule, fg, bg)
        continue
      }
      // It reads, and dark text is never kept where white would read better (a mid-blue pill, for one).
      const lc = apca(k[fg], k[bg])
      const whiteBetter = k[fg] !== WHITE && Math.abs(apca(WHITE, k[bg])) > Math.abs(lc)
      if (!fillReads(k[fg], k[bg]) || whiteBetter) out.push({ scheme, fg, bg, ratio: lc, min: FILL })
    }
    surfacesOf(c, k.primary).forEach((s, i) => {
      check(k.primary, s, AA_TEXT, 'primary', `surface${i}`)
      check(k.link, s, AA_TEXT, 'link', `surface${i}`)
      check(k.focusRing, s, AA_UI, 'focusRing', `surface${i}`)
    })
    check(c.text, k.selection, AA_TEXT, 'text', 'selection')
    check(c.text, k.primaryContainer, AA_TEXT, 'text', 'primaryContainer')
    check(c.text, k.barField, AA_TEXT, 'text', 'barField')
    check(k.inversePrimary, c.toast, AA_TEXT, 'inversePrimary', 'toast')
  }
  return out
}
