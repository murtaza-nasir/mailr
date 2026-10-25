// Ambrose's brief and the summary above a conversation. Both open, they pushed the
// newest message below the fold of a long thread (y ≈ 835 of 900), and an invitation got a summary
// that restated the invitation card. Now, in a thread of three or more messages, or when both cards
// show, they start folded into one compact strip; an open or closed card the user chose stays so.
// An invitation or a reply to one (an RSVP) gets no summary: its card says it all.
import { hasInvite } from '../../lib/calendarForm'
import type { Message } from '../../api/types'

/** A thread long enough that the cards start folded. */
export const LONG_THREAD = 3

export interface AiCardsInput {
  messageCount: number
  hasBrief: boolean
  /** The newest message (not a draft). */
  newest?: Pick<Message, 'attachments' | 'event_change'>
}

/** Whether the summary card shows at all. */
export function summaryShown({ messageCount, newest }: AiCardsInput): boolean {
  if (messageCount < 2) return false
  if (newest && (hasInvite(newest) || newest.event_change)) return false
  return true
}

/** Whether a card starts open when the user has not chosen (`stored` is the remembered choice). */
export function startsOpen(stored: boolean | null, { messageCount, hasBrief }: AiCardsInput, card: 'brief' | 'summary'): boolean {
  if (stored != null) return stored
  if (messageCount >= LONG_THREAD) return false
  // A short thread: the brief (the answer to the user's own order) open, the summary folded under it.
  return card === 'brief' || !hasBrief
}
