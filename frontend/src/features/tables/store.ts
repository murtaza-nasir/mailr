// What the open table shows, for the assistant's screen context (assistant-context/appContext.ts):
// the table, its view with the viewer's unsaved filters and sort, the focused row and column and the
// selected rows. The table screen keeps it up to date.

import { create } from 'zustand'
import type { Filter, Sort } from './types'

export interface TableScreen {
  id: string | null
  view: string | null
  filters: Filter[]
  sort: Sort[]
  focused_row: string | null
  focused_column: string | null
  selected_rows: string[]
  set: (p: Partial<Omit<TableScreen, 'set'>>) => void
}

export const useTableUI = create<TableScreen>((set) => ({
  id: null, view: null, filters: [], sort: [], focused_row: null, focused_column: null, selected_rows: [],
  set: (p) => set(p),
}))
