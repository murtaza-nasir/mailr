// The Notes app's own UI state: the focused card, the selection, and the dialogs any part of the app
// can open (labels, delete forever, history, edit labels, sharing, templates).

import { create } from 'zustand'
import type { Note } from './types'

interface NotesUI {
  focused: string | null
  /** The checklist item last focused in the open note, for the assistant ("this item"). */
  focusedItem: { note: string; item: string } | null
  selected: Set<string>
  /** The labels menu for these notes, at this anchor (the `t` key, a card's Labels button). */
  labelsFor: { ids: string[]; anchor: HTMLElement | DOMRect } | null
  /** "Delete forever" for these notes, confirmed with the count. */
  purge: string[] | null
  /** The open note shows its version history. */
  history: boolean
  /** The Edit labels dialog. */
  editLabels: boolean
  /** The left column is hidden (`[`). */
  leftHidden: boolean
  /** The people dialog (sharing.tsx) for this note or table. */
  share: { kind: 'note' | 'table'; id: string; name: string } | null
  /** "Save as template" for this note or table (templates.tsx). */
  saveTemplate: { kind: 'note' | 'table'; id: string; name: string } | null
  /** The "New from template" gallery. */
  templates: boolean
  /** "Repeat…" for this list (repeat.tsx). */
  repeatFor: Note | null
  setFocused: (id: string | null) => void
  toggleSelected: (id: string) => void
  setSelected: (ids: Set<string>) => void
  set: (p: Partial<Omit<NotesUI, 'set' | 'setFocused' | 'toggleSelected' | 'setSelected'>>) => void
}

export const useNotesUI = create<NotesUI>((set, get) => ({
  focused: null,
  focusedItem: null,
  selected: new Set(),
  labelsFor: null,
  purge: null,
  history: false,
  editLabels: false,
  leftHidden: false,
  share: null,
  saveTemplate: null,
  templates: false,
  repeatFor: null,
  setFocused: (focused) => set({ focused }),
  toggleSelected: (id) => {
    const next = new Set(get().selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    set({ selected: next })
  },
  setSelected: (selected) => set({ selected }),
  set: (p) => set(p),
}))
