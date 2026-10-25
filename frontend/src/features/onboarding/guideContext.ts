// The setup guide's navigation, shared by its steps (SetupGuide.tsx provides it).

import { createContext, useContext } from 'react'
import type { Mark, OnboardingState, StepId } from './steps'
import { t } from '../../i18n'

export interface Guide {
  state: OnboardingState
  step: StepId
  steps: StepId[]
  /** Mark the current step and go to the next one. */
  next: (mark?: Mark) => void
  back: (() => void) | null
  /** Leave the guide, keeping the progress (the app shows "Continue setup"). */
  later: () => void
  /** Finish the guide and open `to` (the inbox by default). */
  finish: (to?: string) => void
  busy: boolean
}

export const GuideContext = createContext<Guide | null>(null)

export function useGuide(): Guide {
  const g = useContext(GuideContext)
  if (!g) throw new Error(t('onboarding.guideContext.useguideOutsideTheSetupGuide'))
  return g
}

