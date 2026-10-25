import { setLocalPictures, setSenderPictures } from '../../lib/avatars'
import { resolveDatePrefs, setClockSetting, setUserZone, todayISO } from '../../lib/dates'
import { Plane } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Link, Navigate, Outlet, useLocation, useNavigate } from 'react-router'
import { setCsrf } from '../../api/client'
import { rememberAppPath } from '../../lib/routes'
import { useLiveUpdates } from '../../api/events'
import { useApi, useMe, useSettings, useUpdateSettings } from '../../api/hooks'
import { ErrorBoundary } from '../../components/bits'
import { useApplyTheme } from '../../hooks/useTheme'
import { useCompose } from '../../stores/compose'
import { useUI } from '../../stores/ui'
import { useGlobalShortcuts } from '../shortcuts/useGlobalShortcuts'
import { useChromeAway } from './chromeAway'
import { useThemeDef } from '../../themes/useUITheme'
import { RecordingSheetSlot } from '../speakr/SheetSlot'
import { useOnline } from '../../hooks/useOnline'
import { useBuildWatch } from '../../lib/appUpdate'
import { classifyError, isTransient } from '../../lib/netErrors'
import { useRestoreDrafts } from '../compose/useRestoreDrafts'
import { loginNext } from '../compose/ComposeLink'
import { useMailtoLinks } from '../compose/mailtoLinks'
import { ConnectionBanner, useReconnect } from './ConnectionBanner'
import { ImportProgress } from '../onboarding/ImportProgress'
import { SetupCard, setupCardPlace } from '../onboarding/SetupCard'
import { setAppNavigate } from '../../lib/appNav'
import { InstallBanner, InstallSheet } from '../pwa/InstallGuide'
import { PendingChip, PendingSheet } from '../pwa/PendingChanges'
import { PhoneChrome } from '../pwa/PhoneChrome'
import { Quiet } from '../pwa/Quiet'
import { AppSkeleton } from '../pwa/AppSkeleton'
import { usePwaSync, useResumePlace } from '../pwa/usePwaSync'
import { t, appLocale } from '../../i18n'
import { useLanguageSync } from '../settings/language'

export function AppShell() {
  const me = useMe()
  const location = useLocation()
  const online = useOnline()
  useApplyTheme()
  // A failed session check retries with backoff, whether or not the app is already showing.
  useReconnect(me)

  useEffect(() => {
    if (me.data?.csrf) setCsrf(me.data.csrf)
  }, [me.data?.csrf])

  // The frame draws at once (the skeleton), while the session check answers.
  if (me.isLoading) return <AppSkeleton />
  // Only a session check with nothing to show yet blocks the app. Once the shell is up, a
  // network error, a timeout or a 5xx leaves it in place under a banner (ConnectionBanner).
  if (!me.data) {
    const kind = classifyError(me.error)
    if (kind === 'unauthorized' || kind === 'forbidden') return <Navigate to={`/login?next=${encodeURIComponent(loginNext(location.pathname + location.search))}`} replace />
    return (
      <div className="empty" style={{ height: '100dvh' }}>
        <h3>{online ? t('layout.appShell.cantReachMailr') : t('layout.appShell.youreOffline')}</h3>
        {/* Our own words for what happened; the server's text only under Details (it
            showed "Can't reach Mailr / down Retrying."). */}
        <p>{online ? `${t(kind === 'server' ? 'layout.appShell.theServerHadAProblem' : 'layout.appShell.theServerDidNotAnswer')}${isTransient(me.error) ? t('layout.appShell.retrying') : ''}` : t('layout.appShell.mailrOpensWhenTheConnection')}</p>
        <button type="button" className="btn tonal" onClick={() => void me.refetch()}>
          {t('common.retry')}
        </button>
        {online && !!(me.error as Error | null)?.message && (
          <details className="error-details">
            <summary>{t('layout.appShell.details')}</summary>
            <code>{(me.error as Error).message}</code>
          </details>
        )}
      </div>
    )
  }
  return <Shell />
}

function Shell() {
  const settings = useSettings()
  const me = useMe().data!
  const location = useLocation()
  const setMobileNav = useUI((s) => s.setMobileNav)
  useLiveUpdates(true, settings.notify_assistant !== false)
  useBuildWatch()
  useRestoreDrafts(me.user?.id)
  useGlobalShortcuts()
  useChromeAway()
  usePwaSync(me.user?.id)
  useResumePlace()
  useLanguageSync()
  // mailto: links on the page open mailr's composer (features/compose/mailtoLinks.ts).
  useMailtoLinks()
  // Times shown outside a component (list dates, chips) follow the account's clock. Set while
  // rendering, so the children's first render already has it.
  setClockSetting((settings as { time_format?: string | null }).time_format)
  setUserZone((settings as { timezone?: string | null }).timezone)
  useEffect(() => setSenderPictures(!!settings.sender_pictures), [settings.sender_pictures])
  // Pictures of this server's users (you, your family), by hashed address (lib/avatars.ts).
  const localPictures = useApi<{ keys: Record<string, number> }>(['pictures', 'local'], '/pictures/local', undefined, { staleTime: 5 * 60_000 })
  useEffect(() => {
    if (localPictures.data) setLocalPictures(localPictures.data.keys)
  }, [localPictures.data])

  // Close the mobile drawer on navigation.
  useEffect(() => {
    setMobileNav(false)
  }, [location.pathname, setMobileNav])

  // Remember the place in each app, so its nav entry returns there.
  useEffect(() => rememberAppPath(location.pathname, location.search), [location.pathname, location.search])

  // PWA shortcut / deep link: ?compose=1 opens a compose window once.
  const navigate = useNavigate()
  // Notifications, toasts and the service worker move the router, never reload the page.
  useEffect(() => {
    setAppNavigate((to) => navigate(to))
    return () => setAppNavigate(null)
  }, [navigate])
  const composeHandled = useRef<string | null>(null)
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    if (params.get('compose') !== '1' || composeHandled.current === location.key) return
    composeHandled.current = location.key
    useCompose.getState().open()
    params.delete('compose')
    const qs = params.toString()
    navigate(`${location.pathname}${qs ? `?${qs}` : ''}`, { replace: true })
  }, [location.search, location.pathname, location.key, navigate])

  const theme = useThemeDef()
  const setup = (settings.onboarding as { status?: string } | undefined)?.status

  // The setup guide is a page of its own, without the theme's frame (features/onboarding).
  if (location.pathname.startsWith('/setup')) {
    return (
      <ErrorBoundary key="setup">
        <Outlet />
      </ErrorBoundary>
    )
  }
  // A brand-new account starts in the guide; "Finish later" or any progress makes it a card.
  if (setup === 'pending') return <Navigate to="/setup" replace />

  return (
    <>
    <SkipLink />
    <theme.Shell>
      {setupCardPlace(location.pathname) && <SetupCard />}
      <ImportProgress />
      <ConnectionBanner />
      <Quiet>
        <PendingChip />
        {location.pathname.startsWith('/mail/') && !/^\/mail\/[^/]+\/[^/]+/.test(location.pathname) && <InstallBanner />}
      </Quiet>
      <AwayBanner />
      {/* Keyed on the app (mail, calendar, …): leaving a crashed app clears its error. */}
      <ErrorBoundary key={location.pathname.split('/')[1] ?? ''}>
        <Outlet />
      </ErrorBoundary>
      <RecordingSheetSlot />
      <Quiet>
        <PhoneChrome />
        <InstallSheet />
        <PendingSheet />
      </Quiet>
    </theme.Shell>
    </>
  )
}

/** "Skip to content" (WCAG 2.4.1): the first stop of the Tab key, shown only while focused. It moves
 * the focus past the navigation columns to the page's main region (every theme's <main id="main">). */
function SkipLink() {
  return (
    <a href="#main" className="skip-link" onClick={(e) => {
      const main = document.getElementById('main')
      if (!main) return
      e.preventDefault()
      if (!main.hasAttribute('tabindex')) main.setAttribute('tabindex', '-1')
      main.focus()
    }}>{t('layout.appShell.skipToContent')}</a>
  )
}


/** While the away message is on (Settings → Mail → Writing), say so, with a way to end it now. */
function AwayBanner() {
  const s = useSettings()
  const update = useUpdateSettings()
  const v = s.vacation
  if (!v?.enabled) return null
  // Today in the user's own zone: the UTC date ended the banner hours early or late.
  const today = todayISO(resolveDatePrefs(s as { timezone?: string | null }).zone)
  if ((v.start && today < v.start) || (v.end && today > v.end)) return null
  return (
    <div className="banner no-print" style={{ margin: 12, borderRadius: 12 }}>
      <Plane />
      <span style={{ flex: 1 }}>
        <b>{t('layout.appShell.yourAwayMessageIsOn')}</b>{v.end ? t('layout.appShell.until', { localeDateString: new Date(`${v.end}T12:00:00`).toLocaleDateString(appLocale(), { month: 'short', day: 'numeric' }) }) : ''}.
      </span>
      <Link className="btn text sm" to="/settings/mail/writing#away">{t('common.settings')}</Link>
      <button type="button" className="btn tonal sm" onClick={() => update.mutate({ vacation: { ...v, enabled: false } })}>
        {t('layout.appShell.turnOffNow')}
      </button>
    </div>
  )
}
