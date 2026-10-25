import { create } from 'zustand'

/** A conversation kept open as its own tab in Office's reading area. */
export interface ConvTab {
  id: number
  title: string
  view: string
  q: string
}

interface TabsState {
  /** Conversation tabs, in order (the preview tab is not stored: it is the routed conversation). */
  pinned: ConvTab[]
  /** The compose window shown in the reading area, or null for the conversation. */
  activeCompose: string | null
  /** Compose tab titles (their subjects). */
  titles: Record<string, string>
  pin: (t: ConvTab) => void
  unpin: (id: number) => void
  retitle: (id: number, title: string) => void
  setActiveCompose: (key: string | null) => void
  setTitle: (key: string, title: string) => void
}

// v2: tabs saved before public ids held internal ids (30 Sep 2026); they are dropped once.
const KEY = 'mailr.office.tabs.v2'

function load(): ConvTab[] {
  try {
    const v = JSON.parse(sessionStorage.getItem(KEY) ?? '[]') as ConvTab[]
    return Array.isArray(v) ? v.filter((t) => typeof t?.id === 'number') : []
  } catch {
    return []
  }
}
function save(tabs: ConvTab[]) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(tabs))
  } catch {
    /* private mode */
  }
}

/** Office's open tabs; conversation tabs last for the browser session. */
export const useOfficeTabs = create<TabsState>((set, get) => ({
  pinned: load(),
  activeCompose: null,
  titles: {},
  pin: (t) => {
    if (get().pinned.some((x) => x.id === t.id)) return
    const pinned = [...get().pinned, t].slice(-12)
    save(pinned)
    set({ pinned })
  },
  unpin: (id) => {
    const pinned = get().pinned.filter((t) => t.id !== id)
    save(pinned)
    set({ pinned })
  },
  retitle: (id, title) => {
    const cur = get().pinned.find((t) => t.id === id)
    if (!cur || cur.title === title) return
    const pinned = get().pinned.map((t) => (t.id === id ? { ...t, title } : t))
    save(pinned)
    set({ pinned })
  },
  setActiveCompose: (key) => set({ activeCompose: key }),
  setTitle: (key, title) => {
    if (get().titles[key] !== title) set({ titles: { ...get().titles, [key]: title } })
  },
}))

/** Save-and-close functions of the mounted compose tabs (the tab's close button calls them). */
export const composeClosers = new Map<string, () => Promise<void>>()
