// How to put mailr on the home screen (web.install): on an iPhone or iPad the Share → Add to Home
// Screen steps with a picture, in Chrome and Edge the browser's own install dialog, elsewhere the
// browser menu. Nothing shows once mailr runs as the installed app. Reused by the phone banner,
// More, Settings → You → Password & devices → Install the app and the onboarding.
import { Download, MoreVertical, PlusSquare, Share, Smartphone, X } from 'lucide-react'
import { useSyncExternalStore } from 'react'
import { BottomSheet } from '../../components/BottomSheet'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import {
  dismissInstall, installDismissed, installPlatform, iosBrowser, iosVersion, isStandalone, platformInput, promptInstall,
  shouldOfferInstall, useInstall, type InstallPlatform,
} from '../../lib/pwa'
import { toast } from '../../stores/ui'
import { t, tx } from '../../i18n'

function subscribeDisplayMode(cb: () => void) {
  const mq = window.matchMedia('(display-mode: standalone)')
  mq.addEventListener?.('change', cb)
  return () => mq.removeEventListener?.('change', cb)
}

/** True while mailr runs as the installed app. */
export function useStandalone(): boolean {
  const installedNow = useInstall((s) => s.installed)
  const standalone = useSyncExternalStore(subscribeDisplayMode, isStandalone, () => false)
  return standalone || (installedNow && isStandalone())
}

/** How this browser installs, following the prompt event as it arrives. */
export function useInstallPlatform(): InstallPlatform {
  const hasPrompt = useInstall((s) => !!s.prompt)
  return installPlatform({ ...platformInput(), hasPrompt })
}

/** The Share → Add to Home Screen picture: Safari's toolbar, then the share sheet's row. */
function IosIllustration() {
  return (
    <svg className="install-art" viewBox="0 0 320 132" role="img" aria-label={t('pwa.installGuide.safarisShareButtonThenAdd')}>
      {/* the toolbar */}
      <rect x="8" y="8" width="140" height="116" rx="16" className="ia-phone" />
      <rect x="18" y="20" width="120" height="16" rx="8" className="ia-bar" />
      <rect x="18" y="44" width="92" height="6" rx="3" className="ia-line" />
      <rect x="18" y="56" width="108" height="6" rx="3" className="ia-line" />
      <rect x="18" y="68" width="76" height="6" rx="3" className="ia-line" />
      <path d="M8 94h140v14a16 16 0 0 1-16 16H24a16 16 0 0 1-16-16z" className="ia-toolbar" />
      <circle cx="78" cy="109" r="13" className="ia-ring" />
      {/* Share: a square with an arrow up */}
      <path d="M72 106v8h12v-8M78 100v10M74.5 103.5L78 100l3.5 3.5" className="ia-icon" />
      <path d="M152 66h20" className="ia-arrow" markerEnd="url(#ia-head)" />
      <defs>
        <marker id="ia-head" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="8" markerHeight="8" orient="auto">
          <path d="M0 0L8 4L0 8z" className="ia-arrowhead" />
        </marker>
      </defs>
      {/* the share sheet */}
      <rect x="178" y="8" width="134" height="116" rx="16" className="ia-phone" />
      <rect x="188" y="22" width="114" height="22" rx="6" className="ia-row" />
      <rect x="188" y="52" width="114" height="26" rx="6" className="ia-row ia-hit" />
      <rect x="194" y="58" width="14" height="14" rx="3" className="ia-icon" />
      <path d="M201 61v8M197 65h8" className="ia-icon" />
      <text x="212" y="68.5" className="ia-text">{t('pwa.installGuide.addToHomeScreen')}</text>
      <rect x="188" y="86" width="114" height="22" rx="6" className="ia-row" />
    </svg>
  )
}

interface GuideProps {
  /** Show these steps whatever the browser is (onboarding previews, tests). */
  platform?: InstallPlatform
  /** Called after a successful install or when the guide has nothing more to do. */
  onDone?: () => void
}

/** The steps for this device. */
export function InstallGuide({ platform, onDone }: GuideProps) {
  const detected = useInstallPlatform()
  const standalone = useStandalone()
  const kind = platform ?? detected
  const ua = typeof navigator === 'undefined' ? '' : navigator.userAgent

  if (standalone && !platform) {
    return (
      <div className="install-guide" data-install="installed">
        <p className="install-lead">
          <Smartphone aria-hidden />{' '}{t('pwa.installGuide.mailrIsInstalledOnThis')}
        </p>
      </div>
    )
  }

  if (kind === 'ios') {
    const browser = iosBrowser(ua)
    const v = iosVersion(ua)
    const oldOther = browser !== 'safari' && v !== null && v < 16.04
    return (
      <div className="install-guide" data-install="ios">
        <IosIllustration />
        {oldOther ? (
          <p className="install-note">{t('pwa.installGuide.openThisPageInSafari')}</p>
        ) : (
          <ol className="install-steps">
            <li>
              {tx('pwa.installGuide.tapShare', undefined, { b: (c) => <b>{c}</b> })} <Share className="install-glyph" aria-label={t('pwa.installGuide.theShareButton')} />{' '}{tx('pwa.installGuide.inToolbarInNewerVersions', { value: browser === 'safari' ? t('pwa.installGuide.safaris') : t('pwa.installGuide.theBrowsers') }, { b: (c) => <b>{c}</b> })}
            </li>
            <li>
              {tx('pwa.installGuide.scrollDownAndTapAdd', undefined, { b: (c) => <b>{c}</b> })} <PlusSquare className="install-glyph" aria-hidden />.
            </li>
            <li>
              {tx('pwa.installGuide.leaveOpenAsWebApp', undefined, { b: (c) => <b>{c}</b> })}
            </li>
            <li>{t('pwa.installGuide.openMailrFromYourHome')}</li>
          </ol>
        )}
      </div>
    )
  }

  if (kind === 'prompt' || kind === 'menu') {
    return <PromptGuide kind={kind} onDone={onDone} />
  }

  return (
    <div className="install-guide" data-install="none">
      <p className="install-note">
        {t('pwa.installGuide.thisBrowserCannotAddMailr')}
      </p>
    </div>
  )
}

function PromptGuide({ kind, onDone }: { kind: 'prompt' | 'menu'; onDone?: () => void }) {
  const hasPrompt = useInstall((s) => !!s.prompt)
  const install = async () => {
    const r = await promptInstall()
    if (r === 'accepted') {
      toast({ message: t('pwa.installGuide.mailrIsBeingAddedTo') })
      onDone?.()
    }
  }
  return (
    <div className="install-guide" data-install={kind === 'menu' && !hasPrompt ? 'menu' : 'prompt'}>
      {hasPrompt ? (
        <>
          <p className="install-lead">{t('pwa.installGuide.installMailrToOpenIt')}</p>
          <button type="button" className="btn filled" onClick={() => void install()} data-autofocus>
            <Download />{' '}{t('pwa.installGuide.installMailr')}
          </button>
        </>
      ) : (
        <ol className="install-steps">
          <li>
            {t('pwa.installGuide.openTheBrowserMenu')}{' '}<MoreVertical className="install-glyph" aria-label={t('pwa.installGuide.theMenuButton')} />.
          </li>
          <li>
            {tx('pwa.installGuide.chooseInstallAppOrAdd', undefined, { b: (c) => <b>{c}</b> })}
          </li>
          <li>{t('pwa.installGuide.openMailrFromYourHome2')}</li>
        </ol>
      )}
    </div>
  )
}

/** The guide in a bottom sheet, opened from anywhere with useInstall.setGuide(true). */
export function InstallSheet() {
  const open = useInstall((s) => s.guideOpen)
  const setGuide = useInstall((s) => s.setGuide)
  return (
    <BottomSheet open={open} onClose={() => setGuide(false)} title={t('pwa.installGuide.addMailrToYourHome')} testId="install-sheet">
      <InstallGuide onDone={() => setGuide(false)} />
    </BottomSheet>
  )
}

/** The phone banner over the mail list: once, until installed or dismissed (30 days). */
export function InstallBanner() {
  const standalone = useStandalone()
  const narrow = useMediaQuery('(max-width: 900px)')
  const hasPrompt = useInstall((s) => !!s.prompt)
  const setGuide = useInstall((s) => s.setGuide)
  const dismissedNow = useInstall((s) => s.bannerDismissed)
  if (dismissedNow || !shouldOfferInstall({ ...platformInput(), hasPrompt }, standalone, installDismissed(), narrow)) return null
  const close = () => {
    dismissInstall()
    useInstall.setState({ bannerDismissed: true })
  }
  return (
    <div className="banner install-banner no-print" role="region" aria-label={t('pwa.installGuide.installMailr')} data-testid="install-banner">
      <Smartphone />
      <span style={{ flex: 1 }}>
        {tx('pwa.installGuide.useMailrLikeAnApp', undefined, { b: (c) => <b>{c}</b> })}
      </span>
      <button type="button" className="btn tonal sm" onClick={() => setGuide(true)}>
        {t('pwa.installGuide.how')}
      </button>
      <button type="button" className="icon-btn sm" aria-label={t('common.dismiss')} onClick={close}>
        <X />
      </button>
    </div>
  )
}
