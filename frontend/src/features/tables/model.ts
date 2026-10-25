// Pure helpers for tables (tested in model.test.ts): how a cell shows, its plain text for copy and
// export, tab-separated text in and out, the grid's focus and range moves, filter chips and footers.

import type { CellValue, Column, ColumnType, Filter, Footer, LinkRef, RefInfo, Row, SelectOption, ViewSpec } from './types'
import { t as tr } from '../../i18n'
import { MONTH_SHORT, WEEKDAY_SHORT } from '../../lib/dates'

export const TYPE_LABELS: Record<ColumnType, string> = {
  text: tr('common.text'), number: tr('tables.model.number'), currency: tr('common.currency'), date: tr('common.date'), datetime: tr('tables.model.dateAndTime'), checkbox: tr('tables.model.checkbox'),
  select: tr('common.select'), multi_select: 'Multi-select', link: tr('common.link'),
}
export const TYPES = Object.keys(TYPE_LABELS) as ColumnType[]
export const OPTION_COLORS = ['gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red'] as const

export const LINK_TARGET_LABELS: Record<string, string> = {
  table: tr('tables.model.rowsOfATable'), conversation: tr('common.emails'), event: 'Events', task: tr('common.tasks'), note: tr('common.notesAndTables'),
}

/** Filter operators per type, with their words. */
export const OPS: Record<ColumnType, [string, string][]> = {
  text: [['contains', 'contains'], ['not_contains', tr('tables.model.doesNotContain')], ['is', 'is'], ['is_not', 'is not'], ['starts_with', tr('tables.model.startsWith')], ['ends_with', tr('tables.model.endsWith')], ['empty', 'is empty'], ['not_empty', 'is not empty']],
  number: [['eq', '='], ['ne', '≠'], ['lt', '<'], ['lte', '≤'], ['gt', '>'], ['gte', '≥'], ['between', 'between'], ['empty', 'is empty'], ['not_empty', 'is not empty']],
  currency: [],
  date: [['is', 'is'], ['before', 'before'], ['after', 'after'], ['on_or_before', 'on or before'], ['on_or_after', 'on or after'], ['between', 'between'], ['last_days', tr('tables.model.inTheLastNDays')], ['next_days', tr('tables.model.inTheNextNDays')], ['empty', 'is empty'], ['not_empty', 'is not empty']],
  datetime: [],
  checkbox: [['checked', 'is checked'], ['not_checked', 'is not checked']],
  select: [['is', 'is'], ['is_not', 'is not'], ['any_of', 'is any of'], ['none_of', 'is none of'], ['empty', 'is empty'], ['not_empty', 'is not empty']],
  multi_select: [['has_any', 'has any of'], ['has_all', 'has all of'], ['has_none', 'has none of'], ['empty', 'is empty'], ['not_empty', 'is not empty']],
  link: [['links_to', tr('tables.model.linksTo')], ['not_links_to', tr('tables.model.doesNotLinkTo')], ['empty', 'is empty'], ['not_empty', 'is not empty']],
}
OPS.currency = OPS.number
OPS.datetime = OPS.date
export const NO_VALUE_OPS = new Set(['empty', 'not_empty', 'checked', 'not_checked'])

/** Footer aggregates per type, with their words. */
export const FOOTERS: Record<ColumnType, [string, string][]> = {
  text: [], number: [['sum', tr('tables.model.sum')], ['avg', tr('tables.model.average')], ['median', tr('tables.model.median')], ['min', tr('tables.model.min')], ['max', tr('tables.model.max')], ['count', tr('tables.model.count')]],
  currency: [], date: [['earliest', tr('tables.model.earliest')], ['latest', tr('tables.model.latest')], ['range', tr('tables.model.range')]], datetime: [],
  checkbox: [['checked', tr('common.checked')], ['unchecked', tr('tables.model.unchecked')], ['percent_checked', tr('tables.model.percentChecked')]],
  select: [['per_option', tr('tables.model.countPerOption')]], multi_select: [], link: [],
}
FOOTERS.currency = FOOTERS.number
FOOTERS.datetime = FOOTERS.date
FOOTERS.multi_select = FOOTERS.select
export const COMMON_FOOTERS: [string, string][] = [['filled', tr('tables.model.filled')], ['empty', tr('tables.model.empty')], ['unique', tr('tables.model.unique')]]

export const EMPTY_SPEC: ViewSpec = { sort: [], filters: [], group_by: null, hidden: [], footers: {}, phone_columns: [] }

export function isEmpty(v: unknown): boolean {
  return v == null || v === '' || v === false || (Array.isArray(v) && v.length === 0)
}

export function refKey(r: LinkRef): string {
  switch (r.kind) {
    case 'row': return `row:${r.row}`
    case 'conversation': return `conversation:${r.conversation_id}`
    case 'event': return `event:${r.uid}`
    case 'task': return `task:${r.uid}`
    default: return `note:${r.id}`
  }
}

export function optionsOf(c: Pick<Column, 'config'>): SelectOption[] {
  return c.config?.options ?? []
}

// ---------------------------------------------------------------- numbers and dates

function groupThousands(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/** A decimal string with the column's decimals and format ("1,250.5", "12%"); exact, no floats. */
export function formatNumber(v: string, cfg: Column['config'] = {}, type: ColumnType = 'number'): string {
  let s = v
  if (cfg.format === 'percent') s = shiftDecimal(s, 2)
  if (cfg.decimals != null) s = roundDecimal(s, cfg.decimals)
  const neg = s.startsWith('-')
  if (neg) s = s.slice(1)
  const [whole, frac] = s.split('.')
  const int = cfg.format === 'thousands' || type === 'currency' ? groupThousands(whole) : whole
  let out = (neg ? '-' : '') + int + (frac ? `.${frac}` : '')
  if (cfg.format === 'percent') out += '%'
  if (type === 'currency') out = currencyPrefix(cfg.currency) + out
  return out
}

const SYMBOLS: Record<string, string> = { USD: '$', EUR: '€', GBP: '£', JPY: '¥', INR: '₹', CAD: 'CA$', AUD: 'A$', PKR: tr('tables.model.rs'), CHF: 'CHF ', KRW: '₩', BRL: 'R$' }
export function currencyPrefix(code?: string): string {
  if (!code) return ''
  return SYMBOLS[code] ?? `${code} `
}

/** Move the decimal point n places right (exact, on the string). */
export function shiftDecimal(v: string, n: number): string {
  const neg = v.startsWith('-')
  let [int, frac = ''] = (neg ? v.slice(1) : v).split('.')
  frac = frac.padEnd(n, '0')
  int = (int + frac.slice(0, n)).replace(/^0+(?=\d)/, '')
  frac = frac.slice(n).replace(/0+$/, '')
  return (neg ? '-' : '') + int + (frac ? `.${frac}` : '')
}

/** Round half away from zero to `places`, exactly, keeping trailing zeros ("82.50"). */
export function roundDecimal(v: string, places: number): string {
  const neg = v.startsWith('-')
  const [int, frac = ''] = (neg ? v.slice(1) : v).split('.')
  const digits = (int + frac.padEnd(places + 1, '0')).split('').map(Number)
  const keep = int.length + places
  const up = digits[keep] >= 5
  const kept = digits.slice(0, keep)
  if (up) {
    let i = kept.length - 1
    while (i >= 0) {
      if (kept[i] === 9) {
        kept[i] = 0
        i--
      } else {
        kept[i]++
        break
      }
    }
    if (i < 0) kept.unshift(1)
  }
  const s = kept.join('')
  const ip = s.slice(0, s.length - places) || '0'
  const fp = places ? s.slice(s.length - places) : ''
  const out = (ip.replace(/^0+(?=\d)/, '') || '0') + (places ? `.${fp}` : '')
  return (neg && /[1-9]/.test(out) ? '-' : '') + out
}

const MONTHS = MONTH_SHORT

/** "3 Oct 2026" (a DATE: never shifted by zones). */
export function formatDate(iso: string, today = new Date()): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return iso
  const y = Number(m[1])
  const s = `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}`
  return y === today.getFullYear() ? s : `${s} ${y}`
}

export function formatDateTime(v: { at: string; tz: string }, viewerTz?: string): string {
  const [d, t] = v.at.split('T')
  const base = `${formatDate(d)} ${t ?? ''}`.trim()
  return v.tz && viewerTz && v.tz !== viewerTz && v.tz !== 'UTC' ? `${base} (${v.tz.split('/').pop()?.replace(/_/g, ' ')})` : base
}

// ---------------------------------------------------------------- cells

/** What a cell shows in the grid. */
export function cellText(col: Column, v: CellValue | undefined, refs: Record<string, RefInfo> = {}, viewerTz?: string): string {
  if (isEmpty(v)) return ''
  switch (col.type) {
    case 'number':
    case 'currency':
      return formatNumber(String(v), col.config, col.type)
    case 'date':
      return formatDate(String(v))
    case 'datetime':
      return formatDateTime(v as { at: string; tz: string }, viewerTz)
    case 'checkbox':
      return v ? '✓' : ''
    case 'select':
    case 'multi_select': {
      const names = new Map(optionsOf(col).map((o) => [o.id, o.name]))
      return (Array.isArray(v) ? v : [v]).map((id) => names.get(String(id)) ?? '').filter(Boolean).join(', ')
    }
    case 'link':
      return (v as LinkRef[]).map((r) => refs[refKey(r)]?.title ?? '…').join(', ')
    default:
      return String(v)
  }
}

/** A cell as plain text for copy and the clipboard: numbers without grouping, ISO dates, option
 * names joined with "; ", links by title (the server's export uses the same form). */
export function plainText(col: Column, v: CellValue | undefined, raw?: string, refs: Record<string, RefInfo> = {}): string {
  if (raw && isEmpty(v)) return raw
  if (isEmpty(v)) return ''
  switch (col.type) {
    case 'datetime': {
      const d = v as { at: string; tz: string }
      return d.at.replace('T', ' ')
    }
    case 'checkbox':
      return v ? 'yes' : ''
    case 'select':
    case 'multi_select': {
      const names = new Map(optionsOf(col).map((o) => [o.id, o.name]))
      return (Array.isArray(v) ? v : [v]).map((id) => names.get(String(id)) ?? '').filter(Boolean).join('; ')
    }
    case 'link':
      return (v as LinkRef[]).map((r) => refs[refKey(r)]?.title ?? '').join('; ')
    default:
      return String(v)
  }
}

/** "Not a number: 'about 80'" (the warning mark's tooltip). */
export function rawTip(col: Column, raw: string): string {
  const what: Partial<Record<ColumnType, string>> = {
    number: tr('tables.model.notANumber'), currency: tr('tables.model.notAnAmount'), date: tr('common.notADate'), datetime: tr('tables.model.notADateAndTime'),
    checkbox: tr('tables.model.notYesOrNo'), select: tr('tables.model.notAnOption'), multi_select: tr('tables.model.notOptions'),
  }
  return `${what[col.type] ?? tr('tables.model.keptAsText')}: '${raw}'`
}

/** The text a cell editor starts with (the value as typed). */
export function editText(col: Column, v: CellValue | undefined, raw?: string): string {
  if (raw && isEmpty(v)) return raw
  if (isEmpty(v)) return ''
  if (col.type === 'datetime') return (v as { at: string }).at.replace('T', ' ')
  if (col.type === 'select' || col.type === 'multi_select') return plainText(col, v)
  return typeof v === 'string' ? v : ''
}

// ---------------------------------------------------------------- tab-separated text

/** Rows of cells as tab-separated text (quotes around cells holding tabs, newlines or quotes). */
export function toTSV(matrix: string[][]): string {
  const q = (s: string) => (/[\t\n\r"]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s)
  return matrix.map((r) => r.map(q).join('\t')).join('\n')
}

/** Tab-separated text (from a spreadsheet or this grid) as rows of cells. */
export function parseTSV(text: string): string[][] {
  const out: string[][] = []
  let row: string[] = []
  let cell = ''
  let i = 0
  let quoted = false
  const t = text.replace(/\r\n?/g, '\n')
  while (i < t.length) {
    const ch = t[i]
    if (quoted) {
      if (ch === '"' && t[i + 1] === '"') {
        cell += '"'
        i += 2
        continue
      }
      if (ch === '"') {
        quoted = false
        i++
        continue
      }
      cell += ch
      i++
      continue
    }
    if (ch === '"' && cell === '') {
      quoted = true
      i++
      continue
    }
    if (ch === '\t') {
      row.push(cell)
      cell = ''
    } else if (ch === '\n') {
      row.push(cell)
      out.push(row)
      row = []
      cell = ''
    } else cell += ch
    i++
  }
  if (cell !== '' || row.length) {
    row.push(cell)
    out.push(row)
  }
  return out
}

// ---------------------------------------------------------------- focus and ranges

export interface Pos { r: number; c: number }
export interface Range { a: Pos; b: Pos }

export function clampPos(p: Pos, rows: number, cols: number): Pos {
  return { r: Math.max(0, Math.min(rows - 1, p.r)), c: Math.max(0, Math.min(cols - 1, p.c)) }
}

/** The grid's focus, kept by row and column id: a refetch, a row move or a row added above leaves
 * it on the same cell. r and c are where it was last drawn. They count only when the row or the
 * column is gone (deleted, filtered out); then the cell now at that place takes the focus. */
export interface FocusAnchor { row: string | null; col: string | null; r: number; c: number }

/** Where the focus is drawn: at the anchored row and column when they show, else at the last
 * place, kept inside the grid. */
export function resolveFocus(a: FocusAnchor, rowIds: readonly string[], colIds: readonly string[]): Pos {
  let r = a.row === null ? -1 : rowIds.indexOf(a.row)
  if (r < 0) r = Math.max(0, Math.min(rowIds.length - 1, a.r))
  let c = a.col === null ? -1 : colIds.indexOf(a.col)
  if (c < 0) c = Math.max(0, Math.min(colIds.length - 1, a.c))
  return { r, c }
}

/** The anchor of the cell drawn at p. */
export function anchorAt(p: Pos, rowIds: readonly string[], colIds: readonly string[]): FocusAnchor {
  return { row: rowIds[p.r] ?? null, col: colIds[p.c] ?? null, r: p.r, c: p.c }
}

/** The anchor after the rows or columns changed: the same object while it matches where the focus
 * is drawn (so React skips the update), else the cell drawn there now. With no rows drawn (a view
 * still loading) the anchor waits. */
export function reanchor(a: FocusAnchor, rowIds: readonly string[], colIds: readonly string[]): FocusAnchor {
  if (!rowIds.length) return a
  const next = anchorAt(resolveFocus(a, rowIds, colIds), rowIds, colIds)
  return next.row === a.row && next.col === a.col && next.r === a.r && next.c === a.c ? a : next
}

/** The cell an arrow, Tab or Enter moves to. Tab wraps to the next row's first cell. */
export function moveFocus(p: Pos, key: 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight' | 'Tab' | 'ShiftTab' | 'Enter', rows: number, cols: number): Pos {
  switch (key) {
    case 'ArrowUp': return clampPos({ r: p.r - 1, c: p.c }, rows, cols)
    case 'ArrowDown':
    case 'Enter': return clampPos({ r: p.r + 1, c: p.c }, rows, cols)
    case 'ArrowLeft': return clampPos({ r: p.r, c: p.c - 1 }, rows, cols)
    case 'ArrowRight': return clampPos({ r: p.r, c: p.c + 1 }, rows, cols)
    case 'Tab':
      if (p.c + 1 < cols) return { r: p.r, c: p.c + 1 }
      return p.r + 1 < rows ? { r: p.r + 1, c: 0 } : p
    case 'ShiftTab':
      if (p.c > 0) return { r: p.r, c: p.c - 1 }
      return p.r > 0 ? { r: p.r - 1, c: cols - 1 } : p
  }
}

export function normRange(rg: Range): { r0: number; r1: number; c0: number; c1: number } {
  return { r0: Math.min(rg.a.r, rg.b.r), r1: Math.max(rg.a.r, rg.b.r), c0: Math.min(rg.a.c, rg.b.c), c1: Math.max(rg.a.c, rg.b.c) }
}

export function inRange(rg: Range | null, p: Pos): boolean {
  if (!rg) return false
  const n = normRange(rg)
  return p.r >= n.r0 && p.r <= n.r1 && p.c >= n.c0 && p.c <= n.c1
}

/** The cells a paste writes, from the focused cell: rows past the last one become new rows. */
export function pastePlan(start: Pos, matrix: string[][], rowIds: string[], colIds: string[]): { rows: { id?: string; cells: Record<string, string> }[]; added: number } {
  const rows: { id?: string; cells: Record<string, string> }[] = []
  let added = 0
  matrix.forEach((line, i) => {
    const r = start.r + i
    const cells: Record<string, string> = {}
    line.forEach((text, j) => {
      const c = colIds[start.c + j]
      if (c) cells[c] = text
    })
    if (!Object.keys(cells).length) return
    if (r < rowIds.length) rows.push({ id: rowIds[r], cells })
    else {
      rows.push({ cells })
      added++
    }
  })
  return { rows, added }
}

/** Fill down: the first row of the range copied into the rows under it. */
export function fillDownPlan(rg: Range, rows: Row[], colIds: string[]): { id: string; cells: Record<string, CellValue> }[] {
  const n = normRange(rg)
  const src = rows[n.r0]
  if (!src) return []
  const out: { id: string; cells: Record<string, CellValue> }[] = []
  for (let r = n.r0 + 1; r <= n.r1; r++) {
    const cells: Record<string, CellValue> = {}
    for (let c = n.c0; c <= n.c1; c++) {
      const id = colIds[c]
      if (id) cells[id] = src.cells[id] ?? null
    }
    if (rows[r]) out.push({ id: rows[r].id, cells })
  }
  return out
}

// ---------------------------------------------------------------- filters, footers, views

/** "Exercise is Squat", "Weight ≥ 80", "Date in the last 7 days" (a filter chip). */
export function filterText(col: Column | undefined, f: Filter, refs: Record<string, RefInfo> = {}): string {
  if (!col) return ''
  const words = OPS[col.type].find(([op]) => op === f.op)?.[1] ?? f.op
  if (NO_VALUE_OPS.has(f.op)) return `${col.name} ${words}`
  const vals = Array.isArray(f.value) ? f.value : [f.value]
  let shown: string
  if (col.type === 'select' || col.type === 'multi_select') {
    const names = new Map(optionsOf(col).map((o) => [o.id, o.name]))
    shown = vals.map((v) => names.get(String(v)) ?? String(v)).join(', ')
  } else if (col.type === 'link') {
    shown = vals.map((v) => (typeof v === 'object' && v ? refs[refKey(v as LinkRef)]?.title ?? '' : String(v))).join(', ')
  } else if (f.op === 'between') shown = vals.map(String).join(' and ')
  else shown = vals.map(String).join(', ')
  if (f.op === 'last_days') return tr('tables.model.inTheLastDays', { colName: col.name, count: Number(shown) || 0 })
  if (f.op === 'next_days') return tr('tables.model.inTheNextDays', { colName: col.name, count: Number(shown) || 0 })
  return `${col.name} ${words} ${shown}`
}

/** A footer value as text ("Sum 315", "Squat 3, Bench 2 +1"). */
export function footerText(col: Column, f: Footer | undefined): string {
  if (!f || f.value == null) return ''
  const label = [...(FOOTERS[col.type] ?? []), ...COMMON_FOOTERS].find(([a]) => a === f.agg)?.[1] ?? f.agg
  const v = f.value
  if (f.agg === 'per_option' && typeof v === 'object') {
    const pv = v as { top: { name: string; count: number }[]; more: number }
    return pv.top.map((t) => `${t.name} ${t.count}`).join(', ') + (pv.more ? ` +${pv.more}` : '')
  }
  if ((col.type === 'number' || col.type === 'currency') && typeof v === 'string' && f.agg !== 'count') return `${label} ${formatNumber(v, col.config, col.type)}`
  if ((f.agg === 'earliest' || f.agg === 'latest') && typeof v === 'string') return `${label} ${formatDate(v)}`
  if (f.agg === 'range') return tr('tables.model.days', { label, v: String(v) })
  if (f.agg === 'percent_checked') return tr('tables.model.checked', { v: String(v) })
  return `${label} ${String(v)}`
}

/** The view's settings with a viewer's unsaved changes on top; and whether they differ. */
export function mergeSpec(saved: ViewSpec | undefined, draft: Partial<ViewSpec> | null): ViewSpec {
  return { ...EMPTY_SPEC, ...(saved ?? {}), ...(draft ?? {}) }
}

export function specDiffers(saved: ViewSpec | undefined, draft: Partial<ViewSpec> | null): boolean {
  if (!draft) return false
  const s = { ...EMPTY_SPEC, ...(saved ?? {}) }
  return (Object.keys(draft) as (keyof ViewSpec)[]).some((k) => JSON.stringify(draft[k]) !== JSON.stringify(s[k]))
}

/** The columns a view shows, in column order (the primary column first and never hidden). */
export function visibleColumns(cols: Column[], spec: ViewSpec, primary: string | null): Column[] {
  const hidden = new Set(spec.hidden)
  const shown = cols.filter((c) => c.id === primary || !hidden.has(c.id))
  const p = shown.find((c) => c.id === primary)
  return p ? [p, ...shown.filter((c) => c !== p)] : shown
}

/** "Dana · 3 Oct 14:02". */
export function whoWhen(who: string, at: string, now = new Date()): string {
  const d = new Date(at)
  const today = d.toDateString() === now.toDateString()
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  const day = today ? tr('common.today') : d.toDateString() === y.toDateString() ? tr('common.yesterday') : `${d.getDate()} ${MONTHS[d.getMonth()]}`
  return `${who} · ${day} ${hm}`
}

export function plural(n: number, word: string): string {
  return tr('tables.model.s', { count: n, word })
}

/** A CSV export's file name. */
export function csvName(name: string, ext: 'csv' | 'xlsx' = 'csv'): string {
  return `${(name || tr('common.table')).replace(/[\\/:*?"<>|]+/g, ' ').trim().slice(0, 80) || tr('common.table')}.${ext}`
}

export function bytesToBase64(bytes: Uint8Array): string {
  let s = ''
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(s)
}

/** The example a date cell shows while empty, from this device's clock: "today, sat or 9 oct"
 *  (three things you can type: today, tomorrow's weekday and the day a week ahead), so it never
 *  names a stale date. The "or" keeps "sat, 9 oct" from reading as one date. */
export function dateHint(now: Date = new Date()): string {
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)
  const week = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 7)
  return tr('tables.model.dateHint', {
    weekday: WEEKDAY_SHORT[next.getDay()].toLowerCase(),
    date: `${week.getDate()} ${MONTH_SHORT[week.getMonth()].toLowerCase()}`,
  })
}
