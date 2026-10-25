// The command palette's key and host, in every shell (CommandPalette.tsx holds the palette itself).
//
// The palette loads with the first page. The speed split (14 Oct 2026) loaded it on first use, but
// fast typing right after Ctrl+K, as people and the browser tests do, then lost keys while its code
// arrived and while the loading box gave way to the palette (tables-app, accounts). Its code is
// about 30 kB gzip; a palette that drops what is typed is not worth that.

import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import type { MailAccount } from '../../api/types'
import { CreateLabelDialogWithParent } from '../../features/layout/Sidebar'
import { PaletteDialog } from './CommandPalette'
import { usePalette } from './paletteStore'

/** Open state of the palette, so any button can open it (paletteStore.ts). */
export { usePalette }

/**
 * Ctrl/⌘K: jump to any view or label, run a mail command on the selection or the open
 * conversation, open the side apps, switch colour or layout, or search. Keyboard only if you like:
 * type to filter, ↑/↓ to move, Enter to run, Esc to close.
 */
export function CommandPalette() {
  const open = usePalette((s) => s.open)
  const setOpen = usePalette((s) => s.setOpen)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'k' || e.altKey || e.shiftKey) return
      // The editor keeps Ctrl+K for links.
      if ((e.target as HTMLElement | null)?.closest?.('.ProseMirror')) return
      e.preventDefault()
      setOpen(!usePalette.getState().open)
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [setOpen])

  // New folder in an account (Focus has no navigation pane): the dialog outlives the palette.
  const [newFolderIn, setNewFolderIn] = useState<MailAccount | undefined>()
  return (
    <>
      {open && createPortal(<PaletteDialog onClose={() => setOpen(false)} setNewFolderIn={setNewFolderIn} />, document.body)}
      <CreateLabelDialogWithParent parent={newFolderIn ? '' : null} account={newFolderIn} onClose={() => setNewFolderIn(undefined)} />
    </>
  )
}
