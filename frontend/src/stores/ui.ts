import { create } from 'zustand'
import { t as tr } from '../i18n'

export interface HeldRow {
  id: number
  /** The list it was opened from ("plain", "main" or a section key) and its index there. */
  key: string
  index: number
  c: import('../api/types').ConversationSummary
}

export interface Toast {
  id: number
  message: string
  actionLabel?: string
  onAction?: () => void
  secondaryLabel?: string
  onSecondary?: () => void
  error?: boolean
  duration?: number // ms; 0 = sticky
  /** Runs once when the toast leaves, however it goes (its time, Dismiss, its action, or pushed out
   *  by newer toasts). A delete that waits for its Undo toast is sent from here. */
  onGone?: () => void
}

export type RailPanel = 'calendar' | 'tasks' | 'notes' | 'agent' | null

interface UIState {
  // selection in the current list (conversation ids)
  selected: Set<number>
  // keyboard cursor in the current list
  focusedId: number | null
  // ordered ids currently rendered (for j/k and *a selection)
  visibleIds: number[]
  // ids by state for *r *u *s *t
  visibleMeta: Record<number, { unread: boolean; starred: boolean }>
  sidebarCollapsed: boolean
  mobileNavOpen: boolean
  panel: RailPanel
  /** Phone layout: a link in Ambrose's chat opened its item, and Ambrose stepped aside. The chat stays
   *  mounted (hidden) so "Back to Ambrose", the Ambrose tab and the back gesture return to it as it was. */
  ambroseAway: boolean
  /** Goes up each time a user action opens Ambrose's panel: the chat then takes the focus, so typed
   *  words go to the message box and never run mail shortcuts. */
  agentFocusSeq: number
  helpOpen: boolean
  toasts: Toast[]
  lastUndo: (() => void) | null
  // Menus opened by keyboard shortcuts (l, v, b) for the current selection.
  shortcutMenu: 'label' | 'move' | 'snooze' | null
  // The user label the current list shows (a label view, or a layout tab built on one label
  // such as "Later"). Archive there also removes it: "done with this".
  contextLabelId: number | null
  // "Select all N conversations" for one list (its key), minus rows unchecked afterwards.
  allMatching: string | null
  /** The list behind allMatching (view/search or layout section, and its size). */
  allMatchingQuery: { view: string; q: string; total: number; layout?: string; tab?: string; sec?: string; name?: string } | null
  /** The list on screen as a query (view, search, layout section, total), for the assistant: "this view"
   *  means every conversation in it, not only the rows shown (agent/screen.py list_query). */
  /** The list on screen for the assistant (MailList): a view and search, a whole inbox tab, or one section. */
  listQuery: { view: string; q: string; total?: number; layout?: string; tab?: string; sec?: string; name?: string } | null
  setListQuery: (q: UIState['listQuery']) => void
  excluded: Set<number>
  // The open conversation stays in the list it was opened from, at the same place, until the
  // user moves on (opens another or goes back), even when reading it changes which section it
  // belongs to. Actions that remove it (archive, delete, …) release it.
  held: HeldRow | null
  // A reading pane is showing next to the list (split right/below, or Office's reader).
  paneVisible: boolean
  // The last action that took conversations out of the list, with the list order just before
  // it: the list moves the reading pane on when the open conversation was one of them.
  removed: { ids: number[]; order: number[]; seq: number } | null

  setSelected: (ids: Set<number>) => void
  toggleSelected: (id: number) => void
  clearSelection: () => void
  /** Take these conversations out of the selection (they left the list). */
  dropFromSelection: (ids: number[]) => void
  setFocused: (id: number | null) => void
  setVisible: (ids: number[], meta: UIState['visibleMeta']) => void
  toggleSidebar: () => void
  setMobileNav: (open: boolean) => void
  setPanel: (p: RailPanel) => void
  /** Phone layout: close Ambrose's panel for an item opened from the chat, keeping the chat (ambroseAway). */
  leaveAmbrose: () => void
  setHelp: (open: boolean) => void
  setShortcutMenu: (m: UIState['shortcutMenu']) => void
  setContextLabel: (id: number | null) => void
  setAllMatching: (key: string | null, query?: UIState['allMatchingQuery']) => void
  toggleExcluded: (id: number) => void
  setHeld: (h: HeldRow | null) => void
  setPaneVisible: (v: boolean) => void
  noteRemoved: (ids: number[]) => void
  toast: (t: Omit<Toast, 'id'>) => number
  dismissToast: (id: number) => void
  /** Hold every toast's timer while the pointer or the focus is on the toasts. */
  pauseToasts: (paused: boolean) => void
  setUndo: (fn: (() => void) | null) => void
}

let toastSeq = 1
// Each toast's timer: the time left and, while running, the timeout and when it started.
const toastTimers = new Map<number, { left: number; started: number; handle: ReturnType<typeof setTimeout> | null }>()
let toastsPaused = false

/** How long a toast stays: errors 10 s, a toast with an action (Undo) at least 10 s. */
export function toastDuration(t: Pick<Toast, 'duration' | 'error' | 'actionLabel'>): number {
  if (t.duration !== undefined) return t.duration === 0 ? 0 : t.actionLabel ? Math.max(t.duration, 10_000) : t.duration
  if (t.error || t.actionLabel) return 10_000
  return 6000
}

function loadBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : v === '1'
  } catch {
    return fallback
  }
}

let agentOpener: HTMLElement | null = null

function loadPanel(): RailPanel {
  // On phone-width screens the panel covers the mail; never reopen it on load.
  if (typeof window !== 'undefined' && window.innerWidth < 900) return null
  try {
    const v = localStorage.getItem('mailr.panel')
    return v === 'calendar' || v === 'tasks' || v === 'notes' || v === 'agent' ? v : null
  } catch {
    return null
  }
}

export const useUI = create<UIState>((set, get) => ({
  selected: new Set(),
  focusedId: null,
  visibleIds: [],
  contextLabelId: null,
  allMatching: null,
  allMatchingQuery: null,
  listQuery: null,
  excluded: new Set(),
  held: null,
  paneVisible: false,
  removed: null,
  visibleMeta: {},
  sidebarCollapsed: loadBool('mailr.sidebarCollapsed', false),
  mobileNavOpen: false,
  panel: loadPanel(),
  ambroseAway: false,
  agentFocusSeq: 0,
  helpOpen: false,
  toasts: [],
  lastUndo: null,
  shortcutMenu: null,

  setSelected: (ids) => set({ selected: ids }),
  toggleSelected: (id) => {
    const next = new Set(get().selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    set({ selected: next })
  },
  clearSelection: () => set({ selected: new Set() }),
  dropFromSelection: (ids) => {
    const cur = get().selected
    if (!ids.some((id) => cur.has(id))) return
    const next = new Set(cur)
    for (const id of ids) next.delete(id)
    set({ selected: next })
  },
  setFocused: (id) => set({ focusedId: id }),
  setVisible: (ids, meta) => set({ visibleIds: ids, visibleMeta: meta }),
  setListQuery: (q) => set({ listQuery: q }),
  setContextLabel: (id) => set({ contextLabelId: id }),
  setHeld: (h) => set({ held: h }),
  setPaneVisible: (v) => set({ paneVisible: v }),
  noteRemoved: (ids) => set((st) => ({ removed: { ids, order: st.visibleIds, seq: (st.removed?.seq ?? 0) + 1 } })),
  setAllMatching: (key, query = null) => set({ allMatching: key, allMatchingQuery: key ? query : null, excluded: new Set() }),
  toggleExcluded: (id) => set((st) => {
    const next = new Set(st.excluded)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return { excluded: next }
  }),
  toggleSidebar: () => {
    const v = !get().sidebarCollapsed
    try {
      localStorage.setItem('mailr.sidebarCollapsed', v ? '1' : '0')
    } catch {
      /* ignore */
    }
    set({ sidebarCollapsed: v })
  },
  setMobileNav: (open) => set({ mobileNavOpen: open }),
  setPanel: (p) => {
    try {
      if (p) localStorage.setItem('mailr.panel', p)
      else localStorage.removeItem('mailr.panel')
    } catch {
      /* ignore */
    }
    const prev = get().panel
    // Opening Ambrose: remember what had the focus (the button, a link, a row) to go back to it.
    if (p === 'agent' && prev !== 'agent') {
      const a = typeof document !== 'undefined' ? document.activeElement : null
      agentOpener = typeof HTMLElement !== 'undefined' && a instanceof HTMLElement && a !== document.body ? a : null
    }
    // Any other choice of panel (open Ambrose again, close it, another app beside) ends the away state.
    set({ panel: p, ambroseAway: false, ...(p === 'agent' && prev !== 'agent' ? { agentFocusSeq: get().agentFocusSeq + 1 } : {}) })
    // Closing Ambrose with the focus in it (or nowhere): back to the opener, else to the Ambrose button.
    if (prev === 'agent' && p !== 'agent' && typeof document !== 'undefined' && typeof requestAnimationFrame === 'function') {
      const opener = agentOpener
      agentOpener = null
      requestAnimationFrame(() => {
        const a = document.activeElement
        if (a && a !== document.body && a.isConnected && !a.closest('.side-panel')) return
        const to = opener?.isConnected ? opener : document.querySelector<HTMLElement>('.ambrose-btn')
        to?.focus()
      })
    }
  },
  leaveAmbrose: () => {
    try {
      localStorage.removeItem('mailr.panel')
    } catch {
      /* ignore */
    }
    set({ panel: null, ambroseAway: true })
  },
  setHelp: (open) => set({ helpOpen: open }),
  setShortcutMenu: (m) => set({ shortcutMenu: m }),
  toast: (t) => {
    const id = toastSeq++
    // Keep at most 3 toasts; newest last. The ones pushed out are gone.
    const before = get().toasts
    const out = before.slice(0, Math.max(0, before.length - 2))
    set({ toasts: [...before.slice(-2), { ...t, id }] })
    for (const o of out) {
      const tm = toastTimers.get(o.id)
      if (tm?.handle) clearTimeout(tm.handle)
      toastTimers.delete(o.id)
      o.onGone?.()
    }
    const duration = toastDuration(t)
    if (duration > 0) {
      const run = () => setTimeout(() => get().dismissToast(id), duration)
      toastTimers.set(id, { left: duration, started: Date.now(), handle: toastsPaused ? null : run() })
    }
    return id
  },
  dismissToast: (id) => {
    const tm = toastTimers.get(id)
    if (tm?.handle) clearTimeout(tm.handle)
    toastTimers.delete(id)
    const gone = get().toasts.find((t) => t.id === id)
    const left = get().toasts.filter((t) => t.id !== id)
    // The last toast gone (perhaps under the pointer, so no mouseleave follows): unpause.
    if (!left.length) toastsPaused = false
    set({ toasts: left })
    gone?.onGone?.()
  },
  pauseToasts: (paused) => {
    if (paused === toastsPaused) return
    toastsPaused = paused
    const now = Date.now()
    for (const [id, tm] of toastTimers) {
      if (paused && tm.handle) {
        clearTimeout(tm.handle)
        tm.left = Math.max(1000, tm.left - (now - tm.started))
        tm.handle = null
      } else if (!paused && !tm.handle) {
        tm.started = now
        tm.handle = setTimeout(() => get().dismissToast(id), tm.left)
      }
    }
  },
  setUndo: (fn) => set({ lastUndo: fn }),
}))

export const toast = (t: Omit<Toast, 'id'>) => useUI.getState().toast(t)
const NETWORK_FAILURES = new Set(['', tr('stores.ui.failedToFetch'), tr('stores.ui.networkerrorWhenAttemptingToFetch'), tr('stores.ui.loadFailed')])

export const toastError = (e: unknown, prefix?: string) => {
  let msg = (e instanceof Error ? e.message : String(e ?? '')).trim()
  // fetch() rejects with a bare TypeError when the server cannot be reached at all.
  if (NETWORK_FAILURES.has(msg)) msg = tr('stores.ui.mailrDidntAnswerCheckYour')
  return useUI.getState().toast({ message: prefix ? `${prefix}: ${msg}` : msg, error: true })
}

/**
 * Where to go after the open conversation left the list. With a reading pane the pane never
 * keeps showing it: the next conversation below (above when the setting says newer), else
 * nothing. Full page follows the auto-advance setting ("list" returns to the list).
 */
export function nextAfterRemove(order: number[], openId: number, removed: number[], mode: 'list' | 'newer' | 'older', pane: boolean): number | null {
  const gone = new Set(removed)
  const idx = order.indexOf(openId)
  if (idx < 0) return null
  const below = order.slice(idx + 1).find((x) => !gone.has(x)) ?? null
  const above = [...order.slice(0, idx)].reverse().find((x) => !gone.has(x)) ?? null
  if (pane) return mode === 'newer' ? (above ?? below) : (below ?? above)
  return mode === 'older' ? below : mode === 'newer' ? above : null
}
