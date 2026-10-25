// Colour tokens: every custom property the web app reads is defined somewhere, and every
// colour token has a light and a dark value, in the base palette and in each theme. An undefined
// token falls back to its literal (often a light-theme hex), which in dark mode drew light
// borders and a 2.4:1 red on a dark surface.
/// <reference types="node" />
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// Every stylesheet and source file under src/, as text (paths relative to src/). Read from disk:
// vitest hands CSS imports over empty, even with ?raw.
const SRC = fileURLToPath(new URL('..', import.meta.url))
function files(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) files(p, out)
    else if (/\.(css|tsx?)$/.test(name) && !name.endsWith('.test.ts')) out.push(p)
  }
  return out
}
const all = files(SRC).map((p) => ({ path: relative(SRC, p), text: readFileSync(p, 'utf8') }))
const read = (path: string) => all.find((f) => f.path === path)!.text

function used(): Map<string, string[]> {
  const out = new Map<string, string[]>()
  for (const f of all)
    for (const m of f.text.matchAll(/var\(\s*(--[\w-]+)/g)) {
      const where = out.get(m[1]) ?? []
      if (!where.includes(f.path)) where.push(f.path)
      out.set(m[1], where)
    }
  return out
}

function defined(): Set<string> {
  const out = new Set<string>()
  for (const f of all) {
    // CSS declarations, `--x: …`
    for (const m of f.text.matchAll(/(?:^|[\s{;(])(--[\w-]+)\s*:/g)) out.add(m[1])
    // style objects ({ '--x': … }) and el.style.setProperty('--x', …)
    for (const m of f.text.matchAll(/['"`](--[\w-]+)['"`]\s*(?:[:,]|as\b)/g)) out.add(m[1])
    // computed keys: { [pane === 'below' ? '--list-h' : '--list-w']: … }
    for (const m of f.text.matchAll(/\[[^\]\n]*\]\s*:/g)) for (const k of m[0].matchAll(/['"`](--[\w-]+)['"`]/g)) out.add(k[1])
  }
  return out
}

/** The declarations of the first rule whose selector is exactly `selector`, in one file. */
function block(text: string, selector: string, from = 0): { decls: Map<string, string>; end: number } | null {
  const i = text.indexOf(`${selector} {`, from)
  if (i < 0) return null
  const open = text.indexOf('{', i)
  const close = text.indexOf('}', open)
  const decls = new Map<string, string>()
  for (const m of text.slice(open + 1, close).matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) decls.set(m[1], m[2].trim())
  return { decls, end: close }
}

const isColour = (v: string) => /^(#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(|color-mix\()/i.test(v)

function colourTokens(decls: Map<string, string>): string[] {
  return [...decls].filter(([, v]) => isColour(v)).map(([k]) => k)
}

describe('colour tokens', () => {
  it('every var(--token) the app reads is defined', () => {
    const def = defined()
    const missing = [...used()].filter(([k]) => !def.has(k)).map(([k, where]) => `${k} (${where.join(', ')})`)
    expect(missing).toEqual([])
  })

  it('the base palette has a dark value for every light colour', () => {
    const text = read('styles/tokens.css')
    const light = block(text, ':root')!.decls
    const dark = block(text, "[data-theme='dark']")!.decls
    // Shadows are drawn in both schemes from one value; every other colour needs two.
    const lightColours = colourTokens(light).filter((k) => !k.startsWith('--shadow-'))
    expect(lightColours.filter((k) => !dark.has(k))).toEqual([])
  })

  for (const theme of ['office', 'focus', 'desk', 'cards']) {
    it(`${theme}: every colour token has a light and a dark value`, () => {
      const text = read(`themes/${theme}/${theme}.css`)
      const lightSel = `[data-ui-theme='${theme}'][data-theme='light']`
      const darkSel = `[data-ui-theme='${theme}'][data-theme='dark']`
      const light = new Set<string>()
      const dark = new Set<string>()
      for (const [sel, into] of [[lightSel, light], [darkSel, dark]] as const) {
        let from = 0
        for (;;) {
          const b = block(text, sel, from)
          if (!b) break
          colourTokens(b.decls).forEach((k) => into.add(k))
          from = b.end
        }
      }
      expect(light.size).toBeGreaterThan(0)
      expect([...light].filter((k) => !dark.has(k))).toEqual([])
      expect([...dark].filter((k) => !light.has(k))).toEqual([])
    })
  }
})
