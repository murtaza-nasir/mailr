// In the app while setup is unfinished: a gentle "Continue setup" card (it replaced the old
// "Welcome to mailr. Connect a mail account…" banner), and the import progress while mail is
// still arriving. A brand-new account goes straight into the guide (AppShell).

import { useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Sparkles, X } from 'lucide-react'
import { Link } from 'react-router'
import { api } from '../../api/client'
import { qk, useMe } from '../../api/hooks'
import { toastError } from '../../stores/ui'
import { qkOnboarding } from './api'
import { STEP_ORDER, type Mark, type StepId } from './steps'
import './onboarding.css'
import { t } from '../../i18n'

interface StoredState {
  status?: string
  step?: StepId
  steps?: Partial<Record<StepId, Mark>>
}

/** Steps done so far: the ones marked in the guide, plus what the account already has. The guide
 *  was open (Welcome), and a mail account is connected (Connect your email), whether or not the
 *  guide marked them (a fully set-up account read "0 of 8 steps done"). The last
 *  page, done, is not counted. */
export function progress(st: StoredState | undefined, hasIdentities = false): { done: number; total: number } {
  const done = new Set(Object.keys(st?.steps ?? {}).filter((k) => k !== 'done'))
  if (st?.status === 'active' || st?.status === 'done') done.add('welcome')
  if (hasIdentities) done.add('accounts')
  return { done: done.size, total: STEP_ORDER.length - 1 }
}

/** Whether to show the card, from the progress in /me settings and whether mail accounts exist.
 *  Only a missing mail account keeps it: every other step has a working default (the look, the
 *  inbox style, notifications) or is optional (phone, calendars, About you), and Settings → You →
 *  Setup guide reopens the guide for them. With every step done there is nothing to continue. */
export function setupCardMode(st: StoredState | undefined, hasIdentities: boolean): 'continue' | 'start' | null {
  if (hasIdentities) return null
  const status = st?.status
  if (status === 'active') {
    const { done, total } = progress(st, hasIdentities)
    return done >= total ? null : 'continue'
  }
  if (status === 'pending') return 'start'
  if (!status) return 'start' // an older account with no mail yet
  return null
}

/** Where the card shows: the mail pages only, never in Calendar, Tasks, Notes or Settings. */
export function setupCardPlace(pathname: string): boolean {
  return pathname === '/' || pathname.startsWith('/mail')
}

export function SetupCard() {
  const me = useMe().data!
  const qc = useQueryClient()
  const st = me.settings.onboarding as StoredState | undefined
  const has = me.identities.length > 0
  const mode = setupCardMode(st, has)
  if (!mode) return null
  const { done, total } = progress(st, has)
  const dismiss = () => {
    api
      .put('/onboarding', { status: 'dismissed' })
      .then(() => {
        void qc.invalidateQueries({ queryKey: qk.me })
        void qc.invalidateQueries({ queryKey: qkOnboarding })
      })
      .catch((e) => toastError(e))
  }
  return (
    <div className="ob-setup-card no-print" role="region" aria-label={t('common.setup')} data-testid="setup-card">
      <span className="ob-setup-icon" aria-hidden>
        <Sparkles />
      </span>
      <span className="ob-setup-text">
        <b>{mode === 'continue' ? t('onboarding.setupCard.continueSettingUpMailr') : t('onboarding.setupCard.letsSetUpMailr')}</b>
        <span>
          {mode === 'continue'
            ? t('onboarding.setupCard.ofStepsDonePickUp', { done, count: total })
            : t('onboarding.setupCard.connectYourEmailAndMake')}
        </span>
        {mode === 'continue' && (
          <span className="ob-setup-bar" aria-hidden>
            <span style={{ width: `${Math.round((done / total) * 100)}%` }} />
          </span>
        )}
      </span>
      <Link className="btn filled" to={st?.step && mode === 'continue' ? `/setup/${st.step}` : '/setup'}>
        {mode === 'continue' ? t('onboarding.setupCard.continueSetup') : t('common.start')} <ArrowRight />
      </Link>
      <button type="button" className="icon-btn" aria-label={t('onboarding.setupCard.hideTheSetupReminder')} data-tip={t('onboarding.setupCard.hideSettingsYouSetupGuide')} onClick={dismiss}>
        <X />
      </button>
    </div>
  )
}
