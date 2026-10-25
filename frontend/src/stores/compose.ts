import { create } from 'zustand'
import type { ComposePayload } from '../api/types'
import { newLocalId } from '../lib/draftStore'
import { composeSnapshots, editorSnapshots, focusOpen, type EditorSnapshot } from '../lib/live/editors'

export type WindowMode = 'normal' | 'minimized' | 'maximized'

export interface ComposeWindow {
  key: string
  draftId: number | null
  payload: ComposePayload
  mode: WindowMode
  /** When set, the compose was opened from a conversation (reply / forward). */
  conversationId?: number | null
  /** Show the AI "Help me write" bar on open. */
  assist?: boolean
  /** The id of the form's local copy (lib/draftStore.ts); kept when a restored draft reopens. */
  localId: string
  /** Reopened from the local copy after a reload, a crash or a sign-out. */
  restored?: boolean
  /** The draft's live version when its payload was read (lib/live): the form's base. */
  version?: number | null
}

export function emptyPayload(identityId: number | null = null): ComposePayload {
  return {
    identity_id: identityId,
    to: [],
    cc: [],
    bcc: [],
    subject: '',
    html: '',
    attachments: [],
  }
}

/** What an open compose form shows right now (for the assistant's view of the screen). */
export interface LiveDraft {
  key: string
  draftId: number | null
  mode: 'new' | 'reply' | 'reply_all' | 'forward'
  to: string[]
  cc: string[]
  bcc: string[]
  subject: string
  /** The user's own text as they see it (no quoted history, no signature). */
  body: string
  inReplyTo: number | null
  conversationId: number | null
  updatedAt: number
}

/** A change the assistant made to an open draft (SSE `draft_edit`), applied by the form. */
export interface DraftEdit {
  id: number
  subject?: string
  to?: { email: string; name?: string }[]
  cc?: { email: string; name?: string }[]
  bcc?: { email: string; name?: string }[]
  body_text?: string
  /** The same text as HTML, its Markdown made into formatting (newer servers). */
  body_html?: string
  /** replace_html: the replacement as HTML (newer servers). */
  edits?: { find: string; replace: string; replace_html?: string }[]
  /** Recipient changes are only proposed: the form shows them with Accept / Reject. */
  proposed_recipients?: {
    to?: { email: string; name?: string }[]
    cc?: { email: string; name?: string }[]
    bcc?: { email: string; name?: string }[]
  }
}

interface ComposeState {
  windows: ComposeWindow[]
  /** The last "bring this window forward" (open() for a draft already open); themes with tabs
   *  switch to it. */
  focusRequest: { key: string; n: number } | null
  live: Record<string, LiveDraft>
  focusedKey: string | null
  edits: Record<string, DraftEdit[]>
  setLive: (d: LiveDraft) => void
  removeLive: (key: string) => void
  setFocused: (key: string | null) => void
  /** Queue an edit for an open form; false when no form with that key is open. */
  pushEdit: (key: string, edit: Omit<DraftEdit, 'id'>) => boolean
  takeEdits: (key: string) => DraftEdit[]
  /** Open a compose window, or bring forward the one (window or inline reply) that has this draft.
   *  `takeOver`: the inline reply pops out into the window (it is closing; do not focus it). */
  open: (w?: Partial<Omit<ComposeWindow, 'key'>> & { takeOver?: boolean }) => string
  /** Close every window without saving (sign-out: the local copies keep the text). */
  reset: () => void
  update: (key: string, patch: Partial<ComposeWindow>) => void
  close: (key: string) => void
  setMode: (key: string, mode: WindowMode) => void
}

let seq = 1
const MAX_OPEN = 3

let editSeq = 1
let focusSeq = 0

export const useCompose = create<ComposeState>((set, get) => ({
  windows: [],
  focusRequest: null,
  live: {},
  focusedKey: null,
  edits: {},
  setLive: (d) => set({ live: { ...get().live, [d.key]: d } }),
  removeLive: (key) => {
    const live = { ...get().live }
    delete live[key]
    const edits = { ...get().edits }
    delete edits[key]
    set({ live, edits, focusedKey: get().focusedKey === key ? null : get().focusedKey })
  },
  setFocused: (key) => {
    if (get().focusedKey !== key) set({ focusedKey: key })
  },
  pushEdit: (key, edit) => {
    if (!get().live[key]) return false
    set({ edits: { ...get().edits, [key]: [...(get().edits[key] ?? []), { ...edit, id: editSeq++ }] } })
    return true
  },
  takeEdits: (key) => {
    const list = get().edits[key] ?? []
    if (list.length) set({ edits: { ...get().edits, [key]: [] } })
    return list
  },
  open: (w = {}) => {
    // One window per draft: an already-open window (or inline reply) for it comes forward.
    if (w.draftId) {
      const existing = get().windows.find((x) => x.draftId === w.draftId)
      if (existing) {
        get().setMode(existing.key, 'normal')
        focusOpen('draft', w.draftId)
        set({ focusRequest: { key: existing.key, n: ++focusSeq } })
        return existing.key
      }
      // An inline reply in a conversation has it open.
      if (!w.takeOver && focusOpen('draft', w.draftId)) return ''
    }
    const key = `c${seq++}`
    const win: ComposeWindow = {
      key,
      draftId: w.draftId ?? null,
      payload: w.payload ?? emptyPayload(),
      mode: w.mode ?? 'normal',
      conversationId: w.conversationId ?? null,
      assist: w.assist,
      localId: w.localId ?? newLocalId(),
      restored: w.restored,
      version: w.version ?? null,
    }
    // Minimise older windows beyond the limit, like Gmail.
    const others = get().windows.map((x, i, arr) =>
      arr.length - i >= MAX_OPEN && x.mode !== 'minimized' ? { ...x, mode: 'minimized' as const } : x,
    )
    set({ windows: [...others, win] })
    return key
  },
  update: (key, patch) => set({ windows: get().windows.map((w) => (w.key === key ? { ...w, ...patch } : w)) }),
  close: (key) => set({ windows: get().windows.filter((w) => w.key !== key) }),
  reset: () => set({ windows: [], live: {}, edits: {}, focusedKey: null, focusRequest: null }),
  setMode: (key, mode) => set({ windows: get().windows.map((w) => (w.key === key ? { ...w, mode } : w)) }),
}))

/** The screen state sent with each assistant turn (backend agent/screen.py): the compose windows
 *  and the other open editors, read from the forms themselves (lib/live/editors.ts), with the
 *  version each started from and whether it holds unsaved changes. */
export function screenContext(): { compose: Record<string, unknown>[]; editors: EditorSnapshot[] } {
  const compose = composeSnapshots()
  return { compose: compose.length ? compose : legacyCompose(), editors: editorSnapshots() }
}

function legacyCompose(): Record<string, unknown>[] {
  const { live, focusedKey, windows } = useCompose.getState()
  const minimized = new Set(windows.filter((w) => w.mode === 'minimized').map((w) => w.key))
  const list = Object.values(live).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 6)
  return (
    list.map((d) => ({
      id: d.key,
      draft_id: d.draftId,
      focused: d.key === focusedKey || (list.length === 1 && !minimized.has(d.key)),
      mode: d.mode,
      to: d.to,
      cc: d.cc,
      bcc: d.bcc,
      subject: d.subject,
      body: d.body.slice(0, 8000),
      in_reply_to_message_id: d.inReplyTo,
      conversation_id: d.conversationId,
    }))
  )
}
