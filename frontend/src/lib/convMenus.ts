// What the conversation menus' submenus offer (features/mail/convMenus.tsx): the searches of
// "Find mail", the filters of "Filter mail like this", and the helpers of "Show in tab" and
// "Category" (the tabs themselves come from the server: GET /layouts/show-in-tab). Pure, so
// vitest covers them.

import type { InboxLayout, Participant } from '../api/types'
import { quoteValue } from './search'
import { t as tr } from '../i18n'

export interface QueryChoice {
  id: 'sender' | 'domain' | 'subject' | 'people' | 'list'
  label: string
  q: string
}

/** Webmail domains: "from @gmail.com" would be half the world, so it is not offered. */
const SHARED_DOMAINS = new Set([
  'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com', 'yahoo.com', 'ymail.com',
  'icloud.com', 'me.com', 'mac.com', 'aol.com', 'proton.me', 'protonmail.com', 'pm.me', 'gmx.com', 'gmx.net',
  'mail.com', 'zoho.com', 'fastmail.com', 'hey.com', 'yandex.com', 'qq.com', '163.com',
])

/** The subject without its Re: / Fwd: prefixes. */
export function bareSubject(s: string): string {
  let t = s.trim()
  for (;;) {
    const next = t.replace(/^\s*(re|fwd?|fw|aw|wg|sv|tr|antw)\s*(\[\d+\])?\s*:\s*/i, '')
    if (next === t) return t
    t = next
  }
}

export function domainOf(email: string): string | null {
  const at = email.lastIndexOf('@')
  if (at < 0) return null
  const d = email.slice(at + 1).trim().toLowerCase()
  return d && !SHARED_DOMAINS.has(d) ? d : null
}

/** The mailing list's id from a List-Id header ("Announcements <announce.example.org>"). */
export function listIdOf(header: string | null | undefined): string | null {
  if (!header) return null
  const m = /<([^>]+)>/.exec(header)
  const id = (m ? m[1] : header).trim()
  return id || null
}

function subjectQuery(subj: string): string {
  return `subject:"${subj.replace(/"/g, '')}"`
}

function senderOf(participants: Participant[]): Participant | null {
  return participants.find((p) => !p.me && p.email) ?? null
}

function others(participants: Participant[]): Participant[] {
  const seen = new Set<string>()
  return participants.filter((p) => {
    const e = p.email?.toLowerCase()
    if (!e || p.me || seen.has(e)) return false
    seen.add(e)
    return true
  })
}

/** "Find mail": from the sender, from the sender's domain, with this subject, with these people. */
export function findChoices(participants: Participant[], subject: string): QueryChoice[] {
  const out: QueryChoice[] = []
  const s = senderOf(participants)
  if (s) {
    out.push({ id: 'sender', label: tr('lib.convMenus.from', { itemName: s.name || s.email }), q: `from:${quoteValue(s.email)}` })
    const d = domainOf(s.email)
    if (d) out.push({ id: 'domain', label: tr('lib.convMenus.from2', { d }), q: `from:@${d}` })
  }
  const subj = bareSubject(subject)
  if (subj) out.push({ id: 'subject', label: tr('lib.convMenus.withThisSubject'), q: subjectQuery(subj) })
  const people = others(participants)
  if (people.length) {
    const list = people.map((p) => p.email).join(',')
    out.push({
      id: 'people',
      label: people.length === 1 ? tr('lib.convMenus.with', { itemName: people[0].name || people[0].email }) : tr('lib.convMenus.withThesePeople'),
      q: `{from:${list} to:${list} cc:${list}}`,
    })
  }
  return out
}

/** "Filter mail like this": from the sender, from the domain, from this list, with this subject. */
export function filterChoices(participants: Participant[], subject: string, listId?: string | null): QueryChoice[] {
  const out: QueryChoice[] = []
  const s = senderOf(participants)
  if (s) {
    out.push({ id: 'sender', label: tr('lib.convMenus.from3', { itemName: s.name || s.email }), q: `from:${quoteValue(s.email)}` })
    const d = domainOf(s.email)
    if (d) out.push({ id: 'domain', label: tr('lib.convMenus.from4', { d }), q: `from:@${d}` })
  }
  const list = listIdOf(listId)
  if (list) out.push({ id: 'list', label: tr('lib.convMenus.fromThisMailingList'), q: `list:${quoteValue(list)}` })
  const subj = bareSubject(subject)
  if (subj) out.push({ id: 'subject', label: tr('lib.convMenus.withThisSubject2'), q: subjectQuery(subj) })
  return out
}

// ---------- Show in tab, Category ----------

/** Category names and icons (core/layouts.py _CAT), in menu order. */
export const CATEGORY_TABS: Record<string, { name: string; icon: string }> = {
  primary: { name: tr('common.primary'), icon: 'inbox' },
  promotions: { name: tr('common.promotions'), icon: 'tag' },
  social: { name: tr('common.social'), icon: 'users' },
  updates: { name: tr('common.updates'), icon: 'info' },
  forums: { name: tr('common.forums'), icon: 'message' },
  purchases: { name: tr('common.purchases'), icon: 'bag' },
}

/** The category every conversation has (no category reads as Primary), or null when they differ:
 *  the Category submenu marks one choice at most. */
export function sharedCategory(items: { category: string | null }[]): string | null {
  if (!items.length) return null
  const cats = new Set(items.map((c) => c.category || 'primary'))
  return cats.size === 1 ? [...cats][0] : null
}

/** "Show in tab" is offered when the layout has more than one tab (one tab leaves no choice). */
export function offersShowInTab(layout: InboxLayout | null): boolean {
  return (layout?.tabs.length ?? 0) > 1
}

/** The toast after Show in tab. */
export function shownInText(name: string, n: number): string {
  return n === 1 ? tr('lib.convMenus.shownIn', { itemName: name }) : tr('lib.convMenus.conversationsShownIn', { count: n, itemName: name })
}
