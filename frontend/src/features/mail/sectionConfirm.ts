// The words of the confirmation a section's bulk actions ask for (SectionHead: Read, Archive,
// Delete and the ⋯ menu; ListToolbar: Mark all as read). One builder, so every theme and every
// entry says the same thing: how many, where, whether mail not loaded on screen is included, and
// whether Undo covers it.

import type { ActionName } from '../../api/types'
import { t, appLocale } from '../../i18n'

export interface ConfirmIn {
  action: ActionName
  /** How many conversations it acts on, from the server (for Read: the unread ones). */
  count: number
  /** The server stopped counting: the list holds at least `count`. */
  capped?: boolean
  /** Where it acts: a section's name ("Promotions"), or the list's name. */
  where: string
  /** How many of the `count` are loaded on screen. */
  loaded: number
  /** 'section': the whole section, every page; 'page': only the rows loaded on screen. */
  scope?: 'section' | 'page'
  /** Over this many there is no Undo (core/tasks.py BULK_UNDO_MAX). */
  undoMax?: number
  /** The target of a Move ("Receipts"), a Snooze ("Mon 5 Oct, 8:00 AM") or a label ("Travel"). */
  detail?: string
}

export interface ConfirmText {
  title: string
  body: string
  confirmLabel: string
  danger: boolean
}

const conv = (n: string, one: boolean) => t('mail.sectionConfirm.conversation', { n, count: one ? 1 : 2 })

/** What the action does, as [title verb phrase, button verb, one sentence of effect]. */
function phrase(a: ActionName, n: string, one: boolean, where: string, detail?: string): [string, string, string] {
  const c = conv(n, one)
  // Grammatical number only: 1 picks the singular sentence ("It moves…"), 2 the plural one.
  const count = one ? 1 : 2
  switch (a) {
    case 'read':
      return [t('mail.sectionConfirm.markInAsRead', { c, where }), t('mail.sectionConfirm.markAsRead', { n }), t('mail.sectionConfirm.markedAsRead', { count })]
    case 'unread':
      return [t('mail.sectionConfirm.markInAsUnread', { c, where }), t('mail.sectionConfirm.markAsUnread', { n }), t('mail.sectionConfirm.markedAsUnread', { count })]
    case 'archive':
      return [t('mail.sectionConfirm.archiveIn', { c, where }), t('mail.sectionConfirm.archive', { n }), t('mail.sectionConfirm.leaveAndStayInAll', { count, where })]
    case 'trash':
      return [t('mail.sectionConfirm.deleteIn', { c, where }), t('mail.sectionConfirm.delete', { n }), t('mail.sectionConfirm.moveToTrash', { count })]
    case 'important':
      return [t('mail.sectionConfirm.markInAsImportant', { c, where }), t('mail.sectionConfirm.markAsImportant', { n }), t('mail.sectionConfirm.markedAsImportant', { count })]
    case 'not_important':
      return [t('mail.sectionConfirm.markInAsNotImportant', { c, where }), t('mail.sectionConfirm.markAsNotImportant', { n }), t('mail.sectionConfirm.markedAsNotImportant', { count })]
    case 'mute':
      return [t('mail.sectionConfirm.muteIn', { c, where }), t('mail.sectionConfirm.mute', { n }), t('mail.sectionConfirm.newRepliesToStayOut', { count })]
    case 'snooze':
      return [t('mail.sectionConfirm.snoozeIn', { c, where }), t('mail.sectionConfirm.snooze', { n }), detail ? t('mail.sectionConfirm.leaveAndComeBackOn', { count, where, when: detail }) : t('mail.sectionConfirm.leaveAndComeBack', { count, where })]
    case 'move':
      return [t('mail.sectionConfirm.moveIn', { c, where, value: detail ? t('mail.sectionConfirm.to', { detail }) : '' }), t('mail.sectionConfirm.move', { n }), t('mail.sectionConfirm.moveTo', { count, detail: detail ?? t('mail.sectionConfirm.theFolderYouPicked') })]
    case 'add_labels':
      return [t('mail.sectionConfirm.labelIn', { c, where }), t('mail.sectionConfirm.label', { n }), t('mail.sectionConfirm.getTheLabel', { count, detail: detail ?? t('mail.sectionConfirm.youPicked') })]
    case 'remove_labels':
      return [t('mail.sectionConfirm.removeALabelFromIn', { c, where }), t('mail.sectionConfirm.removeFrom', { n }), t('mail.sectionConfirm.theLabelComesOff', { count, detail: detail ?? t('mail.sectionConfirm.youPicked') })]
    case 'spam':
      return [t('mail.sectionConfirm.reportInAsSpam', { c, where }), t('mail.sectionConfirm.report', { n }), t('mail.sectionConfirm.moveToSpam', { count })]
    case 'inbox':
      return [t('mail.sectionConfirm.moveInToTheInbox', { c, where }), t('mail.sectionConfirm.move', { n }), t('mail.sectionConfirm.moveToTheInbox', { count })]
    default:
      return [t('mail.sectionConfirm.changeIn', { c, where }), t('mail.sectionConfirm.applyTo', { n }), t('mail.sectionConfirm.changed', { count })]
  }
}

export function sectionConfirmText({ action, count, capped = false, where, loaded, scope = 'section', undoMax = 10_000, detail }: ConfirmIn): ConfirmText {
  if (count === 0)
    return { title: t('mail.sectionConfirm.nothingToChangeIn', { where }), body: t('mail.sectionConfirm.noConversationInNeedsThis', { where }), confirmLabel: 'OK', danger: false }
  const n = `${count.toLocaleString(appLocale())}${capped ? '+' : ''}`
  const one = count === 1 && !capped
  const [title, confirmLabel, effect] = phrase(action, n, one, where, detail)
  let reach: string
  if (scope === 'page') reach = t('mail.sectionConfirm.onlyTheConversationsLoadedOn', { where })
  else if (capped) reach = t('mail.sectionConfirm.holdsAtLeastConversationsEvery', { where, n, count })
  else if (count > loaded) {
    const rest = count - loaded
    reach = t('mail.sectionConfirm.thisCoversAllOfIncluding', { count: rest, where })
  } else reach = t('mail.sectionConfirm.thisCoversAllOfOn', { where, count: one ? 1 : 2 })
  const undo = count > undoMax || (capped && count >= undoMax)
    ? t('mail.sectionConfirm.thisManyCannotBeUndone', { undoMax: undoMax.toLocaleString(appLocale()) })
    : t('mail.sectionConfirm.youCanUndoThisFrom')
  return { title, body: `${effect} ${reach} ${undo}`, confirmLabel, danger: action === 'trash' }
}
