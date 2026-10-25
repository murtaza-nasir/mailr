// The frame every step of the setup guide shares: the step's heading and words, its content, and
// the footer (Back, Skip, Continue). The guide (SetupGuide.tsx) provides moving between steps.

import { ArrowLeft, ArrowRight } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { Spinner } from '../../components/bits'
import { useGuide } from './guideContext'
import { t } from '../../i18n'

export interface Primary {
  label: string
  onClick?: () => void
  disabled?: boolean
  busy?: boolean
}

/**
 * One step: an icon, a heading, a lead sentence, the content, and the footer. `primary` replaces
 * the Continue button; `skip` shows "Skip for now" (optional steps, or anything that can wait).
 */
export function StepFrame({
  icon,
  title,
  lead,
  children,
  primary,
  skip = false,
  skipLabel = t('onboarding.guide.skipForNow'),
  skipMark = 'skipped',
  wide = false,
}: {
  icon?: ReactNode
  title: ReactNode
  lead?: ReactNode
  children?: ReactNode
  /** null: no primary button (the step's own form has one until there is something to continue with). */
  primary?: Primary | null
  skip?: boolean
  skipLabel?: string
  /** How the skip button marks the step (done when something is already set up). */
  skipMark?: 'done' | 'skipped'
  wide?: boolean
}) {
  const g = useGuide()
  const head = useRef<HTMLHeadingElement>(null)
  // Each new step moves focus to its heading, so a screen reader announces it.
  useEffect(() => {
    head.current?.focus({ preventScroll: false })
  }, [g.step])
  const p = primary === null ? null : (primary ?? { label: t('common.continue'), onClick: () => g.next('done') })
  return (
    <section className={`ob-card${wide ? ' wide' : ''}`} aria-labelledby="ob-title" data-step={g.step}>
      <header className="ob-head">
        {icon && <span className="ob-icon" aria-hidden>{icon}</span>}
        <h1 id="ob-title" tabIndex={-1} ref={head}>
          {title}
        </h1>
        {lead && <p className="ob-lead">{lead}</p>}
      </header>
      <div className="ob-content">{children}</div>
      <footer className="ob-foot">
        {g.back ? (
          <button type="button" className="btn text ob-back" onClick={g.back} disabled={g.busy} aria-label={t('common.back')}>
            <ArrowLeft /> <span className="ob-back-text">{t('common.back')}</span>
          </button>
        ) : (
          <span />
        )}
        <span className="spacer" />
        {skip && (
          <button type="button" className="btn text ob-skip" onClick={() => g.next(skipMark)} disabled={g.busy}>
            {skipLabel}
          </button>
        )}
        {p && (
          <button type="button" className="btn filled ob-next" onClick={p.onClick ?? (() => g.next('done'))} disabled={p.disabled || p.busy || g.busy}>
            {p.busy ? <Spinner small /> : null}
            {p.label}
            {!p.busy && <ArrowRight />}
          </button>
        )}
      </footer>
    </section>
  )
}

/** A big choice card (radio). */
export function ChoiceCard({
  on,
  onSelect,
  title,
  desc,
  children,
  testId,
  name,
}: {
  on: boolean
  onSelect: () => void
  title: ReactNode
  desc?: ReactNode
  children?: ReactNode
  testId?: string
  name?: string
}) {
  return (
    <button type="button" role="radio" aria-checked={on} aria-label={name} className={`ob-choice${on ? ' on' : ''}`} onClick={onSelect} data-testid={testId}>
      {children && <span className="ob-choice-art">{children}</span>}
      <span className="ob-choice-text">
        <span className="ob-choice-title">{title}</span>
        {desc && <span className="ob-choice-desc">{desc}</span>}
      </span>
      <span className="ob-radio" aria-hidden />
    </button>
  )
}
