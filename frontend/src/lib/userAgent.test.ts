import { describe, expect, it } from 'vitest'
import { deviceName } from './userAgent'

describe('deviceName', () => {
  it('names browsers and systems', () => {
    expect(deviceName('Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0')).toBe('Firefox on Linux')
    expect(deviceName('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36')).toBe('Chrome on Linux')
    expect(deviceName('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0')).toBe('Edge on Windows')
    expect(deviceName('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1')).toBe('Safari on iOS')
    expect(deviceName('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15')).toBe('Safari on macOS')
  })
  it('names the apps, scripts and the unknown', () => {
    expect(deviceName('mailr-android/0.9.0')).toBe('Mailr app on Android')
    expect(deviceName('mailr-android-notes/1.2.0')).toBe('Notes app on Android')
    expect(deviceName('python-httpx/0.28.1')).toBe('Script or API client')
    expect(deviceName('', 'web')).toBe('Unknown device')
  })
})
