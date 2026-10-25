import { Loader2, X } from 'lucide-react'
import { useNavigate } from 'react-router'
import { t, tx } from '../../i18n'

/** Shown while a draft opened without its version loads; the form is read-only meanwhile. */
export function DraftLoadingNotice() {
  return (
    <div className="compose-notice" role="status" data-testid="draft-loading">
      <Loader2 size={16} style={{ animation: 'spin 0.9s linear infinite' }} />{' '}{t('compose.composeNotices.loadingTheLatestCopyOf')}
    </div>
  )
}

function Dismissable({ text, onDismiss }: { text: string; onDismiss: () => void }) {
  return (
    <div className="compose-notice restored" role="status">
      <span style={{ flex: 1 }}>{text}</span>
      <button type="button" className="icon-btn" aria-label={t('common.dismiss')} data-tip={t('common.dismiss')} onClick={onDismiss}>
        <X />
      </button>
    </div>
  )
}

/** The draft was sent or deleted elsewhere while it was open here. */
export function DraftGoneNotice({ onDismiss }: { onDismiss: () => void }) {
  return <Dismissable text={t('compose.composeNotices.thisDraftWasSentOr')} onDismiss={onDismiss} />
}

/** The form reopened a local copy the server had not confirmed. */
export function DraftRestoredNotice({ onDismiss }: { onDismiss: () => void }) {
  return <Dismissable text={t('compose.composeNotices.restoredUnsentDraftThisText')} onDismiss={onDismiss} />
}

/** The user has no sending address yet. */
export function NoIdentityNotice() {
  const nav = useNavigate()
  return (
    <div className="compose-notice" role="alert">
      {tx('compose.composeNotices.youHaveNoSendingAddress', undefined, { a: (c) => <a href="/settings/mail/accounts" onClick={(e) => { e.preventDefault(); nav('/settings/mail/accounts') }}>{c}</a> })}
    </div>
  )
}
