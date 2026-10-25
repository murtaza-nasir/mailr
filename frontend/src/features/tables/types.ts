// The shapes of the tables API. A table is a separate kind in the Notes app:
// typed columns, rows of cells, saved views.

import type { AccessLevel, NoteLink, Person, SharingSummary, Undo, ViewerState } from '../notes/types'

export type { Undo }

export type ColumnType = 'text' | 'number' | 'currency' | 'date' | 'datetime' | 'checkbox' | 'select' | 'multi_select' | 'link'
export type LinkTarget = 'table' | 'conversation' | 'event' | 'task' | 'note'

export interface SelectOption {
  id: string
  name: string
  color: string
}

export interface ColumnConfig {
  long?: boolean
  decimals?: number
  format?: 'plain' | 'thousands' | 'percent'
  currency?: string
  options?: SelectOption[]
  target?: LinkTarget
  table?: string
  multiple?: boolean
}

export interface Column {
  id: string
  name: string
  type: ColumnType
  config: ColumnConfig
  required: boolean
  width: number | null
  order: string
  deleted_at?: string | null
  /** GET deleted-columns: how many cells hold a value. */
  values?: number
}

export type LinkRef =
  | { kind: 'row'; table: string; row: string }
  | { kind: 'conversation'; conversation_id: number | string }
  | { kind: 'event'; uid: string; occurrence?: string }
  | { kind: 'task'; uid: string }
  | { kind: 'note'; id: string }

export type CellValue = string | boolean | string[] | { at: string; tz: string } | LinkRef[] | null

export interface Row {
  id: string
  cells: Record<string, CellValue>
  raw: Record<string, string>
  order: string
  created_by?: Person | null
  created_at: string
  updated_by?: Person | null
  updated_at: string
  version: number | null
  deleted_at: string | null
  /** GET one row / deleted rows. */
  title?: string
  deleted_by?: Person | null
  refs?: Record<string, RefInfo>
  linked_from?: { table: string; table_name: string; row: string; title: string; column: string }[]
}

/** A link chip as this viewer sees it (`refs` of GET rows). */
export interface RefInfo {
  kind: string
  state: 'ok' | 'hidden' | 'deleted'
  title: string
  table?: string
  table_name?: string
  row?: string
  conversation_id?: number | string
  uid?: string
  date?: string | null
  status?: string
  id?: string
  note_kind?: string
}

export interface Filter {
  col: string
  op: string
  value?: unknown
}

export interface Sort {
  col: string
  dir: 'asc' | 'desc'
}

export interface ViewSpec {
  sort: Sort[]
  filters: Filter[]
  group_by: { col: string; by?: 'day' | 'week' | 'month' | 'year' } | null
  hidden: string[]
  footers: Record<string, string>
  phone_columns: string[]
}

export interface TableView {
  id: string
  name: string
  default: boolean
  order: string
  spec: ViewSpec
}

export interface Table {
  id: string
  kind: 'table'
  name: string
  title: string
  description: string
  color: string | null
  local_only: boolean
  primary_column: string | null
  owner?: { kind: 'user' | 'group'; id?: number; name?: string }
  created_by?: Person | null
  created_at: string
  updated_at: string
  updated_by?: Person | null
  deleted_at: string | null
  purge_at?: string | null
  version: number | null
  level: AccessLevel
  state: ViewerState
  counts: { rows: number; columns: number; deleted_columns?: number }
  links?: NoteLink[]
  limits?: { rows: number; columns: number }
  columns?: Column[]
  views?: TableView[]
  preview?: { columns: { id: string; name: string; type: ColumnType }[]; rows: string[][]; more: number }
  matches?: { row_id: string; text: string }[]
  sharing?: SharingSummary
  changed?: boolean
  can_delete?: boolean
  /** The viewer may turn Local models only off (the owner only; notes/localonly.py). */
  can_lower_local_only?: boolean
}

export interface Footer {
  agg: string
  value: unknown
}

export interface Group {
  key: string
  label: string
  count: number
  rows: string[]
  footers: Record<string, Footer>
}

export interface RowsAnswer {
  rows: Row[]
  count: number
  total: number
  offset: number
  view: string | null
  spec: ViewSpec
  footers: Record<string, Footer>
  groups: Group[] | null
  refs: Record<string, RefInfo>
}

export interface Change {
  seq: number
  at: string
  by: Person & { kind?: string }
  who: string
  source: string
  kind: 'row' | 'column' | 'view' | 'table'
  op: string
  row?: string
  column?: string
  view?: string
  batch: string | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  summary: string
}

export interface Session {
  id: string
  from_seq: number
  to_seq: number
  start: string
  end: string
  who: string
  source: string
  count: number
  summary: string
  changes: Change[]
}

export interface CellValueHistory {
  value: CellValue
  raw: string | null
  text: string
  seq: number
  at: string
  who: string
}

export interface CsvPreview {
  encoding: string
  delimiter: string
  delimiter_name: string
  header: boolean
  columns: { index: number; name: string; type: ColumnType; share: number; currency: string | null; label: string }[]
  rows: string[][]
  total: number
  plan?: { index: number; name: string; action: 'map' | 'add' | 'skip'; col?: string; type?: ColumnType }[]
}
