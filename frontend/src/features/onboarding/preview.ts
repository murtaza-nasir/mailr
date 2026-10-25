// What each inbox style would look like (the Your inbox style step): the person's own newest mail
// when some has arrived, otherwise a representative sample. Pure, tested in preview.test.ts.

import type { InboxStyle } from './steps'
import { t } from '../../i18n'

export interface PreviewRow {
  from: string
  subject: string
  unread: boolean
  important: boolean
  category: string
}

export interface StylePreview {
  /** Tabs across the top (Categories only). */
  tabs: string[]
  groups: { title: string | null; rows: PreviewRow[] }[]
  /** true: the rows are her own mail. */
  own: boolean
}

export const SAMPLE_ROWS: PreviewRow[] = [
  { from: t('onboarding.preview.sam'), subject: t('onboarding.preview.dinnerOnSunday'), unread: true, important: true, category: 'primary' },
  { from: t('onboarding.preview.schoolOffice'), subject: t('onboarding.preview.tripOnFridayPermissionForm'), unread: true, important: true, category: 'primary' },
  { from: t('onboarding.preview.gardenCentre'), subject: t('onboarding.preview.n20OffEverythingThisWeekend'), unread: false, important: false, category: 'promotions' },
  { from: t('onboarding.preview.library'), subject: t('onboarding.preview.yourBooksAreDueNext'), unread: false, important: false, category: 'updates' },
  { from: t('onboarding.preview.neighboursGroup'), subject: t('onboarding.preview.streetPartyPlans'), unread: false, important: false, category: 'social' },
]

const PER_STYLE = 4

export function stylePreview(style: InboxStyle, own: PreviewRow[]): StylePreview {
  const isOwn = own.length > 0
  const rows = isOwn ? own : SAMPLE_ROWS
  if (style === 'important_first') {
    const imp = rows.filter((r) => r.important)
    const rest = rows.filter((r) => !r.important)
    return {
      tabs: [],
      groups: [
        { title: t('common.important'), rows: imp.slice(0, 2) },
        { title: t('onboarding.preview.everythingElse'), rows: rest.slice(0, PER_STYLE - Math.min(2, imp.length)) },
      ].filter((g) => g.rows.length || g.title === t('common.important')),
      own: isOwn,
    }
  }
  if (style === 'default') {
    const primary = rows.filter((r) => !r.category || r.category === 'primary')
    return { tabs: [t('common.primary'), t('common.promotions'), t('common.social'), t('common.updates')], groups: [{ title: null, rows: primary.slice(0, PER_STYLE) }], own: isOwn }
  }
  return { tabs: [], groups: [{ title: null, rows: rows.slice(0, PER_STYLE) }], own: isOwn }
}

/** A conversation summary as a preview row. */
export function toRow(c: { subject: string; unread_count: number; important: boolean; category: string | null; participants: { name?: string | null; email: string; me?: boolean }[] }): PreviewRow {
  const p = c.participants.find((x) => !x.me) ?? c.participants[0]
  return {
    from: (p?.name || p?.email || '').split('@')[0],
    subject: c.subject || t('common.noSubject'),
    unread: c.unread_count > 0,
    important: c.important,
    category: c.category ?? 'primary',
  }
}
