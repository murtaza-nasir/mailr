import { useMemo } from 'react'
import { create } from 'zustand'
import { useSettings, useUpdateSettings } from '../api/hooks'
import type { Settings } from '../api/types'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { THEMES, isThemeId } from './registry'
import { parseChoice, resolveChoice, type AccentChoice, type Resolved } from '../lib/colour/choice'
import type { ThemeDef, ThemeId } from './types'

/** localStorage: a per-device theme that wins over the account setting. */
export const DEVICE_THEME_KEY = 'mailr.uiTheme'
/** localStorage: the last resolved theme, applied before first paint (main.tsx). */
export const LAST_THEME_KEY = 'mailr.uiTheme.last'
/** localStorage: this device's colour choice (JSON), kept with a device-only theme. */
export const DEVICE_ACCENT_KEY = 'mailr.uiAccent'

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
function write(key: string, v: string | null) {
  try {
    if (v === null) localStorage.removeItem(key)
    else localStorage.setItem(key, v)
  } catch {
    /* private mode */
  }
}

interface DeviceState {
  theme: ThemeId | null
  accent: AccentChoice | null
  setTheme: (t: ThemeId | null) => void
  setAccent: (a: AccentChoice | null) => void
}

export const useDeviceTheme = create<DeviceState>((set) => {
  const t = read(DEVICE_THEME_KEY)
  return {
    theme: t && isThemeId(t) ? t : null,
    accent: parseChoice(read(DEVICE_ACCENT_KEY)),
    setTheme: (theme) => {
      write(DEVICE_THEME_KEY, theme)
      set({ theme })
    },
    setAccent: (accent) => {
      write(DEVICE_ACCENT_KEY, accent ? JSON.stringify(accent) : null)
      set({ accent })
    },
  }
})

/** The active theme id: this device's choice, else the account setting, else Classic. */
export function useThemeId(): ThemeId {
  const device = useDeviceTheme((s) => s.theme)
  const account = useSettings().ui_theme
  if (device) return device
  return isThemeId(account) ? account : 'classic'
}

export function useThemeDef(): ThemeDef {
  return THEMES[useThemeId()]
}

/** The colour choice in force: this device's (with a device-only theme), else the account's. */
export function useAccentChoice(): AccentChoice | null {
  const device = useDeviceTheme((s) => s.accent)
  const deviceTheme = useDeviceTheme((s) => s.theme)
  const account = useSettings().ui_accent
  return useMemo(() => (deviceTheme && device ? device : parseChoice(account)), [device, deviceTheme, account])
}

/** What the active theme shows for the choice in force. */
export function useAccent(): Resolved {
  const choice = useAccentChoice()
  const id = useThemeId()
  return useMemo(() => resolveChoice(id, choice), [id, choice])
}

/** Switch theme now. `device` keeps the choice to this browser; otherwise it is saved to the
 *  account and any device override is cleared so the account choice shows. */
export function useSetTheme() {
  const upd = useUpdateSettings()
  const setDevice = useDeviceTheme((s) => s.setTheme)
  return (id: ThemeId, device: boolean) => {
    if (device) {
      setDevice(id)
      return
    }
    setDevice(null)
    upd.mutate({ ui_theme: id })
  }
}

/** Save a colour choice (null: the theme's own). A device-only theme keeps it on the device. */
export function useSetAccent() {
  const upd = useUpdateSettings()
  const setDevice = useDeviceTheme((s) => s.setAccent)
  const deviceTheme = useDeviceTheme((s) => s.theme)
  return (accent: AccentChoice | null) => {
    if (deviceTheme) setDevice(accent)
    else {
      setDevice(null)
      upd.mutate({ ui_accent: accent })
    }
  }
}

/**
 * Settings as the active theme applies them: a theme may force the reading pane or density.
 * Use this where the UI acts on those settings; settings forms keep using useSettings().
 */
export function useThemedSettings(): Settings {
  const settings = useSettings()
  const def = useThemeDef()
  const wide = useMediaQuery('(min-width: 1200px)')
  return useMemo(() => {
    const out = { ...settings, ...(def.overrides ?? {}) }
    if (def.readingPane) {
      const fallback = def.readingPane(wide)
      const pref = settings.reading_pane_by_theme?.[def.id]
      // A user's choice applies where the theme offers choices; a narrow screen that the
      // theme lays out without a split stays that way.
      out.reading_pane = pref && def.paneChoices?.includes(pref) && fallback !== 'none' ? pref : fallback
    }
    return out
  }, [settings, def, wide])
}

export function rememberLastTheme(id: ThemeId) {
  write(LAST_THEME_KEY, id)
}
