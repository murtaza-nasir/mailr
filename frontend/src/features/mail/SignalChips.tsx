// Triage signals on a list row (backend api/mail_read.py conversation_signals): "Reply needed"
// while the newest message is to you and unanswered, "Waiting" while it is yours and asks
// something, "Due <date>" for a deadline. Searchable as is:needs_reply, is:waiting, has:deadline.
// The Due chip has a menu to say it is not a deadline (DeadlineChip.tsx). Mail in Spam or Trash
// shows none (signalGuard.ts).

import { useLabelList } from '../../api/hooks'
import type { ConversationSummary } from '../../api/types'
import { DeadlineChip, dueText } from './DeadlineChip'
import { shownSignals } from './signalGuard'
import { t } from '../../i18n'

export { dueUrgent } from './DeadlineChip'

export function SignalChips({ c }: { c: Pick<ConversationSummary, 'signals'> & { id?: number; label_ids?: number[] } }) {
  // None in Spam or Trash (signalGuard.ts).
  const sigs = shownSignals(c, useLabelList())
  if (!sigs.length) return null
  return (
    <>
      {sigs.map((s) => {
        if (s.name === 'deadline' && c.id) return <DeadlineChip key={s.name} cid={c.id} sig={s} />
        const text = s.name === 'needs_reply' ? t('mail.signalChips.replyNeeded') : s.name === 'waiting_on' ? t('common.waiting') : s.name === 'deadline' ? dueText(s.date) : s.name
        return <span key={s.name} className={`signal-chip ${s.name}`} title={s.reason || text}>{text}</span>
      })}
    </>
  )
}
