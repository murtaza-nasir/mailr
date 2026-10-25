// The overlap layout of timed events (vectors in spec/cases/layout-cases.yaml).
// The Android twin is ui/calendar/EventLayout.kt; both run the same vectors.

import { t } from '../i18n'

export const MIN_MINUTES = 20
export const COMPACT_BELOW = 30
export const MAX_COLUMNS = 4

export type LayoutStyle = 'fill' | 'outline' | 'hatched'

export interface LayoutItem {
  id: string
  /** Minutes from the day's midnight (0..1440). */
  start: number
  end: number
  /** The viewer's participation status; declined and needs-action draw outlined, tentative hatched. */
  partstat?: string | null
  /** A segment that began on an earlier day (draw "↳"). */
  continued?: boolean
}

export interface Placed {
  id: string
  /** Rendered extent in minutes: at least MIN_MINUTES tall, clipped to the day. */
  top: number
  bottom: number
  /** Column, columns across the cluster (at most MAX_COLUMNS), and columns spanned after widening right. */
  col: number
  cols: number
  span: number
  /** Shorter than COMPACT_BELOW minutes: title only, one line. */
  compact: boolean
  style: LayoutStyle
  continued: boolean
}

/** A "+n" chip in the last quarter of a cluster that needs more than MAX_COLUMNS columns. */
export interface OverflowChip {
  ids: string[]
  top: number
  bottom: number
  col: number
  cols: number
}

export interface DayLayout {
  placed: Placed[]
  chips: OverflowChip[]
}

export interface Segment {
  id: string
  /** The local date "2026-10-01". */
  date: string
  start: number
  end: number
  continued: boolean
  continues: boolean
}

function styleOf(partstat?: string | null): LayoutStyle {
  const p = (partstat ?? '').toLowerCase()
  if (p === 'declined' || p === 'needs-action') return 'outline'
  if (p === 'tentative') return 'hatched'
  return 'fill'
}

interface Box { item: LayoutItem; s: number; e: number; top: number; bottom: number; col: number }

/**
 * One day column:
 * 1. events that overlap (directly or through a chain, using their rendered extents) form a cluster;
 * 2. each cluster is packed greedily into columns, by start, then the longer event first, then id;
 * 3. an event widens right over the following columns while none of their events overlaps it;
 * 4. a cluster with more than MAX_COLUMNS columns shows the first three, and the events of the other
 *    columns go into "+n" chips in the fourth quarter (one chip per overlapping group);
 * 5. every block is at least MIN_MINUTES tall.
 */
export function layoutDay(items: LayoutItem[]): DayLayout {
  const boxes: Box[] = items.map((item) => {
    const s = Math.min(1440, Math.max(0, item.start))
    const e = Math.min(1440, Math.max(s, item.end))
    return { item, s, e, top: s, bottom: Math.min(1440, Math.max(e, s + MIN_MINUTES)), col: 0 }
  })
  boxes.sort((a, b) => a.top - b.top || (b.e - b.s) - (a.e - a.s) || (a.item.id < b.item.id ? -1 : a.item.id > b.item.id ? 1 : 0))
  const placed: Placed[] = []
  const chips: OverflowChip[] = []
  let cluster: Box[] = []
  let colEnds: number[] = []
  let clusterBottom = -1

  const flush = () => {
    const cols = colEnds.length
    const overflow = cols > MAX_COLUMNS
    const shown = overflow ? MAX_COLUMNS - 1 : cols
    const overlaps = (a: Box, b: Box) => a.top < b.bottom && b.top < a.bottom
    for (const b of cluster) {
      if (b.col >= shown) continue
      let span = 1
      for (let c = b.col + 1; c < shown; c++) {
        if (cluster.some((o) => o.col === c && overlaps(o, b))) break
        span++
      }
      placed.push({
        id: b.item.id, top: b.top, bottom: b.bottom, col: b.col, cols: overflow ? MAX_COLUMNS : cols, span,
        compact: b.e - b.s < COMPACT_BELOW, style: styleOf(b.item.partstat), continued: !!b.item.continued,
      })
    }
    if (overflow) {
      let chip: OverflowChip | null = null
      for (const b of cluster) {
        if (b.col < shown) continue
        if (chip && b.top < chip.bottom) {
          chip.ids.push(b.item.id)
          chip.bottom = Math.max(chip.bottom, b.bottom)
        } else {
          chip = { ids: [b.item.id], top: b.top, bottom: b.bottom, col: MAX_COLUMNS - 1, cols: MAX_COLUMNS }
          chips.push(chip)
        }
      }
    }
    cluster = []
    colEnds = []
  }

  for (const b of boxes) {
    if (cluster.length && b.top >= clusterBottom) flush()
    let col = colEnds.findIndex((end) => end <= b.top)
    if (col < 0) {
      col = colEnds.length
      colEnds.push(b.bottom)
    } else colEnds[col] = b.bottom
    b.col = col
    cluster.push(b)
    clusterBottom = cluster.length === 1 ? b.bottom : Math.max(clusterBottom, b.bottom)
  }
  if (cluster.length) flush()
  return { placed, chips }
}

const LOCAL = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/

function toMinutes(local: string): number {
  const m = LOCAL.exec(local)
  if (!m) throw new Error(t('lib.eventLayout.notALocalTime', { local }))
  return Date.UTC(+m[1], +m[2] - 1, +m[3]) / 60_000 + +m[4] * 60 + +m[5]
}

function dateOf(day: number): string {
  return new Date(day * 86_400_000).toISOString().slice(0, 10)
}

/**
 * Splits timed events at local midnight (wall clock, "2026-10-01T22:00"). An event that ends exactly
 * at midnight stays on its first day; a later segment is `continued` (drawn with "↳").
 */
export function splitDays(events: Array<{ id: string; start: string; end: string }>): Segment[] {
  const out: Segment[] = []
  for (const ev of events) {
    const s = toMinutes(ev.start)
    const e = Math.max(s, toMinutes(ev.end))
    const first = Math.floor(s / 1440)
    const last = e > s ? Math.floor((e - 1) / 1440) : first
    for (let d = first; d <= last; d++) {
      const a = Math.max(s, d * 1440)
      const b = Math.min(e, (d + 1) * 1440)
      out.push({ id: ev.id, date: dateOf(d), start: a - d * 1440, end: b - d * 1440, continued: d > first, continues: d < last })
    }
  }
  return out
}
