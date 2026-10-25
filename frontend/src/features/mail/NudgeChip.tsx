// mail.follow_up on list rows: "No reply" when a follow-up reminder brought the conversation back.
// ACT mail.follow_up @web.list

import { BellRing } from 'lucide-react'
import type { ConversationSummary } from '../../api/types'
import { t, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'

function since(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
  return days <= 0 ? 'today' : t('mail.nudgeChip.days', { count: days })
}

export function NudgeChip({ c }: { c: Pick<ConversationSummary, 'nudged_at' | 'follow_up_at'> }) {
  if (c.nudged_at) {
    return <span className="nudge-chip" title={t('mail.nudgeChip.noReplyYetItCame', { since: since(c.nudged_at) })}><BellRing size={12} />{t('mail.nudgeChip.noReply')}</span>
  }
  if (c.follow_up_at) {
    const at = new Date(c.follow_up_at)
    return <span className="nudge-chip pending" title={t('mail.nudgeChip.comesBackIfNobodyReplies', { at: at.toLocaleString(appLocale(), { dateStyle: 'medium', timeStyle: 'short', ...clockOpts() }) })} aria-label={t('mail.nudgeChip.followUpReminderSet')}><BellRing size={12} /></span>
  }
  return null
}
