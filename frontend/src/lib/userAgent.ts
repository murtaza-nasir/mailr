// A signed-in device in words (Settings → You → Password & devices): "Firefox on Linux", "Mailr
// app on Android", "Script or API client", from the session's User-Agent. The raw string stays in
// the tooltip.

import { t } from '../i18n'

const BROWSERS: [RegExp, string][] = [
  [/\bEdg(e|A|iOS)?\//, 'Edge'], // i18n-ignore: product names
  [/\b(OPR|Opera)\//, 'Opera'], // i18n-ignore
  [/\b(Firefox|FxiOS)\//, 'Firefox'], // i18n-ignore
  [/\b(HeadlessChrome|Chrome|CriOS|Chromium)\//, 'Chrome'], // i18n-ignore
  [/\bVersion\/[\d.]+.*\bSafari\//, 'Safari'], // i18n-ignore
]

const SYSTEMS: [RegExp, string][] = [
  [/\bAndroid\b/, 'Android'], // i18n-ignore: system names
  [/\biPad\b/, 'iPadOS'], // i18n-ignore
  [/\b(iPhone|iPod)\b/, 'iOS'], // i18n-ignore
  [/\bCrOS\b/, 'ChromeOS'], // i18n-ignore
  [/\bWindows\b/, 'Windows'], // i18n-ignore
  [/\bMac OS X\b|\bMacintosh\b/, 'macOS'], // i18n-ignore
  [/\bLinux\b|\bX11\b/, 'Linux'], // i18n-ignore
]

export function deviceName(userAgent: string, device?: string): string {
  const ua = userAgent || ''
  if (/^mailr-android-notes\//.test(ua)) return t('lib.userAgent.notesAppOnAndroid')
  if (/^mailr-android\//.test(ua) || device === 'android') return t('lib.userAgent.mailrAppOnAndroid')
  if (!ua) return t('lib.userAgent.unknownDevice')
  const browser = BROWSERS.find(([re]) => re.test(ua))?.[1]
  const os = SYSTEMS.find(([re]) => re.test(ua))?.[1]
  if (!browser) return t('lib.userAgent.scriptOrApiClient')
  return os ? t('lib.userAgent.browserOnSystem', { browser, os }) : browser
}
