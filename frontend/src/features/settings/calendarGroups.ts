// Settings → Calendar & tasks → Calendars and task lists: the calendars grouped by where they come
// from (backend mirror.source_of): the Mailr calendar, each connected account by its name and host,
// calendars shared with the user (group calendars too), each phone's calendars, calendar links.
// The default pickers use the same groups, so two calendars of the same name stay apart.

import { t } from '../../i18n'
import type { Collection, CollectionSource } from '../calendar/types'

export interface CollectionGroup {
  /** Stable per group: "builtin", "account:<connector id>", "shared", "phone:<device>", "link". */
  key: string
  kind: CollectionSource['kind']
  title: string
  /** The account's host, or the phone's name. */
  sub?: string
  items: Collection[]
}

/** The account list the Calendar app already loads (GET /calendar/accounts), for older servers. */
export interface AccountInfo {
  id: number
  name: string
  builtin?: boolean
  url?: string | null
}

const ORDER: CollectionSource['kind'][] = ['builtin', 'account', 'shared', 'phone', 'link']

function hostOf(url: string | null | undefined): string {
  try {
    return url ? new URL(url).host : ''
  } catch {
    return ''
  }
}

/** The calendar's source: the server's, else worked out from the older fields. */
export function sourceOf(c: Collection, accounts: AccountInfo[] = []): CollectionSource {
  if (c.source) return c.source
  if (c.shared) return { kind: 'shared' }
  if (c.phone) return { kind: 'phone', device: c.phone.device }
  if (c.subscription) return { kind: 'link' }
  const a = accounts.find((x) => x.id === c.connector_id)
  if (a?.builtin) return { kind: 'builtin' }
  return { kind: 'account', name: a?.name ?? '', host: hostOf(a?.url) }
}

function keyOf(c: Collection, s: CollectionSource): string {
  if (s.kind === 'account') return `account:${c.connector_id}`
  if (s.kind === 'phone') return `phone:${s.device ?? ''}`
  return s.kind
}

function titleOf(s: CollectionSource): { title: string; sub?: string } {
  switch (s.kind) {
    case 'builtin': return { title: t('settings.calendarTab.mailrCalendar') }
    case 'shared': return { title: t('settings.calendarGroups.sharedWithYou') }
    case 'phone': return { title: t('settings.calendarGroups.fromYourPhone'), sub: s.device || undefined }
    case 'link': return { title: t('settings.calendarGroups.calendarLinks') }
    default: return { title: s.name || s.host || t('common.account'), sub: s.name && s.host ? s.host : undefined }
  }
}

/** The calendars in groups, in this order: the Mailr calendar, the connected accounts (in the
 *  order the server lists them), shared with you, phones, links. Within a group the server's order
 *  stays, and hidden calendars are listed like the others. */
export function groupCollections(cols: Collection[], accounts: AccountInfo[] = []): CollectionGroup[] {
  const groups = new Map<string, CollectionGroup>()
  for (const c of cols) {
    const s = sourceOf(c, accounts)
    const key = keyOf(c, s)
    let g = groups.get(key)
    if (!g) {
      g = { key, kind: s.kind, ...titleOf(s), items: [] }
      groups.set(key, g)
    }
    g.items.push(c)
  }
  const all = [...groups.values()]
  return all
    .map((g, i) => ({ g, i }))
    .sort((a, b) => ORDER.indexOf(a.g.kind) - ORDER.indexOf(b.g.kind) || a.i - b.i)
    .map(({ g }) => g)
}

/** A group's name in one line ("Studio · cal.example.org"), for a picker's option group. */
export function groupLabel(g: CollectionGroup): string {
  return g.sub ? `${g.title} · ${g.sub}` : g.title
}
