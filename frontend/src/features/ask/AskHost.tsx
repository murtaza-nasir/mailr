// Mounted once per shell (GlobalOverlays). The capture itself (AskCapture.tsx, with dictation) loads
// the first time it opens; the first page does not need it.

import { lazy, Suspense, useEffect } from 'react'
import { isTypingTarget } from '../../lib/shortcuts'
import { useThemedSettings } from '../../themes/useUITheme'
import { openAskFromScreen, useAskCapture } from './askStore'

const AskCapture = lazy(() => import('./AskCapture').then((m) => ({ default: m.AskCapture })))

/**
 * The capture when open, and Shift+A while a dialog or a popover is open (the global keys stand
 * aside then; useGlobalShortcuts handles it otherwise).
 */
export function AskHost() {
  const open = useAskCapture((s) => s.open)
  const settings = useThemedSettings()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.key !== 'A' || !e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return
      if (settings.keyboard_shortcuts === false || isTypingTarget(e.target)) return
      if (useAskCapture.getState().open || !document.querySelector('.scrim, .popover')) return
      e.preventDefault()
      openAskFromScreen()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [settings.keyboard_shortcuts])
  return open ? (
    <Suspense fallback={null}>
      <AskCapture />
    </Suspense>
  ) : null
}
