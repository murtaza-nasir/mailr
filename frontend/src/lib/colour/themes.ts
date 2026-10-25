// Each theme's colour choices: its presets, the surfaces under its accents (for the AA checks) and
// one mapping table from the theme's CSS custom properties to the engine's roles. Neutrals,
// surfaces, layout and character stay the theme's own; only the properties in the table change.
// Plain data (no React), so the boot code in main.tsx can use it before the first paint.

import { t } from '../../i18n'
import { mixOklab } from './contrast'
import type { EngineContext, Role, Scheme } from './engine'
import type { Harmony } from './harmony'

export type ThemeKey = 'classic' | 'office' | 'focus' | 'desk' | 'cards'

export interface Preset {
  /** `<theme>.<name>`: a preset belongs to one theme. */
  id: string
  seed: string
  harmony: Harmony
  /** The theme's own hand-tuned colours: nothing is overridden. The seed is only for the swatch,
   *  the wheel's starting point and Android. */
  native?: boolean
  readonly name: string
}

function preset(id: string, seed: string, name: () => string, native = false): Preset {
  return {
    id,
    seed,
    harmony: 'single',
    native: native || undefined,
    get name() {
      return name()
    },
  }
}

/** A CSS custom property's source: a role of the same scheme, or a role of the named scheme. */
export type MapSource = Role | [Scheme, Role]

/** The accent spots every theme has. */
const COMMON: Record<string, MapSource> = {
  '--primary': 'primary',
  '--on-primary': 'onPrimary',
  '--primary-container': 'primaryContainer',
  '--on-primary-container': 'onPrimaryContainer',
  '--secondary': 'secondary',
  '--secondary-container': 'secondaryContainer',
  '--on-secondary-container': 'onSecondaryContainer',
  '--tertiary': 'tertiary',
  '--tertiary-container': 'tertiaryContainer',
  '--on-tertiary-container': 'onTertiaryContainer',
  '--focus-ring': 'focusRing',
  '--row-focused-bar': 'primary',
  '--row-selected': 'selection',
  '--link': 'link',
  '--toast-action': 'inversePrimary',
  '--accent-chip-bg': 'chip',
  '--accent-chip-fg': 'onChip',
  '--accent-badge-bg': 'badge',
  '--accent-badge-fg': 'onBadge',
  '--accent-selection': 'selection',
}

export interface ThemeColours {
  presets: Preset[]
  context: EngineContext
  map: Record<string, MapSource>
}

const TOASTS = { light: '#303030', dark: '#e3e3e3' }

export const THEME_COLOURS: Record<ThemeKey, ThemeColours> = {
  classic: {
    presets: [
      preset('classic.ocean', '#1f6c86', () => t('colour.presets.ocean'), true),
      preset('classic.forest', '#2e6b4f', () => t('colour.presets.forest')),
      preset('classic.plum', '#7b4f8a', () => t('colour.presets.plum')),
      preset('classic.brick', '#9a4a3a', () => t('colour.presets.brick')),
      preset('classic.slate', '#4a5f78', () => t('colour.presets.slate')),
    ],
    context: {
      light: { surfaces: ['#ffffff', '#f4f8f9', '#eff5f7'], text: '#1f1f1f', toast: TOASTS.light },
      dark: { surfaces: ['#282828', '#1b1b1b', '#1f1f1f'], text: '#e3e3e3', toast: TOASTS.dark },
    },
    map: {
      ...COMMON,
      '--nav-active': 'navActive',
      '--nav-active-fg': 'navActiveFg',
      '--compose-bg': 'fab',
      '--compose-fg': 'onFab',
    },
  },
  office: {
    presets: [
      preset('office.ocean', '#17607a', () => t('colour.presets.ocean'), true),
      preset('office.navy', '#1f4e8c', () => t('colour.presets.navy')),
      preset('office.wine', '#8c2f4b', () => t('colour.presets.wine')),
      preset('office.moss', '#4f6b2f', () => t('colour.presets.moss')),
      preset('office.graphite', '#4b5563', () => t('colour.presets.graphite')),
    ],
    context: {
      light: { surfaces: ['#ffffff', '#f0f0f0', '#fafafa'], text: '#242424', toast: TOASTS.light },
      dark: { surfaces: ['#242424', '#141414', '#1f1f1f'], text: '#f0f0f0', toast: TOASTS.dark },
    },
    map: {
      ...COMMON,
      '--nav-active': 'navActive',
      '--nav-active-fg': 'navActiveFg',
      '--compose-bg': 'solid',
      '--compose-fg': 'onSolid',
      '--o-title-bg': 'bar',
      '--o-title-fg': 'onBar',
      '--o-search-bg': 'barField',
    },
  },
  focus: {
    // Focus keeps its ink compose button and quiet folders: only the accent spots take the colour.
    presets: [
      preset('focus.iris', '#5151cd', () => t('colour.presets.iris'), true),
      preset('focus.ocean', '#2a6f97', () => t('colour.presets.ocean')),
      preset('focus.sage', '#4f7a5f', () => t('colour.presets.sage')),
      preset('focus.clay', '#a0583c', () => t('colour.presets.clay')),
      preset('focus.plum', '#7a4d8c', () => t('colour.presets.plum')),
    ],
    context: {
      light: { surfaces: ['#ffffff', '#fbfbfa', '#f6f6f4'], text: '#1c1c1a', toast: TOASTS.light },
      dark: { surfaces: ['#18181b', '#111113', '#141416'], text: '#ededef', toast: TOASTS.dark },
    },
    map: { ...COMMON },
  },
  desk: {
    presets: [
      preset('desk.ocean', '#1d6a8a', () => t('colour.presets.ocean'), true),
      preset('desk.steel', '#3d5a80', () => t('colour.presets.steel')),
      preset('desk.forest', '#2f6d43', () => t('colour.presets.forest')),
      preset('desk.amber', '#8a5a00', () => t('colour.presets.amber')),
      preset('desk.crimson', '#a3333d', () => t('colour.presets.crimson')),
    ],
    context: {
      light: { surfaces: ['#ffffff', '#eef2f4', '#f8fafb'], text: '#151a1d', toast: TOASTS.light },
      dark: { surfaces: ['#282d31', '#191d20', '#23282b', '#202528'], text: '#f6f9fa', toast: TOASTS.dark },
    },
    map: {
      ...COMMON,
      '--nav-active': 'navActive',
      '--nav-active-fg': 'navActiveFg',
      '--compose-bg': 'solid',
      '--compose-fg': 'onSolid',
      '--d-sel': 'solid',
      '--d-sel-fg': 'onSolid',
    },
  },
  cards: {
    // Today's five accents, now built by the engine (same seeds, so the same primary tones).
    presets: [
      preset('cards.violet', '#6750a4', () => t('themes.cards.index.violet')),
      preset('cards.teal', '#006a6a', () => t('themes.cards.index.teal')),
      preset('cards.rose', '#984061', () => t('themes.cards.index.rose')),
      preset('cards.green', '#3b6a1f', () => t('themes.cards.index.green')),
      preset('cards.blue', '#00629e', () => t('themes.cards.index.blue')),
    ],
    context: {
      // Cards tints its surfaces with the accent (cards.css): the same mixes, for the checks.
      light: {
        surfaces: (p) => [mixOklab(p, 2, '#ffffff'), mixOklab(p, 8, '#fdfbff'), mixOklab(p, 4, '#fcfaff')],
        text: '#1c1b1f',
        toast: TOASTS.light,
      },
      dark: {
        surfaces: (p) => [mixOklab(p, 7, '#1b1b1f'), mixOklab(p, 4, '#131316'), mixOklab(p, 5, '#18181b')],
        text: '#e6e1e6',
        toast: TOASTS.dark,
      },
    },
    map: {
      ...COMMON,
      '--c-accent': ['light', 'primary'],
      '--c-accent-d': ['dark', 'primary'],
      '--c-secondary': 'navActive',
      '--nav-active': 'navActive',
      '--nav-active-fg': 'navActiveFg',
      '--compose-bg': 'primaryContainer',
      '--compose-fg': 'onPrimaryContainer',
      // Cards selects in its colour, as before.
      '--row-selected': 'primaryContainer',
    },
  },
}

/** Cards' accent ids before the engine (ui_accent was one of these strings). */
export const LEGACY_CARDS = ['violet', 'teal', 'rose', 'green', 'blue']

export function presetById(id: string): Preset | undefined {
  const theme = id.split('.')[0] as ThemeKey
  return THEME_COLOURS[theme]?.presets.find((p) => p.id === id)
}
