// "Learn from my choices": how sure the label model must be before it applies a label by itself.
// Three levels, each a pair of settings (auto_label_precision, auto_label_min_examples; backend
// intel/classify.py).

import type { Settings } from '../../../api/types'
import { t } from '../../../i18n'

export type LearningLevelId = 'cautious' | 'balanced' | 'eager'

export interface LearningLevel {
  id: LearningLevelId
  label: string
  desc: string
  precision: number
  minExamples: number
}

export const LEARNING_LEVELS: LearningLevel[] = [
  { id: 'cautious', label: t('settings.assistant.learningLevels.cautious'), precision: 0.95, minExamples: 50,
    desc: t('settings.assistant.learningLevels.aLabelIsAppliedAutomatically') },
  { id: 'balanced', label: t('settings.assistant.learningLevels.balanced'), precision: 0.9, minExamples: 30,
    desc: t('settings.assistant.learningLevels.aLabelIsAppliedAutomatically2') },
  { id: 'eager', label: t('settings.assistant.learningLevels.eager'), precision: 0.8, minExamples: 15,
    desc: t('settings.assistant.learningLevels.moreLabelsAreAppliedAutomatically') },
]

/** The level the two settings match, or 'custom' when they were set another way (the API). */
export function learningLevel(s: Partial<Settings> | undefined): LearningLevelId | 'custom' {
  const precision = typeof s?.auto_label_precision === 'number' ? s.auto_label_precision : 0.9
  const min = typeof s?.auto_label_min_examples === 'number' ? s.auto_label_min_examples : 30
  const hit = LEARNING_LEVELS.find((l) => Math.abs(l.precision - precision) < 1e-6 && l.minExamples === min)
  return hit ? hit.id : 'custom'
}

/** The settings patch for a level. */
export function levelPatch(id: LearningLevelId): Pick<Settings, 'auto_label_precision' | 'auto_label_min_examples'> {
  const l = LEARNING_LEVELS.find((x) => x.id === id) ?? LEARNING_LEVELS[1]
  return { auto_label_precision: l.precision, auto_label_min_examples: l.minExamples }
}

/** Whether learning is on (auto_label, on by default). */
export function learningOn(s: Partial<Settings> | undefined): boolean {
  return s?.auto_label !== false
}
