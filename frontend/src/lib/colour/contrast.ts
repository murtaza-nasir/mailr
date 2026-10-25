// Colour arithmetic the engine needs besides HCT: hex <-> ARGB, WCAG 2 contrast, APCA contrast, and
// the oklab mix that CSS color-mix(in oklab, …) does (Cards derives its surfaces that way).

export function argbFromHex(hex: string): number {
  let h = hex.trim().replace(/^#/, '')
  if (h.length === 3) h = h.replace(/./g, (c) => c + c)
  return (0xff000000 | parseInt(h.slice(0, 6), 16)) >>> 0
}

export function hexFromArgb(argb: number): string {
  return '#' + (argb & 0xffffff).toString(16).padStart(6, '0')
}

export function isHex(v: unknown): v is string {
  return typeof v === 'string' && /^#[0-9a-fA-F]{6}$/.test(v)
}

function lin(c: number): number {
  const s = c / 255
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
}

function rgb(hex: string): [number, number, number] {
  const a = argbFromHex(hex)
  return [(a >> 16) & 255, (a >> 8) & 255, a & 255]
}

/** WCAG 2 relative luminance of an sRGB hex colour. */
export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex)
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

/** WCAG 2 contrast ratio, 1 to 21. */
export function contrast(a: string, b: string): number {
  const la = luminance(a)
  const lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/** WCAG AA: 4.5:1 for text, 3:1 for large text and parts of controls (focus rings, bars). */
export const AA_TEXT = 4.5
export const AA_UI = 3

// APCA (the Accessible Perceptual Contrast Algorithm), version APCA-W3 0.0.98G-4g, the constants of
// the apca-w3 package. WCAG 2's ratio misjudges text on saturated mid tones (white on #009ccd scores
// 3.2:1 and reads better than black at 5.6:1); APCA follows perception there. The engine uses it to
// pick the text on a filled colour. Lc runs from about -108 (light text on dark) to 106 (dark text on
// light); its size is what counts.
const APCA = {
  trc: 2.4,
  r: 0.2126729,
  g: 0.7151522,
  b: 0.072175,
  normBg: 0.56,
  normTxt: 0.57,
  revTxt: 0.62,
  revBg: 0.65,
  blkThrs: 0.022,
  blkClmp: 1.414,
  scale: 1.14,
  offset: 0.027,
  deltaYMin: 0.0005,
  loClip: 0.1,
}

/** APCA's screen luminance of an sRGB hex colour (a plain 2.4 curve, not WCAG's piecewise one). */
export function apcaY(hex: string): number {
  const [r, g, b] = rgb(hex).map((c) => (c / 255) ** APCA.trc)
  return APCA.r * r + APCA.g * g + APCA.b * b
}

/** APCA lightness contrast Lc of text `fg` on background `bg`. Order matters: positive for dark
 *  text on a light background, negative for light text on a dark one. */
export function apca(fg: string, bg: string): number {
  const clamp = (y: number) => (y > APCA.blkThrs ? y : y + (APCA.blkThrs - y) ** APCA.blkClmp)
  const t = clamp(apcaY(fg))
  const b = clamp(apcaY(bg))
  if (Math.abs(b - t) < APCA.deltaYMin) return 0
  if (b > t) {
    const s = (b ** APCA.normBg - t ** APCA.normTxt) * APCA.scale
    return s < APCA.loClip ? 0 : (s - APCA.offset) * 100
  }
  const s = (b ** APCA.revBg - t ** APCA.revTxt) * APCA.scale
  return s > -APCA.loClip ? 0 : (s + APCA.offset) * 100
}

/** APCA's level for small bold UI text (pills, badges, button labels): |Lc| 60. */
export const APCA_FILL = 60

function toOklab(hex: string): [number, number, number] {
  const [r, g, b] = rgb(hex).map(lin)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ]
}

function fromOklab([L, A, B]: [number, number, number]): string {
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3
  const lr = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
  const lg = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
  const lb = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s
  const enc = (c: number) => {
    const v = c <= 0.0031308 ? 12.92 * c : 1.055 * Math.max(c, 0) ** (1 / 2.4) - 0.055
    return Math.round(Math.min(1, Math.max(0, v)) * 255)
  }
  return '#' + [lr, lg, lb].map((c) => enc(c).toString(16).padStart(2, '0')).join('')
}

/** color-mix(in oklab, a pct%, b). */
export function mixOklab(a: string, pct: number, b: string): string {
  const x = toOklab(a)
  const y = toOklab(b)
  const p = pct / 100
  return fromOklab([x[0] * p + y[0] * (1 - p), x[1] * p + y[1] * (1 - p), x[2] * p + y[2] * (1 - p)])
}
