// The theme × light/dark × direction × width grids, in one place.
// E2E_MATRIX=full gives every combination (a nightly run). Anything else gives the reduced matrix:
// - every theme runs, light and left to right (the theme's own layout code is what a grid tests);
// - each theme adds one more look, fixed in EXTRA, so that across the five themes dark and right to
//   left both appear more than once and each of the four looks appears at least once;
// - a width list keeps its narrowest and widest entry;
// - a spec that tests logic the themes share runs in the one or two themes it names (`release`).
// Test titles carry the combination and are the same in both modes, so a nightly failure names it.

export const THEMES = ['classic', 'office', 'focus', 'desk', 'cards'] as const
export type Theme = (typeof THEMES)[number]

export const FULL_MATRIX = process.env.E2E_MATRIX === 'full'

/** The one look each theme adds to light, left to right, in the reduced matrix. Dark appears in
 *  three themes, right to left in four; dark and right to left together in two. */
export const EXTRA: Record<Theme, { dark: boolean; rtl: boolean }> = {
  classic: { dark: true, rtl: false },
  office: { dark: true, rtl: true },
  focus: { dark: false, rtl: true },
  desk: { dark: false, rtl: true },
  cards: { dark: true, rtl: true },
}

/** The themes a grid runs in. Full: every theme. Reduced: `release` when given (a spec of shared
 *  logic names one or two themes), else every theme. `all` replaces the five themes in both modes
 *  (a spec that leaves a theme out on purpose). */
export function themes<T extends string = Theme>(opts: { release?: readonly T[]; all?: readonly T[] } = {}): T[] {
  const all = (opts.all ?? THEMES) as readonly T[]
  if (FULL_MATRIX || !opts.release) return [...all]
  return all.filter((t) => opts.release!.includes(t))
}

export interface Look<T extends string = Theme> {
  theme: T
  dark: boolean
  rtl: boolean
  scheme: 'light' | 'dark'
  dir: 'ltr' | 'rtl'
}

/** The looks of a grid, in the order of nested loops theme → dark → rtl. `dark` and `rtl` say which
 *  axes the grid has (an axis left out stays light, or left to right). Full: the cross product.
 *  Reduced: per theme, light left to right plus EXTRA projected onto the grid's axes. */
export function looks<T extends string = Theme>(opts: { dark?: boolean; rtl?: boolean; release?: readonly T[]; all?: readonly T[] }): Look<T>[] {
  const out: Look<T>[] = []
  for (const theme of themes<T>(opts)) {
    const extra = EXTRA[theme as unknown as Theme] ?? { dark: true, rtl: true }
    for (const dark of opts.dark ? [false, true] : [false]) {
      for (const rtl of opts.rtl ? [false, true] : [false]) {
        const base = !dark && !rtl
        const isExtra = dark === (opts.dark ? extra.dark : false) && rtl === (opts.rtl ? extra.rtl : false)
        if (FULL_MATRIX || base || isExtra) out.push({ theme, dark, rtl, scheme: dark ? 'dark' : 'light', dir: rtl ? 'rtl' : 'ltr' })
      }
    }
  }
  return out
}

/** The schemes one theme runs in: both in full; in the reduced matrix light, plus dark when the
 *  theme's EXTRA look is dark. */
export function schemes(theme: string): ('light' | 'dark')[] {
  return FULL_MATRIX || EXTRA[theme as Theme]?.dark !== false ? ['light', 'dark'] : ['light']
}

/** A width list (numbers, or sizes with a width): every entry in full, the narrowest and widest
 *  otherwise, in the list's order. */
export function widths<W extends number | { width: number }>(list: readonly W[]): W[] {
  if (FULL_MATRIX || list.length <= 2) return [...list]
  const w = (x: W) => (typeof x === 'number' ? x : x.width)
  const lo = list.reduce((a, b) => (w(b) < w(a) ? b : a))
  const hi = list.reduce((a, b) => (w(b) > w(a) ? b : a))
  return list.filter((x) => x === lo || x === hi)
}

/** Any other axis: `full` nightly, `reduced` otherwise. */
export function pick<V>(full: readonly V[], reduced: readonly NoInfer<V>[]): V[] {
  return [...(FULL_MATRIX ? full : reduced)]
}
