// The saved colour choice (the account's `ui_accent`, or this device's override) and how a theme
// resolves it. Shape: {preset, seed, harmony}. A preset carries its seed too, so a reader that
// does not know the presets (the Android app) can still build the colours. Before 8 Oct 2026
// `ui_accent` was a bare Cards accent id ("teal"); parseChoice reads that as the Cards preset.

import { isHex } from './contrast'
import { isHarmony, type Harmony } from './harmony'
import { LEGACY_CARDS, THEME_COLOURS, presetById, type Preset, type ThemeKey } from './themes'

export interface AccentChoice {
  preset?: string
  seed?: string
  harmony?: Harmony
}

export function parseChoice(v: unknown): AccentChoice | null {
  if (typeof v === 'string') {
    const s = v.trim()
    if (s.startsWith('{')) {
      try {
        return parseChoice(JSON.parse(s))
      } catch {
        return null
      }
    }
    if (LEGACY_CARDS.includes(s)) return { preset: `cards.${s}` }
    if (presetById(s)) return { preset: s }
    if (isHex(s)) return { seed: s.toLowerCase(), harmony: 'single' }
    return null
  }
  if (!v || typeof v !== 'object') return null
  const o = v as Record<string, unknown>
  const out: AccentChoice = {}
  if (typeof o.preset === 'string' && presetById(o.preset)) out.preset = o.preset
  else if (typeof o.preset === 'string' && LEGACY_CARDS.includes(o.preset)) out.preset = `cards.${o.preset}`
  if (isHex(o.seed)) out.seed = o.seed.toLowerCase()
  if (isHarmony(o.harmony)) out.harmony = o.harmony
  if (!out.preset && !out.seed) return null
  return out
}

/** What a theme shows for a choice. */
export type Resolved =
  | { kind: 'native'; preset: Preset; seed: string; harmony: Harmony }
  | { kind: 'engine'; preset: Preset | null; seed: string; harmony: Harmony }

/**
 * A preset applies in its own theme; in another theme that theme's first preset shows. A custom
 * colour (a seed with no preset) applies in every theme.
 */
export function resolveChoice(theme: ThemeKey, choice: AccentChoice | null): Resolved {
  const presets = THEME_COLOURS[theme].presets
  const fallback = presets[0]
  let p: Preset | null = null
  if (choice?.preset) p = presets.find((x) => x.id === choice.preset) ?? fallback
  else if (!choice?.seed) p = fallback
  if (p) return p.native ? { kind: 'native', preset: p, seed: p.seed, harmony: p.harmony } : { kind: 'engine', preset: p, seed: p.seed, harmony: p.harmony }
  return { kind: 'engine', preset: null, seed: choice!.seed!, harmony: choice!.harmony ?? 'single' }
}

/** The value to save for a preset. */
export function presetChoice(p: Preset): AccentChoice {
  return { preset: p.id, seed: p.seed, harmony: p.harmony }
}

export function sameChoice(a: AccentChoice | null, b: AccentChoice | null): boolean {
  return (a?.preset ?? null) === (b?.preset ?? null) && (a?.seed ?? null) === (b?.seed ?? null) && (a?.harmony ?? 'single') === (b?.harmony ?? 'single')
}
