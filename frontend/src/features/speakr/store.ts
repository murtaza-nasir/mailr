// The recording shown in the sheet (web.recording). It is opened from citation chips, the event
// detail and the compose picker; /recordings/<cid>/<rid> has the same view as a page.

import { create } from 'zustand'

export interface SheetTarget {
  cid: number
  rid: number
  t: number | null
}

/** The action-item review list (it outlives a popover that opened it, so it lives here). */
export interface TasksTarget {
  cid: number
  rid: number
  title: string
  ev: { uid: string; occurrence?: string | null } | null
}

interface SheetState {
  open: SheetTarget | null
  tasks: TasksTarget | null
  show: (cid: number, rid: number, t?: number | null) => void
  close: () => void
  showTasks: (t: TasksTarget | null) => void
}

export const useRecordingSheet = create<SheetState>((set) => ({
  open: null,
  tasks: null,
  show: (cid, rid, t = null) => set({ open: { cid, rid, t } }),
  close: () => set({ open: null }),
  showTasks: (tasks) => set({ tasks }),
}))
