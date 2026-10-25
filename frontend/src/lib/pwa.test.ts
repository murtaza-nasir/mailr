import { describe, expect, it } from 'vitest'
import { DISMISS_MS, installDismissed, installPlatform, iosBrowser, iosVersion, isIOS, shouldOfferInstall } from './pwa'

const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
const IPHONE_CHROME = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0.6367.88 Mobile/15E148 Safari/604.1'
const IPAD = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15'
const PIXEL = 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36'
const ANDROID_FIREFOX = 'Mozilla/5.0 (Android 14; Mobile; rv:125.0) Gecko/125.0 Firefox/125.0'
const DESKTOP_FIREFOX = 'Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0'
const DESKTOP_CHROME = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

describe('install platform', () => {
  it('iPhone and iPad (desktop-class Safari with touch) install from Share', () => {
    expect(installPlatform({ ua: IPHONE })).toBe('ios')
    expect(installPlatform({ ua: IPHONE_CHROME })).toBe('ios')
    expect(isIOS({ ua: IPAD, touchPoints: 5 })).toBe(true)
    expect(installPlatform({ ua: IPAD, touchPoints: 5 })).toBe('ios')
    // A Mac without touch is desktop Safari: nothing to install.
    expect(installPlatform({ ua: IPAD, touchPoints: 0 })).toBe('none')
  })
  it('Chromium installs with the prompt; Firefox on Android from its menu; desktop Firefox not at all', () => {
    expect(installPlatform({ ua: PIXEL })).toBe('prompt')
    expect(installPlatform({ ua: DESKTOP_CHROME })).toBe('prompt')
    expect(installPlatform({ ua: ANDROID_FIREFOX })).toBe('menu')
    expect(installPlatform({ ua: DESKTOP_FIREFOX })).toBe('none')
    expect(installPlatform({ ua: DESKTOP_FIREFOX, hasPrompt: true })).toBe('prompt')
  })
  it('names the iOS browser and version', () => {
    expect(iosBrowser(IPHONE)).toBe('safari')
    expect(iosBrowser(IPHONE_CHROME)).toBe('chrome')
    expect(iosVersion(IPHONE)).toBeCloseTo(17.04)
    expect(iosVersion(PIXEL)).toBeNull()
  })
})

describe('install banner', () => {
  it('is offered on phones that can install, never when installed or dismissed', () => {
    expect(shouldOfferInstall({ ua: IPHONE }, false, false, true)).toBe(true)
    expect(shouldOfferInstall({ ua: PIXEL }, false, false, true)).toBe(true)
    expect(shouldOfferInstall({ ua: IPHONE }, true, false, true)).toBe(false)
    expect(shouldOfferInstall({ ua: IPHONE }, false, true, true)).toBe(false)
    expect(shouldOfferInstall({ ua: DESKTOP_CHROME }, false, false, false)).toBe(false)
    expect(shouldOfferInstall({ ua: DESKTOP_FIREFOX }, false, false, true)).toBe(false)
  })
  it('a dismissal lasts 30 days', () => {
    const now = 1_000_000_000_000
    const store = (v: string | null) => ({ getItem: () => v })
    expect(installDismissed(now, store(null))).toBe(false)
    expect(installDismissed(now, store(String(now - 1000)))).toBe(true)
    expect(installDismissed(now, store(String(now - DISMISS_MS - 1)))).toBe(false)
  })
})
