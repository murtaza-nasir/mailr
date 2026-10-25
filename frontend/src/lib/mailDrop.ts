// ACT mail.not_spam, ACT mail.untrash: a drop on Inbox from Spam or Trash.
// What a conversation drag does on a nav target. Pure, so every theme and the
// tests share one answer: the verb the target shows and the request it runs.

import type { ActionRequest, Label } from '../api/types'
import { labelLeaf } from './format'
import { baseView, viewLabelOf } from './views'
import { t } from '../i18n'

export interface DropContext {
  /** The view the conversations were dragged from (the route's first segment). */
  from: string
  /** Shift is held: a label moves instead of labelling. */
  shift: boolean
  labels: Label[]
  /** The label of the list dragged from when that list is a label's tab (archive removes it too). */
  contextLabelId?: number | null
}

export interface DropPlan {
  /** Shown beside the highlighted target: "Label: Financial", "Move to Trash". */
  verb: string
  req: Omit<ActionRequest, 'ids'>
  /** The conversations leave the list they were dragged from. */
  removes: boolean
}

const labelFrom = (view: string): number | undefined => viewLabelOf(view) ?? undefined

/**
 * The plan for dropping conversations dragged from `ctx.from` on the nav target `target`, or null
 * when the target takes no drop (no highlight): the view itself, and views that are not places
 * (Sent, Drafts, Snoozed, Scheduled, categories). The requests are the ones the toolbar, the
 * Move to and Label as menus and the keys send.
 */
export function dropPlan(target: string, ctx: DropContext): DropPlan | null {
  const { shift } = ctx
  // An account's own folder acts as the folder it narrows (acct:3:inbox is an Inbox).
  const from = baseView(ctx.from)
  if (target === ctx.from || target === from || baseView(target) === from) return null
  // A label seen through an account (acct:3:label:7) files into the same label: it is one label.
  const targetLabel = viewLabelOf(target)
  if (targetLabel !== null) {
    const id = targetLabel
    const label = ctx.labels.find((l) => l.id === id && !l.role)
    if (!label) return null
    const name = labelLeaf(label.name)
    if (shift) {
      // ACT mail.move @web.nav: the Move to menu's label item (adds the label, takes it out of this list or the Inbox).
      const fromId = labelFrom(from)
      return { verb: t('lib.mailDrop.moveTo', { itemName: name }), req: { action: 'move', label_id: id, ...(fromId ? { from_label_id: fromId } : {}) }, removes: true }
    }
    // ACT mail.label @web.nav: the Label as menu with one label ticked; the mail stays where it is.
    return { verb: t('lib.mailDrop.label', { itemName: name }), req: { action: 'add_labels', label_ids: [id] }, removes: false }
  }
  switch (target) {
    case 'inbox':
      // ACT mail.move: Move to → Inbox (out of Trash or Spam it is a restore).
      return {
        verb: t('common.moveToInbox'),
        req: { action: from === 'trash' ? 'untrash' : from === 'spam' ? 'not_spam' : 'inbox' },
        removes: from !== 'all' && from !== 'starred' && from !== 'important' && !from.startsWith('label:'),
      }
    case 'all': {
      // ACT mail.archive @web.nav: the Archive button. Trash and Spam have Restore and Not spam instead.
      if (from === 'trash' || from === 'spam') return null
      const ctxLabel = ctx.contextLabelId ?? null
      return { verb: t('common.archive'), req: { action: 'archive', ...(ctxLabel ? { from_label_id: ctxLabel } : {}) }, removes: true }
    }
    case 'spam':
      // ACT mail.spam @web.nav
      return { verb: t('lib.mailDrop.moveToSpam'), req: { action: 'spam' }, removes: true }
    case 'trash':
      // ACT mail.delete @web.nav
      return { verb: t('common.moveToTrash'), req: { action: 'trash' }, removes: true }
    case 'starred':
      // ACT mail.star @web.nav
      return { verb: t('common.star'), req: { action: 'star' }, removes: false }
    case 'important':
      // ACT mail.important @web.nav
      return { verb: t('common.markAsImportant'), req: { action: 'important' }, removes: false }
    default:
      return null
  }
}

/** The drag image's text: the subject for one conversation, else the count. */
export function dragLabel(subjects: string[], count: number): string {
  if (count === 1) return subjects[0]?.trim() || t('common.noSubject')
  return t('lib.mailDrop.conversations', { count })
}
