// The friendly "bringing in your mail" indicator (ImportProgress.tsx): what the mail accounts'
// import progress adds up to. Pure, so it is tested on its own (importProgress.test.ts).

import type { Connector } from '../../api/types'
import { t, appLocale } from '../../i18n'

const MAIL_KINDS = new Set(['imap', 'gmail', 'm365', 'file'])

export interface ImportSummary {
  /** Something is still coming in. */
  active: boolean
  /** recent: the first import (the newest mail); older: the background import of older mail. */
  phase: 'recent' | 'older' | null
  imported: number
  /** null while the older mail is still being counted. */
  total: number | null
  /** 0–1, or null when there is no count to go by. */
  fraction: number | null
  accounts: string[]
}

export function importSummary(conns: Connector[] | undefined): ImportSummary {
  const mail = (conns ?? []).filter((c) => MAIL_KINDS.has(c.kind) && c.enabled)
  const first = mail.filter((c) => c.progress && !c.progress.initial_done && c.status !== 'error')
  if (first.length) {
    return { active: true, phase: 'recent', imported: 0, total: null, fraction: null, accounts: first.map((c) => c.name) }
  }
  const older = mail.filter((c) => c.progress?.backfill && !c.progress.backfill.done)
  if (!older.length) return { active: false, phase: null, imported: 0, total: null, fraction: null, accounts: [] }
  const counted = older.every((c) => c.progress!.backfill!.total != null)
  const imported = older.reduce((n, c) => n + (c.progress!.backfill!.imported || 0), 0)
  const total = counted ? older.reduce((n, c) => n + (c.progress!.backfill!.total || 0), 0) : null
  return {
    active: true,
    phase: 'older',
    imported,
    total,
    fraction: total ? Math.min(1, imported / total) : null,
    accounts: older.map((c) => c.name),
  }
}

/** The indicator's words. */
export function importText(s: ImportSummary): string {
  if (!s.active) return ''
  if (s.phase === 'recent') return t('onboarding.importProgress.bringingInYourRecentMail')
  if (s.total == null) return t('onboarding.importProgress.bringingInYourOlderMail')
  return t('onboarding.importProgress.bringingInYourOlderMail2', { imported: s.imported.toLocaleString(appLocale()), total: s.total.toLocaleString(appLocale()) })
}
