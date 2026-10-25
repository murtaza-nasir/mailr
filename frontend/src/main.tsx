import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { captureInstallPrompt, isIOS, isStandalone } from './lib/pwa'
import { registerServiceWorker } from './lib/serviceWorker'
import { startErrorReports } from './lib/errorReports'
import { LAST_ACCENT_KEY, showAccent } from './lib/colour/boot'
import { parseChoice } from './lib/colour/choice'
import { THEME_COLOURS, type ThemeKey } from './lib/colour/themes'
import './styles/tokens.css'
import './styles/base.css'
import './styles/components.css'
import './styles/layout.css'
import './styles/list.css'
import './styles/conversation.css'
import './styles/compose.css'
import './styles/settings.css'
import './styles/rail.css'
import './styles/apps.css'
import './themes/themes.css'
import './styles/phone.css'

// Apply the last resolved theme before first paint to avoid a flash.
try {
  const t = localStorage.getItem('mailr.theme')
  const dark = t === 'dark' || (t !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  // The UI theme (layout + style): this device's choice, else the last one the account used.
  const ui = localStorage.getItem('mailr.uiTheme') || localStorage.getItem('mailr.uiTheme.last')
  if (ui) document.documentElement.dataset.uiTheme = ui
  // The colours too (lib/colour): the last choice this browser applied, built before the first render.
  showAccent(ui && ui in THEME_COLOURS ? (ui as ThemeKey) : 'classic', parseChoice(localStorage.getItem(LAST_ACCENT_KEY)))
} catch {
  /* ignore */
}

// The installed app and iOS get their own touches (styles/phone.css): safe areas, no
// rubber-band bounce of the whole page, no text callouts on long-press.
if (isStandalone()) document.documentElement.dataset.standalone = 'true'
if (isIOS({ ua: navigator.userAgent, touchPoints: navigator.maxTouchPoints })) document.documentElement.dataset.platform = 'ios'
// The install prompt fires once, early; keep it for the Install button (features/pwa).
captureInstallPrompt()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Error reports, when the server has an error tracker (lib/errorReports.ts); nothing loads otherwise.
if (import.meta.env.PROD) void startErrorReports()

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => void registerServiceWorker())
}
