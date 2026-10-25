// Settings → Mail → Writing → Email links: make mailr the browser's app for email (mailto:) links.
// The button calls navigator.registerProtocolHandler('mailto', <origin>/compose?mailto=%s) (it needs a
// click and HTTPS); the browser then asks the user to confirm. Chromium browsers and Firefox on a
// computer support it; Safari and the phone browsers do not, and the section says so. A browser
// never tells a page whether the user accepted, so the section says that too and offers a test
// link (left to the browser: data-mailto="native"). The installed app (PWA) lists the same handler
// in its manifest (protocol_handlers). The desktop steps (Linux .desktop file, xdg-mime) are in
// the user guide, user-guide/email-links.md.
// ACT settings.mail_handler @web.settings

import { ExternalLink, Mail } from 'lucide-react'
import { useState } from 'react'
import { handlerUrl } from '../../lib/mailto'
import { isAndroid, isIOS, isStandalone, platformInput, type PlatformInput } from '../../lib/pwa'
import { Section } from './common'
import { t } from '../../i18n'

export type HandlerSupport =
  /** Chrome, Edge, Brave, Opera, Vivaldi on a computer. */
  | 'chromium'
  /** Firefox on a computer. */
  | 'firefox'
  /** Safari on a Mac. */
  | 'safari'
  /** An iPhone or iPad: no browser there can do it. */
  | 'ios'
  /** An Android phone: the mailr app is the email app there. */
  | 'android'
  /** The page is not on HTTPS (or localhost). */
  | 'insecure'
  /** Any other browser without the API. */
  | 'unsupported'

export interface SupportInput extends PlatformInput {
  /** window.isSecureContext */
  secure: boolean
  /** 'registerProtocolHandler' in navigator */
  hasApi: boolean
}

/** What this browser can do about email links. */
export function handlerSupport(p: SupportInput): HandlerSupport {
  if (isIOS(p)) return 'ios'
  if (isAndroid(p)) return 'android'
  if (!p.secure) return 'insecure'
  if (/Firefox\//.test(p.ua) && p.hasApi) return 'firefox'
  if (/Chrome\/|Chromium\/|Edg\//.test(p.ua) && p.hasApi) return 'chromium'
  if (/Safari\//.test(p.ua) && /Version\//.test(p.ua) && !/Chrome\//.test(p.ua)) return 'safari'
  return p.hasApi ? 'chromium' : 'unsupported'
}

function supportNow(): HandlerSupport {
  if (typeof window === 'undefined') return 'unsupported'
  return handlerSupport({ ...platformInput(), secure: window.isSecureContext, hasApi: typeof navigator.registerProtocolHandler === 'function' })
}

const ASKED = 'mailr.mailtoHandlerAsked'

function readAsked(): boolean {
  try {
    return !!localStorage.getItem(ASKED)
  } catch {
    return false
  }
}

const NOT_HERE: Partial<Record<HandlerSupport, string>> = {
  safari: t('settings.emailLinks.safari'),
  ios: t('settings.emailLinks.ios'),
  android: t('settings.emailLinks.android'),
  insecure: t('settings.emailLinks.insecure'),
  unsupported: t('settings.emailLinks.unsupported'),
}

export function EmailLinksSection() {
  const [support] = useState(supportNow)
  const [asked, setAsked] = useState(readAsked)
  const [error, setError] = useState<string | null>(null)
  const can = support === 'chromium' || support === 'firefox'
  const ask = () => {
    setError(null)
    try {
      navigator.registerProtocolHandler('mailto', handlerUrl(window.location.origin))
      setAsked(true)
      try {
        localStorage.setItem(ASKED, new Date().toISOString())
      } catch {
        /* the note is only a convenience */
      }
    } catch (e) {
      setError(t('settings.emailLinks.refused', { message: (e as Error).message }))
    }
  }
  return (
    <Section title={t('settings.emailLinks.title')} desc={t('settings.emailLinks.desc')}>
      <div className="stack" style={{ gap: 10 }} data-testid="email-links" data-support={support}>
        {can ? (
          <>
            <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
              <button type="button" className="btn tonal" onClick={ask} data-testid="email-links-use">
                <Mail size={16} />{t('settings.emailLinks.use')}
              </button>
              {/* Left to the browser on purpose: it shows what an email link from another site does. */}
              <a className="btn text" href="mailto:?subject=Mailr%20test" data-mailto="native" data-testid="email-links-test">
                {t('settings.emailLinks.test')}
              </a>
            </div>
            <p className="muted" style={{ margin: 0 }} role="status">
              {asked ? t('settings.emailLinks.asked') : support === 'firefox' ? t('settings.emailLinks.confirmFirefox') : t('settings.emailLinks.confirmChromium')}
            </p>
            {error && <p className="banner error" style={{ margin: 0 }}>{error}</p>}
            <p className="muted" style={{ margin: 0, fontSize: 13 }}>
              {support === 'firefox' ? t('settings.emailLinks.undoFirefox') : t('settings.emailLinks.undoChromium')}
              {support === 'chromium' && isStandalone() ? ` ${t('settings.emailLinks.installedApp')}` : ''}
            </p>
          </>
        ) : (
          <p style={{ margin: 0 }} data-testid="email-links-unsupported">{NOT_HERE[support]}</p>
        )}
        <a className="btn text sm" style={{ alignSelf: 'flex-start' }} href="/help/user-guide/email-links/" target="_blank" rel="noopener">
          <ExternalLink size={14} />{t('settings.emailLinks.guide')}
        </a>
      </div>
    </Section>
  )
}
