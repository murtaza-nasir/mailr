// The label recency setting (backend intel/classify.py `recency`): read with defaults, and the
// half-life choices offered in Settings → Ambrose → Models.

import type { HalfLifeMonths, LabelRecency, Settings } from '../../../api/types'

export const HALF_LIVES: HalfLifeMonths[] = [6, 12, 24]
export const DEFAULT_RECENCY: LabelRecency = { enabled: true, half_life_months: 12 }

export function labelRecency(s: Partial<Settings> | undefined): LabelRecency {
  const r = s?.label_recency
  const hl = HALF_LIVES.includes(r?.half_life_months as HalfLifeMonths) ? r!.half_life_months : DEFAULT_RECENCY.half_life_months
  return { enabled: typeof r?.enabled === 'boolean' ? r.enabled : DEFAULT_RECENCY.enabled, half_life_months: hl }
}

/** How much a label given `months` ago counts now, for the help line. */
export function weightAfter(months: number, halfLife: HalfLifeMonths): number {
  return 0.5 ** (Math.max(0, months) / halfLife)
}
