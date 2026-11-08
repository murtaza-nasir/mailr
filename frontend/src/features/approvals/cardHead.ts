// The words at the top of an approval card and the grouping of the waiting list (agent/present.py
// card_head): the small type label ("New task"), the heading (the object's own title: the task's or
// event's title, the email's subject), the key facts on one line, the short reason with its whole
// text behind ⓘ, and groups of consecutive requests of one kind from one requester.

import type { Display, DisplayField } from '../rail/assistant/present/types'
import { displayTitle, msgText, type Msg } from '../rail/assistant/present/serverText'
import { SERVER_KEYS } from '../rail/assistant/present/serverKeys'
import { t } from '../../i18n'

/** The server's head of a card (GET /approvals, approval_card). Older servers send none. */
export interface CardHead {
  kind: string
  kind_label: string
  kind_label_msg?: Msg
  name: string | null
  reason_short: string | null
  reason_short_msg?: Msg | null
}

interface HeadSource {
  action: string
  title?: string
  display?: Display | null
  head?: CardHead | null
}

/** The kind of request, for grouping: the server's kind, else the action. */
export function kindOf(a: Pick<HeadSource, 'action' | 'head'>): string {
  return a.head?.kind || a.action
}

/** "New task", "Send email": the card's type label, in this app's language. */
export function kindLabel(a: Pick<HeadSource, 'head'>): string | null {
  const h = a.head
  if (!h) return null
  return msgText(h.kind_label_msg, h.kind_label || '') || null
}

/** The card's heading: the object's own title as it is (no quotes, no question mark), else the card's
 *  title. Never cut: the heading wraps and clamps to two lines on screen, with "more". */
export function headingOf(a: HeadSource): string {
  const name = a.head?.name?.trim()
  if (name) return name
  if (a.display) return displayTitle(a.display)
  return a.title ?? a.action
}

/** Value types short enough to sit in the facts line; the others are drawn below it. */
const INLINE = new Set(['text', 'when', 'count', 'labels', 'people', 'diff'])
const LONG_TEXT = 120

/** The display's fields split into the facts line ("Due Mon 30 Nov, 09:00 · List Calendar") and the
 *  blocks below it (an email's text, a list, a search). A field that only repeats the heading (a
 *  task's "Task", an email's "Subject") is left out of the facts; every other field is kept. */
export function splitFields(fields: DisplayField[], heading: string): { facts: DisplayField[]; blocks: DisplayField[] } {
  const facts: DisplayField[] = []
  const blocks: DisplayField[] = []
  const same = (s: string) => s.trim() === heading.trim()
  for (const f of fields) {
    const v = f.value
    if (v.t === 'text' && same(v.text)) continue
    if (INLINE.has(v.t) && (v.t !== 'text' || v.text.length <= LONG_TEXT) && !v.text.includes('\n')) facts.push(f)
    else blocks.push(f)
  }
  return { facts, blocks }
}

/** The short line of why a card asks ("Asks first: written after reading email") and its whole
 *  text for ⓘ; null when the card gives no reason. */
export function reasonOf(a: HeadSource): { short: string; full: string } | null {
  const d = a.display
  const full = d?.reason ? msgText(d.reason_msg, d.reason) : ''
  const h = a.head
  const short = h?.reason_short ? msgText(h.reason_short_msg, h.reason_short) : ''
  if (!short && !full) return null
  return { short: short || t('serverText.approvals.reason_short.other'), full: full || short }
}

/** "claude-code wants to add 2 tasks": the header of a group, by its kind (approvals.group.*). */
export function groupPhrase(kind: string, who: string, count: number): string {
  const key = SERVER_KEYS[`approvals.group.${kind}`] ?? SERVER_KEYS['approvals.group.request']
  return t(key, { who, count })
}

/** Kinds that never group: a review card and a cleanup plan have their own buttons. */
const ALONE = new Set(['review', 'cleanup'])

export interface Segment<T> {
  key: string
  /** One card, or a group of two or more. */
  items: T[]
  group: boolean
}

/**
 * The list in segments: consecutive waiting requests (in the order shown, newest first) from one
 * requester (`requester`, the backend's grouping for decide-many) of one kind make a group; every
 * other card stands alone. A group's Allow all and Deny all decide exactly its listed requests.
 */
export function segments<T extends { id: number; status: string; action: string; head?: CardHead | null }>(
  shown: T[], requester: (p: T) => string,
): Segment<T>[] {
  const out: Segment<T>[] = []
  let cur: T[] = []
  const flush = () => {
    if (cur.length >= 2) out.push({ key: `g${cur[0].id}`, items: cur, group: true })
    else for (const p of cur) out.push({ key: `c${p.id}`, items: [p], group: false })
    cur = []
  }
  const groupable = (p: T) => p.status === 'pending' && !ALONE.has(kindOf(p))
  for (const p of shown) {
    const prev = cur[cur.length - 1]
    if (prev && groupable(p) && requester(p) === requester(prev) && kindOf(p) === kindOf(prev)) {
      cur.push(p)
      continue
    }
    flush()
    if (groupable(p)) cur = [p]
    else out.push({ key: `c${p.id}`, items: [p], group: false })
  }
  flush()
  return out
}
