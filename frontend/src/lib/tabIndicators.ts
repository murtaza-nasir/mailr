// What an inbox tab shows by its label: unread ("3 new"),
// all ("120") and due ("Due Oct 4 · 2"). The server counts them (GET /layouts/{id}/counts?detail);
// this file turns the counts into indicators, orders them by importance and keeps as many as
// the theme and the space allow.

import type { LayoutTab } from '../api/types'
import { t, appLocale, dateLocale } from '../i18n'

export type TabIndicatorKind = 'unread' | 'all' | 'due'

/** One tab's counts from GET /layouts/{id}/counts?detail=true (only the tab's indicators). */
export interface TabCounts {
  unread?: number
  all?: number
  due_count?: number
  earliest_due?: string | null
  earliest_due_cid?: number | null
  earliest_due_from?: string
  earliest_due_subject?: string
}

/**
 * How many indicators a tab strip has room for. "full": up to two pills (Classic, Cards,
 * Office, Focus). "compact": one, as a small number or a coloured dot (Desk). "minimal": one,
 * the most important (any theme at phone width).
 */
export type IndicatorCapacity = 'full' | 'compact' | 'minimal'

export interface TabIndicator {
  kind: TabIndicatorKind
  /** The pill's text: "3 new", "1,204", "Due Oct 4 · 2", "Overdue". */
  text: string
  /** The text in compact form: the number, or "" for a dot. */
  short: string
  /** urgent: red (due within two days or overdue); warning: a later deadline; accent: unread;
   *  neutral: all. */
  tone: 'urgent' | 'warning' | 'accent' | 'neutral'
  /** The full sentence for the tooltip and the long press. */
  title: string
}

/** Above this the server stops counting a tab's conversations (TOTAL_CAP in mail_read.py). */
const TOTAL_CAP = 200_000
const SHORT_DAY = new Intl.DateTimeFormat(dateLocale('en-US'), { month: 'short', day: 'numeric' })
const LONG_DAY = new Intl.DateTimeFormat(dateLocale('en-GB'), { weekday: 'short', day: 'numeric', month: 'short' })

function fmtAll(n: number): string {
  return n > TOTAL_CAP ? `${TOTAL_CAP.toLocaleString(appLocale())}+` : n.toLocaleString(appLocale())
}

/** Whole days from today to an ISO date (negative when it has passed). */
export function daysUntil(date: string, now = new Date()): number {
  const due = new Date(`${date}T00:00:00`)
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((due.getTime() - today.getTime()) / 86_400_000)
}

/** The tab's indicators in order of importance: an urgent deadline, unread, a later deadline, all. */
export function tabIndicators(tab: Pick<LayoutTab, 'badge'> & Partial<Pick<LayoutTab, 'indicators'>>, c: TabCounts | undefined, now = new Date()): TabIndicator[] {
  const want = indicatorsOf(tab)
  if (!c) return []
  const out: { ind: TabIndicator; rank: number }[] = []
  if (want.includes('due') && (c.due_count ?? 0) > 0 && c.earliest_due) {
    const n = c.due_count!
    const days = daysUntil(c.earliest_due, now)
    const overdue = days < 0
    const urgent = days <= 2
    const more = n > 1 ? ` · ${n}` : ''
    const day = new Date(`${c.earliest_due}T12:00:00`)
    const who = c.earliest_due_from ? ` (${c.earliest_due_from})` : ''
    out.push({
      rank: urgent ? 0 : 2,
      ind: {
        kind: 'due',
        text: `${overdue ? t('lib.tabIndicators.overdue') : t('lib.tabIndicators.due', { format: SHORT_DAY.format(day) })}${more}`,
        short: '',
        tone: urgent ? 'urgent' : 'warning',
        title: t('lib.tabIndicators.dueEarliest', { n, replace: LONG_DAY.format(day).replace(',', ''), value: overdue ? ', overdue' : '', who }),
      },
    })
  }
  if (want.includes('unread') && (c.unread ?? 0) > 0) {
    const n = c.unread!
    const num = n >= 1000 ? '999+' : String(n)
    out.push({ rank: 1, ind: { kind: 'unread', text: t('lib.tabIndicators.new', { num }), short: num, tone: 'accent', title: t('lib.tabIndicators.unreadConversations', { count: n, num }) } })
  }
  if (want.includes('all') && (c.all ?? 0) > 0) {
    const n = c.all!
    out.push({ rank: 3, ind: { kind: 'all', text: fmtAll(n), short: fmtAll(n), tone: 'neutral', title: t('lib.tabIndicators.conversations', { count: n, all: fmtAll(n) }) } })
  }
  return out.sort((a, b) => a.rank - b.rank).map((x) => x.ind)
}

/** Room on a tab strip: how many indicators, and whether they are pills or compact (a small
 *  number, or a coloured dot for a deadline). */
export interface StripRoom {
  max: 1 | 2 | 3
  form: 'pill' | 'compact'
}

/** The room a strip has: the theme's capacity, one pill in a narrow pane, minimal on a phone. */
export function stripRoom(capacity: IndicatorCapacity, narrowPane: boolean, phone: boolean): StripRoom {
  if (phone || capacity === 'minimal') return { max: 1, form: 'compact' }
  if (capacity === 'compact') return { max: 1, form: 'compact' }
  return { max: narrowPane ? 1 : 2, form: 'pill' }
}

/**
 * The room a tab may take when its layout shows more on tabs (InboxLayout.tab_detail) and the
 * strip has spare width: every indicator the tab shows, as pills. In Desk this is one step up
 * from its compact form (its small pills), since three bare numbers side by side ("22 23") do not
 * say which is which. The narrow-pane limit gives way, since the strip measures what fits
 * (fitDetail). A phone keeps its one compact indicator: null.
 */
export function detailRoom(capacity: IndicatorCapacity, phone: boolean): StripRoom | null {
  if (phone || capacity === 'minimal') return null
  return { max: 3, form: 'pill' }
}

/** One tab's width in the strip with its usual indicators and with all of them. */
export interface TabWidths {
  compact: number
  detail: number
}

/**
 * Which tabs show all their indicators. Every tab starts in its usual form; then, from the left,
 * a tab takes its fuller form when the strip still holds every tab at its width. A tab that does
 * not fit stays as it is and a later, smaller one may still fit. The strip never needs to scroll
 * because of this: when the usual forms already overflow, nothing changes. Which tab is selected
 * plays no part, so selecting another tab never resizes the strip (6 Oct 2026).
 * `room` is the strip's inner width (without its side padding), `gap` the space between tabs.
 */
export function fitDetail(room: number, widths: TabWidths[], gap: number): boolean[] {
  const out = widths.map(() => false)
  let used = widths.reduce((sum, w) => sum + w.compact, 0) + gap * Math.max(0, widths.length - 1)
  // Half a pixel of slack for rounding in the measured widths.
  const limit = room + 0.5
  if (used > limit) return out
  for (let i = 0; i < widths.length; i++) {
    const extra = widths[i].detail - widths[i].compact
    if (extra <= 0) out[i] = true
    else if (used + extra <= limit) {
      used += extra
      out[i] = true
    }
  }
  return out
}

/** As many indicators as the room allows, the most important first. */
export function pickIndicators(list: TabIndicator[], room: StripRoom): TabIndicator[] {
  return list.slice(0, room.max)
}

/** A tab's indicators; a tab from before indicators existed has only `badge`. */
export function indicatorsOf(tab: Pick<LayoutTab, 'badge'> & Partial<Pick<LayoutTab, 'indicators'>>): TabIndicatorKind[] {
  if (tab.indicators) return tab.indicators
  return tab.badge && tab.badge !== 'none' ? [tab.badge] : []
}
