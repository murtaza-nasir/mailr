// How to put mailr on a phone: the Android app (download, open with the server filled in) or, on
// an iPhone, the web app added to the Home Screen. The setup guide's phone step and the public
// Get the app page (/get-app) draw it.
//
// The iPhone part is the PWA's own guidance (features/pwa/InstallGuide); the Android part adds the
// app download and the setup link. Callers use `InstallGuide` with InstallGuideProps.

import { Download, NotebookPen, Smartphone } from 'lucide-react'
import type { ComponentType, ReactNode } from 'react'
import { formatSize } from '../../lib/format'
import { InstallGuide as PwaInstallGuide } from '../pwa/InstallGuide'
import { androidSetupLink, appRelease, notesSetupLink, type Phone, type PhoneAppRelease } from './steps'
import { t, tx } from '../../i18n'

export interface InstallGuideProps {
  platform: Phone
  /** The server address the app connects to, e.g. https://mail.example.com. */
  server: string
  /** Where the Android app is downloaded when `apps` does not say (MAILR_ANDROID_APK_URL). */
  apkUrl: string
  /** Both Android apps with their versions (GET /app/releases). */
  apps?: PhoneAppRelease[]
  /** On the phone itself: buttons instead of instructions to scan. */
  onPhone?: boolean
}

function Steps({ items }: { items: ReactNode[] }) {
  return (
    <ol className="ob-steps">
      {items.map((it, i) => (
        <li key={i}>
          <span className="ob-step-num" aria-hidden>{i + 1}</span>
          <span>{it}</span>
        </li>
      ))}
    </ol>
  )
}

/** "Version 0.6.2 · 10 MB" and the SHA-256 to compare, when the server holds the APK. */
export function AppVersion({ app }: { app: PhoneAppRelease }) {
  if (!app.version_name) return null
  return (
    <span className="ob-hint ob-app-version" data-testid={`app-version-${app.app}`}>
      {t('onboarding.installGuide.version', { versionName: app.version_name, value: app.size ? ` · ${formatSize(app.size)}` : '' })}
      {app.sha256 && (
        <details className="ob-sha">
          <summary>{t('onboarding.installGuide.sha256Checksum')}</summary>
          <code>{app.sha256}</code>
        </details>
      )}
    </span>
  )
}

export function AndroidInstall({ server, apkUrl, onPhone, apps }: InstallGuideProps) {
  const mailr = appRelease(apps, 'mailr', apkUrl)
  return (
    <div className="ob-install" data-testid="install-android">
      <Steps
        items={[
          onPhone ? (
            <>
              <a className="btn filled ob-big" href={mailr.url} target="_blank" rel="noreferrer">
                <Download />{' '}{t('onboarding.installGuide.downloadTheMailrApp')}
              </a>
              <AppVersion app={mailr} />
              <span className="ob-hint">{t('onboarding.installGuide.yourPhoneMayAskYou')}</span>
            </>
          ) : (
            <>{t('onboarding.installGuide.pointYourPhonesCameraAt')}</>
          ),
          onPhone ? (
            <>{tx('onboarding.installGuide.openTheDownloadedFileAnd', undefined, { b: (c) => <b>{c}</b> })}</>
          ) : (
            <>
              {tx('onboarding.installGuide.tapDownloadTheMailrApp', undefined, { b: (c) => <b>{c}</b> })}
              <AppVersion app={mailr} />
            </>
          ),
          onPhone ? (
            <>
              <a className="btn tonal ob-big" href={androidSetupLink(server)}>
                <Smartphone />{' '}{t('onboarding.installGuide.openMailrReadyToSign')}
              </a>
              <span className="ob-hint">{t('onboarding.installGuide.thisFillsInTheServer')}</span>
            </>
          ) : (
            <>{tx('onboarding.installGuide.backOnThatPageTap', undefined, { b: (c) => <b>{c}</b> })}</>
          ),
          <>{tx('onboarding.installGuide.tapSignInAndUse', undefined, { b: (c) => <b>{c}</b> })}</>,
          <>{t('onboarding.installGuide.allowNotificationsWhenTheApp')}</>,
        ]}
      />
      <NotesAppOffer server={server} apps={apps} onPhone={onPhone} />
    </div>
  )
}

/** The separate Notes app for Android: notes, checklists and tables with widgets and quick
 *  capture. Optional, so it follows the mailr app's steps. */
export function NotesAppOffer({ server, apps, onPhone }: { server: string; apps?: PhoneAppRelease[]; onPhone?: boolean }) {
  const notes = appRelease(apps, 'notes', 'https://github.com/murtaza-nasir/mailr/releases/latest')
  return (
    <div className="ob-notes-app" data-testid="install-notes-app">
      <h3 className="ob-label">
        <NotebookPen aria-hidden />{' '}{t('onboarding.installGuide.alsoTheNotesAppOptional')}
      </h3>
      <p className="ob-hint">{t('onboarding.installGuide.yourNotesChecklistsAndTables')}</p>
      {onPhone ? (
        <div className="ob-notes-app-actions">
          <a className="btn outlined" href={notes.url} target="_blank" rel="noreferrer">
            <Download />{' '}{t('onboarding.installGuide.downloadMailrNotes')}
          </a>
          <a className="btn text" href={notesSetupLink(server)}>
            {t('onboarding.installGuide.openNotesReadyToSign')}
          </a>
        </div>
      ) : (
        <p className="ob-hint">{t('onboarding.installGuide.thePageTheCodeOpens')}</p>
      )}
      <AppVersion app={notes} />
    </div>
  )
}

/** iPhone and iPad: the PWA's own Add to Home Screen guidance (features/pwa/InstallGuide), with
 *  the address to open and where notifications are turned on. */
export function IosInstall({ server, onPhone }: InstallGuideProps) {
  return (
    <div className="ob-install" data-testid="install-ios">
      {!onPhone && <p className="ob-hint">{t('onboarding.installGuide.pointYourIphonesCameraAt')}</p>}
      <PwaInstallGuide platform="ios" />
      <p className="ob-hint">
        {tx('onboarding.installGuide.signInThereWithThe', { replace: server.replace(/^https?:\/\//, '') }, { b: (c) => <b>{c}</b> })}
      </p>
      <p className="ob-hint" data-testid="ios-notes">
        {tx('onboarding.installGuide.yourNotesChecklistsAndTables2', undefined, { b: (c) => <b>{c}</b> })}
      </p>
    </div>
  )
}

/** The default guidance. */
export const InstallGuide: ComponentType<InstallGuideProps> = (p) => (p.platform === 'android' ? <AndroidInstall {...p} /> : <IosInstall {...p} />)
