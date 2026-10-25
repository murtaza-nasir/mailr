// Every style's own colours (the CSS defaults, before any colour choice) keep the engine's text rules:
// text on a filled colour reads by APCA (|Lc| 60, WCAG 3:1
// as a floor, and no dark text where white reads better); text on containers and surfaces keeps
// WCAG AA 4.5:1; the focus ring and the focused row's bar keep 3:1. The tokens come from the CSS
// files themselves, resolved the way the browser does on <html>.
// AUDIT=1 prints the whole table.

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { AA_TEXT, AA_UI, APCA_FILL, apca, contrast, isHex, mixOklab } from '../lib/colour/contrast'

// Read from disk: Vitest hands CSS imports back empty, even with ?raw.
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), 'utf8')

type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'
type Scheme = 'light' | 'dark'
const THEMES: Theme[] = ['classic', 'office', 'focus', 'desk', 'cards']

/** Top-level rule blocks as [selector, declarations], comments removed, selectors on one line. */
function blocks(css: string): [string, Record<string, string>][] {
  const out: [string, Record<string, string>][] = []
  const src = css.replace(/\/\*[\s\S]*?\*\//g, '')
  for (const m of src.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const decls: Record<string, string> = {}
    for (const d of m[2].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) decls[d[1]] = d[2].trim()
    out.push([m[1].trim().replace(/\s+/g, ' '), decls])
  }
  return out
}

/** The custom properties on <html> for a style and scheme, in cascade order (equal specificity
 *  goes by source order; the style's blocks are more specific and come later). */
function tokens(theme: Theme, scheme: Scheme): Record<string, string> {
  const base = blocks(read('../styles/tokens.css'))
  const want = [":root, [data-theme='light']", ':root, [data-theme]']
  if (scheme === 'dark') want.push("[data-theme='dark']")
  const vars: Record<string, string> = {}
  for (const [sel, decls] of base) if (want.includes(sel)) Object.assign(vars, decls)
  if (theme !== 'classic') {
    const own = [`[data-ui-theme='${theme}']`, `[data-ui-theme='${theme}'][data-theme='${scheme}']`]
    for (const [sel, decls] of blocks(read(`./${theme}/${theme}.css`))) if (own.includes(sel)) Object.assign(vars, decls)
  }
  return vars
}

/** A token's value as a hex colour (var() and color-mix(in oklab, …) resolved), or null. */
function resolve(vars: Record<string, string>, v: string, depth = 0): string | null {
  if (depth > 20) return null
  const s = v.trim()
  const ref = s.match(/^var\((--[\w-]+)\)$/)
  if (ref) return vars[ref[1]] === undefined ? null : resolve(vars, vars[ref[1]], depth + 1)
  const mix = s.match(/^color-mix\(in oklab,\s*(.+?)\s+([\d.]+)%,\s*(.+)\)$/)
  if (mix) {
    const a = resolve(vars, mix[1], depth + 1)
    const b = resolve(vars, mix[3], depth + 1)
    return a && b ? mixOklab(a, Number(mix[2]), b) : null
  }
  if (/^#[0-9a-f]{3}$/i.test(s)) return '#' + s.slice(1).replace(/./g, (c) => c + c).toLowerCase()
  return isHex(s) ? s.toLowerCase() : null
}

const FILL = 'fill'
type Rule = number | typeof FILL
/** [text, background, rule]. Fills: the primary (buttons, the tab's "n new" pill, unread dots), the
 *  compose button, Workspace's title bar, Desk's selected row. */
const PAIRS: [string, string, Rule][] = [
  ['--on-primary', '--primary', FILL],
  ['--compose-fg', '--compose-bg', FILL],
  ['--o-title-fg', '--o-title-bg', FILL],
  ['--d-sel-fg', '--d-sel', FILL],
  ['--on-primary-container', '--primary-container', AA_TEXT],
  ['--nav-active-fg', '--nav-active', AA_TEXT],
  ['--accent-chip-fg', '--accent-chip-bg', AA_TEXT],
  ['--accent-badge-fg', '--accent-badge-bg', AA_TEXT],
  ['--chip-fg', '--chip-bg', AA_TEXT],
  ['--text', '--row-selected', AA_TEXT],
  ['--text', '--primary-container', AA_TEXT],
  ['--toast-action', '--toast-bg', AA_TEXT],
  ['--primary', '--bg', AA_TEXT],
  ['--primary', '--surface', AA_TEXT],
  ['--primary', '--surface-dim', AA_TEXT],
  ['--link', '--surface', AA_TEXT],
  ['--focus-ring', '--surface', AA_UI],
  ['--row-focused-bar', '--surface', AA_UI],
]

interface Row {
  theme: Theme
  scheme: Scheme
  fg: string
  bg: string
  fgHex: string
  bgHex: string
  rule: Rule
  wcag: number
  lc: number
  ok: boolean
  note: string
}

function audit(): Row[] {
  const rows: Row[] = []
  for (const theme of THEMES) {
    for (const scheme of ['light', 'dark'] as const) {
      const vars = tokens(theme, scheme)
      for (const [fg, bg, rule] of PAIRS) {
        if (vars[fg] === undefined || vars[bg] === undefined) continue
        const f = resolve(vars, vars[fg])
        const b = resolve(vars, vars[bg])
        if (!f || !b) continue
        const wcag = contrast(f, b)
        const lc = apca(f, b)
        let ok: boolean
        let note = ''
        if (rule === FILL) {
          const white = Math.abs(apca('#ffffff', b))
          const whiteBetter = lc > 0 && white > Math.abs(lc)
          ok = Math.abs(lc) >= APCA_FILL && wcag >= AA_UI && !whiteBetter
          if (whiteBetter) note = `white Lc ${(-white).toFixed(1)} reads better`
        } else ok = wcag >= rule
        rows.push({ theme, scheme, fg, bg, fgHex: f, bgHex: b, rule, wcag, lc, ok, note })
      }
    }
  }
  return rows
}

describe("each style's own colours", () => {
  const rows = audit()
  if (process.env.AUDIT) {
    const line = (r: Row) =>
      `| ${r.theme} | ${r.scheme} | ${r.fg} ${r.fgHex} | ${r.bg} ${r.bgHex} | ${r.rule === FILL ? 'Lc 60' : r.rule} | ${r.wcag.toFixed(2)} | ${r.lc.toFixed(1)} | ${r.ok ? 'pass' : 'FAIL'} ${r.note} |`
    console.log(['| style | scheme | text | background | rule | WCAG | APCA Lc | result |', '|---|---|---|---|---|---|---|---|', ...rows.map(line)].join('\n'))
  }

  it('every style and scheme resolves its fill pairs', () => {
    for (const theme of THEMES)
      for (const scheme of ['light', 'dark'] as const) {
        const got = rows.filter((r) => r.theme === theme && r.scheme === scheme)
        expect(got.filter((r) => r.rule === FILL).map((r) => r.fg)).toContain('--on-primary')
        expect(got.filter((r) => r.rule === FILL).map((r) => r.fg)).toContain('--compose-fg')
        expect(got.length, `${theme} ${scheme}`).toBeGreaterThanOrEqual(PAIRS.length - 2)
      }
    expect(rows.some((r) => r.theme === 'office' && r.fg === '--o-title-fg')).toBe(true)
    expect(rows.some((r) => r.theme === 'desk' && r.fg === '--d-sel-fg')).toBe(true)
  })

  it('text on fills reads by APCA, the rest by WCAG AA', () => {
    const fails = rows
      .filter((r) => !r.ok)
      .map((r) => `${r.theme} ${r.scheme}: ${r.fg} ${r.fgHex} on ${r.bg} ${r.bgHex} WCAG ${r.wcag.toFixed(2)} Lc ${r.lc.toFixed(1)} ${r.note}`.trim())
    expect(fails).toEqual([])
  })
})
