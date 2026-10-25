// The bell's accessible name and tooltip: what is waiting, in a few words.

import { t } from '../../i18n'

export interface WaitingCounts {
  approvals: number
  notSent: number
  reminders: number
  missed: number
}

/** "Waiting for you: 2 approvals, 1 not sent, 3 reminders (1 missed)", or "Nothing waiting". */
export function waitingLabel(c: WaitingCounts): string {
  const parts: string[] = []
  if (c.approvals) parts.push(t('waiting.label.approvals', { count: c.approvals }))
  if (c.notSent) parts.push(t('waiting.label.notSent', { notSent: c.notSent }))
  if (c.reminders) parts.push(`${t('waiting.label.reminders', { count: c.reminders })}${c.missed ? t('waiting.label.missed', { missed: c.missed }) : ''}`)
  return parts.length ? t('waiting.label.waitingForYou', { parts: parts.join(', ') }) : t('waiting.label.nothingWaiting')
}
