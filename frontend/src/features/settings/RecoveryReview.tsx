// "Review after signing out" (Settings → You → Password & devices): what Sign out everywhere, a
// password change or an administrator's reset took back because it was set up while the ended
// sessions were signed in (backend core/recovery.py). Shown until dismissed
// (DELETE /me/recovery-review); the list is the server-owned setting `recovery_review`.

import { ShieldAlert } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api } from '../../api/client'
import { qk, useMe } from '../../api/hooks'
import { formatListDate, formatRelative } from '../../lib/format'
import { toastError } from '../../stores/ui'
import { t } from '../../i18n'

export interface RecoveryReviewData {
  at: string
  since: string
  forwarded_notes_reset?: boolean
  paused_rules?: { id: number; action: string; source?: string }[]
  trusted?: { email: string; confirmed_at: string }[]
  forwards?: { email: string; verified_at: string }[]
}

/** The review kept on the account, if any. */
export function recoveryReviewOf(settings: unknown): RecoveryReviewData | null {
  const v = (settings as { recovery_review?: RecoveryReviewData } | null | undefined)?.recovery_review
  return v && typeof v === 'object' && v.at ? v : null
}

export function RecoveryReview() {
  const qc = useQueryClient()
  const review = recoveryReviewOf(useMe().data?.settings)
  const dismiss = useMutation({
    mutationFn: () => api.del('/me/recovery-review'),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.me }),
    onError: (e) => toastError(e),
  })
  if (!review) return null
  const rules = review.paused_rules ?? []
  const trusted = review.trusted ?? []
  const forwards = review.forwards ?? []
  return (
    <div className="card" role="status" data-testid="recovery-review" style={{ padding: 14, marginBottom: 12, maxWidth: 760 }}>
      <div className="row" style={{ gap: 8, alignItems: 'center', marginBottom: 6 }}>
        <ShieldAlert size={18} aria-hidden />
        <strong>{t('settings.recoveryReview.title')}</strong>
        <span className="muted" style={{ fontSize: 12.5 }}>{formatRelative(review.at)}</span>
      </div>
      <p style={{ margin: '0 0 8px' }}>{t('settings.recoveryReview.intro', { when: formatListDate(review.since) })}</p>
      <ul style={{ margin: '0 0 10px', paddingInlineStart: 20 }}>
        {review.forwarded_notes_reset && (
          <li>
            {t('settings.recoveryReview.forwardedNotes', { name: t('settings.assistant.ordersSection.builtinForwardsName') })}{' '}
            <Link to="/settings/ambrose/orders">{t('settings.groups.standingOrdersRoutines')}</Link>
          </li>
        )}
        {rules.length > 0 && (
          <li>
            {t('settings.recoveryReview.pausedRules', { count: rules.length })}{' '}
            <Link to="/settings/agents/agents#permissions">{t('settings.groups.permissions')}</Link>
          </li>
        )}
        {trusted.length > 0 && (
          <li>
            {t('settings.recoveryReview.trusted', { addresses: trusted.map((a) => a.email).join(', ') })}{' '}
            <Link to="/settings/ambrose/forwards">{t('settings.assistant.trustedSection.title')}</Link>
          </li>
        )}
        {forwards.length > 0 && (
          <li>
            {t('settings.recoveryReview.forwards', { addresses: forwards.map((a) => a.email).join(', ') })}{' '}
            <Link to="/settings/mail/filters">{t('settings.groups.filtersAndBlockedAddresses')}</Link>
          </li>
        )}
      </ul>
      <p className="muted" style={{ margin: '0 0 10px', fontSize: 12.5 }}>{t('settings.recoveryReview.notYou')}</p>
      <button type="button" className="btn tonal sm" disabled={dismiss.isPending} onClick={() => dismiss.mutate()}>
        {t('common.dismiss')}
      </button>
    </div>
  )
}
