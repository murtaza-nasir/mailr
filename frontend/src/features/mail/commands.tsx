// ACT mail.archive, ACT mail.delete, ACT mail.delete_forever, ACT mail.untrash, ACT mail.move, ACT mail.mute, ACT mail.spam, ACT mail.not_spam, ACT mail.read, ACT mail.unread, ACT mail.star (the command bars and the palette)
// Mail commands as data: what can be done now to the selection (or the open or focused
// conversation), with labels, icons and shortcuts. Command bars (Office's ribbon, Desk's
// toolbar) and the command palette (Focus) draw these; the actions run through useActions,
// so undo toasts, optimistic updates and SSE refreshes behave as everywhere else.

import {
  AlarmClock, Archive, ArchiveRestore, Forward, FolderInput, Mail, MailOpen, OctagonAlert, Pencil, RefreshCw, Reply, ReplyAll,
  ShieldCheck, Star, StarOff, Tag, Trash2, VolumeX,
} from 'lucide-react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { invalidateMail, qk, useActions } from '../../api/hooks'
import type { ActionName, ConversationDetail, ConversationSummary, Page } from '../../api/types'
import { mailPath, baseView } from '../../lib/views'
import { archiveSlot, useArchiveContext, useLabelsById } from '../../lib/rowArchive'
import { useCompose } from '../../stores/compose'
import { useUI } from '../../stores/ui'
import { useMailRoute } from '../layout/mailboxes'
import { LabelMenu, MoveMenu, SnoozeMenu } from './menus'
import { t } from '../../i18n'

export type CommandGroup = 'new' | 'respond' | 'delete' | 'move' | 'tags' | 'view'

export interface MailCommand {
  id: string
  label: string
  /** Short label for tight bars. */
  short?: string
  icon: ReactNode
  group: CommandGroup
  shortcut?: string
  enabled: boolean
  /** Menu commands (move, label, snooze) open a menu at the element they were run from. */
  menu?: boolean
  run: (anchor?: HTMLElement | null) => void
}

/** Conversation summaries for ids, from the lists and conversations already loaded. */
export function summariesFor(qc: QueryClient, ids: number[]): ConversationSummary[] {
  const want = new Set(ids)
  const found = new Map<number, ConversationSummary>()
  for (const [, data] of qc.getQueriesData<Page<ConversationSummary>>({ queryKey: qk.conversations })) {
    for (const c of data?.items ?? []) if (want.has(c.id) && !found.has(c.id)) found.set(c.id, c)
  }
  for (const id of ids) {
    if (found.has(id)) continue
    const det = qc.getQueryData<ConversationDetail>(qk.conversation(id))
    if (det) found.set(id, det.conversation)
  }
  return ids.map((id) => found.get(id)).filter((c): c is ConversationSummary => !!c)
}

export interface CommandOptions {
  /** Called with the ids an action took out of the current view (e.g. to close their tabs). */
  onRemoved?: (ids: number[]) => void
  /** After removing the open conversation, open the next one (reading-pane shells) or go back to the list. */
  advance?: boolean
}

export function useMailCommands(opts: CommandOptions = {}) {
  const { view, convId, q, isMail } = useMailRoute()
  const nav = useNavigate()
  const qc = useQueryClient()
  const { run } = useActions()
  const selected = useUI((s) => s.selected)
  const focusedId = useUI((s) => s.focusedId)
  const visibleIds = useUI((s) => s.visibleIds)
  // Re-read summaries whenever lists change, so toggles (read/unread, star) stay right.
  useUI((s) => s.visibleMeta)
  const [menu, setMenu] = useState<{ kind: 'label' | 'move' | 'snooze'; anchor: HTMLElement | null } | null>(null)

  const targets = selected.size ? Array.from(selected) : convId ? [convId] : focusedId && visibleIds.includes(focusedId) ? [focusedId] : []
  const items = summariesFor(qc, targets)
  const any = isMail && targets.length > 0
  // With nothing to act on, the toggles show their first state ("Mark as read", "Star").
  const anyUnread = !items.length || items.some((c) => c.unread_count > 0)
  const anyUnstarred = !items.length || items.some((c) => !c.starred)
  const inTrash = baseView(view) === 'trash'
  const inSpam = baseView(view) === 'spam'
  // Archive, or Move to Inbox when none of the targets is in the Inbox (lib/rowArchive.ts).
  const labelsById = useLabelsById()
  const slot = archiveSlot(items, view, labelsById, useArchiveContext())

  const clear = () => useUI.getState().clearSelection()
  const afterRemove = (ids: number[]) => {
    opts.onRemoved?.(ids)
    if (!convId || !ids.includes(convId)) return
    const idx = visibleIds.indexOf(convId)
    const rest = visibleIds.filter((id) => !ids.includes(id))
    const next = opts.advance ? (visibleIds.slice(idx + 1).find((id) => rest.includes(id)) ?? rest[rest.length - 1]) : undefined
    nav(mailPath(view, next ?? null, q || undefined))
  }
  const act = (action: ActionName, removes = false) => {
    const ids = targets
    if (!ids.length) return
    const from = action === 'archive' ? useUI.getState().contextLabelId : null
    void run({ ids, action, ...(from ? { from_label_id: from } : {}) },
      { silent: action === 'star' || action === 'unstar', onDone: () => { clear(); if (removes) afterRemove(ids) } })
  }
  const reply = (mode: 'reply' | 'reply_all' | 'forward') => window.dispatchEvent(new CustomEvent('mailr:reply', { detail: mode }))
  const canRespond = !!convId && selected.size === 0

  const commands: MailCommand[] = [
    { id: 'compose', label: t('common.compose'), icon: <Pencil />, group: 'new', shortcut: 'c', enabled: true, run: () => useCompose.getState().open() },
    { id: 'reply', label: t('common.reply'), icon: <Reply />, group: 'respond', shortcut: 'r', enabled: canRespond, run: () => reply('reply') },
    { id: 'replyAll', label: t('common.replyAll'), icon: <ReplyAll />, group: 'respond', shortcut: 'a', enabled: canRespond, run: () => reply('reply_all') },
    { id: 'forward', label: t('common.forward'), icon: <Forward />, group: 'respond', shortcut: 'f', enabled: canRespond, run: () => reply('forward') },
    inTrash
      ? { id: 'restore', label: t('common.restore'), icon: <ArchiveRestore />, group: 'delete', enabled: any, run: () => act('untrash', true) }
      : slot === 'inbox'
        ? { id: 'archive', label: t('common.moveToInbox'), short: t('common.inbox'), icon: <ArchiveRestore />, group: 'delete', enabled: any, run: () => act('inbox') }
        : { id: 'archive', label: t('common.archive'), icon: <Archive />, group: 'delete', shortcut: 'e', enabled: any && !inSpam, run: () => act('archive', true) },
    {
      id: 'delete',
      label: inTrash ? t('common.deleteForever') : t('common.delete2'),
      icon: <Trash2 />,
      group: 'delete',
      shortcut: '#',
      enabled: any,
      run: () => act(inTrash ? 'delete_forever' : 'trash', true),
    },
    inSpam
      ? { id: 'notSpam', label: t('common.notSpam'), icon: <ShieldCheck />, group: 'delete', shortcut: '!', enabled: any, run: () => act('not_spam', true) }
      : { id: 'spam', label: t('common.reportSpam'), short: t('mail.commands.junk'), icon: <OctagonAlert />, group: 'delete', shortcut: '!', enabled: any && !inTrash, run: () => act('spam', true) },
    { id: 'move', label: t('common.moveToFolder'), short: t('common.move'), icon: <FolderInput />, group: 'move', shortcut: 'v', enabled: any, menu: true, run: (a) => setMenu({ kind: 'move', anchor: a ?? null }) },
    { id: 'label', label: t('common.label'), icon: <Tag />, group: 'move', shortcut: 'l', enabled: any, menu: true, run: (a) => setMenu({ kind: 'label', anchor: a ?? null }) },
    {
      id: 'read',
      label: anyUnread ? t('common.markAsRead') : t('common.markAsUnread'),
      short: anyUnread ? t('common.read') : t('common.unread'),
      icon: anyUnread ? <MailOpen /> : <Mail />,
      group: 'tags',
      shortcut: anyUnread ? 'I' : 'U',
      enabled: any,
      run: () => act(anyUnread ? 'read' : 'unread'),
    },
    {
      id: 'star',
      label: anyUnstarred ? t('common.star') : t('common.removeStar'),
      short: anyUnstarred ? t('common.star') : t('common.unstar'),
      icon: anyUnstarred ? <Star /> : <StarOff />,
      group: 'tags',
      shortcut: 's',
      enabled: any,
      run: () => act(anyUnstarred ? 'star' : 'unstar'),
    },
    { id: 'snooze', label: t('common.snooze'), icon: <AlarmClock />, group: 'tags', shortcut: 'b', enabled: any && !inTrash && !inSpam, menu: true, run: (a) => setMenu({ kind: 'snooze', anchor: a ?? null }) },
    { id: 'mute', label: t('common.mute'), icon: <VolumeX />, group: 'tags', shortcut: 'm', enabled: any, run: () => act('mute', baseView(view) === 'inbox') },
    { id: 'refresh', label: t('common.refresh'), short: t('common.refresh'), icon: <RefreshCw />, group: 'view', enabled: true, run: () => invalidateMail(qc) },
  ]

  const close = () => setMenu(null)
  const menus = (
    <>
      <LabelMenu anchor={menu?.kind === 'label' ? menu.anchor : null} open={menu?.kind === 'label' && !!menu.anchor} onClose={close} ids={targets} items={items} onDone={clear} />
      <MoveMenu anchor={menu?.kind === 'move' ? menu.anchor : null} open={menu?.kind === 'move' && !!menu.anchor} onClose={close} ids={targets} currentView={view} onDone={() => { clear(); afterRemove(targets) }} />
      <SnoozeMenu anchor={menu?.kind === 'snooze' ? menu.anchor : null} open={menu?.kind === 'snooze' && !!menu.anchor} onClose={close} ids={targets} onDone={() => { clear(); afterRemove(targets) }} />
    </>
  )

  const byId = (id: string) => commands.find((c) => c.id === id)
  return { commands, byId, menus, targets }
}
