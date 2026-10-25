// The colour palette: the 12 named swatches for lists and tags. Each has a
// visible name and an accessible label; the hex is what the server stores (COLOR, calendar-color).

import { t } from '../../i18n'

export interface Swatch {
  name: string
  hex: string
}

export const SWATCHES: Swatch[] = [
  { name: t('common.tomato'), hex: '#d50000' },
  { name: t('common.flamingo'), hex: '#e67c73' },
  { name: t('common.tangerine'), hex: '#f4511e' },
  { name: t('common.banana'), hex: '#f6bf26' },
  { name: t('common.sage'), hex: '#33b679' },
  { name: t('common.basil'), hex: '#0b8043' },
  { name: t('common.peacock'), hex: '#039be5' },
  { name: t('common.blueberry'), hex: '#3f51b5' },
  { name: t('common.lavender'), hex: '#7986cb' },
  { name: t('common.grape'), hex: '#8e24aa' },
  { name: t('common.graphite'), hex: '#616161' },
  { name: t('common.cocoa'), hex: '#795548' },
]

/** A stable fallback colour for a list or tag with none (by its name). */
export function autoColor(name: string): string {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return SWATCHES[h % SWATCHES.length].hex
}
