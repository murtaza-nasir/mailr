// The Subscriptions and Clean up pages (SubscriptionsView, CleanupView): the server's shapes
// (backend core/mailgroups.py, api/mailgroup_routes.py) and the words the pages use for them.
// Kept free of React so the wording and the query building are unit-tested.

import { formatSize } from '../../lib/format'
import { t, appLocale, dateLocale } from '../../i18n'

export type UnsubMethod = 'one_click' | 'mailto' | 'link' | 'unknown' | 'none' | null

export interface Subscription {
  key: string
  kind: 'list' | 'sender'
  list_id: string | null
  name: string
  email: string
  subject: string
  unsubscribe: UnsubMethod
  total: number
  last_30: number
  last_90: number
  read: number
  open_rate: number
  score: number
  last_received: string | null
  last_read: string | null
  conversations: number
  in_inbox: number
  replied: boolean
  local_only: boolean
  size: number
  kept: boolean
  unsubscribed_at: string | null
  unsubscribed_by: string | null
  auto_archive_rule_id: number | null
  rule_query: string | null
}

export interface SubscriptionPage {
  items: Subscription[]
  total: number
  next_offset: number | null
  unknown: number
}

export type GroupBy = 'sender' | 'domain' | 'list' | 'category'
export type Where = 'archived' | 'inbox' | 'all'

export interface Scope {
  where: Where
  older_than_days: number
  uncategorised: boolean
  bulk_only: boolean
  include_starred: boolean
  include_important: boolean
  include_replied: boolean
}

export const DEFAULT_SCOPE: Scope = {
  where: 'archived',
  older_than_days: 0,
  uncategorised: false,
  bulk_only: false,
  include_starred: false,
  include_important: false,
  include_replied: false,
}

export interface Suggestion {
  category: string
  confidence: number | null
  model: string | null
  samples: number | null
  at: string | null
}

export interface CleanupGroup {
  key: string
  label: string
  conversations: number
  messages: number
  size: number
  unread: number
  protected: number
  in_inbox: number
  uncategorised: number
  oldest: string | null
  newest: string | null
  local_only: boolean
  rule_query: string | null
  reviewed: boolean
  suggestion: Suggestion | null
}

export interface GroupPage {
  by: GroupBy
  items: CleanupGroup[]
  total: number
  next_offset: number | null
}

export interface Sample {
  conversation_id: number
  subject: string
  from_name: string
  from_email: string
  date: string | null
  unread: boolean
  messages: number
}

export interface Preview {
  key: string
  label: string
  conversations: number
  messages: number
  samples: Sample[]
  protected: { starred: number; important: number; replied: number }
  included: { starred: boolean; important: boolean; replied: boolean }
  background: boolean
  undo: boolean
  rule_query: string | null
}

export interface UnsubPlan {
  key: string
  method: 'one_click' | 'mailto' | 'link' | 'refused' | 'none'
  url?: string
  to?: string
  reason?: string
}

export type CleanupAction = 'archive' | 'label' | 'trash' | 'read' | 'category'

export const GROUP_BY: { id: GroupBy; label: string }[] = [
  { id: 'sender', label: t('mail.mailgroups.sender') },
  { id: 'domain', label: t('mail.mailgroups.domain') },
  { id: 'list', label: t('common.list') },
  { id: 'category', label: t('common.category') },
]

export const WHERE: { id: Where; label: string }[] = [
  { id: 'archived', label: t('mail.mailgroups.archivedMail') },
  { id: 'inbox', label: t('common.inbox') },
  { id: 'all', label: t('common.allMail') },
]

export const AGES: { days: number; label: string }[] = [
  { days: 0, label: t('mail.mailgroups.anyAge') },
  { days: 30, label: t('mail.mailgroups.olderThanAMonth') },
  { days: 180, label: t('mail.mailgroups.olderThan6Months') },
  { days: 365, label: t('mail.mailgroups.olderThanAYear') },
  { days: 730, label: t('mail.mailgroups.olderThan2Years') },
  { days: 1825, label: t('mail.mailgroups.olderThan5Years') },
]

export const CATEGORY_NAMES: Record<string, string> = {
  primary: t('common.primary'),
  social: t('common.social'),
  promotions: t('common.promotions'),
  updates: t('common.updates'),
  forums: t('common.forums'),
  purchases: t('common.purchases'),
}

export const SUB_FILTERS: { id: string; label: string }[] = [
  { id: 'all', label: t('common.all') },
  { id: 'review', label: t('mail.mailgroups.toReview') },
  { id: 'never_read', label: t('mail.mailgroups.neverRead') },
  { id: 'auto_archived', label: 'Auto-archived' },
  { id: 'kept', label: t('common.kept') },
  { id: 'unsubscribed', label: t('mail.mailgroups.unsubscribed') },
]

export const SUB_SORTS: { id: string; label: string }[] = [
  { id: 'score', label: t('mail.mailgroups.leastReadFirst') },
  { id: 'volume', label: t('mail.mailgroups.mostMail') },
  { id: 'last', label: t('mail.mailgroups.latestMail') },
  { id: 'open_rate', label: t('mail.mailgroups.lowestReadShare') },
  { id: 'total', label: t('mail.mailgroups.mostMailEver') },
  { id: 'name', label: t('common.name') },
]

export const GROUP_SORTS: { id: string; label: string }[] = [
  { id: 'count', label: t('mail.mailgroups.mostConversations') },
  { id: 'size', label: t('mail.mailgroups.largest') },
  { id: 'oldest', label: t('mail.mailgroups.oldest') },
  { id: 'newest', label: t('mail.mailgroups.newest') },
  { id: 'name', label: t('common.name') },
]

/** How the subscription's newest message unsubscribes, as a short badge. */
export function unsubscribeLabel(m: UnsubMethod): string {
  switch (m) {
    case 'one_click':
      return 'One-click'
    case 'mailto':
      return t('mail.mailgroups.byEmail')
    case 'link':
      return t('mail.mailgroups.webPage')
    case 'unknown':
      return t('mail.mailgroups.checking')
    case 'none':
      return t('mail.mailgroups.notOffered')
    default:
      return t('mail.mailgroups.noLink')
  }
}

/** Whether Unsubscribe can act from mailr (one-click or email); a web page is opened by the user. */
export function canUnsubscribe(m: UnsubMethod): boolean {
  return m === 'one_click' || m === 'mailto' || m === 'link' || m === 'unknown'
}

export function percent(rate: number): string {
  return `${Math.round(Math.max(0, Math.min(1, rate)) * 100)}%`
}

/** The display name of a subscription: the sender's name, else the list id or the address. */
export function subscriptionName(s: Pick<Subscription, 'name' | 'list_id' | 'email'>): string {
  return s.name || s.list_id || s.email
}

/** The line under a subscription's name: the list id or the address. */
export function subscriptionAddress(s: Pick<Subscription, 'kind' | 'list_id' | 'email'>): string {
  return s.kind === 'list' && s.list_id ? t('mail.mailgroups.list', { value: s.email ? `${s.email} · ` : '', listId: s.list_id }) : s.email
}

/** The query string of a cleanup scope: only what differs from the defaults. */
export function scopeQuery(scope: Scope): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {}
  for (const [k, v] of Object.entries(scope) as [keyof Scope, Scope[keyof Scope]][]) {
    if (v !== DEFAULT_SCOPE[k]) out[k] = v
  }
  return out
}

export function groupLabel(by: GroupBy, g: Pick<CleanupGroup, 'key' | 'label'>): string {
  if (by === 'category') return g.key ? (CATEGORY_NAMES[g.key] ?? g.label) : t('mail.mailgroups.uncategorised')
  return g.label || g.key
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString(appLocale())} ${n === 1 ? one : many}`
}

/** "3 starred, 2 important and 5 with your replies", or '' when nothing is left alone. */
export function protectedText(p: Preview['protected']): string {
  const parts = [
    p.starred ? t('mail.mailgroups.starred', { starred: p.starred.toLocaleString(appLocale()) }) : '',
    p.important ? t('mail.mailgroups.important', { important: p.important.toLocaleString(appLocale()) }) : '',
    p.replied ? t('mail.mailgroups.withYourReplies', { replied: p.replied.toLocaleString(appLocale()) }) : '',
  ].filter(Boolean)
  if (parts.length < 2) return parts.join('')
  return t('mail.mailgroups.and', { parts: parts.slice(0, -1).join(', '), parts2: parts[parts.length - 1] })
}

/** What the confirm button says. */
export function actionVerb(action: CleanupAction | 'archive_all' | 'label_sub', extra = ''): string {
  switch (action) {
    case 'archive':
    case 'archive_all':
      return t('common.archive')
    case 'label':
    case 'label_sub':
      return extra ? t('mail.mailgroups.label', { extra }) : t('common.label')
    case 'trash':
      return t('common.moveToTrash')
    case 'read':
      return t('common.markRead')
    case 'category':
      return t('mail.mailgroups.moveTo', { categoryNames: CATEGORY_NAMES[extra] ?? extra })
  }
}

/** The sentences a preview shows above its samples: what changes, what is left alone, and how it runs. */
export function previewLines(p: Pick<Preview, 'conversations' | 'messages' | 'protected' | 'background' | 'undo'>, action: CleanupAction | 'archive_all' | 'label_sub', extra = ''): string[] {
  if (p.conversations === 0) {
    const left = protectedText(p.protected)
    return [left ? t('mail.mailgroups.nothingToChangeLeftAlone', { left }) : t('mail.mailgroups.nothingToChange')]
  }
  const what = `${plural(p.conversations, 'conversation')} (${plural(p.messages, 'message')})`
  const verb: Record<string, string> = {
    archive: t('mail.mailgroups.leaveTheInboxTheyStay', { what }),
    archive_all: t('mail.mailgroups.leaveTheInboxTheyStay', { what }),
    label: t('mail.mailgroups.getTheLabel', { what, extra }),
    label_sub: t('mail.mailgroups.getTheLabel', { what, extra }),
    trash: t('mail.mailgroups.moveToTrashNothingIs', { what }),
    read: t('mail.mailgroups.areMarkedRead', { what }),
    category: t('mail.mailgroups.moveTo2', { what, categoryNames: CATEGORY_NAMES[extra] ?? extra }),
  }
  const out = [verb[action]]
  const left = protectedText(p.protected)
  if (left) out.push(t('mail.mailgroups.leftAlone', { left }))
  if (p.background) out.push(t('mail.mailgroups.theChangeIsMadeIn'))
  out.push(p.undo ? t('mail.mailgroups.youCanUndoItAfterwards') : t('mail.mailgroups.moreThan10000Conversations'))
  return out
}

/** The stats line of a cleanup group. */
export function groupStats(g: Pick<CleanupGroup, 'conversations' | 'messages' | 'size' | 'unread'>): string {
  const parts = [plural(g.conversations, 'conversation'), plural(g.messages, 'message'), formatSize(g.size)]
  if (g.unread) parts.push(t('mail.mailgroups.unread', { unread: g.unread.toLocaleString(appLocale()) }))
  return parts.join(' · ')
}

/** "2019 – 2024", "Mar 2024", or '' from the oldest and newest dates. */
export function dateSpan(oldest: string | null, newest: string | null): string {
  const fmt = (iso: string) => {
    const d = new Date(iso)
    return d.toLocaleDateString(dateLocale('en-US'), { month: 'short', year: 'numeric' })
  }
  if (!oldest && !newest) return ''
  if (!oldest || !newest) return fmt((oldest ?? newest)!)
  const a = fmt(oldest)
  const b = fmt(newest)
  return a === b ? a : `${a} – ${b}`
}

/** The plain sentence of an unsubscribe plan, shown before acting. */
export function unsubscribeText(p: UnsubPlan): string {
  switch (p.method) {
    case 'one_click': {
      let host = p.url ?? ''
      try {
        host = new URL(p.url ?? '').host
      } catch {
        // keep the raw text
      }
      return t('mail.mailgroups.aOneClickUnsubscribeRequest', { host })
    }
    case 'mailto':
      return t('mail.mailgroups.anEmailWithTheSubject', { to: p.to })
    case 'link':
      return t('mail.mailgroups.thisListOnlyOffersA')
    case 'refused':
      return t('mail.mailgroups.notUnsubscribingByEmailTo', { to: p.to, reason: p.reason ?? t('mail.mailgroups.theAddressIsNotThe') })
    default:
      return t('mail.mailgroups.thisSubscriptionOffersNoWay')
  }
}
