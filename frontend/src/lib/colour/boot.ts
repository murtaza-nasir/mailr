// The colours at page load, without the colour engine. The engine
// (apply.ts, with the HCT library) is a chunk of its own: each sheet it builds is kept in
// localStorage under its theme, seed and harmony, and the next load puts the kept sheet on the page
// before the first render (main.tsx), so the colours never flash. A choice with no kept sheet
// (another device chose it) loads the engine, builds it, and keeps it.

import { resolveChoice, type AccentChoice, type Resolved } from './choice'
import type { ThemeKey } from './themes'

const STYLE_ID = 'mailr-accent'
/** localStorage: the last choice the page applied, for the next load's first paint. */
export const LAST_ACCENT_KEY = 'mailr.uiAccent.last'
/** localStorage: built sheets, {key: css}, the last few. */
export const SHEETS_KEY = 'mailr.uiAccent.sheets'
const KEEP = 6

export function accentKey(theme: ThemeKey, res: Resolved): string {
  return `${theme}|${res.seed}|${res.harmony}`
}

function readSheets(): Record<string, string> {
  try {
    const v = JSON.parse(localStorage.getItem(SHEETS_KEY) ?? '{}') as unknown
    return v && typeof v === 'object' ? (v as Record<string, string>) : {}
  } catch {
    return {}
  }
}

export function rememberSheet(key: string, css: string) {
  const all = readSheets()
  if (all[key] === css) return
  delete all[key]
  const keys = Object.keys(all)
  const kept: Record<string, string> = {}
  for (const k of keys.slice(Math.max(0, keys.length - (KEEP - 1)))) kept[k] = all[k]
  kept[key] = css
  try {
    localStorage.setItem(SHEETS_KEY, JSON.stringify(kept))
  } catch {
    /* private mode or full: the next load builds it again */
  }
}

/** Put a sheet on the page (null: the theme's own colours). */
export function setAccentSheet(res: Resolved, css: string | null, doc: Document = document) {
  const root = doc.documentElement
  root.dataset.accent = res.preset?.id ?? 'custom'
  let style = doc.getElementById(STYLE_ID) as HTMLStyleElement | null
  if (css === null) {
    delete root.dataset.accentSrc
    style?.remove()
    return
  }
  if (!style) {
    style = doc.createElement('style')
    style.id = STYLE_ID
    doc.head.appendChild(style)
  }
  if (style.textContent !== css) style.textContent = css
  root.dataset.accentSrc = 'engine'
}

interface Engine {
  applyAccent: (theme: ThemeKey, choice: AccentChoice | null, doc?: Document) => Resolved
}
let engine: Engine | null = null
let loading: Promise<unknown> | null = null
/** Bumped by every showAccent, so a late engine load cannot show an older choice. */
let generation = 0

/** apply.ts calls this when it loads (by import() here, or with the colour picker). */
export function engineReady(e: Engine) {
  engine = e
}

/** The engine chunk (loaded once). */
export function loadEngine(): Promise<unknown> {
  loading ??= import('./apply')
  return loading
}

/**
 * Show a choice in a theme: at once when the sheet is kept or the engine has loaded, else as soon
 * as the engine has. Returns what was resolved.
 */
export function showAccent(theme: ThemeKey, choice: AccentChoice | null, doc: Document = document): Resolved {
  const res = resolveChoice(theme, choice)
  const g = ++generation
  if (res.kind === 'native') {
    setAccentSheet(res, null, doc)
    return res
  }
  if (engine) return engine.applyAccent(theme, choice, doc)
  const kept = readSheets()[accentKey(theme, res)]
  if (kept) setAccentSheet(res, kept, doc)
  // The engine is wanted soon anyway (a theme switch, the picker); load it in the background.
  void loadEngine().then(() => {
    // Only if nothing else was shown since.
    if (g === generation) engine?.applyAccent(theme, choice, doc)
  })
  return res
}
