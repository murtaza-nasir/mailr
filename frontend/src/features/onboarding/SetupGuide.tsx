// The setup guide (/setup/<step>): one step per screen, a progress bar, Back, Skip and Continue,
// and "Finish later". Progress is saved on the server (PUT /onboarding) at every move, so the guide
// resumes where it was left, on any device. Docs: site/docs/getting-started/your-first-sign-in.md.

import { X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router'
import { Logo, Spinner } from '../../components/bits'
import { toastError } from '../../stores/ui'
import { useOnboarding, useSaveProgress } from './api'
import { GuideContext, type Guide } from './guideContext'
import { STEP_TITLES, isStepId, nextStep, prevStep, progress, resumeStep, visibleSteps, type Mark, type StepId } from './steps'
import { AboutStep } from './steps/About'
import { AccountsStep } from './steps/Accounts'
import { CalendarStep } from './steps/Calendar'
import { DoneStep } from './steps/Done'
import { InboxStyleStep } from './steps/InboxStyle'
import { LookStep } from './steps/Look'
import { NotificationsStep } from './steps/Notifications'
import { PhoneStep } from './steps/Phone'
import { WelcomeStep } from './steps/Welcome'
import './onboarding.css'
import { t } from '../../i18n'

const STEPS: Record<StepId, () => ReactNode> = {
  welcome: () => <WelcomeStep />,
  accounts: () => <AccountsStep />,
  look: () => <LookStep />,
  inbox: () => <InboxStyleStep />,
  phone: () => <PhoneStep />,
  calendar: () => <CalendarStep />,
  notifications: () => <NotificationsStep />,
  about: () => <AboutStep />,
  done: () => <DoneStep />,
}

function Progress({ steps, current }: { steps: StepId[]; current: StepId }) {
  const p = progress(steps, current)
  const counted = steps.filter((s) => s !== 'done')
  return (
    <div className="ob-progress">
      <span className="ob-progress-text" aria-live="polite">
        {current === 'done' ? t('onboarding.setupGuide.allDone') : t('onboarding.setupGuide.stepOf', { index: p.index, count: p.count })}
        <span className="ob-progress-name"> · {STEP_TITLES[current]}</span>
      </span>
      <span
        className="ob-progress-bar"
        role="progressbar"
        aria-label={t('onboarding.setupGuide.setupProgress')}
        aria-valuemin={0}
        aria-valuemax={p.count}
        aria-valuenow={current === 'done' ? p.count : p.index - 1}
      >
        {counted.map((s, i) => (
          <span key={s} className={i < p.index - 1 || current === 'done' ? 'done' : i === p.index - 1 ? 'on' : ''} />
        ))}
      </span>
    </div>
  )
}

export default function SetupGuide() {
  const params = useParams()
  const navigate = useNavigate()
  const q = useOnboarding()
  const save = useSaveProgress()
  const state = q.data
  const steps = useMemo(() => (state ? visibleSteps(state) : []), [state])
  const wanted = params.step
  const step: StepId | null = !state ? null : isStepId(wanted) && steps.includes(wanted) ? wanted : resumeStep(steps, state)

  useEffect(() => {
    document.title = t('onboarding.setupGuide.setUpMailr')
  }, [])

  // The step on screen is the step on record. Opening the guide starts it (pending, or reopened
  // after it was finished or closed): only once, so finishing is not undone while it closes.
  const opened = useRef(false)
  // The step a move already saved (next() sends it with the mark), so it is not sent twice.
  const saved = useRef<StepId | null>(null)
  useEffect(() => {
    if (!state || !step) return
    const reopen = !opened.current && state.status !== 'active'
    opened.current = true
    // The last page counts as finished once it is shown, so leaving it any way ends setup
    // (the in-app card used to say "8 of 8 steps done. Pick up where you left off.").
    const status = step === 'done' ? (state.status !== 'done' ? 'done' as const : undefined) : reopen ? 'active' as const : undefined
    if (status || (state.step !== step && saved.current !== step)) save.mutate({ step, ...(status ? { status } : {}) })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- when the step changes (and once on open)
  }, [step, !!state])

  const go = useCallback((to: StepId) => navigate(`/setup/${to}`), [navigate])

  const finish = useCallback((to = '/mail/inbox') => {
    save.mutate(
      { status: 'done', mark: step && step !== 'done' ? { [step]: 'done' } : undefined },
      { onSuccess: () => navigate(to, { replace: true }), onError: (e) => toastError(e, t('common.notSaved')) },
    )
  }, [save, step, navigate])

  const guide: Guide | null = useMemo(() => {
    if (!state || !step) return null
    const back = prevStep(steps, step)
    return {
      state,
      step,
      steps,
      busy: save.isPending,
      next: (mark: Mark = 'done') => {
        if (step === 'done') return finish()
        const to = nextStep(steps, step)
        saved.current = to
        save.mutate({ step: to, mark: { [step]: mark } }, { onError: (e) => toastError(e, t('common.notSaved')) })
        go(to)
      },
      back: back ? () => go(back) : null,
      later: () => {
        save.mutate({ step, status: 'active' }, { onSettled: () => navigate('/mail/inbox') })
      },
      finish,
    }
  }, [state, step, steps, save, go, finish, navigate])

  if (q.isError) return <Navigate to="/mail/inbox" replace />
  if (!guide || !step) {
    return (
      <div className="ob-page ob-loading">
        <Spinner />
      </div>
    )
  }
  if (wanted !== step) return <Navigate to={`/setup/${step}`} replace />

  return (
    <GuideContext.Provider value={guide}>
      <div className="ob-page" data-testid="setup-guide">
        <div className="ob-top">
          <span className="ob-brand">
            <Logo size={28} />
            <span className="ob-word"><b>Mail</b>r</span>
          </span>
          <Progress steps={steps} current={step} />
          {step !== 'done' && (
            <button type="button" className="btn text ob-later" onClick={guide.later}>
              <X /> <span>{t('onboarding.setupGuide.finishLater')}</span>
            </button>
          )}
        </div>
        <main className="ob-main" key={step}>
          {STEPS[step]()}
        </main>
      </div>
    </GuideContext.Provider>
  )
}
