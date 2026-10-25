import { useEffect, useLayoutEffect, useSyncExternalStore } from 'react'
import { useMe, useSettings } from '../api/hooks'
import { LAST_ACCENT_KEY, showAccent } from '../lib/colour/boot'
import { rememberLastTheme, useAccentChoice, useThemedSettings, useThemeId } from '../themes/useUITheme'

const mq = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null

function subscribe(cb: () => void) {
  mq?.addEventListener('change', cb)
  return () => mq?.removeEventListener('change', cb)
}

function systemDark() {
  return mq?.matches ?? false
}

/** Resolved theme ('light' | 'dark'), following the setting or the OS. */
export function useTheme(): 'light' | 'dark' {
  const settings = useSettings()
  const sys = useSyncExternalStore(subscribe, systemDark, () => false)
  if (settings.theme === 'dark') return 'dark'
  if (settings.theme === 'light') return 'light'
  return sys ? 'dark' : 'light'
}

/** Apply colour scheme, UI theme, accent and density to <html>. Mount once. */
export function useApplyTheme() {
  const theme = useTheme()
  const settings = useThemedSettings()
  const uiTheme = useThemeId()
  const accent = useAccentChoice()
  // Until the account has loaded, the colours applied at boot (main.tsx) stay.
  const loaded = !!useMe().data
  // Before paint: the colours change in the same frame as the theme (no flash of the old accent).
  useLayoutEffect(() => {
    const el = document.documentElement
    el.dataset.uiTheme = uiTheme
    rememberLastTheme(uiTheme)
    if (!loaded) return
    showAccent(uiTheme, accent)
    try {
      if (accent) localStorage.setItem(LAST_ACCENT_KEY, JSON.stringify(accent))
      else localStorage.removeItem(LAST_ACCENT_KEY)
    } catch {
      /* private mode */
    }
  }, [uiTheme, accent, loaded])
  useEffect(() => {
    const el = document.documentElement
    el.dataset.theme = theme
    try {
      localStorage.setItem('mailr.theme', theme)
    } catch {
      /* ignore */
    }
    el.dataset.density = settings.density
    // The browser chrome (and the installed app's title bar) takes the page background of the
    // active theme; index.html has one tag per colour scheme, and the app's choice wins over both.
    const bg = getComputedStyle(el).getPropertyValue('--bg').trim()
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.setAttribute('content', bg || (theme === 'dark' ? '#1b1b1b' : '#f4f8f9')))
  }, [theme, settings.density, uiTheme, accent])
}
