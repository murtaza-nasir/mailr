// The colour palette: the 12 named swatches (the same palette as Android), each with a light and a dark
// variant, plus the computed text colour for a block (contrast is computed, not assumed).

import { t as tr } from '../../i18n'

export interface Swatch {
  id: string
  name: string
  light: string
  dark: string
}

export const SWATCHES: Swatch[] = [
  { id: 'tomato', name: tr('common.tomato'), light: '#d50000', dark: '#e66a5f' },
  { id: 'flamingo', name: tr('common.flamingo'), light: '#e67c73', dark: '#ee9b94' },
  { id: 'tangerine', name: tr('common.tangerine'), light: '#f4511e', dark: '#f28b5b' },
  { id: 'banana', name: tr('common.banana'), light: '#f6bf26', dark: '#f5cf60' },
  { id: 'sage', name: tr('common.sage'), light: '#33b679', dark: '#5cc794' },
  { id: 'basil', name: tr('common.basil'), light: '#0b8043', dark: '#3fa36d' },
  { id: 'peacock', name: tr('common.peacock'), light: '#039be5', dark: '#4fb6eb' },
  { id: 'blueberry', name: tr('common.blueberry'), light: '#3f51b5', dark: '#6f7fd6' },
  { id: 'lavender', name: tr('common.lavender'), light: '#7986cb', dark: '#a0aadb' },
  { id: 'grape', name: tr('common.grape'), light: '#8e24aa', dark: '#b36fc7' },
  { id: 'graphite', name: tr('common.graphite'), light: '#616161', dark: '#9e9e9e' },
  { id: 'cocoa', name: tr('common.cocoa'), light: '#795548', dark: '#a1887f' },
]

const CSS_NAMES: Record<string, string> = {
  red: '#ff0000', orange: '#ffa500', yellow: '#ffff00', green: '#008000', blue: '#0000ff', purple: '#800080',
  pink: '#ffc0cb', brown: '#a52a2a', black: '#000000', white: '#ffffff', gray: '#808080', grey: '#808080',
  teal: '#008080', navy: '#000080', maroon: '#800000', olive: '#808000', lime: '#00ff00', aqua: '#00ffff',
  cyan: '#00ffff', magenta: '#ff00ff', fuchsia: '#ff00ff', silver: '#c0c0c0', gold: '#ffd700', coral: '#ff7f50',
  tomato: '#ff6347', salmon: '#fa8072', orchid: '#da70d6', indigo: '#4b0082', violet: '#ee82ee', crimson: '#dc143c',
  turquoise: '#40e0d0', khaki: '#f0e68c', plum: '#dda0dd', tan: '#d2b48c', chocolate: '#d2691e', steelblue: '#4682b4',
  royalblue: '#4169e1', seagreen: '#2e8b57', forestgreen: '#228b22', darkorange: '#ff8c00', slategray: '#708090',
}

/** "#rrggbb" for a hex (3 or 6 digits, with or without #) or a CSS colour name; null otherwise. */
export function normalizeColour(c: string | null | undefined): string | null {
  if (!c) return null
  const t = c.trim().toLowerCase()
  if (CSS_NAMES[t]) return CSS_NAMES[t]
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})([0-9a-f]{2})?$/.exec(t)
  if (!m) return null
  const h = m[1].length === 3 ? m[1].split('').map((x) => x + x).join('') : m[1]
  return `#${h}`
}

export function swatchFor(c: string | null | undefined): Swatch | null {
  const n = normalizeColour(c)
  if (!n) return null
  return SWATCHES.find((s) => s.light === n) ?? SWATCHES.find((s) => s.dark === n) ?? null
}

/** The colour to draw: a palette colour takes the theme's variant; any other colour is used as is. */
export function displayColour(c: string, dark: boolean): string {
  const s = swatchFor(c)
  if (s) return dark ? s.dark : s.light
  return normalizeColour(c) ?? c
}

/** A stable palette colour for a collection that has none (by its position or id). */
export function fallbackColour(seed: string | number): string {
  let h = 0
  const s = String(seed)
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  const order = ['peacock', 'tomato', 'sage', 'grape', 'tangerine', 'blueberry', 'banana', 'cocoa', 'flamingo', 'basil', 'lavender', 'graphite']
  return SWATCHES.find((x) => x.id === order[typeof seed === 'number' ? seed % 12 : h % 12])!.light
}

function channel(v: number): number {
  const c = v / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

/** WCAG relative luminance of "#rrggbb". */
export function luminance(hex: string): number {
  const n = normalizeColour(hex) ?? '#808080'
  const r = parseInt(n.slice(1, 3), 16)
  const g = parseInt(n.slice(3, 5), 16)
  const b = parseInt(n.slice(5, 7), 16)
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

export function contrast(a: string, b: string): number {
  const x = luminance(a)
  const y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

export const DARK_TEXT = '#1f1f1f'
export const LIGHT_TEXT = '#ffffff'

/** The text colour with the better contrast on a fill (dark text on Banana, white on Basil). */
export function textOn(fill: string): string {
  return contrast(fill, LIGHT_TEXT) >= contrast(fill, DARK_TEXT) ? LIGHT_TEXT : DARK_TEXT
}

/** A translucent tint of a colour for outlined and needs-reply blocks ("#rrggbb" + alpha). */
export function tint(hex: string, alpha: number): string {
  const n = normalizeColour(hex) ?? '#808080'
  const a = Math.round(clamp01(alpha) * 255).toString(16).padStart(2, '0')
  return `${n}${a}`
}

function clamp01(x: number) {
  return Math.min(1, Math.max(0, x))
}
