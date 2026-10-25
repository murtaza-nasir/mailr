// App-wide status the shell shows as banners: a newer build is on the server.
import { create } from 'zustand'

interface AppStatus {
  /** The server has a newer build than this tab runs (index.html changed, or a chunk is gone). */
  newVersion: boolean
  setNewVersion: (v: boolean) => void
}

export const useAppStatus = create<AppStatus>((set) => ({
  newVersion: false,
  setNewVersion: (v) => set({ newVersion: v }),
}))
