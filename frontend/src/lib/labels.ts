// Label colours. A label's `color` is stored in one of three forms:
//   "@<scheme>/<hue>/<shade>"  a scheme token, e.g. "@default/teal/3": the colours come from
//                              labelColours.json (a copy of spec/fixtures/label-colours.json), with a
//                              light-theme and a dark-theme pair for every shade;
//   "#bg,#fg"                  Gmail's background and text pair (the form before schemes);
//   "#bg"                      a background alone, with a readable text colour chosen for it.
// Hex colours look the same in light and dark themes; tokens follow the theme.

import type { CSSProperties } from 'react'
import data from './labelColours.json'
import { t as tr } from '../i18n'

export interface Swatch {
  bg: string
  fg: string
  /** The list swatch beside a label's name (it sits on the page background). */
  dot: string
}
export type Mode = 'light' | 'dark'
export interface LabelHue {
  id: string
  name: string
  light: Swatch[]
  dark: Swatch[]
}
export interface LabelScheme {
  id: string
  name: string
  description: string
  hues: LabelHue[]
}

export const SCHEMES: LabelScheme[] = data.schemes
export const HUES: string[] = data.hues
export const SHADES: number = data.shades
export const DEFAULT_SCHEME = 'default'

export interface Token {
  scheme: string
  hue: string
  shade: number
}

const TOKEN = /^@([a-z]+)\/([a-z]+)\/([0-9])$/

export function schemeById(id: string | null | undefined): LabelScheme {
  return SCHEMES.find((s) => s.id === id) ?? SCHEMES[0]
}

/** The parts of a scheme token; null for hex colours and for names no scheme has. */
export function parseToken(color: string | null | undefined): Token | null {
  const m = TOKEN.exec((color ?? '').trim().toLowerCase())
  if (!m) return null
  const shade = Number(m[3])
  if (!SCHEMES.some((s) => s.id === m[1]) || !HUES.includes(m[2]) || shade < 1 || shade > SHADES) return null
  return { scheme: m[1], hue: m[2], shade }
}

export function tokenOf(scheme: string, hue: string, shade: number): string {
  return `@${scheme}/${hue}/${shade}`
}

export function hueOf(t: Token): LabelHue {
  return schemeById(t.scheme).hues.find((h) => h.id === t.hue)!
}

/** "Teal, shade 3" (the hue's name in its scheme). */
export function describeToken(t: Token): string {
  return tr('lib.labels.shade', { itemName: hueOf(t).name, shade: t.shade })
}

/** A human name for any stored colour: "Teal, shade 3 (Default)", "#16a766" or "No colour". */
export function describeColor(color: string | null | undefined): string {
  const t = parseToken(color)
  if (t) return `${describeToken(t)} (${schemeById(t.scheme).name})`
  const c = decodeHex(color)
  return c ? c.bg : tr('common.noColour')
}

function decodeHex(color: string | null | undefined): { bg: string; fg: string } | null {
  if (!color || color.startsWith('@')) return null
  const [bg, fg] = color.split(/[,|]/).map((x) => x.trim())
  if (!bg || !/^#[0-9a-f]{6}$/i.test(bg)) return null
  return { bg, fg: fg && /^#[0-9a-f]{6}$/i.test(fg) ? fg : readableText(bg) }
}

/** True for the hex forms (the colours "Convert my labels" offers to replace). */
export function isHexColor(color: string | null | undefined): boolean {
  return decodeHex(color) !== null
}

/** The light and dark colours of a stored label colour; null for none (or one no client knows). */
export function resolveColor(color: string | null | undefined): { light: Swatch; dark: Swatch } | null {
  const t = parseToken(color)
  if (t) {
    const h = hueOf(t)
    return { light: h.light[t.shade - 1], dark: h.dark[t.shade - 1] }
  }
  const c = decodeHex(color)
  if (!c) return null
  const sw = { ...c, dot: c.bg }
  return { light: sw, dark: sw }
}

/** Background and text of a stored colour in one mode. */
export function decodeColor(color: string | null | undefined, mode: Mode = 'light'): { bg: string; fg: string } | null {
  const r = resolveColor(color)
  return r ? { bg: r[mode].bg, fg: r[mode].fg } : null
}

const both = (a: string, b: string) => (a === b ? a : tr('lib.labels.lightDark', { a, b }))

/** Inline style for a chip: the pair is chosen by CSS light-dark() (color-scheme on <html> comes
 * from tokens.css), so a theme change needs no re-render. */
export function chipStyle(color: string | null | undefined): CSSProperties | undefined {
  const r = resolveColor(color)
  return r ? { background: both(r.light.bg, r.dark.bg), color: both(r.light.fg, r.dark.fg) } : undefined
}

/** Inline style for the list swatch beside a label's name. */
export function dotStyle(color: string | null | undefined): CSSProperties | undefined {
  const r = resolveColor(color)
  return r ? { background: both(r.light.dot, r.dark.dot) } : undefined
}

/** The same hue and shade in another scheme (hex colours stay as they are). */
export function reschemeColor(color: string | null | undefined, scheme: string): string | null | undefined {
  const t = parseToken(color)
  return t ? tokenOf(scheme, t.hue, t.shade) : color
}

// ---------------------------------------------------------------- colour maths

function channel(v: number): number {
  const c = v / 255
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function rgb(hex: string): [number, number, number] {
  const n = parseInt(hex.replace('#', ''), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/** WCAG relative luminance of "#rrggbb". */
export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map(channel)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG contrast ratio of two "#rrggbb" colours (1 to 21). */
export function contrast(a: string, b: string): number {
  const la = luminance(a)
  const lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/** Black or white text, whichever contrasts more with the background. */
export function readableText(hex: string): string {
  if (!/^#?[0-9a-f]{6}$/i.test(hex)) return '#000000'
  const h = hex.startsWith('#') ? hex : `#${hex}`
  return contrast(h, '#000000') >= contrast(h, '#ffffff') ? '#000000' : '#ffffff'
}

/** OKLab coordinates of "#rrggbb" (for nearest-colour matching). */
export function oklab(hex: string): [number, number, number] {
  const [r, g, b] = rgb(hex).map(channel)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808762589 * s,
  ]
}

/** The scheme token nearest to a hex colour (its light-theme background): greys map to grey,
 * any colour with visible chroma to one of the nine hues. Used by "Convert my labels". */
export function nearestToken(hex: string, schemeId: string): string {
  const scheme = schemeById(schemeId)
  const [L, a, b] = oklab(hex)
  const greyish = Math.hypot(a, b) < 0.035
  let best = { d: Infinity, token: tokenOf(scheme.id, 'grey', 1) }
  for (const h of scheme.hues) {
    if ((h.id === 'grey') !== greyish) continue
    h.light.forEach((sw, i) => {
      const [L2, a2, b2] = oklab(sw.bg)
      // Hue weighs more than lightness: a saturated red stays red at some shade.
      const d = Math.hypot(L - L2, (a - a2) * 1.6, (b - b2) * 1.6)
      if (d < best.d) best = { d, token: tokenOf(scheme.id, h.id, i + 1) }
    })
  }
  return best.token
}

/** Hex colour → nearest token, for every label that has a hex colour. */
export function conversionPlan<L extends { id: number; color: string | null }>(labels: L[], schemeId: string): { label: L; to: string }[] {
  return labels.flatMap((l) => {
    const c = decodeHex(l.color)
    return c ? [{ label: l, to: nearestToken(c.bg, schemeId) }] : []
  })
}
