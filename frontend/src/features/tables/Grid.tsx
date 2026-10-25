// The grid of a table (web.table): a frozen primary column, a header per column with its menu, typed cells edited in place,
// ranges, copy and paste as tab-separated text, fill down, a selection column, "+ New row", group
// headers and footers. Rows are drawn in a window around the scroll position, so 10,000 rows stay
// quick. Every edit goes through the row actions with the version this tab saw; a clash on a cell
// shows "Keep theirs · Use mine" on that cell.

import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, GripVertical, Plus } from 'lucide-react'
import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent, type KeyboardEvent, type MouseEvent, type Ref } from 'react'
import { useNavigate } from 'react-router'
import { ApiError } from '../../api/client'
import { ConfirmDialog } from '../../components/Dialog'
import { MenuItem, MenuSep, Popover } from '../../components/Popover'
import { toast } from '../../stores/ui'
import { addRow, bulk, deleteRows, duplicateRow, editRow, moveRow, patchColumn, patchRowCached } from './actions'
import { undoLast } from './api'
import { CellView, LinkPicker, SelectPicker, TYPE_ICON } from './cells'
import { ColumnMenu } from './columns'
import {
  COMMON_FOOTERS, FOOTERS, anchorAt, editText, fillDownPlan, footerText, inRange, isEmpty, moveFocus, normRange, optionsOf, parseTSV, pastePlan, plainText, rawTip, reanchor,
  resolveFocus, toTSV, type FocusAnchor, type Pos, type Range, dateHint } from './model'
import { openRef } from './refs'
import type { CellValue, Column, LinkRef, Row, RowsAnswer, Table, ViewSpec } from './types'
import { t as tr } from '../../i18n'
import { dirSign, isRtl, xFromStart } from '../../lib/rtl'

export const ROW_H = 36
const OVERSCAN = 12
const SEL_W = 44
const DEFAULT_W: Record<string, number> = { text: 220, number: 140, currency: 150, date: 140, datetime: 180, checkbox: 130, select: 150, multi_select: 200, link: 200 }

export function colWidth(c: Column): number {
  return c.width ?? DEFAULT_W[c.type] ?? 160
}

type Line = { kind: 'group'; key: string; label: string; count: number; footers: RowsAnswer['footers'] } | { kind: 'row'; row: Row; idx: number }

export interface GridHandle {
  focusGrid: () => void
  newRow: () => void
}

interface Conflict { row: string; col: string; theirs: CellValue; mine: CellValue; parse: boolean }

export interface GridProps {
  table: Table
  columns: Column[]
  data: RowsAnswer
  spec: ViewSpec
  canEdit: boolean
  onOpenRow: (id: string) => void
  onSpec: (patch: Partial<ViewSpec>) => void
  onEarlier: (row: string, col: string) => void
  onFocusChange?: (row: string | null, col: string | null, selected: string[]) => void
  onSearch?: () => void
  onHelp?: () => void
  onClearFilters?: () => void
  onFilter?: (col: Column) => void
  /** The row whose form is open: the grid's focus follows it. */
  openRow?: string | null
  handle?: Ref<GridHandle>
  viewerTz?: string
}

export function Grid({ table, columns, data, spec, canEdit, onOpenRow, onSpec, onEarlier, onFocusChange, onSearch, onHelp, onClearFilters, onFilter, openRow, handle, viewerTz }: GridProps) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const scroller = useRef<HTMLDivElement>(null)
  /** The focus by row and column id (model.ts FocusAnchor); `focus` below is where it is drawn. A
   * position alone pointed at another row while a move waited for its refetch, and a Shift+Enter
   * then added the row below that one (tables-app e2e, integ run of 10 Oct 2026). */
  const [anchor, setAnchor] = useState<FocusAnchor>({ row: null, col: null, r: 0, c: 0 })
  const [range, setRange] = useState<Range | null>(null)
  // The cell being edited, its picker and its menu are kept by id too: a refetch while they are
  // open must not move them, or the save, to another row.
  const [editing, setEditing] = useState<{ row: string; col: string; text: string } | null>(null)
  const [picker, setPicker] = useState<{ row: string; col: string; rect: DOMRect } | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  /** A row just added with Shift+Enter: the focus moves to it when it shows. */
  const newRowFocus = useRef<{ id: string; until: number } | null>(null)
  const [conflict, setConflict] = useState<Conflict | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number; row: string; col: string } | null>(null)
  const [colMenu, setColMenu] = useState<{ col: Column; anchor: HTMLElement } | null>(null)
  const [footMenu, setFootMenu] = useState<{ col: Column; anchor: HTMLElement } | null>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [height, setHeight] = useState(600)
  const [askPaste, setAskPaste] = useState<{ rows: { id?: string; cells: Record<string, string> }[]; added: number } | null>(null)
  const [widths, setWidths] = useState<Record<string, number>>({})
  const [dragCol, setDragCol] = useState<string | null>(null)
  const [dropRow, setDropRow] = useState<{ id: string; where: 'before' | 'after' } | null>(null)
  const manual = !spec.sort.length && !spec.group_by
  const refs = data.refs ?? {}

  const byId = useMemo(() => new Map(data.rows.map((r) => [r.id, r])), [data.rows])
  const lines: Line[] = useMemo(() => {
    const out: Line[] = []
    let i = 0
    if (data.groups) {
      for (const g of data.groups) {
        out.push({ kind: 'group', key: g.key, label: g.label, count: g.count, footers: g.footers })
        for (const id of g.rows) {
          const row = byId.get(id)
          if (row) out.push({ kind: 'row', row, idx: i++ })
        }
      }
    } else for (const row of data.rows) out.push({ kind: 'row', row, idx: i++ })
    return out
  }, [data.groups, data.rows, byId])
  const rowLines = useMemo(() => lines.filter((l): l is Extract<Line, { kind: 'row' }> => l.kind === 'row').map((l) => l.row), [lines])
  const nRows = rowLines.length
  const nCols = columns.length
  const rowIds = useMemo(() => rowLines.map((r) => r.id), [rowLines])
  const colIds = useMemo(() => columns.map((c) => c.id), [columns])
  const rowById = useMemo(() => new Map(rowLines.map((r) => [r.id, r])), [rowLines])
  const colById = useMemo(() => new Map(columns.map((c) => [c.id, c])), [columns])
  const focus = useMemo(() => resolveFocus(anchor, rowIds, colIds), [anchor, rowIds, colIds])
  const widthOf = (c: Column) => widths[c.id] ?? colWidth(c)
  const template = `${SEL_W}px ${columns.map((c) => `${widthOf(c)}px`).join(' ')} minmax(48px, 1fr)`

  useEffect(() => {
    if (!openRow) return
    const r = rowIds.indexOf(openRow)
    if (r >= 0) setAnchor((a) => (a.row === openRow ? a : { ...a, row: openRow, r }))
  }, [openRow, rowIds])

  // When rows or columns come and go: the anchor notes where its cell is drawn now, and when its
  // row or column is gone, the cell now at that place takes the focus.
  useEffect(() => {
    setAnchor((a) => reanchor(a, rowIds, colIds))
  }, [rowIds, colIds])

  useEffect(() => {
    onFocusChange?.(rowLines[focus.r]?.id ?? null, columns[focus.c]?.id ?? null, [...selected].slice(0, 50))
  }, [focus, selected, rowLines, columns, onFocusChange])

  useLayoutEffect(() => {
    const el = scroller.current
    if (!el) return
    const ro = new ResizeObserver(() => setHeight(el.clientHeight))
    ro.observe(el)
    setHeight(el.clientHeight)
    return () => ro.disconnect()
  }, [])

  const lineOfRow = useCallback((r: number) => lines.findIndex((l) => l.kind === 'row' && l.idx === r), [lines])
  const scrollTo = useCallback((r: number) => {
    const el = scroller.current
    if (!el) return
    const top = lineOfRow(r) * ROW_H
    const head = ROW_H
    if (top < el.scrollTop) el.scrollTop = top
    else if (top + ROW_H + head * 2 > el.scrollTop + el.clientHeight) el.scrollTop = top + ROW_H * 3 - el.clientHeight
  }, [lineOfRow])

  const go = (p: Pos, extend = false) => {
    setAnchor(anchorAt(p, rowIds, colIds))
    setRange(extend ? { a: range?.a ?? focus, b: p } : null)
    scrollTo(p.r)
  }

  /** A row whose new place the grid scrolls to once it is drawn there (Alt+↑/↓, Shift+Enter). */
  const follow = useRef<string | null>(null)
  useEffect(() => {
    if (follow.current && rowIds[focus.r] === follow.current) {
      follow.current = null
      scrollTo(focus.r)
    }
  }, [focus.r, rowIds, scrollTo])

  const focusGrid = () => scroller.current?.focus({ preventScroll: true })

  // ---------------------------------------------------------------- writes

  const save = useCallback(async (rowId: string, colId: string, value: CellValue, parse = false) => {
    const row = rowById.get(rowId)
    const col = colById.get(colId)
    if (!row || !col) return
    patchRowCached(qc, table.id, { id: row.id, cells: { [col.id]: parse ? row.cells[col.id] : value } })
    try {
      // ACT row.edit @web.table
      await editRow(qc, table.id, row, { [col.id]: value }, { parse })
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 409) return
      const body = (e.body ?? {}) as { conflict?: boolean; theirs?: { content?: { cells?: Record<string, CellValue> } }; detail?: { deleted?: boolean; deleted_by?: string } }
      if (body.conflict) setConflict({ row: row.id, col: col.id, theirs: body.theirs?.content?.cells?.[col.id] ?? null, mine: value, parse })
      else if (body.detail?.deleted) {
        toast({
          message: tr('tables.grid.deletedThisRow', { deletedBy: body.detail.deleted_by ?? tr('common.someone') }), actionLabel: tr('common.restore'), duration: 10_000,
          onAction: () => void editRow(qc, table.id, row, { [col.id]: value }, { parse, restore: true }),
        })
      }
    }
  }, [qc, table.id, rowById, colById])

  const startEdit = (p: Pos, text?: string) => {
    if (!canEdit) return
    const col = columns[p.c]
    const row = rowLines[p.r]
    if (!col || !row) return
    if (col.type === 'checkbox') {
      void save(row.id, col.id, !row.cells[col.id])
      return
    }
    if (col.type === 'select' || col.type === 'multi_select' || col.type === 'link') {
      const el = cellEl(p)
      if (el) setPicker({ row: row.id, col: col.id, rect: el.getBoundingClientRect() })
      return
    }
    setEditing({ row: row.id, col: col.id, text: text ?? editText(col, row.cells[col.id], row.raw[col.id]) })
  }

  const cellEl = (p: Pos) => scroller.current?.querySelector<HTMLElement>(`[data-r="${p.r}"][data-c="${p.c}"]`) ?? null

  const commitEdit = (then?: Pos) => {
    if (!editing) return
    const col = colById.get(editing.col)
    const row = rowById.get(editing.row)
    const text = editing.text
    setEditing(null)
    if (col && row && text !== editText(col, row.cells[col.id], row.raw[col.id])) {
      const parse = col.type !== 'text'
      void save(row.id, col.id, col.type === 'text' ? text : (text.trim() || null), parse)
    }
    if (then) go(then)
    focusGrid()
  }

  const rangeCells = (): { r0: number; r1: number; c0: number; c1: number } => (range ? normRange(range) : { r0: focus.r, r1: focus.r, c0: focus.c, c1: focus.c })

  const copy = async () => {
    const n = rangeCells()
    const m: string[][] = []
    for (let r = n.r0; r <= n.r1; r++) {
      const row = rowLines[r]
      if (!row) continue
      m.push(columns.slice(n.c0, n.c1 + 1).map((c) => plainText(c, row.cells[c.id], row.raw[c.id], refs)))
    }
    const text = toTSV(m)
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      /* no clipboard permission: the copy event fills it */
    }
    return text
  }

  const paste = (text: string) => {
    if (!canEdit) return
    const m = parseTSV(text)
    if (!m.length) return
    const editable = columns.map((c) => (c.type === 'link' ? '' : c.id))
    const plan = pastePlan(focus, m, rowLines.map((r) => r.id), editable)
    plan.rows.forEach((r) => Object.keys(r.cells).forEach((k) => !k && delete r.cells[k]))
    if (plan.added > 20) setAskPaste(plan)
    else void doPaste(plan)
  }
  const doPaste = async (plan: { rows: { id?: string; cells: Record<string, string> }[] }) => {
    // ACT row.bulk_edit @web.table (paste a range; rows past the end are added)
    await bulk(qc, table.id, { op: 'paste', rows: plan.rows })
  }

  const clear = () => {
    if (!canEdit) return
    const n = rangeCells()
    if (n.r0 === n.r1 && n.c0 === n.c1) {
      if (rowLines[n.r0] && columns[n.c0]) void save(rowLines[n.r0].id, columns[n.c0].id, null)
      return
    }
    const rows = []
    for (let r = n.r0; r <= n.r1; r++) {
      const row = rowLines[r]
      if (!row) continue
      rows.push({ id: row.id, cells: Object.fromEntries(columns.slice(n.c0, n.c1 + 1).map((c) => [c.id, null])) })
    }
    void bulk(qc, table.id, { op: 'set', rows })
  }

  const fillDown = () => {
    if (!canEdit || !range) return
    const plan = fillDownPlan(range, rowLines, columns.map((c) => c.id))
    if (plan.length) void bulk(qc, table.id, { op: 'set', rows: plan })
  }

  const newRowBelow = async (after?: string) => {
    if (!canEdit) return
    // ACT row.add @web.table
    const row = await addRow(qc, table.id, {}, { after, filters: spec.filters })
    // The focus goes to the new row once the grid draws it (by its id: a sorted view puts it
    // elsewhere, and a position worked out before it was drawn pointed at the row below).
    if (!row) return
    newRowFocus.current = { id: row.id, until: Date.now() + 5000 }
    follow.current = row.id
    // addRow puts the row into the cached view before it returns, so the grid may have drawn it
    // already, before the line above ran (the effect below then had nothing to wait for and the
    // focus stayed put: tables-app e2e under load, 9 Oct 2026).
    focusNewRow()
  }
  const rowLinesRef = useRef(rowLines)
  rowLinesRef.current = rowLines
  const colIdsRef = useRef(colIds)
  colIdsRef.current = colIds
  const focusNewRow = () => {
    const want = newRowFocus.current
    if (!want) return
    const r = rowLinesRef.current.findIndex((x) => x.id === want.id)
    // By id: this runs after an await too, where the positions of this render are out of date.
    if (r >= 0) {
      setAnchor({ row: want.id, col: colIdsRef.current[0] ?? null, r, c: 0 })
      setRange(null)
    }
    if (r >= 0 || Date.now() > want.until) newRowFocus.current = null
  }
  useEffect(focusNewRow, [rowLines])

  const targetRows = () => (selected.size ? [...selected] : rowLines[focus.r] ? [rowLines[focus.r].id] : [])

  const toggleSelect = (id: string) => setSelected((s) => {
    const n = new Set(s)
    if (n.has(id)) n.delete(id)
    else n.add(id)
    return n
  })

  useImperativeHandle(handle, () => ({ focusGrid, newRow: () => void newRowBelow() }))

  // ---------------------------------------------------------------- keys

  const onKey = (e: KeyboardEvent) => {
    if (editing || picker) return
    // Keys typed in a dialog over the grid (the row form in a popover or a card, a menu) reach this
    // handler through React's tree; only keys on the grid itself are grid keys.
    const target = e.target as HTMLElement | null
    if (target && (!scroller.current?.contains(target) || target.closest('input, textarea, select, [contenteditable="true"]'))) return
    const mod = e.ctrlKey || e.metaKey
    const k = e.key
    const done = () => {
      e.preventDefault()
      e.stopPropagation()
    }
    if (k === 'ArrowUp' || k === 'ArrowDown' || k === 'ArrowLeft' || k === 'ArrowRight') {
      if (e.altKey && (k === 'ArrowUp' || k === 'ArrowDown')) {
        done()
        const row = rowLines[focus.r]
        if (!row || !canEdit) return
        if (!manual) {
          toast({ message: tr('tables.grid.rowsMoveByHandIn'), actionLabel: tr('tables.grid.switchToManualOrder'), onAction: () => onSpec({ sort: [], group_by: null }) })
          return
        }
        // ACT row.move @web.table. The focus stays on the row by its id: it shows at the new place
        // when the refetch draws it there, and the grid scrolls to it then.
        setRange(null)
        follow.current = row.id
        void moveRow(qc, table.id, row.id, { direction: k === 'ArrowUp' ? 'up' : 'down' })
        return
      }
      done()
      // The columns run right to left in a right-to-left page: ArrowLeft goes to the next one.
      const key = isRtl() && (k === 'ArrowLeft' || k === 'ArrowRight') ? (k === 'ArrowLeft' ? 'ArrowRight' : 'ArrowLeft') : k
      go(moveFocus(focus, key, nRows, nCols), e.shiftKey)
      return
    }
    if (k === 'Tab') {
      done()
      go(moveFocus(focus, e.shiftKey ? 'ShiftTab' : 'Tab', nRows, nCols))
      return
    }
    if (k === 'Enter' && e.shiftKey) {
      done()
      void newRowBelow(rowLines[focus.r]?.id)
      return
    }
    if ((k === 'Enter' && mod) || (k === 'o' && !mod && !e.altKey)) {
      done()
      if (rowLines[focus.r]) onOpenRow(rowLines[focus.r].id)
      return
    }
    if (k === 'Enter' || k === 'F2') {
      done()
      startEdit(focus)
      return
    }
    if (k === ' ') {
      done()
      startEdit(focus)
      return
    }
    if (k === 'Escape') {
      if (range || selected.size) {
        done()
        setRange(null)
        setSelected(new Set())
      }
      return
    }
    if (k === 'Backspace' && mod && e.shiftKey) {
      done()
      const ids = targetRows()
      // ACT row.bulk_delete @web.table, ACT row.delete @web.table
      if (ids.length && canEdit) void deleteRows(qc, table.id, ids).then(() => setSelected(new Set()))
      return
    }
    if (k === 'Delete' || k === 'Backspace') {
      done()
      clear()
      return
    }
    if (mod && k.toLowerCase() === 'c') {
      done()
      void copy()
      return
    }
    if (mod && k.toLowerCase() === 'd') {
      done()
      fillDown()
      return
    }
    if (mod && k.toLowerCase() === 'z') {
      done()
      undoLast(qc)
      return
    }
    if (mod || e.altKey) return
    if (k === 'x') {
      done()
      if (rowLines[focus.r]) toggleSelect(rowLines[focus.r].id)
      return
    }
    if (k === 'z') {
      done()
      undoLast(qc)
      return
    }
    if (k === '/') {
      done()
      onSearch?.()
      return
    }
    if (k === '?') {
      done()
      onHelp?.()
      return
    }
    if (k.length === 1 && canEdit) {
      const col = columns[focus.c]
      if (col && !['checkbox', 'select', 'multi_select', 'link'].includes(col.type)) {
        done()
        startEdit(focus, k)
      }
    }
  }

  // ---------------------------------------------------------------- column resize and reorder

  const startResize = (e: React.PointerEvent, col: Column) => {
    e.preventDefault()
    e.stopPropagation()
    const x0 = e.clientX
    const w0 = widthOf(col)
    let w = w0
    const move = (ev: PointerEvent) => {
      w = Math.max(60, Math.min(1200, w0 + (ev.clientX - x0) * dirSign()))
      setWidths((s) => ({ ...s, [col.id]: w }))
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      if (w !== w0 && canEdit) void patchColumn(qc, table.id, col.id, { width: Math.round(w) }, { quiet: true })
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  const fitWidth = (col: Column) => {
    const longest = Math.max(col.name.length + 4, ...rowLines.slice(0, 500).map((r) => plainText(col, r.cells[col.id], r.raw[col.id], refs).length))
    const w = Math.max(70, Math.min(600, longest * 8 + 32))
    setWidths((s) => ({ ...s, [col.id]: w }))
    if (canEdit) void patchColumn(qc, table.id, col.id, { width: w }, { quiet: true })
  }
  const dropOnHeader = (e: DragEvent, target: Column) => {
    const id = e.dataTransfer.getData('application/x-mailr-column')
    setDragCol(null)
    if (!id || id === target.id || !canEdit) return
    e.preventDefault()
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const before = xFromStart(e.clientX, r) < r.width / 2
    // ACT column.reorder @web.table
    void patchColumn(qc, table.id, id, before ? { before: target.id } : { after: target.id }, { label: tr('tables.grid.columnMoved') })
  }

  // ---------------------------------------------------------------- drawing

  const first = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN)
  const last = Math.min(lines.length, Math.ceil((scrollTop + height) / ROW_H) + OVERSCAN)
  const visible = lines.slice(first, last)
  const rangeN = range ? normRange(range) : null
  const conflictFor = (row: string, col: string) => conflict && conflict.row === row && conflict.col === col

  const cellClick = (e: MouseEvent, p: Pos) => {
    if (e.shiftKey) {
      setRange({ a: range?.a ?? focus, b: p })
      setAnchor(anchorAt(p, rowIds, colIds))
    } else go(p)
    focusGrid()
  }

  const stickyLeft = (c: number): CSSProperties | undefined => (c === 0 ? { position: 'sticky', insetInlineStart: SEL_W, zIndex: 2 } : undefined)
  const headStyle: CSSProperties = { gridTemplateColumns: template }

  return (
    <div className="tb-grid-wrap">
      <div ref={scroller} className="tb-grid" role="grid" aria-label={table.name} aria-rowcount={nRows + 1} aria-colcount={nCols} tabIndex={0}
        aria-multiselectable data-testid="table-grid"
        onKeyDown={onKey}
        onScroll={(e) => setScrollTop((e.target as HTMLElement).scrollTop)}
        onCopy={(e) => {
          if (editing) return
          e.preventDefault()
          const n = rangeCells()
          const m: string[][] = []
          for (let r = n.r0; r <= n.r1; r++) {
            const row = rowLines[r]
            if (row) m.push(columns.slice(n.c0, n.c1 + 1).map((c) => plainText(c, row.cells[c.id], row.raw[c.id], refs)))
          }
          e.clipboardData.setData('text/plain', toTSV(m))
        }}
        onPaste={(e) => {
          if (editing) return
          e.preventDefault()
          paste(e.clipboardData.getData('text/plain'))
        }}>
        <div className="tb-row tb-head" role="row" style={headStyle}>
          <div className="tb-cell tb-sel-cell tb-head-cell" role="columnheader" style={{ position: 'sticky', insetInlineStart: 0, zIndex: 3 }}>
            <input type="checkbox" className="checkbox" aria-label={tr('tables.grid.selectAllRows')} checked={!!nRows && selected.size === nRows}
              onChange={(e) => setSelected(e.target.checked ? new Set(rowLines.map((r) => r.id)) : new Set())} />
          </div>
          {columns.map((c, i) => {
            const sorted = spec.sort.find((s) => s.col === c.id)
            return (
              <div key={c.id} role="columnheader" className={`tb-cell tb-head-cell${i === 0 ? ' tb-primary' : ''}${dragCol === c.id ? ' dragging' : ''}`}
                style={stickyLeft(i) ? { ...stickyLeft(i), zIndex: 3 } : undefined}
                aria-sort={sorted ? (sorted.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                draggable={canEdit}
                onDragStart={(e) => {
                  e.dataTransfer.setData('application/x-mailr-column', c.id)
                  setDragCol(c.id)
                }}
                onDragEnd={() => setDragCol(null)}
                onDragOver={(e) => e.dataTransfer.types.includes('application/x-mailr-column') && e.preventDefault()}
                onDrop={(e) => dropOnHeader(e, c)}
                data-col={c.id}>
                <span className="tb-type" aria-hidden>{TYPE_ICON[c.type]}</span>
                <span className="tb-head-name" title={c.name}><bdi>{c.name}</bdi>{c.required && <span className="tb-req" aria-label="required">*</span>}</span>
                {sorted && <span className="tb-sorted muted" aria-hidden>{sorted.dir === 'asc' ? '↑' : '↓'}</span>}
                <button type="button" className="icon-btn sm tb-head-menu" aria-label={tr('tables.grid.columnMenu', { itemName: c.name })} onClick={(e) => setColMenu({ col: c, anchor: e.currentTarget })}><ChevronDown size={14} /></button>
                <span className="tb-resize" role="separator" aria-orientation="vertical" aria-label={tr('tables.grid.resize', { itemName: c.name })} onPointerDown={(e) => startResize(e, c)} onDoubleClick={() => fitWidth(c)} />
              </div>
            )
          })}
          <div className="tb-cell tb-head-cell tb-add-col" role="columnheader">
            {canEdit && <button type="button" className="icon-btn sm" aria-label={tr('tables.grid.addAColumn')} data-tip={tr('tables.grid.addAColumn')} onClick={(e) => setColMenu({ col: { id: '', name: '', type: 'text', config: {}, required: false, width: null, order: '' }, anchor: e.currentTarget })}><Plus size={16} /></button>}
          </div>
        </div>
        <div style={{ height: first * ROW_H }} aria-hidden />
        {visible.map((line, li) => {
          if (line.kind === 'group') {
            return (
              <div key={`g:${line.key}:${first + li}`} className="tb-row tb-group" role="row" style={headStyle}>
                <div className="tb-cell tb-group-label" role="gridcell" style={{ gridColumn: `1 / span ${Math.min(3, nCols + 1)}`, position: 'sticky', insetInlineStart: 0 }}>
                  <strong>{line.label}</strong> <span className="muted">{line.count}</span>
                </div>
                {columns.map((c, i) => i >= 2 ? <div key={c.id} className="tb-cell tb-group-foot muted" role="gridcell">{footerText(c, line.footers[c.id])}</div> : null)}
              </div>
            )
          }
          const row = line.row
          const r = line.idx
          const isSel = selected.has(row.id)
          return (
            <div key={`${row.id}:${first + li}`} className={`tb-row${isSel ? ' selected' : ''}${dropRow?.id === row.id ? ` drop-${dropRow.where}` : ''}`} role="row" aria-rowindex={r + 2} aria-selected={isSel} style={headStyle} data-row={row.id}
              onDragOver={(e) => {
                if (!e.dataTransfer.types.includes('application/x-mailr-row')) return
                e.preventDefault()
                const b = (e.currentTarget as HTMLElement).getBoundingClientRect()
                setDropRow({ id: row.id, where: e.clientY < b.top + b.height / 2 ? 'before' : 'after' })
              }}
              onDragLeave={() => setDropRow(null)}
              onDrop={(e) => {
                const id = e.dataTransfer.getData('application/x-mailr-row')
                const where = dropRow?.where
                setDropRow(null)
                if (id && id !== row.id && where) {
                  e.preventDefault()
                  void moveRow(qc, table.id, id, { [where]: row.id })
                }
              }}>
              <div className="tb-cell tb-sel-cell" role="gridcell" style={{ position: 'sticky', insetInlineStart: 0, zIndex: 2 }}>
                <span className="tb-handle" draggable={canEdit} aria-label={tr('tables.grid.dragToMoveTheRow')} title={manual ? tr('tables.grid.dragToMove') : tr('tables.grid.switchToManualOrderTo')}
                  onDragStart={(e) => {
                    if (!manual) {
                      e.preventDefault()
                      toast({ message: tr('tables.grid.rowsMoveByHandIn'), actionLabel: tr('tables.grid.switchToManualOrder'), onAction: () => onSpec({ sort: [], group_by: null }) })
                      return
                    }
                    e.dataTransfer.setData('application/x-mailr-row', row.id)
                  }}><GripVertical size={14} /></span>
                <input type="checkbox" className="checkbox" aria-label={tr('tables.grid.selectTheRow')} checked={isSel} onChange={() => toggleSelect(row.id)} />
              </div>
              {columns.map((c, ci) => {
                const p = { r, c: ci }
                const isFocus = focus.r === r && focus.c === ci
                const raw = row.raw[c.id]
                const v = row.cells[c.id]
                const inRg = rangeN ? inRange(range, p) : false
                const ed = editing && editing.row === row.id && editing.col === c.id
                const conf = conflictFor(row.id, c.id)
                return (
                  <div key={c.id} role="gridcell" data-r={r} data-c={ci} data-col={c.id}
                    className={`tb-cell tb-c-${c.type}${ci === 0 ? ' tb-primary' : ''}${isFocus ? ' focused' : ''}${inRg ? ' in-range' : ''}${raw && isEmpty(v) ? ' raw' : ''}${c.required && isEmpty(v) && !raw ? ' missing' : ''}${conf ? ' conflict' : ''}`}
                    style={stickyLeft(ci)} aria-selected={isFocus || inRg}
                    onMouseDown={(e) => {
                      if (e.button === 0 && !ed) cellClick(e, p)
                    }}
                    onDoubleClick={() => startEdit(p)}
                    onContextMenu={(e) => {
                      e.preventDefault()
                      go(p)
                      setMenu({ x: e.clientX, y: e.clientY, row: row.id, col: c.id })
                    }}
                    title={raw && isEmpty(v) ? rawTip(c, raw) : c.required && isEmpty(v) ? tr('tables.grid.isRequired', { itemName: c.name }) : undefined}>
                    {ed ? (
                      <CellInput col={c} text={editing!.text} onText={(t) => setEditing((x) => (x ? { ...x, text: t } : x))}
                        onCommit={(how) => commitEdit(how === 'down' ? moveFocus(p, 'Enter', nRows, nCols) : how === 'right' ? moveFocus(p, 'Tab', nRows, nCols) : how === 'left' ? moveFocus(p, 'ShiftTab', nRows, nCols) : undefined)}
                        onCancel={() => {
                          setEditing(null)
                          focusGrid()
                        }} />
                    ) : c.type === 'checkbox' ? (
                      <input type="checkbox" className="checkbox" aria-label={c.name} checked={!!v} disabled={!canEdit} tabIndex={-1}
                        onMouseDown={(e) => e.stopPropagation()} onChange={() => {
                          go(p)
                          void save(row.id, c.id, !v)
                        }} />
                    ) : (
                      <>
                        {raw && isEmpty(v) ? <><AlertTriangle size={13} className="tb-warn" aria-label={tr('tables.grid.keptAsText')} /><span className="tb-text">{raw}</span></>
                          : <CellView col={c} value={v} refs={refs} tz={viewerTz} onOpenRef={(ref) => openRef(nav, ref, refs)} />}
                      </>
                    )}
                    {conf && (
                      <div className="tb-conflict" role="alert" onMouseDown={(e) => e.stopPropagation()}>
                        <span>{tr('tables.grid.changedBySomeoneElse', { plainText: plainText(c, conflict!.theirs, undefined, refs) || '(empty)' })}</span>
                        <button type="button" className="btn text sm" onClick={() => setConflict(null)}>{tr('common.keepTheirs')}</button>
                        <button type="button" className="btn text sm" onClick={() => {
                          const cf = conflict!
                          setConflict(null)
                          void editRow(qc, table.id, { id: cf.row, version: null }, { [cf.col]: cf.mine }, { parse: cf.parse, force: true })
                        }}>{tr('common.useMine')}</button>
                      </div>
                    )}
                  </div>
                )
              })}
              <div className="tb-cell tb-filler" role="gridcell" />
            </div>
          )
        })}
        <div style={{ height: Math.max(0, lines.length - last) * ROW_H }} aria-hidden />
        {canEdit && (
          <div className="tb-row tb-new" style={headStyle}>
            <div className="tb-cell" style={{ gridColumn: '1 / -1', position: 'sticky', insetInlineStart: 0 }}>
              <button type="button" className="btn text sm tb-new-row" data-testid="table-new-row" onClick={() => void newRowBelow()}><Plus size={16} aria-hidden />{' '}{tr('common.newRow')}</button>
            </div>
          </div>
        )}
        {!nRows && data.total > 0 && (
          <div className="tb-empty-view" role="status">
            {tr('tables.grid.noRowsMatchTheseFilters')}{' '}<button type="button" className="btn text sm" onClick={onClearFilters}>{tr('tables.grid.clearFilters')}</button>
          </div>
        )}
        {Object.keys(spec.footers).length > 0 || columns.length ? (
          <div className="tb-row tb-foot" role="row" style={headStyle}>
            <div className="tb-cell tb-sel-cell muted" style={{ position: 'sticky', insetInlineStart: 0, zIndex: 3 }}>{data.count}</div>
            {columns.map((c, i) => (
              <div key={c.id} className={`tb-cell tb-foot-cell${i === 0 ? ' tb-primary' : ''}`} style={stickyLeft(i) ? { ...stickyLeft(i), zIndex: 3 } : undefined} role="gridcell">
                <button type="button" className="tb-foot-btn" aria-label={tr('tables.grid.footerOf', { itemName: c.name })} onClick={(e) => setFootMenu({ col: c, anchor: e.currentTarget })}>
                  {footerText(c, data.footers[c.id]) || <span className="tb-foot-hint">{tr('tables.grid.calculate')}</span>}
                </button>
              </div>
            ))}
            <div className="tb-cell" />
          </div>
        ) : null}
      </div>

      {picker && (() => {
        const row = rowById.get(picker.row)
        const col = colById.get(picker.col)
        if (!row || !col) return null
        const close = () => {
          setPicker(null)
          focusGrid()
        }
        return (
          <Popover anchor={picker.rect} open onClose={close} role="dialog" label={col.name} placement="bottom-start" minWidth={240}>
            {col.type === 'link' ? (
              <LinkPicker table={table.id} col={col} value={row.cells[col.id]} refs={refs} onClose={close}
                onChange={(v: LinkRef[]) => void save(row.id, col.id, v.length ? v : null)} />
            ) : (
              <SelectPicker col={col} value={row.cells[col.id]} canCreate={canEdit} onClose={close}
                onPick={(v) => void save(row.id, col.id, v)}
                onCreate={(name) => {
                  // A new option's name goes through the text reader, which makes the option (column.options).
                  const cur = col.type === 'multi_select' ? (row.cells[col.id] as string[] | null ?? []).map((id) => optionsOf(col).find((o) => o.id === id)?.name).filter(Boolean) : []
                  void save(row.id, col.id, [...cur, name].join('; '), true)
                  if (col.type === 'select') close()
                }} />
            )}
          </Popover>
        )
      })()}

      {menu && (() => {
        const row = rowById.get(menu.row)
        const col = colById.get(menu.col)
        const close = () => setMenu(null)
        return (
          <Popover anchor={new DOMRect(menu.x, menu.y, 0, 0)} open onClose={close} label={tr('tables.grid.cell')} placement="bottom-start">
            {row && col && <MenuItem onSelect={() => {
              close()
              onEarlier(row.id, col.id)
            }}>{tr('tables.grid.earlierValues')}</MenuItem>}
            <MenuItem onSelect={() => {
              close()
              void copy()
            }} shortcut="Ctrl+C">{tr('common.copy')}</MenuItem>
            {canEdit && <MenuItem onSelect={() => {
              close()
              void navigator.clipboard?.readText?.().then(paste).catch(() => toast({ message: tr('tables.grid.useCtrlVToPaste') }))
            }} shortcut="Ctrl+V">{tr('tables.grid.paste')}</MenuItem>}
            {canEdit && <MenuItem onSelect={() => {
              close()
              clear()
            }} shortcut="Delete">{tr('tables.grid.clear')}</MenuItem>}
            {canEdit && row && <>
              <MenuSep />
              <MenuItem onSelect={() => {
                close()
                void addRow(qc, table.id, {}, { before: row.id, filters: spec.filters })
              }}>{tr('tables.grid.insertRowAbove')}</MenuItem>
              <MenuItem onSelect={() => {
                close()
                void addRow(qc, table.id, {}, { after: row.id, filters: spec.filters })
              }}>{tr('tables.grid.insertRowBelow')}</MenuItem>
              {/* ACT row.duplicate @web.table */}
              <MenuItem onSelect={() => {
                close()
                void duplicateRow(qc, table.id, row.id)
              }}>{tr('tables.grid.duplicateRow')}</MenuItem>
              <MenuItem danger onSelect={() => {
                close()
                void deleteRows(qc, table.id, [row.id])
              }}>{tr('tables.grid.deleteRow')}</MenuItem>
            </>}
          </Popover>
        )
      })()}

      {colMenu && <ColumnMenu table={table} col={colMenu.col} anchor={colMenu.anchor} spec={spec} canEdit={canEdit} onSpec={onSpec} onFilter={onFilter} onClose={() => setColMenu(null)} />}
      {footMenu && <FooterMenu col={footMenu.col} anchor={footMenu.anchor} current={spec.footers[footMenu.col.id]} onPick={(agg) => {
        const f = { ...spec.footers }
        if (agg) f[footMenu.col.id] = agg
        else delete f[footMenu.col.id]
        setFootMenu(null)
        onSpec({ footers: f })
      }} onClose={() => setFootMenu(null)} />}
      <ConfirmDialog open={!!askPaste} title={tr('tables.grid.addRows', { count: askPaste?.added ?? 0 })} body={tr('tables.grid.thePastedTextReachesPast')}
        confirmLabel={tr('tables.grid.paste')} onClose={() => setAskPaste(null)} onConfirm={() => {
          const p = askPaste!
          setAskPaste(null)
          void doPaste(p)
        }} />
    </div>
  )
}

/** The inline editor of a text, number, currency, date or date-and-time cell. Enter saves and
 * moves down, Tab right, Shift+Tab left, Esc cancels. Long text: a box where Shift+Enter is a new
 * line. */
function CellInput({ col, text, onText, onCommit, onCancel }: { col: Column; text: string; onText: (t: string) => void; onCommit: (how?: 'down' | 'right' | 'left') => void; onCancel: () => void }) {
  const long = col.type === 'text' && !!col.config.long
  const ref = useRef<HTMLInputElement & HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }, [])
  const onKeyDown = (e: KeyboardEvent) => {
    e.stopPropagation()
    if (e.key === 'Escape') {
      e.preventDefault()
      onCancel()
    } else if (e.key === 'Enter' && !(long && e.shiftKey)) {
      e.preventDefault()
      onCommit('down')
    } else if (e.key === 'Tab') {
      e.preventDefault()
      onCommit(e.shiftKey ? 'left' : 'right')
    }
  }
  const hint = col.type === 'date' ? dateHint() : col.type === 'datetime' ? tr('tables.grid.fri3pm') : undefined
  if (long) return <textarea ref={ref} className="tb-input tb-long" value={text} aria-label={col.name} onChange={(e) => onText(e.target.value)} onKeyDown={onKeyDown} onBlur={() => onCommit()} rows={5} />
  return <input ref={ref} className={`tb-input${col.type === 'number' || col.type === 'currency' ? ' num' : ''}`} value={text} aria-label={col.name} placeholder={hint}
    inputMode={col.type === 'number' || col.type === 'currency' ? 'decimal' : undefined}
    onChange={(e) => onText(e.target.value)} onKeyDown={onKeyDown} onBlur={() => onCommit()} />
}

function FooterMenu({ col, anchor, current, onPick, onClose }: { col: Column; anchor: HTMLElement; current?: string; onPick: (agg: string | null) => void; onClose: () => void }) {
  return (
    <Popover anchor={anchor} open onClose={onClose} label={tr('tables.grid.footerOf2', { colName: col.name })} placement="top-start">
      <MenuItem selected={!current} onSelect={() => onPick(null)}>{tr('common.none')}</MenuItem>
      {[...(FOOTERS[col.type] ?? []), ...COMMON_FOOTERS].map(([a, label]) => <MenuItem key={a} selected={current === a} onSelect={() => onPick(a)}>{label}</MenuItem>)}
    </Popover>
  )
}
