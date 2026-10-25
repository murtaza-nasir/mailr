// Spam and Trash show no triage signals (backend core/spamtrash.py leaves them out of every
// response). This guard covers rows the client still holds from before a move, so a conversation
// that is all in Spam or Trash never shows Reply needed, Waiting or Due. Its decisions stay stored:
// moved back out, it shows its chips again.

import type { ConversationSummary, Label } from '../../api/types'

type Sig = NonNullable<ConversationSummary['signals']>[number]

/** Every label of the conversation is Spam or Trash. */
export function inSpamOrTrash(labelIds: readonly number[] | undefined, labels: readonly Pick<Label, 'id' | 'role'>[]): boolean {
  if (!labelIds?.length) return false
  const hidden = new Set(labels.filter((l) => l.role === 'junk' || l.role === 'trash').map((l) => l.id))
  return hidden.size > 0 && labelIds.every((id) => hidden.has(id))
}

/** The signals a row or the reader may show. */
export function shownSignals(c: { signals?: Sig[]; label_ids?: number[] }, labels: readonly Pick<Label, 'id' | 'role'>[]): Sig[] {
  return inSpamOrTrash(c.label_ids, labels) ? [] : (c.signals ?? [])
}
