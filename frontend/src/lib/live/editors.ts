// The editors open on screen (compose windows and inline replies, the event editor, the task pane,
// note and filter editors), for the assistant panel: before it sends a question it saves what they
// hold (flush) and sends what they show (snapshot), and "Open draft" focuses the one already open.

import type { LiveKind } from './store'

export interface EditorSnapshot {
  kind: Exclude<LiveKind, 'draft'>
  id: string | number
  base_version: number | null
  dirty: boolean
  focused?: boolean
  /** What the editor shows, including what is not saved yet (backend agent/screen.py EDITOR_FIELDS). */
  fields?: Record<string, unknown>
}

export interface OpenEditor {
  kind: LiveKind
  /** The item's id; null while it has none (a new draft not saved yet). */
  id: () => string | number | null
  /** Save pending changes now; resolves when the server has them (or the save failed). */
  flush: () => Promise<void>
  /** Drafts: the compose window entry of agent/screen.py; other kinds: an EditorSnapshot. */
  snapshot: () => Record<string, unknown> | null
  /** Bring this editor forward (focus, scroll into view, switch to its tab). */
  focus?: () => void
  /** Last time the user worked in it, for ordering. */
  touched?: () => number
}

const editors = new Map<string, OpenEditor>()

export function registerEditor(key: string, e: OpenEditor): () => void {
  editors.set(key, e)
  return () => {
    if (editors.get(key) === e) editors.delete(key)
  }
}

/** Save every open editor and wait (at most `timeoutMs`): the assistant then works on saved text. */
export async function flushOpenEditors(timeoutMs = 5000): Promise<void> {
  const all = Promise.allSettled([...editors.values()].map((e) => e.flush()))
  await Promise.race([all, new Promise((r) => setTimeout(r, timeoutMs))])
}

/** The compose windows (drafts) as agent/screen.py reads them, most recently used first. */
export function composeSnapshots(): Record<string, unknown>[] {
  return [...editors.values()]
    .filter((e) => e.kind === 'draft')
    .sort((a, b) => (b.touched?.() ?? 0) - (a.touched?.() ?? 0))
    .map((e) => e.snapshot())
    .filter((s): s is Record<string, unknown> => !!s)
    .slice(0, 6)
}

export function editorSnapshots(): EditorSnapshot[] {
  return [...editors.values()]
    .filter((e) => e.kind !== 'draft')
    .map((e) => e.snapshot() as EditorSnapshot | null)
    .filter((s): s is EditorSnapshot => !!s && s.id != null)
    .slice(0, 6)
}

/** Focus the editor that has this item open; false when none has. */
export function focusOpen(kind: LiveKind, id: string | number | null | undefined): boolean {
  if (id == null) return false
  for (const e of editors.values()) {
    if (e.kind === kind && e.id() != null && String(e.id()) === String(id) && e.focus) {
      e.focus()
      return true
    }
  }
  return false
}

export function isOpen(kind: LiveKind, id: string | number | null | undefined): boolean {
  if (id == null) return false
  return [...editors.values()].some((e) => e.kind === kind && e.id() != null && String(e.id()) === String(id))
}
