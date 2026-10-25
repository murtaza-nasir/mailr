// What sits where Archive sits on a list row: the row's own state decides, not the list it is in.
// An archived conversation in an Inbox tab (a label
// section beside Inbox sections) used to offer Archive, which then said "Nothing to archive".
//
// `archiveSlot` is used for every theme's row buttons (Classic, Cards, Focus), the swipes, the
// right-click menu and the command bars (Office ribbon, Desk toolbar, Focus palette). The reader has
// the same rule (ConversationView: Archive when in the Inbox or the label list's label applies, Move to
// Inbox when not in the Inbox). Android: MailPlace.archiveSlot.

import { useMemo } from 'react'
import { useLabelList, useSettings } from '../api/hooks'
import type { ConversationSummary, Label } from '../api/types'
import { useUI } from '../stores/ui'
import { baseView } from './views'

/** 'archive': Archive changes something. 'inbox': Move to Inbox in its place. 'keep': Trash or
 *  Spam, whose lists keep their own actions (Restore, Not spam). */
export type ArchiveSlot = 'archive' | 'inbox' | 'keep'

export interface ArchiveContext {
  /** The label whose list this is (a label view, or a layout tab made of one label's sections). */
  contextLabelId: number | null
  /** Settings → Mail → Archive also removes the label of the list (archive_removes_current_label). */
  removesCurrent: boolean
}

type Rows = ReadonlyArray<Pick<ConversationSummary, 'label_ids'>>
type Roles = ReadonlyMap<number, Pick<Label, 'role'>>

const hasRole = (c: Pick<ConversationSummary, 'label_ids'>, labelsById: Roles, ...roles: string[]) =>
  c.label_ids.some((id) => roles.includes(labelsById.get(id)?.role ?? ''))

/** Archive for rows when it would change one of them: a row in the Inbox, or a row that carries
 *  the list's label when Archive also removes it (the Later tab: Archive means done). Otherwise
 *  Move to Inbox. Rows in Trash or Spam (or a Trash or Spam list) keep their own actions. No rows
 *  or no labels (nothing loaded yet): Archive. */
export function archiveSlot(rows: Rows, view: string, labelsById: Roles, ctx: ArchiveContext): ArchiveSlot {
  const v = baseView(view)
  if (v === 'trash' || v === 'spam') return 'keep'
  // Nothing loaded yet (no rows, or no labels to tell the Inbox by): Archive, as before.
  if (!rows.length || ![...labelsById.values()].some((l) => l.role === 'inbox')) return 'archive'
  if (rows.every((c) => hasRole(c, labelsById, 'trash', 'junk'))) return 'keep'
  const ctxLabel = ctx.removesCurrent ? ctx.contextLabelId : null
  return rows.some((c) => hasRole(c, labelsById, 'inbox') || (ctxLabel != null && c.label_ids.includes(ctxLabel)))
    ? 'archive'
    : 'inbox'
}

/** The context for archiveSlot from the store and the settings. */
export function useArchiveContext(): ArchiveContext {
  const contextLabelId = useUI((s) => s.contextLabelId)
  const removesCurrent = useSettings().archive_removes_current_label ?? true
  return { contextLabelId, removesCurrent }
}

/** archiveSlot for rows rendered by a component. */
export function useArchiveSlot(rows: Rows, view: string, labelsById: Roles): ArchiveSlot {
  return archiveSlot(rows, view, labelsById, useArchiveContext())
}

/** The user's labels by id, for components that have no labelsById of their own. */
export function useLabelsById(): Map<number, Label> {
  const labels = useLabelList()
  return useMemo(() => new Map(labels.map((l) => [l.id, l])), [labels])
}

/** The server action the Archive key (`e`, `[` and `]`) runs on these rows: what the row's
 *  button shows (archiveSlot). Move to Inbox on rows outside the Inbox; Archive otherwise (in a
 *  label's own list it also removes that label). */
export function archiveKeyAction(rows: Rows, view: string, labelsById: Roles, ctx: ArchiveContext): 'archive' | 'inbox' {
  return archiveSlot(rows, view, labelsById, ctx) === 'inbox' ? 'inbox' : 'archive'
}
