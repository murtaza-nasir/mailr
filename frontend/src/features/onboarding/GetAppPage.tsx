// /get-app: the page a phone opens from the setup guide's QR code. No sign-in needed. On Android:
// download the app, then open it with this server filled in (mailr://setup?server=…). On an
// iPhone: add the web app to the Home Screen. On a computer: both, to choose from. Android shows
// both apps (mailr and the Notes app) with the version, size and SHA-256 of the APKs this server
// holds (GET /app/releases, backend core/phone_apps.py).

import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Logo } from '../../components/bits'
import { useAuthConfig } from '../../api/hooks'
import { InstallGuide } from './InstallGuide'
import { detectPhone, type PhoneAppRelease } from './steps'
import './onboarding.css'
import { t } from '../../i18n'

export function GetAppPage() {
  const cfg = useAuthConfig()
  const server = window.location.origin
  const apk = cfg.data?.android_apk_url || 'https://github.com/murtaza-nasir/mailr/releases/latest'
  const phone = detectPhone(navigator.userAgent, navigator.maxTouchPoints ?? 0)
  const rel = useQuery({ queryKey: ['app', 'releases'], queryFn: ({ signal }) => api.get<{ apps: PhoneAppRelease[] }>('/app/releases', undefined, signal), staleTime: 60_000 })
  const apps = rel.data?.apps
  return (
    <div className="ob-page ob-getapp" data-testid="get-app">
      <main className="ob-main">
        <section className="ob-card" aria-labelledby="ga-title">
          <header className="ob-head">
            <span className="ob-icon" aria-hidden>
              <Logo size={36} />
            </span>
            <h1 id="ga-title">{t('onboarding.getAppPage.getMailrOnYourPhone')}</h1>
            <p className="ob-lead">{t('onboarding.getAppPage.yourMailCalendarAndTasks', { replace: server.replace(/^https?:\/\//, '') })}</p>
          </header>
          <div className="ob-content">
            {phone ? (
              <InstallGuide platform={phone} server={server} apkUrl={apk} apps={apps} onPhone />
            ) : (
              <>
                <h2 className="ob-sub">{t('common.android')}</h2>
                <InstallGuide platform="android" server={server} apkUrl={apk} apps={apps} onPhone />
                <h2 className="ob-sub">{t('onboarding.getAppPage.iphone')}</h2>
                <InstallGuide platform="ios" server={server} apkUrl={apk} apps={apps} onPhone />
              </>
            )}
          </div>
        </section>
      </main>
    </div>
  )
}
