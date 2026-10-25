// The frame drawn while the session check answers: a top bar, rows and (on phones) the tab bar,
// in the shapes the app will have, so a start shows the app at once instead of a spinner.
// index.html draws the same frame in plain HTML before any script has loaded.
import { RowSkeleton } from '../mail/ConversationRow'
import { t } from '../../i18n'

export function AppSkeleton() {
  return (
    <div className="app-skeleton" aria-busy="true" aria-label={t('pwa.appSkeleton.loadingMailr')}>
      <div className="app-skeleton-top">
        <span className="skeleton" style={{ width: 32, height: 32, borderRadius: '50%' }} />
        <span className="skeleton" style={{ flex: 1, height: 44, borderRadius: 24 }} />
        <span className="skeleton" style={{ width: 32, height: 32, borderRadius: '50%' }} />
      </div>
      <div className="app-skeleton-body">
        <RowSkeleton count={14} />
      </div>
      <div className="app-skeleton-tabs" aria-hidden>
        {Array.from({ length: 5 }).map((_, i) => (
          <span key={i} className="skeleton" style={{ width: 28, height: 28, borderRadius: 8 }} />
        ))}
      </div>
    </div>
  )
}
