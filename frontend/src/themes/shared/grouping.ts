// Pure list arrangements themes share: date buckets (Office), sender bundles (Cards) and
// column sorting with quick filters (Desk). Each returns entries in display order; MailList
// uses the same order for keyboard navigation.

import type { ConversationSummary } from '../../api/types'
import type { ArrangeContext, ListEntry, ListGroup, SortKey } from '../types'
import { t as tr } from '../../i18n'
import { baseView } from '../../lib/views'

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

/** Date buckets (Today, Yesterday, This week, …) for a timestamp, relative to now. */
export function dateBucket(iso: string | null, now = new Date()): { id: string; title: string } {
  if (!iso) return { id: 'none', title: tr('common.noDate') }
  const t = new Date(iso).getTime()
  const today = startOfDay(now)
  const day = 86_400_000
  if (t >= today) return { id: 'today', title: tr('common.today') }
  if (t >= today - day) return { id: 'yesterday', title: tr('common.yesterday') }
  // Weeks start on Monday.
  const dow = (now.getDay() + 6) % 7
  const weekStart = today - dow * day
  if (t >= weekStart) return { id: 'week', title: tr('themes.shared.grouping.earlierThisWeek') }
  if (t >= weekStart - 7 * day) return { id: 'lastweek', title: tr('themes.shared.grouping.lastWeek') }
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1).getTime()
  if (t >= monthStart) return { id: 'month', title: tr('themes.shared.grouping.earlierThisMonth') }
  const lastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1).getTime()
  if (t >= lastMonth) return { id: 'lastmonth', title: tr('themes.shared.grouping.lastMonth') }
  return { id: 'older', title: tr('common.older') }
}

function groupEntries(
  buckets: { id: string; title: string; items: ConversationSummary[]; collapsible?: boolean; defaultCollapsed?: boolean }[],
  ctx: ArrangeContext,
  prefix: string,
): ListEntry[] {
  const out: ListEntry[] = []
  for (const b of buckets) {
    if (!b.items.length) continue
    const gid = `${prefix}:${b.id}`
    const collapsed = b.collapsible ? (ctx.prefs.collapsed[gid] ?? !!b.defaultCollapsed) : false
    const group: ListGroup = {
      id: gid,
      title: b.title,
      count: b.items.length,
      unread: b.items.filter((c) => c.unread_count > 0).length,
      collapsed,
      collapsible: b.collapsible,
      sample: b.items.slice(0, 4),
    }
    out.push({ kind: 'group', group })
    if (!collapsed) for (const c of b.items) out.push({ kind: 'row', c })
  }
  return out
}

/** Rows under "Today", "Yesterday", "Last week"… headings, newest first. */
export function arrangeByDate(items: ConversationSummary[], ctx: ArrangeContext): ListEntry[] {
  const now = new Date()
  const order: string[] = []
  const map = new Map<string, { id: string; title: string; items: ConversationSummary[]; collapsible: boolean }>()
  for (const c of items) {
    const when = baseView(ctx.view) === 'snoozed' && c.snoozed_until ? null : c.last_date
    const b = baseView(ctx.view) === 'snoozed' ? { id: 'snoozed', title: tr('common.snoozed') } : dateBucket(when, now)
    if (!map.has(b.id)) {
      map.set(b.id, { ...b, items: [], collapsible: true })
      order.push(b.id)
    }
    map.get(b.id)!.items.push(c)
  }
  return groupEntries(order.map((id) => map.get(id)!), ctx, `date:${ctx.view}`)
}

const NOTIFY = /(^|[._-])(no-?reply|do-?not-?reply|notifications?|notify|alerts?|mailer|mailer-daemon|accounts?|security|billing|receipts?|orders?|shipment|shipping|tracking|support|service|updates?|team|info)([._-]|@|$)/i
const NEWS = /(^|[._-])(news(letter)?s?|digest|weekly|offers?|deals|promo(tions)?|marketing|hello|editors?)([._-]|@|$)/i

/** People, notifications or newsletters, from the sender address and the category. */
export function bundleOf(c: ConversationSummary): 'people' | 'notes' | 'notifications' | 'newsletters' {
  const other = c.participants.find((p) => !p.me)
  if (!other && c.participants.length) return 'notes'
  if (!other) return 'people'
  const local = (other?.email ?? '').split('@')[0]
  if (c.category === 'promotions' || NEWS.test(local)) return 'newsletters'
  if (c.category === 'social' || c.category === 'updates' || c.category === 'purchases' || c.category === 'forums' || NOTIFY.test(local))
    return 'notifications'
  return 'people'
}

const BUNDLES = [
  { id: 'people', title: tr('common.people'), folds: false },
  { id: 'notes', title: tr('themes.shared.grouping.notesToSelf'), folds: false },
  { id: 'notifications', title: tr('common.notifications'), folds: true },
  { id: 'newsletters', title: tr('themes.shared.grouping.newsletters'), folds: true },
] as const

/** People first, then notification and newsletter bundles (collapsed by default). */
export function arrangeByBundle(items: ConversationSummary[], ctx: ArrangeContext): ListEntry[] | null {
  // Outgoing views have only you as the sender; a bundle there says nothing.
  if (['sent', 'drafts', 'scheduled'].includes(ctx.view)) return groupEntries([{ id: 'all', title: tr('common.recent'), items }], ctx, `bundle:${ctx.view}`)
  const mode = ctx.bundles ?? 'flat'
  if (mode === 'never' || (mode === 'flat' && ctx.sectioned)) return null
  const by = { people: [] as ConversationSummary[], notes: [] as ConversationSummary[], notifications: [] as ConversationSummary[], newsletters: [] as ConversationSummary[] }
  for (const c of items) by[bundleOf(c)].push(c)
  return groupEntries(
    BUNDLES.map((b) => ({ ...b, items: by[b.id], collapsible: b.folds, defaultCollapsed: b.folds })),
    ctx,
    `bundle:${ctx.view}`,
  )
}

function senderKey(c: ConversationSummary): string {
  const p = c.participants.find((x) => !x.me) ?? c.participants[0]
  return (p?.name || p?.email || '').toLowerCase()
}

const cmp: Record<SortKey, (a: ConversationSummary, b: ConversationSummary, ctx: ArrangeContext) => number> = {
  date: (a, b) => (a.last_date ?? '').localeCompare(b.last_date ?? ''),
  from: (a, b) => senderKey(a).localeCompare(senderKey(b)),
  subject: (a, b) => a.subject.replace(/^(re|fwd?):\s*/i, '').localeCompare(b.subject.replace(/^(re|fwd?):\s*/i, ''), undefined, { sensitivity: 'base' }),
  star: (a, b) => Number(a.starred) - Number(b.starred),
  count: (a, b) => a.message_count - b.message_count,
  attach: (a, b) => Number(a.has_attachments) - Number(b.has_attachments),
  labels: (a, b, ctx) => {
    const name = (c: ConversationSummary) =>
      c.label_ids.map((id) => ctx.labelsById.get(id)).filter((l) => l && !l.role).map((l) => l!.name).sort()[0] ?? '￿'
    return name(a).localeCompare(name(b))
  },
}

/** Table order: quick filters, then the chosen column sort (stable, newest first on ties). */
export function arrangeSorted(items: ConversationSummary[], ctx: ArrangeContext): ListEntry[] {
  const { sort, quick } = ctx.prefs
  let list = items
  if (quick.unread) list = list.filter((c) => c.unread_count > 0)
  if (quick.starred) list = list.filter((c) => c.starred)
  if (quick.attachments) list = list.filter((c) => c.has_attachments)
  if (sort && !(sort.key === 'date' && sort.dir === 'desc')) {
    const f = cmp[sort.key]
    const dir = sort.dir === 'asc' ? 1 : -1
    list = [...list].sort((a, b) => f(a, b, ctx) * dir || (b.last_date ?? '').localeCompare(a.last_date ?? ''))
  }
  return list.map((c) => ({ kind: 'row', c }))
}
