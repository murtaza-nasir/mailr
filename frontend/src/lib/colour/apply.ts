// Build the style sheet for a colour choice: a light and a dark block of custom properties on
// :root, switched on by <html data-accent-src="engine">. Both schemes are in the sheet, so a
// light/dark switch needs no rebuild. This module holds the HCT library and loads on demand;
// boot.ts puts a kept sheet on the page at load without it.

import { buildScheme, type EngineResult, type Scheme, type Tokens } from './engine'
import { accentKey, engineReady, rememberSheet, setAccentSheet } from './boot'
import { resolveChoice, type AccentChoice, type Resolved } from './choice'
import { THEME_COLOURS, type ThemeKey } from './themes'

/** The properties a theme sets for one scheme, from the engine's result. */
export function themeVars(theme: ThemeKey, r: EngineResult, scheme: Scheme): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [prop, src] of Object.entries(THEME_COLOURS[theme].map)) {
    const [s, role] = Array.isArray(src) ? src : [scheme, src]
    out[prop] = (r[s] as Tokens)[role]
  }
  return out
}

const cache = new Map<string, EngineResult>()

/** The engine's result for a theme and a resolved choice (memoised: the picker redraws often). */
export function schemeFor(theme: ThemeKey, seed: string, harmony: Resolved['harmony']): EngineResult {
  const key = `${theme}|${seed}|${harmony}`
  let r = cache.get(key)
  if (!r) {
    r = buildScheme(seed, harmony, THEME_COLOURS[theme].context)
    if (cache.size > 64) cache.clear()
    cache.set(key, r)
  }
  return r
}

export function accentCss(theme: ThemeKey, r: EngineResult): string {
  const block = (scheme: Scheme) =>
    `:root[data-accent-src='engine'][data-theme='${scheme}']{` +
    Object.entries(themeVars(theme, r, scheme))
      .map(([k, v]) => `${k}:${v};`)
      .join('') +
    '}'
  return `${block('light')}\n${block('dark')}\n:root[data-accent-src='engine'] ::selection{background:var(--accent-selection);color:var(--text)}`
}

/** The style sheet for a resolved choice: null for a native preset (the theme's own CSS). */
export function cssFor(theme: ThemeKey, res: Resolved): string | null {
  return res.kind === 'native' ? null : accentCss(theme, schemeFor(theme, res.seed, res.harmony))
}

/** Apply a choice now, building it if needed, and keep the sheet for the next load. */
export function applyAccent(theme: ThemeKey, choice: AccentChoice | null, doc: Document = document): Resolved {
  const res = resolveChoice(theme, choice)
  const css = cssFor(theme, res)
  setAccentSheet(res, css, doc)
  if (css) rememberSheet(accentKey(theme, res), css)
  return res
}

engineReady({ applyAccent })
