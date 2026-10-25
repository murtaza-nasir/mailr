import { Mail } from 'lucide-react'
import { convFromSlug } from '../../lib/pubid'
import { useEffect, useRef, type CSSProperties, lazy, Suspense } from 'react'
import { useParams, useSearchParams } from 'react-router'
import { useLabelList } from '../../api/hooks'
import { ErrorBoundary } from '../../components/bits'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { documentTitle } from '../../hooks/useDocumentTitle'
import { viewTitle } from '../../lib/views'
import { LazyConversationView as ConversationView, preloadReader } from '../conversation/LazyConversationView'
import { useInboxCount } from '../layout/inboxCount'
import { useMailAccounts } from '../layout/accounts'
import { inboxNavCounts } from '../../lib/inboxCount'
import { AttachmentsView } from './AttachmentsView'
// Pages of their own (Mail → Screener, Clean up, Subscriptions) load when first opened.
const ScreenerView = lazy(() => import('./ScreenerView').then((m) => ({ default: m.ScreenerView })))
const CleanupView = lazy(() => import('./CleanupView').then((m) => ({ default: m.CleanupView })))
const SubscriptionsView = lazy(() => import('./SubscriptionsView').then((m) => ({ default: m.SubscriptionsView })))
import { MailList } from './MailList'
import { SplitHandle, useSplitSize } from './SplitHandle'
import { useThemeDef, useThemedSettings } from '../../themes/useUITheme'
import { t, appLocale } from '../../i18n'

export function MailPage() {
  const params = useParams()
  const [search] = useSearchParams()
  const view = decodeURIComponent(params.view ?? 'inbox')
  const convId = convFromSlug(params.convId)
  const q = search.get('q') ?? ''
  const full = search.get('full') === '1'
  const settings = useThemedSettings()
  const theme = useThemeDef()
  const labels = useLabelList()
  const { accounts } = useMailAccounts()
  const ic = useInboxCount()
  const inboxUnread = inboxNavCounts(ic.mode, ic.data, labels.find((l) => l.role === 'inbox')).unread
  const mobile = useMediaQuery('(max-width: 900px)')
  const pane = mobile || full ? 'none' : settings.reading_pane
  const splitRef = useRef<HTMLDivElement>(null)
  const split = useSplitSize(theme.id, pane === 'below' ? 'below' : 'right', splitRef)

  useEffect(preloadReader, [])

  useEffect(() => {
    const title = q ? t('common.searchResults') : viewTitle(view, labels, accounts)
    // Unread count of the list's label, as in Gmail's "Inbox (3)"; the Inbox shows the sidebar's
    // number (the Inbox count setting).
    const lb = view.startsWith('label:') ? labels.find((l) => l.id === Number(view.slice(6))) : undefined
    const n = view === 'inbox' ? inboxUnread : lb?.unread
    const unread = !q && n ? ` (${n.toLocaleString(appLocale())})` : ''
    document.title = documentTitle(`${title}${unread}`)
  }, [view, q, labels, accounts, inboxUnread])

  if (view === 'attachments' && !convId) {
    return (
      <ErrorBoundary>
        <AttachmentsView q={q} />
      </ErrorBoundary>
    )
  }
  // Mail → Subscriptions and Mail → Clean up: pages of groups, the same in every theme.
  if (view === 'subscriptions' && !convId) {
    return (
      <ErrorBoundary>
        <Suspense fallback={null}>
          <SubscriptionsView q={q} />
        </Suspense>
      </ErrorBoundary>
    )
  }
  if (view === 'cleanup' && !convId) {
    return (
      <ErrorBoundary>
        <Suspense fallback={null}>
          <CleanupView q={q} />
        </Suspense>
      </ErrorBoundary>
    )
  }

  // ACT screener.review @web.screener: Mail → Screener, the senders waiting (features/mail/screener.ts).
  if (view === 'screener' && !convId) {
    return (
      <ErrorBoundary>
        <Suspense fallback={null}>
          <ScreenerView />
        </Suspense>
      </ErrorBoundary>
    )
  }

  // A shell that draws the reader itself (Office's tabbed reading area) gets the list only.
  if (theme.externalReader && !mobile) {
    return (
      <ErrorBoundary>
        <MailList view={view} q={q} openId={convId} narrow withReader />
      </ErrorBoundary>
    )
  }

  if (pane === 'none') {
    return convId ? (
      <ErrorBoundary key={convId}>
        <ConversationView id={convId} view={view} q={q} />
      </ErrorBoundary>
    ) : (
      <ErrorBoundary>
        <MailList view={view} q={q} openId={null} narrow={mobile} />
      </ErrorBoundary>
    )
  }

  return (
    <div
      ref={splitRef}
      className={`split ${pane}`}
      style={split.size ? ({ [pane === 'below' ? '--list-h' : '--list-w']: `${split.size}px` } as CSSProperties) : undefined}
    >
      <ErrorBoundary>
        <MailList view={view} q={q} openId={convId} narrow={pane === 'right'} withReader />
      </ErrorBoundary>
      <SplitHandle pane={pane === 'below' ? 'below' : 'right'} splitRef={splitRef} size={split.size} onSize={split.set} />
      <div className="reader-pane">
        {convId ? (
          <ErrorBoundary key={convId}>
            <ConversationView id={convId} view={view} q={q} pane />
          </ErrorBoundary>
        ) : (
          <div className="reader-empty">
            <div>
              <Mail size={56} style={{ opacity: 0.35 }} />
              <p>{t('mail.mailPage.noConversationsSelected')}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
