// The table writes: one function per action id, each through `write` (the
// request, the refresh and the undo toast). The grid, the row form, the Notes grid's cards and the
// palette share them.

import type { QueryClient } from '@tanstack/react-query'
import { ApiError, buildUrl, request } from '../../api/client'
import { toast, useUI } from '../../stores/ui'
import { uuid7 } from '../notes/model'
import { offerUndos, tablePath as P, tableKeys, write } from './api'
import { csvName } from './model'
import type { CellValue, Column, ColumnType, CsvPreview, Filter, Row, RowsAnswer, Table, TableView, Undo, ViewSpec } from './types'
import { t as tr } from '../../i18n'

type R<T = object> = T & { undo?: Undo | null }
const enc = encodeURIComponent

// ---------------------------------------------------------------- tables

export interface ColumnSpec {
  name: string
  type: ColumnType
  config?: Column['config']
  options?: string[]
}

// ACT table.create
export async function createTable(qc: QueryClient, body: { name: string; columns?: ColumnSpec[]; template?: 'log' | 'inventory'; labels?: string[]; color?: string | null }): Promise<Table> {
  return (await write<R<{ table: Table }>>(qc, 'POST', '/tables', body, { label: tr('tables.actions.tableCreated') })).table
}

// ACT table.rename, ACT table.color, ACT column.set_primary
export async function patchTable(qc: QueryClient, t: Pick<Table, 'id' | 'version'>, fields: Partial<Pick<Table, 'name' | 'description' | 'color' | 'local_only' | 'primary_column'>>, label?: string): Promise<Table> {
  const r = await write<R<{ table: Table }>>(qc, 'PATCH', P(t.id), { ...fields, base_version: t.version ?? undefined }, { label })
  qc.setQueryData(tableKeys.table(t.id), r.table)
  return r.table
}

// ACT table.delete
export const trashTable = (qc: QueryClient, id: string) => write(qc, 'DELETE', P(id), undefined, { label: tr('tables.actions.tableMovedToTrash') })
// ACT table.restore
export const restoreTable = (qc: QueryClient, id: string) => write(qc, 'POST', `${P(id)}/restore`, undefined, { label: tr('tables.actions.tableRestored') })
// ACT table.purge
export const purgeTable = (qc: QueryClient, id: string) => write(qc, 'DELETE', `${P(id)}?forever=true`, undefined, { label: tr('tables.actions.tableDeletedForever'), undo: false })
// ACT table.duplicate
export async function duplicateTable(qc: QueryClient, id: string, rows = true): Promise<Table> {
  return (await write<R<{ table: Table }>>(qc, 'POST', `${P(id)}/duplicate`, { rows }, { label: tr('tables.actions.copyMade') })).table
}

export interface TableStateIn {
  pinned?: boolean
  archived?: boolean
  labels?: string[]
  add_labels?: string[]
  remove_labels?: string[]
  before?: string
  after?: string
  /** A date column this viewer shows on their calendar, or null (tables/calendar.py). */
  calendar_column?: string | null
}

// ACT table.pin, ACT table.archive, ACT table.label
export async function setTableState(qc: QueryClient, id: string, body: TableStateIn, label?: string): Promise<void> {
  await write(qc, 'PUT', `${P(id)}/state`, body, { label })
}

// ACT table.link
export const linkTable = (qc: QueryClient, id: string, target_kind: string, target_ref: Record<string, unknown>) =>
  write(qc, 'POST', `${P(id)}/links`, { target_kind, target_ref }, { label: tr('tables.actions.linked') })
// ACT table.unlink
export const unlinkTable = (qc: QueryClient, id: string, link: number) => write(qc, 'DELETE', `${P(id)}/links/${link}`, undefined, { label: tr('tables.actions.linkRemoved') })

// ---------------------------------------------------------------- rows

/** Put a changed row into the cached views at once, so an edit shows before the answer. */
export function patchRowCached(qc: QueryClient, table: string, row: Partial<Row> & { id: string }): void {
  qc.setQueriesData<RowsAnswer>({ queryKey: tableKeys.rows(table) }, (d) => (d ? { ...d, rows: d.rows.map((r) => (r.id === row.id ? { ...r, ...row, cells: { ...r.cells, ...(row.cells ?? {}) } } : r)) } : d))
}

// ACT row.add
export async function addRow(qc: QueryClient, table: string, cells: Record<string, CellValue>, opts: { after?: string; before?: string; filters?: Filter[]; parse?: boolean; id?: string; quiet?: boolean } = {}): Promise<Row> {
  const id = opts.id ?? uuid7()
  const r = await write<R<{ rows: Row[] }>>(qc, 'POST', `${P(table)}/rows`, { id, cells, after: opts.after, before: opts.before, view_filters: opts.filters, parse: opts.parse }, { label: tr('tables.actions.rowAdded'), quiet: opts.quiet })
  if (r.rows[0]) insertRowCached(qc, table, r.rows[0], opts)
  return r.rows[0]
}

/** Put a new row into the cached views in the manual order, so it shows with its "Row added"
 *  toast. Until the refetch drew it, a click and a key meant for the rows below landed one row
 *  off (tables-app e2e, ship run of 9 Oct 2026). Sorted and grouped views wait for the refetch. */
export function insertRowCached(qc: QueryClient, table: string, row: Row, at: { after?: string; before?: string }): void {
  qc.setQueriesData<RowsAnswer>({ queryKey: tableKeys.rows(table) }, (d) => {
    if (!d || d.groups || d.spec?.sort?.length || d.spec?.group_by || d.rows.some((x) => x.id === row.id)) return d
    const anchor = at.after ?? at.before
    let i = anchor ? d.rows.findIndex((x) => x.id === anchor) : d.rows.length
    if (i < 0 || (!anchor && d.offset + d.rows.length < d.total)) return d
    if (at.after) i += 1
    return { ...d, rows: [...d.rows.slice(0, i), row, ...d.rows.slice(i)], count: d.count + 1, total: d.total + 1 }
  })
}

export interface EditResult { row: Row; kept_as_text?: number; merged?: boolean; undo?: Undo | null }

/** Edits to one row within this time share one toast and one Undo. */
export const COALESCE_MS = 6000
const rowToasts = new Map<string, { toast: number; undos: Undo[]; at: number }>()

/** "Hotel": the row's value in the table's first column, when it is short text. */
export function rowName(table: Pick<Table, 'primary_column'> | undefined, row: Pick<Row, 'cells'>): string | null {
  const v = table?.primary_column ? row.cells?.[table.primary_column] : null
  const t = typeof v === 'string' ? v.trim() : typeof v === 'number' ? String(v) : ''
  return t && t.length <= 40 ? t : null
}

/** One "Hotel row changed · Undo" for a row's edits in quick succession: the earlier toast goes and
 * its undos join the new one, which undoes them all, newest first. */
function offerRowUndo(qc: QueryClient, table: string, row: Row, undo: Undo) {
  const key = `${table}|${row.id}`
  const prev = rowToasts.get(key)
  const live = !!prev && Date.now() - prev.at < COALESCE_MS && useUI.getState().toasts.some((t) => t.id === prev.toast)
  if (prev && live) useUI.getState().dismissToast(prev.toast)
  const undos = prev && live ? [...prev.undos, undo] : [undo]
  const name = rowName(qc.getQueryData<Table>(tableKeys.table(table)), row)
  const id = offerUndos(qc, undos, name ? tr('tables.actions.rowChanged', { itemName: name }) : tr('tables.actions.rowChanged2'))
  rowToasts.set(key, { toast: id, undos, at: Date.now() })
}

/** PATCH a row's changed cells with the version this tab saw. A 409 is thrown without a toast:
 * the grid shows the conflict on the cell, or "deleted this row · Restore". */
// ACT row.edit
export async function editRow(qc: QueryClient, table: string, row: Pick<Row, 'id' | 'version'>, cells: Record<string, CellValue>, opts: { parse?: boolean; restore?: boolean; force?: boolean } = {}): Promise<EditResult> {
  const r = await write<R<EditResult>>(qc, 'PATCH', `${P(table)}/rows/${enc(row.id)}`, {
    cells, parse: opts.parse, restore: opts.restore, base_version: opts.force || !row.version ? undefined : row.version,
  }, { conflicts: true, quiet: true })
  patchRowCached(qc, table, r.row)
  if (r.undo) offerRowUndo(qc, table, r.row, r.undo)
  else toast({ message: tr('tables.actions.rowChanged2'), duration: 3000 })
  if (r.kept_as_text) toast({ message: tr('tables.actions.cellsKeptAsText', { count: r.kept_as_text }), duration: 4000 })
  return r
}

// ACT row.delete, ACT row.bulk_delete
export async function deleteRows(qc: QueryClient, table: string, ids: string[]): Promise<void> {
  if (ids.length === 1) await write(qc, 'DELETE', `${P(table)}/rows/${enc(ids[0])}`, undefined, { label: tr('tables.actions.rowDeleted') })
  else await write(qc, 'POST', `${P(table)}/rows/bulk`, { op: 'delete', rows: ids }, { label: tr('tables.actions.rowsDeleted', { count: ids.length }) })
}

// ACT row.restore
export const restoreRows = (qc: QueryClient, table: string, ids: string[]) => write(qc, 'POST', `${P(table)}/rows/restore`, { rows: ids }, { label: tr('tables.actions.rowsRestored', { count: ids.length }) })
// ACT row.duplicate
export const duplicateRow = (qc: QueryClient, table: string, id: string) => write(qc, 'POST', `${P(table)}/rows/${enc(id)}/duplicate`, {}, { label: tr('tables.actions.rowDuplicated') })
// ACT row.move
export const moveRow = (qc: QueryClient, table: string, id: string, body: { before?: string; after?: string; direction?: 'up' | 'down' }) =>
  write(qc, 'POST', `${P(table)}/rows/${enc(id)}/move`, body, { label: tr('tables.actions.rowMoved') })

export interface BulkResult { edited?: number; added?: number; kept_as_text?: number; deleted?: number }

// ACT row.bulk_edit
export async function bulk(qc: QueryClient, table: string, body: Record<string, unknown>): Promise<BulkResult> {
  const r = await write<R<BulkResult>>(qc, 'POST', `${P(table)}/rows/bulk`, body)
  if (r.kept_as_text) toast({ message: tr('tables.actions.cellsKeptAsText', { count: r.kept_as_text }), duration: 4000 })
  return r
}

// ACT row.restore_version
export const restoreVersion = (qc: QueryClient, table: string, row: string, seq: number, col?: string) =>
  write(qc, 'POST', `${P(table)}/rows/${enc(row)}/restore`, { seq, col }, { label: col ? tr('tables.actions.valueRestored') : tr('tables.actions.rowRestored') })

// ---------------------------------------------------------------- columns

// ACT column.add
export const addColumn = (qc: QueryClient, table: string, body: ColumnSpec & { before?: string; after?: string }) =>
  write<R<{ column: Column }>>(qc, 'POST', `${P(table)}/columns`, body, { label: tr('tables.actions.addedColumn', { bodyName: body.name }) })

// ACT column.rename, ACT column.retype, ACT column.options, ACT column.config, ACT column.reorder
export const patchColumn = <T extends object = { column: Column }>(qc: QueryClient, table: string, col: string, body: Record<string, unknown>, opts: { label?: string; quiet?: boolean } = {}) =>
  write<R<T>>(qc, 'PATCH', `${P(table)}/columns/${enc(col)}`, body, opts)

/** The retype preview: "48 cells convert, 3 do not (kept as text)". */
export async function retypePreview(table: string, col: string, type: ColumnType): Promise<{ convert: number; fail: number; examples: string[]; new_options?: string[] }> {
  return (await request<{ preview: { convert: number; fail: number; examples: string[] } }>(`${P(table)}/columns/${enc(col)}`, { method: 'PATCH', body: { type, preview: true } })).preview
}

// ACT column.delete
export const deleteColumn = (qc: QueryClient, table: string, col: string) => write(qc, 'DELETE', `${P(table)}/columns/${enc(col)}`, undefined, { label: tr('tables.actions.columnDeleted') })
// ACT column.restore
export const restoreColumn = (qc: QueryClient, table: string, col: string) => write(qc, 'POST', `${P(table)}/columns/${enc(col)}/restore`, {}, { label: tr('tables.actions.columnRestored') })

// ---------------------------------------------------------------- views

// ACT table.view_save
export const saveView = (qc: QueryClient, table: string, body: { name: string; spec: ViewSpec; view_id?: string; default?: boolean }) =>
  write<R<{ view: TableView }>>(qc, 'POST', `${P(table)}/views`, body, { label: tr('tables.actions.savedView', { bodyName: body.name }) })
// ACT table.view_edit
export const editView = (qc: QueryClient, table: string, view: string, body: { name?: string; spec?: Partial<ViewSpec>; default?: boolean }) =>
  write<R<{ view: TableView }>>(qc, 'PATCH', `${P(table)}/views/${enc(view)}`, body, { label: tr('tables.actions.viewSaved') })
// ACT table.view_delete
export const deleteView = (qc: QueryClient, table: string, view: string) => write(qc, 'DELETE', `${P(table)}/views/${enc(view)}`, undefined, { label: tr('tables.actions.viewDeleted') })

// ---------------------------------------------------------------- history

// ACT table.restore_point
export async function restorePointPreview(table: string, body: { at?: string; seq?: number; label?: string }): Promise<{ summary: string; counts: Record<string, number> }> {
  return request(`${P(table)}/restore-point`, { method: 'POST', body: { ...body, preview: true } })
}
export const restorePoint = (qc: QueryClient, table: string, body: { at?: string; seq?: number; label?: string }) =>
  write<R<{ summary: string }>>(qc, 'POST', `${P(table)}/restore-point`, body, { label: tr('tables.actions.tableRestored') })

// ---------------------------------------------------------------- CSV

/** The file's bytes as base64 (the server detects the encoding). */
export async function fileBase64(f: File): Promise<string> {
  const { bytesToBase64 } = await import('./model')
  return bytesToBase64(new Uint8Array(await f.arrayBuffer()))
}

export interface CsvSource { text?: string; base64?: string; delimiter?: string; header?: boolean }

// ACT table.import_csv
export async function csvPreview(src: CsvSource, table?: string): Promise<CsvPreview> {
  return request(table ? `${P(table)}/import` : '/tables/import', { method: 'POST', body: { ...src, preview: true } as never })
}
export async function importCsv(qc: QueryClient, src: CsvSource & { name?: string; columns?: unknown[]; add_unmatched?: boolean }, table?: string): Promise<{ table?: Table; summary: string; imported: number; kept_as_text: number }> {
  return write(qc, 'POST', table ? `${P(table)}/import` : '/tables/import', src, { quiet: true })
}

// ACT table.export_csv, ACT table.export_xlsx: the server builds the file (typed cells in .xlsx).
export async function exportCsv(t: Pick<Table, 'id' | 'name'>, opts: { scope: 'view' | 'all'; view?: string | null; spec?: Partial<ViewSpec> | null; bom?: boolean; format?: 'csv' | 'xlsx' }): Promise<void> {
  const format = opts.format ?? 'csv'
  const url = buildUrl(`${P(t.id)}/export.${format}`, {
    scope: opts.scope, view: opts.view ?? undefined, spec: opts.spec && Object.keys(opts.spec).length ? JSON.stringify(opts.spec) : undefined,
    bom: opts.bom && format === 'csv' ? 'true' : undefined,
  })
  const res = await fetch(url, { credentials: 'same-origin' })
  if (!res.ok) throw new ApiError(res.status, tr('tables.actions.couldNotExportTheTable'))
  const blob = await res.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = csvName(t.name, format)
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(a.href), 2000)
}
