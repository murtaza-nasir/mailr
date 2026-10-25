// Mail views: route segment ↔ API `view` param, titles, and the actions each view offers.

import type { Label, MailAccount } from '../api/types'
import { convSlug } from './pubid'
import { t } from '../i18n'

export type SystemView =
  | 'inbox' | 'starred' | 'snoozed' | 'important' | 'sent' | 'drafts' | 'scheduled' | 'all' | 'spam' | 'trash'

export const SYSTEM_VIEWS: { id: SystemView; title: string; role?: Label['role'] }[] = [
  { id: 'inbox', title: t('common.inbox'), role: 'inbox' },
  { id: 'starred', title: t('common.starred') },
  { id: 'snoozed', title: t('common.snoozed') },
  { id: 'important', title: t('common.important') },
  { id: 'sent', title: t('common.sent'), role: 'sent' },
  { id: 'drafts', title: t('common.drafts'), role: 'drafts' },
  { id: 'scheduled', title: t('common.scheduled') },
  { id: 'all', title: t('common.allMail2') },
  { id: 'spam', title: t('common.spam'), role: 'junk' },
  { id: 'trash', title: t('common.trash'), role: 'trash' },
]

export const CATEGORIES: { id: string; title: string }[] = [
  { id: 'primary', title: t('common.primary') },
  { id: 'purchases', title: t('common.purchases') },
  { id: 'social', title: t('common.social') },
  { id: 'updates', title: t('common.updates') },
  { id: 'forums', title: t('common.forums') },
  { id: 'promotions', title: t('common.promotions') },
]

/**
 * A route view id. One of: a SystemView, `label:<id>`, `category:<name>`, `search`, or an
 * account's own folder `acct:<id>:<view>` (Settings → Mail → Inbox → "Show each account
 * separately"; backend core/mailaccounts.py). It is the first path segment under /mail/.
 */
export type ViewId = string

/** The folders an account has of its own (the server's ACCOUNT_VIEWS). */
export const ACCOUNT_VIEWS = ['inbox', 'sent', 'drafts', 'scheduled', 'spam', 'trash'] as const
export type AccountViewBase = (typeof ACCOUNT_VIEWS)[number]

/** The view of one account's own folder. */
export function accountView(accountId: number, base: AccountViewBase): ViewId {
  return `acct:${accountId}:${base}`
}

/** A label seen through one account (its folders below Trash): `acct:<id>:label:<label id>`. The
 *  label is shared by every account; this view holds the mail of that account only. */
export function accountLabelView(accountId: number, labelId: number): ViewId {
  return `acct:${accountId}:label:${labelId}`
}

const ACCOUNT_VIEW_RE = /^acct:(\d+):([a-z]+|label:\d+)$/

/** The account of an account folder view, else null. */
export function viewAccount(view: ViewId): number | null {
  const m = ACCOUNT_VIEW_RE.exec(view)
  return m ? Number(m[1]) : null
}

/** The unified view an account folder narrows (acct:3:inbox → inbox, acct:3:label:7 → label:7);
 *  any other view as it is. What a view offers (archive, Delete forever, the empty state…)
 *  follows this. */
export function baseView(view: ViewId): ViewId {
  const m = ACCOUNT_VIEW_RE.exec(view)
  return m ? m[2] : view
}

/** The label a view lists (label:7, or acct:3:label:7 seen through an account), else null. */
export function viewLabelOf(view: ViewId): number | null {
  const m = /^label:(\d+)$/.exec(baseView(view))
  return m ? Number(m[1]) : null
}

export function isSystemView(v: string): v is SystemView {
  return SYSTEM_VIEWS.some((s) => s.id === v)
}

export function viewTitle(view: ViewId, labels: Label[] = [], accounts: MailAccount[] = []): string {
  const acct = viewAccount(view)
  if (acct !== null) {
    const folder = viewTitle(baseView(view), labels)
    const a = accounts.find((x) => x.id === acct)
    return a ? t('lib.views.accountFolder', { folder, address: a.address }) : folder
  }
  if (isSystemView(view)) return SYSTEM_VIEWS.find((s) => s.id === view)!.title
  if (view.startsWith('label:')) {
    const id = Number(view.slice(6))
    return labels.find((l) => l.id === id)?.name ?? t('common.label')
  }
  if (view.startsWith('category:')) {
    const c = view.slice(9)
    return CATEGORIES.find((x) => x.id === c)?.title ?? c
  }
  if (view === 'search') return t('common.searchResults')
  if (view === 'attachments') return t('common.attachments')
  if (view === 'screener') return t('common.screener')
  if (view === 'subscriptions') return t('common.subscriptions')
  if (view === 'cleanup') return t('common.cleanUp')
  if (view === 'outbox') return t('common.outbox')
  return view
}

/** API `view` param for a route view. Search uses view=all with q. */
export function apiView(view: ViewId): string {
  return view === 'search' ? 'all' : view
}

export function mailPath(view: ViewId, convId?: number | null, q?: string): string {
  const base = `/mail/${encodeURIComponent(view)}`
  const path = convId ? `${base}/${convSlug(convId)}` : base
  return q ? `${path}?q=${encodeURIComponent(q)}` : path
}

export function labelView(id: number): ViewId {
  return `label:${id}`
}

/** Whether "archive" makes sense (conversation leaves the view). */
export function viewRemovesOnArchive(view: ViewId): boolean {
  const v = baseView(view)
  return v === 'inbox' || v.startsWith('category:')
}

export function viewIsTrashOrSpam(view: ViewId): boolean {
  const v = baseView(view)
  return v === 'trash' || v === 'spam'
}

/** User labels sorted by path, with depth for nested display. */
export function labelTree(labels: Label[]): { label: Label; depth: number; hasChildren: boolean; parent: string | null }[] {
  const user = labels.filter((l) => !l.role).sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
  const names = new Set(user.map((l) => l.name))
  return user.map((label) => {
    const parts = label.name.split('/')
    const parent = parts.length > 1 ? parts.slice(0, -1).join('/') : null
    return {
      label,
      depth: parent && names.has(parent) ? parts.length - 1 : 0,
      hasChildren: user.some((o) => o.name.startsWith(`${label.name}/`)),
      parent: parent && names.has(parent) ? parent : null,
    }
  })
}
