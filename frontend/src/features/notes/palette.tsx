// The command palette's notes entries (web.palette): "Go to Notes" (g n),
// "New note: …", "New list: …" (a checklist; the same words also offer a task list), "Add to <list>:
// milk, eggs" (the item grammar, its chips shown under the entry), "Go to <note>", "Search notes for …",
// the notes side panel, and the actions of the note that is open.

import { useQueryClient } from '@tanstack/react-query'
import { Archive, ListChecks, ListPlus, Pin, Search, StickyNote, Table2, Trash2 } from 'lucide-react'
import { useMemo, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { useAppRoute } from '../../hooks/useAppRoute'
import { matchList, parseItems, quickNote as parseQuick } from '../../lib/itemadd'
import { appHome, notePath, notesPath, tableRoutePath } from '../../lib/routes'
import { createTable } from '../tables/actions'
import { toast, useUI } from '../../stores/ui'
import { addItems, convertNote, createNote, quickNote, setState, trashNote } from './actions'
import { runUndo, useNote, useNotes } from './api'
import type { Undo } from './types'
import { qtyLabel } from './model'
import { t as tr } from '../../i18n'

export interface NotePaletteItem {
  id: string
  section: 'Notes'
  label: string
  hint?: string
  icon: ReactNode
  keys?: string
  chips?: string[]
  stay?: boolean
  /** Guidance, not a command: no ↵ (CommandPalette). */
  info?: boolean
  run: () => void
}

/** "New note: …", "New table: …", "Add to <list>: …": the palette shows only the notes items for them.
 *  "Add to" takes over only once the colon is typed: "Add to Favorites" is a mail command. */
export function noteMode(q: string): 'note' | 'list' | 'add' | 'table' | null {
  if (/^\s*new\s+note\s*:/i.test(q)) return 'note'
  if (/^\s*new\s+table\s*:/i.test(q)) return 'table'
  if (/^\s*add\s+to\s+[^:]+:/i.test(q)) return 'add'
  return null
}

// ACT note.create @web.palette, ACT note.add_items @web.palette, ACT note.search @web.palette
export function useNotePaletteItems(q: string, setQ: (s: string) => void): { fixed: NotePaletteItem[]; dynamic: NotePaletteItem[]; only: boolean } {
  const nav = useNavigate()
  const qc = useQueryClient()
  const { notes: route } = useAppRoute()
  const all = useNotes('all', null, 'edited')
  const open = useNote(route?.open ?? null)
  const notes = useMemo(() => all.data?.notes ?? [], [all.data])
  const tables = useMemo(() => all.data?.tables ?? [], [all.data])
  const lists = useMemo(() => notes.filter((n) => n.kind === 'checklist'), [notes])
  const mode = noteMode(q)
  // "New note: #Shopping Costco: milk": the named list's sections, so the chips name the section.
  const quickText = /^\s*new\s+note\s*:\s*(.*)$/i.exec(q)?.[1] ?? ''
  const quickGuess = useMemo(() => {
    const g = parseQuick(quickText, lists.map((l) => ({ id: l.id, name: l.title, sections: [] })))
    return g.kind === 'items' ? g.list?.id ?? null : null
  }, [quickText, lists])
  const quickList = useNote(quickGuess)

  const fixed = useMemo<NotePaletteItem[]>(() => {
    const out: NotePaletteItem[] = [
      { id: 'notes:app', section: 'Notes', label: tr('notes.palette.goToNotes'), icon: <StickyNote />, keys: 'g n', run: () => nav(appHome('notes')) },
      { id: 'notes:new', section: 'Notes', label: tr('notes.palette.newNote'), hint: tr('notes.palette.orShoppingCostcoMilk'), icon: <StickyNote />, keys: 'N', stay: true, run: () => setQ(tr('notes.palette.newNote2')) },
      { id: 'notes:newlist', section: 'Notes', label: tr('notes.palette.newList'), hint: tr('notes.palette.aChecklistTypeItsName'), icon: <ListPlus />, stay: true, run: () => setQ(tr('notes.palette.newList2')) },
      { id: 'notes:newtable', section: 'Notes', label: tr('notes.palette.newTable'), hint: tr('notes.palette.typeItsName'), icon: <Table2 />, stay: true, run: () => setQ(tr('notes.palette.newTable2')) },
      { id: 'notes:add', section: 'Notes', label: tr('notes.palette.addToAList'), hint: tr('notes.palette.addToShoppingMilkEggs'), icon: <ListChecks />, stay: true, run: () => setQ(tr('notes.palette.addTo')) },
      { id: 'notes:panel', section: 'Notes', label: tr('notes.palette.notesBeside'), hint: tr('notes.palette.pinnedListsAtAGlance'), icon: <ListChecks />, run: () => useUI.getState().setPanel('notes') },
    ]
    const n = open.data
    if (n && !n.deleted_at) {
      out.push(
        { id: 'notes:open:pin', section: 'Notes', label: n.state.pinned ? tr('notes.palette.unpinThisNote') : tr('notes.palette.pinThisNote'), icon: <Pin />, run: () => void setState(qc, n.id, { pinned: !n.state.pinned }, n.state.pinned ? tr('common.unpinned') : tr('common.pinned')) },
        { id: 'notes:open:archive', section: 'Notes', label: n.state.archived ? tr('notes.palette.unarchiveThisNote') : tr('notes.palette.archiveThisNote'), icon: <Archive />, run: () => void setState(qc, n.id, { archived: !n.state.archived }, n.state.archived ? tr('common.unarchived') : tr('common.archived')) },
        { id: 'notes:open:convert', section: 'Notes', label: n.kind === 'text' ? tr('notes.palette.makeThisNoteAList') : tr('notes.palette.makeThisListText'), icon: <ListChecks />, run: () => void convertNote(qc, n.id, n.kind === 'text' ? 'checklist' : 'text') },
        { id: 'notes:open:delete', section: 'Notes', label: tr('notes.palette.deleteThisNote'), icon: <Trash2 />, run: () => void trashNote(qc, n.id) },
      )
    }
    return out
  }, [nav, setQ, open.data, qc])

  const dynamic = useMemo<NotePaletteItem[]>(() => {
    const out: NotePaletteItem[] = []
    const newNote = /^\s*new\s+note\s*:\s*(.*)$/i.exec(q)
    const newList = /^\s*(?:new|add|create)\s+list\s*:\s*(.*)$/i.exec(q)
    const addTo = /^\s*add\s+to\s+(.+?)\s*:\s*(.*)$/i.exec(q)
    const newTable = /^\s*new\s+table\s*:\s*(.*)$/i.exec(q)
    if (newTable) {
      const t = newTable[1].trim()
      // ACT table.create @web.palette
      if (t) out.push({
        id: 'notes:create:table', section: 'Notes', label: tr('notes.palette.newTable3', { t }), icon: <Table2 />,
        run: () => void createTable(qc, { name: t }).then((x) => nav(tableRoutePath(x.id), { state: { fresh: true } })),
      })
      else out.push({ id: 'notes:create:thint', section: 'Notes', label: tr('notes.palette.typeTheNameOfThe'), icon: <Table2 />, stay: true, info: true, run: () => {} })
    } else if (newNote) {
      // ACT note.quick @web.palette: "#List" first adds items to that list (the item grammar); anything
      // else is a text note (lib/itemadd.ts quickNote, the twins' `quick` cases).
      const t = newNote[1].trim()
      const known = quickList.data
      const g = parseQuick(t, lists.map((l) => ({ id: l.id, name: l.title, sections: l.id === known?.id ? (known.sections ?? []).map((x) => x.name) : [] })))
      const done = (r: { kind: 'note' | 'items'; note: { id: string; title: string }; undo?: Undo | null; created?: boolean }) => toast({
        message: r.kind === 'note' ? tr('notes.palette.noteAdded') : tr('notes.palette.addedTo', { noteTitle: r.note.title, value: r.created ? tr('notes.palette.aNewList') : '' }),
        actionLabel: tr('common.open'), onAction: () => nav(notePath(r.note.id)),
        ...(r.undo ? { secondaryLabel: tr('common.undo'), onSecondary: () => void runUndo(qc, r.undo!) } : {}),
      })
      if (!t) out.push({ id: 'notes:create:hint', section: 'Notes', label: tr('notes.palette.typeANoteOrList'), hint: '#Shopping Costco: milk', icon: <StickyNote />, stay: true, info: true, run: () => {} })
      else if (g.kind === 'items' && !g.items.length) out.push({ id: 'notes:quick:hint', section: 'Notes', label: g.list?.create ? tr('notes.palette.typeTheItemsForA', { listName: g.list.name }) : tr('notes.palette.typeTheItemsToAdd', { listName: g.list?.name ?? tr('notes.palette.theList') }), icon: <ListChecks />, stay: true, info: true, run: () => {} })
      else if (g.kind === 'items') out.push({
        id: 'notes:quick:items', section: 'Notes', label: g.list?.create ? tr('notes.palette.newList3', { listName: g.list.name, map: g.items.map((i) => i.text).join(', ') }) : tr('notes.palette.addTo2', { listName: g.list?.name, map: g.items.map((i) => i.text).join(', ') }),
        icon: <ListChecks />,
        chips: g.items.map((i) => `${i.qty ? `${qtyLabel(i.qty) || i.qty.amount} ` : ''}${i.text}${i.section ? ` · ${i.section}` : ''}`),
        run: () => void quickNote(qc, t).then(done, () => undefined),
      })
      else out.push({
        id: 'notes:create:note', section: 'Notes', label: tr('notes.palette.newNote3', { title: g.title }), hint: g.body ? tr('notes.palette.withText') : undefined, icon: <StickyNote />,
        run: () => void quickNote(qc, t).then(done, () => undefined),
      })
    } else if (newList) {
      const t = newList[1].trim()
      if (t) out.push({
        id: 'notes:create:list', section: 'Notes', label: tr('notes.palette.newChecklist', { t }), icon: <ListPlus />,
        run: () => void createNote(qc, { kind: 'checklist', title: t }, { quiet: true }).then((n) => nav(notePath(n.id))),
      })
    } else if (addTo) {
      const list = matchList(addTo[1], lists.map((l) => ({ id: l.id, name: l.title, sections: (l.sections ?? []).map((s) => s.name) })))
      const text = addTo[2]
      if (!list) out.push({ id: 'notes:add:none', section: 'Notes', label: tr('notes.palette.noListNamed', { addTo: addTo[1] }), hint: tr('notes.palette.typeNewListToMake'), icon: <ListChecks />, stay: true, info: true, run: () => {} })
      else if (!text.trim()) out.push({ id: 'notes:add:hint', section: 'Notes', label: tr('notes.palette.typeTheItemsToAdd', { listName: list.name }), icon: <ListChecks />, stay: true, info: true, run: () => {} })
      else {
        const parsed = parseItems(text, { mode: 'list', list })
        out.push({
          id: 'notes:add:now', section: 'Notes', label: tr('notes.palette.addTo3', { listName: list.name, text: text.trim() }), icon: <ListChecks />,
          chips: parsed.items.map((i) => `${i.qty ? `${qtyLabel(i.qty) || i.qty.amount} ` : ''}${i.text}${i.section ? ` · ${i.section}` : ''}`),
          run: () => void addItems(qc, list.id, { lines: text }),
        })
      }
    } else if (q.trim().length >= 2 && !mode) {
      const t = q.trim().toLowerCase()
      for (const n of notes.filter((x) => (x.title || '').toLowerCase().includes(t)).slice(0, 5))
        out.push({ id: `notes:go:${n.id}`, section: 'Notes', label: tr('notes.palette.goTo', { title: n.title }), hint: n.kind === 'checklist' ? 'list' : 'note', icon: n.kind === 'checklist' ? <ListChecks /> : <StickyNote />, run: () => nav(notePath(n.id)) })
      for (const x of tables.filter((y) => (y.name || '').toLowerCase().includes(t)).slice(0, 5))
        out.push({ id: `notes:go:${x.id}`, section: 'Notes', label: tr('notes.palette.goTo2', { itemName: x.name }), hint: 'table', icon: <Table2 />, run: () => nav(tableRoutePath(x.id)) })
      out.push({ id: 'notes:search', section: 'Notes', label: tr('notes.palette.searchNotesFor', { q: q.trim() }), icon: <Search />, run: () => nav(notesPath('search', q.trim())) })
    }
    return out
  }, [q, qc, nav, lists, notes, tables, mode, quickList.data])

  return { fixed, dynamic, only: mode !== null }
}
