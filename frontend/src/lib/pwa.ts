// The installed web app (PWA): where it runs, whether it can be installed, and the browser's
// install prompt. Pure detection takes the user agent and flags as arguments, so it is tested
// without a browser (pwa.test.ts); the store keeps the deferred `beforeinstallprompt` event.
import { create } from 'zustand'

export type InstallPlatform =
  /** Safari (or another browser) on an iPhone or iPad: Share → Add to Home Screen. */
  | 'ios'
  /** Chrome, Edge and other Chromium browsers: the install prompt (beforeinstallprompt). */
  | 'prompt'
  /** A browser that installs from its own menu (Firefox on Android, Samsung Internet without a prompt yet). */
  | 'menu'
  /** Desktop Safari and Firefox: no home-screen install. */
  | 'none'

export interface PlatformInput {
  ua: string
  /** navigator.maxTouchPoints: iPadOS reports a Mac user agent with touch. */
  touchPoints?: number
  /** A deferred beforeinstallprompt event is waiting. */
  hasPrompt?: boolean
}

/** An iPhone, iPod or iPad (iPadOS 13+ says "Macintosh" but has a touch screen). */
export function isIOS({ ua, touchPoints = 0 }: PlatformInput): boolean {
  return /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && touchPoints > 1)
}

export function isAndroid({ ua }: PlatformInput): boolean {
  return /Android/.test(ua)
}

/** How this browser installs mailr to the home screen, if at all. */
export function installPlatform(p: PlatformInput): InstallPlatform {
  if (isIOS(p)) return 'ios'
  if (p.hasPrompt) return 'prompt'
  if (isAndroid(p)) return /Chrome\/|EdgA\/|SamsungBrowser\//.test(p.ua) ? 'prompt' : 'menu'
  if (/Edg\/|Chrome\//.test(p.ua) && !/OPR\//.test(p.ua)) return 'prompt'
  return 'none'
}

/** The browser on iOS that is not Safari (Chrome, Firefox, Edge): Add to Home Screen sits in its Share menu too (iOS 16.4+). */
export function iosBrowser(ua: string): 'safari' | 'chrome' | 'firefox' | 'edge' | 'other' {
  if (/CriOS\//.test(ua)) return 'chrome'
  if (/FxiOS\//.test(ua)) return 'firefox'
  if (/EdgiOS\//.test(ua)) return 'edge'
  if (/Safari\//.test(ua) && /Version\//.test(ua)) return 'safari'
  return 'other'
}

/** The iOS major.minor version, or null (16.4 brought Web Push and badges to Home Screen apps). */
export function iosVersion(ua: string): number | null {
  const m = /OS (\d+)[_.](\d+)/.exec(ua)
  return m ? Number(m[1]) + Number(m[2]) / 100 : null
}

/** mailr runs as an installed app (its own window, no browser bar). */
export function isStandalone(): boolean {
  if (typeof window === 'undefined') return false
  try {
    if ((navigator as Navigator & { standalone?: boolean }).standalone === true) return true
    return ['standalone', 'fullscreen', 'minimal-ui', 'window-controls-overlay'].some((m) => window.matchMedia(`(display-mode: ${m})`).matches)
  } catch {
    return false
  }
}

export function platformInput(): PlatformInput {
  if (typeof navigator === 'undefined') return { ua: '' }
  return { ua: navigator.userAgent, touchPoints: navigator.maxTouchPoints ?? 0, hasPrompt: !!useInstall.getState().prompt }
}

/** The event Chromium browsers fire when the app may be installed (not in the DOM typings). */
export interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform?: string }>
}

interface InstallState {
  prompt: BeforeInstallPromptEvent | null
  installed: boolean
  /** The install guide is open (from the banner, More or Settings). */
  guideOpen: boolean
  /** The phone banner was dismissed in this session. */
  bannerDismissed: boolean
  setGuide: (open: boolean) => void
}

export const useInstall = create<InstallState>((set) => ({
  prompt: null,
  installed: isStandalone(),
  guideOpen: false,
  bannerDismissed: false,
  setGuide: (guideOpen) => set({ guideOpen }),
}))

let listening = false

/** Keep the browser's install prompt for later (call once, early: it fires soon after load). */
export function captureInstallPrompt(): void {
  if (listening || typeof window === 'undefined') return
  listening = true
  window.addEventListener('beforeinstallprompt', (e) => {
    // Hold the browser's own mini-infobar; mailr offers Install where it fits.
    e.preventDefault()
    useInstall.setState({ prompt: e as BeforeInstallPromptEvent })
  })
  window.addEventListener('appinstalled', () => useInstall.setState({ prompt: null, installed: true, guideOpen: false }))
}

/** Show the browser's install dialog. Resolves to the user's answer, or 'unavailable'. */
export async function promptInstall(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  const p = useInstall.getState().prompt
  if (!p) return 'unavailable'
  try {
    await p.prompt()
    const { outcome } = await p.userChoice
    // A prompt can be used once.
    useInstall.setState({ prompt: null, ...(outcome === 'accepted' ? { installed: true, guideOpen: false } : {}) })
    return outcome
  } catch {
    useInstall.setState({ prompt: null })
    return 'unavailable'
  }
}

const DISMISS_KEY = 'mailr.install.dismissedAt'
/** A dismissed install banner stays away this long. */
export const DISMISS_MS = 30 * 24 * 3600_000

export function installDismissed(now = Date.now(), store: Pick<Storage, 'getItem'> | null = safeLocal()): boolean {
  try {
    const at = Number(store?.getItem(DISMISS_KEY))
    return !!at && now - at < DISMISS_MS
  } catch {
    return false
  }
}

export function dismissInstall(now = Date.now()): void {
  try {
    safeLocal()?.setItem(DISMISS_KEY, String(now))
  } catch {
    /* private mode */
  }
}

function safeLocal(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

/** Whether the phone banner offers installing: a phone browser that can install, not installed, not dismissed. */
export function shouldOfferInstall(p: PlatformInput, standalone: boolean, dismissed: boolean, narrow: boolean): boolean {
  if (standalone || dismissed || !narrow) return false
  const kind = installPlatform(p)
  return kind === 'ios' || kind === 'prompt' || kind === 'menu'
}
