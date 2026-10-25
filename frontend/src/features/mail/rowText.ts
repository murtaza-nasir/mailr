import type { ConversationSummary, InboxLayout, Label, LayoutTab } from '../../api/types'
import { formatListDate, formatParticipants } from '../../lib/format'
import { t as tr } from '../../i18n'
import { baseView, viewLabelOf } from '../../lib/views'

const norm = (s: string) => s.replace(/\s+/g, ' ').trim()

/**
 * The snippet as a list row shows it. A snippet that repeats the subject (a screenshot sent to
 * oneself, a note whose first line became the subject, a "Subject - body" header, a subject
 * printed twice) loses that repeat, and one that is only the subject becomes empty. The repeat
 * goes only when the whole subject ends at a sentence or line boundary: the end of the text, a
 * newline, or a separator (. : ; — – - | ·) followed by whitespace. "Your code is 715204"
 * under the subject "Your code" is one sentence and stays whole. Android does the same
 * (ConversationRow.kt rowSnippet).
 */
export function rowSnippet(subject: string, snippet: string): string {
  const raw = (snippet ?? '').trim()
  const s = norm(raw)
  const subj = norm(subject ?? '')
  if (!s || !subj || !s.toLowerCase().startsWith(subj.toLowerCase())) return s
  // Walk the raw snippet past the subject's characters, so a newline right after it still counts.
  let i = 0
  for (const ch of subj) {
    if (/\s/.test(ch)) { while (i < raw.length && /\s/.test(raw[i])) i++ } else i += ch.length
  }
  const rest = raw.slice(i)
  if (!rest.trim()) return ''
  // A subject printed twice ("Title Title body", a heading over the same bold title) loses one copy.
  const again = norm(rest)
  if (/^\s/.test(rest) && again.toLowerCase().startsWith(subj.toLowerCase()) && !/^[\p{L}\p{N}]/u.test(again.slice(subj.length))) {
    return rowSnippet(subject, rest)
  }
  if (/^[^\S\n]*\n/.test(rest)) return norm(rest)
  const sep = /^\s*[.:;—–\-|·]+(?:\s+|$)/.exec(rest)
  return sep ? norm(rest.slice(sep[0].length)) : s
}

/**
 * The people column of a list row: the senders ("Alice, me 3"), or, for a conversation only the user
 * wrote in that went to other people (a sent email without a reply), whom it went to, "To: Dana
 * Whitfield", as in Gmail's Sent list. A note to self keeps "me". Android does the same
 * (ConversationRow.kt rowSender). The message count follows as in formatParticipants.
 */
export function rowSender(c: Pick<ConversationSummary, 'participants' | 'message_count' | 'draft_count'>
  & Partial<Pick<ConversationSummary, 'to_self' | 'recipients'>>): string {
  const own = c.participants.length > 0 && c.participants.every((p) => p.me)
  const to = (c.recipients ?? []).filter((p) => p.email || p.name)
  if (!own || c.to_self || to.length === 0) return formatParticipants(c.participants, c.message_count, c.draft_count)
  const names = formatParticipants(to.map((p) => ({ ...p, me: false })), 1)
  return tr('mail.rowText.toRecipients', { value: names }) + (c.message_count > 1 ? ` ${c.message_count}` : '')
}

/**
 * What a screen reader says for a list row: "Unread, Jordan Lee, Invitation:
 * Bluefin kickoff, 2 messages, 10:00 AM". The row's own text ran the count into the name
 * ("Jordan Lee2"), never said unread, and started with the names of the row's three buttons.
 */
export function rowAccessibleName(c: Pick<ConversationSummary, 'participants' | 'message_count' | 'draft_count' | 'to_self' | 'recipients' | 'unread_count' | 'starred' | 'subject' | 'last_date' | 'snoozed_until'>, view: string): string {
  const who = rowSender({ ...c, message_count: 1 })
  const date = baseView(view) === 'snoozed' && c.snoozed_until ? formatListDate(c.snoozed_until) : formatListDate(c.last_date)
  return [
    c.unread_count > 0 ? tr('common.unread') : null,
    c.starred ? tr('common.starred') : null,
    who || null,
    c.subject || tr('common.noSubject'),
    c.message_count > 1 ? tr('mail.rowText.messages', { count: c.message_count }) : null,
    c.draft_count > 0 ? tr('common.draft') : null,
    date,
  ].filter(Boolean).join(', ')
}

/**
 * The label a list is about, whose chip would only repeat the view: the context label (a label's
 * view, or a tab built on one label), else a label named like the open tab ("Later").
 */
export function viewLabelId(view: string, contextLabel: number | null, tab: LayoutTab | null, tabs: number, labels: Label[]): number | undefined {
  if (contextLabel) return contextLabel
  if (viewLabelOf(view) !== null) return viewLabelOf(view) || undefined
  if (!tab || tabs < 2) return undefined
  const name = tab.name.trim().toLowerCase()
  return labels.find((l) => !l.role && (l.name.toLowerCase() === name || l.name.split('/').pop()!.toLowerCase() === name))?.id
}

/**
 * Whether a list's rows sit in inbox sections the user's layout defines, for the theme's own
 * grouping (Cards bundles only lists without sections when card_bundles is "flat"). Every tab
 * of a layout counts, also a tab of one section (a "Later" tab is still the user's own section);
 * only plain views (no layout: labels, folders, search), the built-in Default inbox, whose
 * tabs are the categories, and the built-in Simple inbox (one plain list) are unsectioned.
 */
export function isSectionedLayout(lay: Pick<InboxLayout, 'id' | 'preset'> | null | undefined): boolean {
  if (!lay) return false
  return !(lay.preset && (lay.id === 'default' || lay.id === 'simple'))
}
