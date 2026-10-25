// The Tasks app's UI state: the focused row, the selection, the row being edited inline, rows
// waiting for a delayed delete, the open conflict, and per-list view options (sort, grouping,
// show completed/deferred), which are kept per device in localStorage (a saved view).

import { create } from 'zustand'
import type { GroupKey, SortKey, TaskItem } from './model'

export interface ViewPrefs {
  sort: SortKey
  group: GroupKey
  showCompleted: boolean
  showDeferred: boolean
}

export const DEFAULT_PREFS: ViewPrefs = { sort: 'auto', group: 'auto', showCompleted: false, showDeferred: false }

const PREFS_KEY = 'mailr.tasks.views'

function loadPrefs(): Record<string, ViewPrefs> {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') as Record<string, ViewPrefs>
  } catch {
    return {}
  }
}

/** A conflict on a write: the change that failed and the server's copy. */
export interface Conflict {
  uid: string
  title: string
  /** The PATCH body that met a newer version. */
  mine: Record<string, unknown>
  /** The server's current task, or null when it was deleted elsewhere. */
  server: TaskItem | null
  /** The local copy before the change (for "Restore as new"). */
  local: TaskItem
}

interface TasksUI {
  focused: string | null
  /** The anchor for Shift+click ranges. */
  anchor: string | null
  selected: Set<string>
  editing: string | null
  /** Rows deleted locally whose DELETE waits for the undo window. */
  hidden: Set<string>
  /** Parents whose subtasks are expanded (or collapsed, in views that expand by default). */
  toggled: Set<string>
  conflict: Conflict | null
  prefs: Record<string, ViewPrefs>
  search: string
  /** The last task created from quick add, for Tab (the next entry becomes its subtask). */
  lastCreated: string | null
  subtaskOf: string | null
  setFocused: (uid: string | null) => void
  select: (uids: string[], on: boolean, anchor?: string | null) => void
  setSelection: (uids: string[]) => void
  clearSelection: () => void
  setEditing: (uid: string | null) => void
  hide: (uids: string[]) => void
  unhide: (uids: string[]) => void
  toggle: (uid: string) => void
  setConflict: (c: Conflict | null) => void
  setPrefs: (key: string, p: Partial<ViewPrefs>) => void
  setSearch: (q: string) => void
  setLastCreated: (uid: string | null) => void
  setSubtaskOf: (uid: string | null) => void
}

export const useTasksUI = create<TasksUI>((set, get) => ({
  focused: null,
  anchor: null,
  selected: new Set(),
  editing: null,
  hidden: new Set(),
  toggled: new Set(),
  conflict: null,
  prefs: loadPrefs(),
  search: '',
  lastCreated: null,
  subtaskOf: null,
  setFocused: (uid) => set({ focused: uid }),
  select: (uids, on, anchor) => {
    const next = new Set(get().selected)
    for (const u of uids) {
      if (on) next.add(u)
      else next.delete(u)
    }
    set({ selected: next, ...(anchor !== undefined ? { anchor } : {}) })
  },
  setSelection: (uids) => set({ selected: new Set(uids) }),
  clearSelection: () => set({ selected: new Set(), anchor: null }),
  setEditing: (uid) => set({ editing: uid }),
  hide: (uids) => set({ hidden: new Set([...get().hidden, ...uids]) }),
  unhide: (uids) => {
    const next = new Set(get().hidden)
    for (const u of uids) next.delete(u)
    set({ hidden: next })
  },
  toggle: (uid) => {
    const next = new Set(get().toggled)
    if (next.has(uid)) next.delete(uid)
    else next.add(uid)
    set({ toggled: next })
  },
  setConflict: (c) => set({ conflict: c }),
  setPrefs: (key, p) => {
    const prefs = { ...get().prefs, [key]: { ...DEFAULT_PREFS, ...get().prefs[key], ...p } }
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs))
    } catch {
      /* private mode: the choice lasts for this tab */
    }
    set({ prefs })
  },
  setSearch: (q) => set({ search: q }),
  setLastCreated: (uid) => set({ lastCreated: uid }),
  setSubtaskOf: (uid) => set({ subtaskOf: uid }),
}))

export function prefsFor(all: Record<string, ViewPrefs>, key: string): ViewPrefs {
  return { ...DEFAULT_PREFS, ...all[key] }
}

const LAST_KEY = 'mailr.tasks.last'

/** The saved view: the last list the user opened. */
export function lastList(): string | null {
  try {
    return localStorage.getItem(LAST_KEY)
  } catch {
    return null
  }
}

export function rememberList(seg: string) {
  try {
    localStorage.setItem(LAST_KEY, seg)
  } catch {
    /* ignore */
  }
}
