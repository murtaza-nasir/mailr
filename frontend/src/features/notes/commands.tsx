// Notes commands as data (the useMailCommands pattern): Office's Notes ribbon tab draws these. Each
// command runs the Notes app's key handler for the same action (useAppShortcuts in NotesApp), so a
// button and its key always do the same thing, on the focused card or the selection.

import { Archive, ListChecks, Pin, Search, StickyNote, Tag, Trash2, Undo2, CheckSquare } from 'lucide-react'
import type { ReactNode } from 'react'
import { commandSet, type AppCommand, type AppCommands } from '../layout/appCommands'
import { shortcutHandler, type NoteShortcut } from '../../lib/shortcuts'
import { t } from '../../i18n'

function viaKey(action: NoteShortcut) {
  return () => void shortcutHandler('notes', action)?.()
}

export function useNoteCommands(): AppCommands {
  const cmd = (id: string, label: string, icon: ReactNode, group: string, shortcut: string, action: NoteShortcut, short?: string): AppCommand => ({
    id, label, short, icon, group, shortcut, enabled: true, run: viaKey(action),
  })
  const commands: AppCommand[] = [
    // ACT note.create @web.notes
    cmd('new-note', t('notes.commands.newNote'), <StickyNote />, 'new', 'c', 'notes.newNote'),
    cmd('new-list', t('common.newList'), <ListChecks />, 'new', 'l', 'notes.newList'),
    cmd('pin', t('common.pin'), <Pin />, 'note', 'f', 'notes.pin'),
    cmd('archive', t('common.archive'), <Archive />, 'note', 'e', 'notes.archive'),
    cmd('labels', t('common.labels'), <Tag />, 'note', 't', 'notes.labels'),
    cmd('delete', t('common.delete2'), <Trash2 />, 'note', '#', 'notes.delete'),
    cmd('select', t('common.select'), <CheckSquare />, 'tools', 'x', 'notes.select'),
    cmd('undo', t('common.undo'), <Undo2 />, 'tools', 'z', 'notes.undo'),
    cmd('search', t('notes.commands.searchNotes'), <Search />, 'tools', '/', 'notes.search', t('common.search')),
  ]
  return commandSet(commands)
}
