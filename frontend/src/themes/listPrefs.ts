import { create } from 'zustand'
import type { ListPrefs, SortKey } from './types'

const KEY = 'mailr.listPrefs'

function load(): Pick<ListPrefs, 'sort' | 'collapsed'> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<ListPrefs>
    return { sort: v.sort ?? null, collapsed: v.collapsed ?? {} }
  } catch {
    return { sort: null, collapsed: {} }
  }
}

interface PrefsState extends ListPrefs {
  toggleSort: (key: SortKey) => void
  toggleQuick: (k: keyof ListPrefs['quick']) => void
  clearQuick: () => void
  toggleCollapsed: (groupId: string, fallback: boolean) => void
}

/** Client-side list preferences (sort order, quick filters, collapsed groups), per device. */
export const useListPrefs = create<PrefsState>((set, get) => {
  const save = () => {
    const { sort, collapsed } = get()
    try {
      localStorage.setItem(KEY, JSON.stringify({ sort, collapsed }))
    } catch {
      /* private mode */
    }
  }
  return {
    ...load(),
    quick: { unread: false, starred: false, attachments: false },
    toggleSort: (key) => {
      // No choice means the server's order, newest first.
      const cur = get().sort ?? { key: 'date' as SortKey, dir: 'desc' as const }
      // date and star start newest/starred first; text columns start A→Z.
      const first = key === 'date' || key === 'star' || key === 'count' || key === 'attach' ? 'desc' : 'asc'
      let sort: ListPrefs['sort']
      if (cur.key !== key) sort = { key, dir: first }
      else if (cur.dir === first) sort = { key, dir: first === 'asc' ? 'desc' : 'asc' }
      else sort = null // third click: back to the server's order (newest first)
      set({ sort })
      save()
    },
    toggleQuick: (k) => set({ quick: { ...get().quick, [k]: !get().quick[k] } }),
    clearQuick: () => set({ quick: { unread: false, starred: false, attachments: false } }),
    toggleCollapsed: (groupId, fallback) => {
      const cur = get().collapsed[groupId] ?? fallback
      set({ collapsed: { ...get().collapsed, [groupId]: !cur } })
      save()
    },
  }
})
