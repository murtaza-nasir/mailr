// The ask capture's state (web.ask): which item it is about, where it opens, and the
// words. The words survive closing the capture (and a reload of the tab) until they are sent or
// moved to the chat. Send and Open chat hand them to the assistant panel (assistant-context/askAbout),
// which starts a chat about the item; the capture itself never acts on anything.
//
// What is open registers itself here (useAskTarget), so Shift+A and the palette's "Ask about this…"
// attach the item on top: a reminder sheet, an event detail, a recording sheet, the task pane or the
// open conversation.
// ACT agent.ask_about @web.ask

import { useEffect } from 'react'
import { create } from 'zustand'
import { useAskAbout, type AskAbout } from '../assistant-context/askAbout'

const DRAFT_KEY = 'mailr.ask.draft'
const AUTO_KEY = 'mailr.ask.autoSend'

function read(store: 'session' | 'local', key: string): string | null {
  try {
    return (store === 'session' ? sessionStorage : localStorage).getItem(key)
  } catch {
    return null
  }
}

function write(store: 'session' | 'local', key: string, v: string | null) {
  try {
    const s = store === 'session' ? sessionStorage : localStorage
    if (v === null || v === '') s.removeItem(key)
    else s.setItem(key, v)
  } catch {
    /* storage blocked: the words stay in memory */
  }
}

export interface AskAnchor {
  top: number
  left: number
  right: number
  bottom: number
}

interface CaptureState {
  open: boolean
  about: AskAbout | null
  anchor: AskAnchor | null
  draft: string
  autoSend: boolean
  show: (about: AskAbout | null, anchor?: Element | DOMRect | null) => void
  close: () => void
  setDraft: (text: string) => void
  detach: () => void
  setAutoSend: (on: boolean) => void
  /** Send the words in a new chat about the item. False when there is nothing to send. */
  send: () => boolean
  /** Open the chat about the item; the words go to its message box unsent. */
  openChat: () => void
}

function anchorOf(a: Element | DOMRect | null | undefined): AskAnchor | null {
  if (!a) return null
  const r = 'getBoundingClientRect' in a ? a.getBoundingClientRect() : a
  if (!r.width && !r.height) return null
  return { top: r.top, left: r.left, right: r.right, bottom: r.bottom }
}

export const useAskCapture = create<CaptureState>((set, get) => ({
  open: false,
  about: null,
  anchor: null,
  draft: read('session', DRAFT_KEY) ?? '',
  autoSend: read('local', AUTO_KEY) === '1',
  show: (about, anchor) => set({ open: true, about, anchor: anchorOf(anchor) }),
  close: () => set({ open: false }),
  setDraft: (draft) => {
    write('session', DRAFT_KEY, draft)
    set({ draft })
  },
  detach: () => set({ about: null }),
  setAutoSend: (autoSend) => {
    write('local', AUTO_KEY, autoSend ? '1' : null)
    set({ autoSend })
  },
  send: () => {
    const { draft, about } = get()
    const text = draft.trim()
    if (!text) return false
    useAskAbout.getState().ask(about, { text, send: true })
    write('session', DRAFT_KEY, null)
    set({ draft: '', open: false })
    return true
  },
  openChat: () => {
    const { draft, about } = get()
    useAskAbout.getState().ask(about, draft.trim() ? { text: draft, send: false } : undefined)
    write('session', DRAFT_KEY, null)
    set({ draft: '', open: false })
  },
}))

/** Open the capture about [about] (null: no item), placed by the button that opened it. */
export function openAsk(about: AskAbout | null, anchor?: Element | DOMRect | null) {
  useAskCapture.getState().show(about, anchor)
}

// ---------- what is open, for Shift+A and the palette ----------

let seq = 0
const targets: { id: number; about: AskAbout }[] = []

/** Put [about] on top of what is open; returns the function that takes it off again. */
export function registerAskTarget(about: AskAbout): () => void {
  const id = ++seq
  targets.push({ id, about })
  return () => {
    const i = targets.findIndex((t) => t.id === id)
    if (i >= 0) targets.splice(i, 1)
  }
}

/** While mounted, [about] is what "Ask about this" means (the last one mounted wins). */
export function useAskTarget(about: AskAbout | null) {
  const key = about ? JSON.stringify(about) : ''
  useEffect(() => (key ? registerAskTarget(JSON.parse(key) as AskAbout) : undefined), [key])
}

/** The item on top of the screen, or null. */
export function currentAskTarget(): AskAbout | null {
  return targets.length ? targets[targets.length - 1].about : null
}

/** Shift+A and the palette: the capture about what is open. */
export function openAskFromScreen() {
  openAsk(currentAskTarget())
}

/** A reminder's record as the item to ask about. */
export function reminderAbout(r: { kind: 'event' | 'task' | 'note'; uid: string; occurrence: string; title: string }): AskAbout {
  // A note reminder's uid is the note's id (n|<id>|abs|<instant>).
  if (r.kind === 'note') return { kind: 'note', id: r.uid, title: r.title, list: false }
  return r.kind === 'task' ? { kind: 'task', uid: r.uid, title: r.title } : { kind: 'event', uid: r.uid, occurrence: r.occurrence || null, title: r.title }
}
