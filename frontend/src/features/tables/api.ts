// Data for tables. Reads go through react-query under the 'tables' prefix; a
// write refreshes them and the Notes grid's cards, and offers the server's `undo` in the 7 s toast
// and on `z`.

import { keepPreviousData, useQuery, type QueryClient } from '@tanstack/react-query'
import { ApiError, request } from '../../api/client'
import { toast, toastError, useUI } from '../../stores/ui'
import type { Change, CellValueHistory, Column, Row, RowsAnswer, Session, Table, Undo, ViewSpec } from './types'
import { t } from '../../i18n'

export const UNDO_MS = 7000
const enc = encodeURIComponent

export const tableKeys = {
  all: ['tables'] as const,
  list: (view: string, label: string | null) => ['tables', 'list', view, label ?? ''] as const,
  table: (id: string) => ['tables', 'table', id] as const,
  rows: (id: string) => ['tables', 'rows', id] as const,
  rowsOf: (id: string, view: string | null, spec: string, q: string) => ['tables', 'rows', id, view ?? '', spec, q] as const,
  row: (id: string, row: string) => ['tables', 'row', id, row] as const,
  rowHistory: (id: string, row: string) => ['tables', 'rowhistory', id, row] as const,
  history: (id: string) => ['tables', 'history', id] as const,
  deletedRows: (id: string, q: string) => ['tables', 'deletedrows', id, q] as const,
  deletedCols: (id: string) => ['tables', 'deletedcols', id] as const,
  linked: (kind: string, ref: string) => ['tables', 'linked', kind, ref] as const,
}

export const tablePath = (id: string) => `/tables/${enc(id)}`

// ---------------------------------------------------------------- reads

export function useTables(view: string, label: string | null, enabled = true) {
  return useQuery<Table[]>({
    queryKey: tableKeys.list(view, label),
    queryFn: async ({ signal }) => (await request<{ tables: Table[] }>('/tables', { query: { view, label: label ?? undefined }, signal })).tables ?? [],
    enabled,
    staleTime: 5_000,
  })
}

export function useTable(id: string | null) {
  return useQuery<Table>({
    queryKey: tableKeys.table(id ?? ''),
    queryFn: ({ signal }) => request(tablePath(id!), { signal }),
    enabled: !!id,
    staleTime: 2_000,
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  })
}

/** The rows of a view: `spec` (a viewer's unsaved changes) and `q` (find text) on top of it. */
export function useRows(id: string | null, view: string | null, spec: Partial<ViewSpec> | null, q: string) {
  const s = spec && Object.keys(spec).length ? JSON.stringify(spec) : ''
  return useQuery<RowsAnswer>({
    queryKey: tableKeys.rowsOf(id ?? '', view, s, q),
    queryFn: ({ signal }) => request(`${tablePath(id!)}/rows`, { query: { view: view ?? undefined, spec: s || undefined, q: q || undefined }, signal }),
    enabled: !!id,
    staleTime: 2_000,
    placeholderData: keepPreviousData,
  })
}

export function useRow(id: string | null, row: string | null) {
  return useQuery<Row>({
    queryKey: tableKeys.row(id ?? '', row ?? ''),
    queryFn: ({ signal }) => request(`${tablePath(id!)}/rows/${enc(row!)}`, { signal }),
    enabled: !!id && !!row,
    staleTime: 2_000,
    retry: false,
  })
}

export function useRowHistory(id: string | null, row: string | null, enabled = true) {
  return useQuery<{ changes: Change[]; values: Record<string, CellValueHistory[]>; deleted: boolean }>({
    queryKey: tableKeys.rowHistory(id ?? '', row ?? ''),
    queryFn: ({ signal }) => request(`${tablePath(id!)}/rows/${enc(row!)}/changes`, { signal }),
    enabled: !!id && !!row && enabled,
  })
}

export function useTableHistory(id: string | null, enabled: boolean) {
  return useQuery<{ sessions: Session[]; more: boolean; next_before: number | null; oldest: string | null }>({
    queryKey: tableKeys.history(id ?? ''),
    queryFn: ({ signal }) => request(`${tablePath(id!)}/changes`, { signal }),
    enabled: !!id && enabled,
  })
}

export function useDeletedRows(id: string | null, q: string, enabled: boolean) {
  return useQuery<{ rows: Row[]; total: number }>({
    queryKey: tableKeys.deletedRows(id ?? '', q),
    queryFn: ({ signal }) => request(`${tablePath(id!)}/deleted-rows`, { query: { q: q || undefined }, signal }),
    enabled: !!id && enabled,
  })
}

export function useDeletedColumns(id: string | null, enabled: boolean) {
  return useQuery<Column[]>({
    queryKey: tableKeys.deletedCols(id ?? ''),
    queryFn: async ({ signal }) => (await request<{ columns: Column[] }>(`${tablePath(id!)}/deleted-columns`, { signal })).columns ?? [],
    enabled: !!id && enabled,
  })
}

export interface LinkedAnswer {
  tables: { id: string; name: string; link_id: number }[]
  rows: { table: string; table_name: string; row: string; title: string; column: string }[]
}

/** Tables and rows linked to a conversation, an event or a task (the links panels). */
export function useLinkedTables(kind: string, ref: string | number | null) {
  return useQuery<LinkedAnswer>({
    queryKey: tableKeys.linked(kind, String(ref ?? '')),
    queryFn: ({ signal }) => request('/tables/linked', {
      query: kind === 'conversation' ? { target_kind: kind, conversation_id: ref as number } : { target_kind: kind, ref: String(ref ?? '') }, signal,
    }),
    enabled: ref != null && ref !== '',
    staleTime: 30_000,
    retry: false,
  })
}

// ---------------------------------------------------------------- writes

let lastUndo: Undo | null = null
// Several undos as one (a row's edits within a few seconds), newest last.
let lastUndos: Undo[] | null = null

export function refresh(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: tableKeys.all })
  void qc.invalidateQueries({ queryKey: ['notes'] })
}

export async function runUndo(qc: QueryClient, u: Undo): Promise<void> {
  lastUndo = null
  lastUndos = null
  try {
    await request(u.path.startsWith('/') ? u.path : `/${u.path}`, { method: u.method, body: (u.body ?? undefined) as never })
    toast({ message: t('common.undone'), duration: 2500 })
  } catch (e) {
    toastError(e, t('common.couldNotUndo'))
  }
  refresh(qc)
}

/** Undo several changes as one, newest first (a row's coalesced edits). */
export async function runUndos(qc: QueryClient, us: Undo[]): Promise<void> {
  lastUndo = null
  lastUndos = null
  try {
    for (const u of [...us].reverse()) await request(u.path.startsWith('/') ? u.path : `/${u.path}`, { method: u.method, body: (u.body ?? undefined) as never })
    toast({ message: t('common.undone'), duration: 2500 })
  } catch (e) {
    toastError(e, t('common.couldNotUndo'))
  }
  refresh(qc)
}

/** One toast with one Undo for several changes; returns the toast's id. */
export function offerUndos(qc: QueryClient, us: Undo[], message: string): number {
  lastUndo = null
  lastUndos = us
  const run = () => {
    useUI.getState().setUndo(null)
    void runUndos(qc, us)
  }
  useUI.getState().setUndo(run)
  return toast({ message, actionLabel: t('common.undo'), onAction: run, duration: UNDO_MS })
}

/** The last table action's undo (`z`, Ctrl+Z outside an edit). False when there is none. */
export function undoLast(qc: QueryClient): boolean {
  if (lastUndos) {
    void runUndos(qc, lastUndos)
    return true
  }
  if (!lastUndo) return false
  void runUndo(qc, lastUndo)
  return true
}

export function offerUndo(qc: QueryClient, u: Undo | null | undefined, message?: string): void {
  if (!u) {
    if (message) toast({ message, duration: 3000 })
    return
  }
  lastUndo = u
  lastUndos = null
  const run = () => {
    useUI.getState().setUndo(null)
    void runUndo(qc, u)
  }
  useUI.getState().setUndo(run)
  toast({ message: message || u.label || t('common.done'), actionLabel: t('common.undo'), onAction: run, duration: UNDO_MS })
}

export interface WriteOpts {
  label?: string
  quiet?: boolean
  undo?: boolean
  /** Throw a 409 without a toast (the caller shows a conflict). */
  conflicts?: boolean
}

export async function write<T extends object>(qc: QueryClient, method: string, path: string, body?: unknown, opts: WriteOpts = {}): Promise<T> {
  try {
    const res = await request<T>(path, { method, body: body as never })
    const undo = (res as { undo?: Undo | null } | undefined)?.undo
    if (!opts.quiet && opts.undo !== false) offerUndo(qc, undo, opts.label)
    else if (!opts.quiet && opts.label) toast({ message: opts.label, duration: 3000 })
    refresh(qc)
    return res
  } catch (e) {
    if (!(opts.conflicts && e instanceof ApiError && e.status === 409)) toastError(e)
    refresh(qc)
    throw e
  }
}
