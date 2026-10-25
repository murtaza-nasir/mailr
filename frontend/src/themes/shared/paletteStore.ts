// The command palette's open state, apart from the palette itself, so a key or a button anywhere can
// open it without loading it. `initial` fills the input when it opens ("New note: " for Shift+N).
import { create } from 'zustand'

export const usePalette = create<{ open: boolean; initial: string; setOpen: (v: boolean) => void }>((set) => ({
  open: false,
  initial: '',
  setOpen: (open) => set({ open, ...(open ? {} : { initial: '' }) }),
}))

/** Open the palette with this text typed (the global "New note…" key). */
export function openPaletteWith(text: string): void {
  usePalette.setState({ open: true, initial: text })
}
