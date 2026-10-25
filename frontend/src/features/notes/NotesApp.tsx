// The Notes app (web.notes): Keep-style notes and checklists. The left
// column has Notes, Lists, Tables, the viewer's labels with counts, Shared with me, Archive and Trash;
// the header has Grid or List and the sort (search is the top bar's box on the Notes scope); the capture bar takes a note or a list
// inline; the grid shows Pinned, then Others. A note opens where the theme puts a detail:
// a card over the grid (Classic, Focus), the reading pane (Workspace), the pane
// below (Desk) or a bottom sheet (Cards, and every phone). The keys of the `notes` scope work here.

import { useQueryClient } from '@tanstack/react-query'
import {
  Archive, ArrowDownUp, Bell, FileUp, Grid2x2, LayoutList, LayoutTemplate, ListChecks, Menu, MoreVertical, Pencil, Plus, Search, Share2, StickyNote, Table2, Tag, Trash2, Users, X,
} from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { ModalDrawer } from '../../components/ModalDrawer'
import { MenuItem, Popover, useAnchor } from '../../components/Popover'
import { Skeleton } from '../../components/bits'
import { useAppRoute } from '../../hooks/useAppRoute'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { useModalFocus } from '../../hooks/useModalFocus'
import { useOnline } from '../../hooks/useOnline'
import { notePath, notesPath, tableRoutePath, type NotesView } from '../../lib/routes'
import { toast } from '../../stores/ui'
import { useThemeDef } from '../../themes/useUITheme'
import { useAppShortcuts } from '../shortcuts/useAppShortcuts'
import { createLabel, createNoteWithUndo, deleteLabel, emptyTrash, patchLabel, purgeNote, setReminders, setState } from './actions'
import { focusSearch } from '../search/SearchBar'
import { batch, noteKeys, runUndo, undoLast, useNote, useNoteLabels, useNotes, useNoteSearchAll, write } from './api'
import { ImportDialog } from '../imports/ImportDialog'
import { purgeTable, setTableState } from '../tables/actions'
import { NewTableDialog } from '../tables/dialogs'
import { TableCard, useTableVerbs } from '../tables/TableCard'
import type { Table } from '../tables/types'
import { ColorGrid, LabelsMenu } from './menus'
import { Masonry } from './Masonry'
import { NoteCard, type CardProps } from './NoteCard'
import { NoteEditor } from './NoteEditor'
import { colorHex, colorName, downloadText, notesMarkdown, sortLabels, sortNotes, splitPinned, type NoteSort } from './model'
import { remindLabel, remindPresets, useRemindPrefs } from './remind'
import { ShareDialog, ShareNotices } from './sharing'
import { SaveTemplateDialog, TemplatesDialog } from './templates'
import { RepeatDialog } from './repeat'
import { useNotesUI } from './store'
import type { Note } from './types'
import { useNoteVerbs } from './useVerbs'
import './notes.css'
import { useDocumentTitle } from '../../hooks/useDocumentTitle'
import { t as tr } from '../../i18n'

const SORTS: { id: NoteSort; label: string }[] = [
  { id: 'manual', label: tr('common.manual') },
  { id: 'edited', label: tr('notes.notesApp.edited') },
  { id: 'created', label: tr('common.created') },
  { id: 'title', label: tr('common.title') },
]

function stored<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  try {
    const v = localStorage.getItem(key) as T | null
    return v && allowed.includes(v) ? v : fallback
  } catch {
    return fallback
  }
}
function store(key: string, v: string) {
  try {
    localStorage.setItem(key, v)
  } catch {
    /* private mode */
  }
}

/** A card of the grid: a note or a table. */
type Card = Note | Table

const VIEW_TITLE: Record<NotesView, string> = {
  all: tr('common.notes'), lists: tr('notes.notesApp.lists'), tables: tr('notes.notesApp.tables'), archive: tr('common.archive'), trash: tr('common.trash'), shared: tr('notes.notesApp.sharedWithMe'), reminders: tr('common.reminders'), label: tr('common.label'), search: 'Search',
}

/** The writes of a batch on several selected cards (one undo for the
 *  batch). Each returns the server's answer with its own undo; `batch` shows one toast for all. */
const bulkWrite = {
  state: (qc: ReturnType<typeof useQueryClient>, c: Note | Table, body: Record<string, unknown>) =>
    write(qc, 'PUT', `/${c.kind === 'table' ? 'tables' : 'notes'}/${encodeURIComponent(c.id)}/state`, body, { quiet: true }),
  trash: (qc: ReturnType<typeof useQueryClient>, c: Note | Table) =>
    write(qc, 'DELETE', `/${c.kind === 'table' ? 'tables' : 'notes'}/${encodeURIComponent(c.id)}`, undefined, { quiet: true }),
  color: (qc: ReturnType<typeof useQueryClient>, c: Note, color: string | null) =>
    write(qc, 'PATCH', `/notes/${encodeURIComponent(c.id)}`, { color }, { quiet: true }),
}

/** Cards drawn at a time in the grid. */
const CARD_PAGE = 120

export default function NotesApp() {
  const { notes: route } = useAppRoute()
  const nav = useNavigate()
  const qc = useQueryClient()
  const theme = useThemeDef()
  const narrow = useMediaQuery('(max-width: 760px)')
  const online = useOnline()
  const verb = useNoteVerbs()
  const placement = narrow ? 'sheet' : theme.apps.noteDetail
  const cardW = narrow ? 150 : theme.id === 'desk' ? 220 : 240
  const gap = narrow ? 8 : 14
  const open = route?.open ?? null
  const ui = useNotesUI()
  const shownNote = useNote(open)
  useDocumentTitle(open ? shownNote.data?.title || tr('common.note') : null, tr('common.notes'))

  // Opening a note keeps the grid it was opened from.
  const [listState, setListState] = useState<{ view: NotesView; label: string | null; q: string }>({ view: route?.view ?? 'all', label: route?.label ?? null, q: route?.q ?? '' })
  useEffect(() => {
    if (route && !route.open) setListState({ view: route.view, label: route.label, q: route.q })
  }, [route])
  const { view, label, q } = listState
  const [sort, setSort] = useState<NoteSort>(() => stored('mailr.notes.sort', 'manual', ['manual', 'edited', 'created', 'title'] as const))
  const [layout, setLayout] = useState<'grid' | 'list'>(() => stored('mailr.notes.layout', 'grid', ['grid', 'list'] as const))
  const [drawer, setDrawer] = useState(false)
  const [capture, setCapture] = useState<'text' | 'list' | null>(null)
  const [fresh, setFresh] = useState<string | null>(null)
  const [emptyAsk, setEmptyAsk] = useState(false)
  const [newTable, setNewTable] = useState(false)
  const tverb = useTableVerbs()
  const sortA = useAnchor<HTMLButtonElement>()
  const moreA = useAnchor<HTMLButtonElement>()
  const [importKeep, setImportKeep] = useState(false)

  const listView = view === 'search' ? 'all' : view === 'tables' || view === 'shared' ? view : view
  const notesQ = useNotes(listView, label, sort, view !== 'search')
  const searchQ = useNoteSearchAll(q, 50, view === 'search')
  const labelsQ = useNoteLabels()
  const labels = useMemo(() => sortLabels(labelsQ.data ?? notesQ.data?.labels ?? []), [labelsQ.data, notesQ.data?.labels])

  const notes = useMemo(() => {
    if (view === 'search') return searchQ.data?.notes ?? []
    if (view === 'tables') return []
    return notesQ.data?.notes ?? []
  }, [view, searchQ.data, notesQ.data])
  // Tables sit in the same grid, in the viewer's one manual order.
  const tables = useMemo(() => (view === 'search' ? searchQ.data?.tables ?? [] : notesQ.data?.tables ?? []), [view, searchQ.data, notesQ.data])
  const cards = useMemo<Card[]>(() => {
    const base: Card[] = [...notes, ...tables]
    if (view === 'search' || view === 'reminders') return base // reminders: the server's order, next reminder first
    if (view === 'trash') return base.sort((a, b) => (b.deleted_at ?? '').localeCompare(a.deleted_at ?? ''))
    const sorted = sortNotes(base, sort)
    // Shared with me: by owner, then the sort.
    return view === 'shared' ? [...sorted].sort((a, b) => (a.owner?.name ?? '').localeCompare(b.owner?.name ?? '')) : sorted
  }, [view, notes, tables, sort])
  const withPinned = view !== 'archive' && view !== 'trash' && view !== 'search' && view !== 'reminders'
  const { pinned, others } = splitPinned(cards, withPinned)
  const order = useMemo(() => [...pinned, ...others].map((n) => n.id), [pinned, others])
  // The grid draws its cards a page at a time (up to 2000 notes): the next page when the end
  // comes into view, and always far enough to hold the focused card (j/k).
  const [shown, setShown] = useState(CARD_PAGE)
  const listKey = `${view}|${label ?? ''}|${q}`
  const [shownFor, setShownFor] = useState(listKey)
  if (shownFor !== listKey) {
    setShownFor(listKey)
    setShown(CARD_PAGE)
  }
  const focusedAt = ui.focused ? others.findIndex((n) => n.id === ui.focused) : -1
  const upTo = Math.max(shown, focusedAt + 1)
  const more = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = more.current
    if (!el) return
    const io = new IntersectionObserver((e) => { if (e.some((x) => x.isIntersecting)) setShown((n) => n + CARD_PAGE) }, { rootMargin: '600px' })
    io.observe(el)
    return () => io.disconnect()
  }, [upTo, others.length])
  const loading = view === 'search' ? searchQ.isLoading : notesQ.isLoading
  const byId = (id: string | null) => notes.find((n) => n.id === id) ?? null
  const targets = (): Note[] => {
    if (ui.selected.size) return notes.filter((n) => ui.selected.has(n.id))
    const f = byId(ui.focused ?? open)
    return f ? [f] : []
  }
  const tableTargets = (): Table[] => {
    if (ui.selected.size) return tables.filter((t) => ui.selected.has(t.id))
    const f = tables.find((t) => t.id === ui.focused)
    return f ? [f] : []
  }

  const go = (v: NotesView, arg?: string | null) => {
    setDrawer(false)
    ui.setSelected(new Set())
    nav(notesPath(v, arg))
  }
  const openNote = (id: string) => {
    ui.setFocused(id)
    nav(id.startsWith('t') ? tableRoutePath(id) : notePath(id))
  }
  const closeNote = () => {
    ui.set({ history: false })
    setFresh(null)
    nav(view === 'search' ? notesPath('search', q) : notesPath(view, label))
  }

  const move = (d: 1 | -1) => {
    if (!order.length) return
    const cur = ui.focused ?? open
    const i = cur ? order.indexOf(cur) : -1
    const next = order[Math.max(0, Math.min(order.length - 1, i < 0 ? 0 : i + d))]
    ui.setFocused(next)
    const el = document.querySelector<HTMLElement>(`.nt-card[data-note="${CSS.escape(next)}"]`)
    el?.focus()
    el?.scrollIntoView({ block: 'nearest' })
  }

  // ACT note.reorder @web.notes: a drop, or Alt+←/→ on the focused card (Manual sort).
  const reorder = (id: string, target: string, where: 'before' | 'after') => {
    if (sort !== 'manual') {
      toast({ message: tr('notes.notesApp.cardsMoveByHandIn'), actionLabel: tr('notes.notesApp.sortByHand'), onAction: () => pickSort('manual') })
      return
    }
    // ACT table.pin @web.notes (a table's place in the grid is its viewer state too)
    if (id.startsWith('t')) void setTableState(qc, id, where === 'before' ? { before: target } : { after: target }, tr('notes.notesApp.tableMoved'))
    else void setState(qc, id, where === 'before' ? { before: target } : { after: target }, tr('notes.notesApp.noteMoved'))
  }
  const pickSort = (s: NoteSort) => {
    setSort(s)
    store('mailr.notes.sort', s)
  }

  const startCapture = (kind: 'text' | 'list' | 'table') => {
    if (!online || view === 'trash' || view === 'archive') return
    if (open) closeNote()
    if (kind === 'table') setNewTable(true)
    else setCapture(kind)
  }

  useAppShortcuts('notes', {
    'notes.newNote': () => startCapture('text'),
    'notes.newList': () => startCapture('list'),
    'notes.next': () => move(1),
    'notes.prev': () => move(-1),
    'notes.open': () => {
      const f = ui.focused
      if (!f || !order.includes(f)) return false
      openNote(f)
    },
    'notes.pin': () => {
      const all: Card[] = [...targets(), ...tableTargets()]
      if (all.length > 1) {
        const pin = !all.every((c) => c.state.pinned)
        void batch(qc, all, (c) => bulkWrite.state(qc, c, { pinned: pin }), tr(pin ? 'notes.notesApp.itemsPinned' : 'notes.notesApp.itemsUnpinned', { count: all.length }))
        return
      }
      targets().forEach((n) => void verb(n, 'pin'))
      tableTargets().forEach((t) => void tverb(t, 'pin'))
    },
    'notes.archive': () => {
      const all: Card[] = [...targets(), ...tableTargets()]
      if (all.length > 1) {
        const archive = !all.every((c) => c.state.archived)
        void batch(qc, all, (c) => bulkWrite.state(qc, c, { archived: archive }), tr(archive ? 'notes.notesApp.itemsArchived' : 'notes.notesApp.itemsUnarchived', { count: all.length }))
        ui.setSelected(new Set())
        return
      }
      targets().forEach((n) => void verb(n, 'archive'))
      tableTargets().forEach((t) => void tverb(t, 'archive'))
    },
    'notes.labels': () => {
      const t = [...targets(), ...tableTargets()]
      if (!t.length) return false
      const el = document.querySelector<HTMLElement>(`.nt-card[data-note="${CSS.escape(t[0].id)}"]`) ?? document.querySelector<HTMLElement>('.nt-head')
      if (el) ui.set({ labelsFor: { ids: t.map((n) => n.id), anchor: el } })
    },
    'notes.delete': () => {
      const t = targets()
      const tt = tableTargets()
      if (!t.length && !tt.length) return false
      if (view === 'trash') ui.set({ purge: [...t, ...tt].map((n) => n.id) })
      else if (t.length + tt.length > 1) {
        const all: Card[] = [...t, ...tt]
        void batch(qc, all, (c) => bulkWrite.trash(qc, c), tr('notes.notesApp.movedToTrash', { count: all.length }))
      } else {
        t.forEach((n) => void verb(n, 'delete'))
        tt.forEach((x) => void tverb(x, 'delete'))
      }
      ui.setSelected(new Set())
    },
    'notes.select': () => {
      const f = ui.focused
      if (!f) return false
      ui.toggleSelected(f)
    },
    'notes.selectAll': () => ui.setSelected(new Set(order)),
    'notes.selectNone': () => ui.setSelected(new Set()),
    'notes.moveLeft': () => {
      const f = ui.focused
      const i = f ? order.indexOf(f) : -1
      if (i > 0) reorder(f!, order[i - 1], 'before')
    },
    'notes.moveRight': () => {
      const f = ui.focused
      const i = f ? order.indexOf(f) : -1
      if (i >= 0 && i < order.length - 1) reorder(f!, order[i + 1], 'after')
    },
    // The one search box, on the Notes scope.
    'notes.search': () => focusSearch('notes'),
    'notes.goLists': () => go('lists'),
    'notes.goTables': () => go('tables'),
    'notes.toggleLeft': () => ui.set({ leftHidden: !ui.leftHidden }),
    'notes.undo': () => (undoLast(qc) ? undefined : false),
    'notes.escape': () => {
      if (open) closeNote()
      else if (capture) setCapture(null)
      else if (ui.selected.size) ui.setSelected(new Set())
      else if (view === 'search') nav(notesPath('all'))
      else return false
    },
  })

  const sidebar = (
    <Sidebar view={view} label={label} labels={labels} onGo={go}
      onDropOnLabel={(id, name) => void setState(qc, id, { add_labels: [name] }, tr('notes.notesApp.labelAdded', { itemName: name }))}
      onEditLabels={() => ui.set({ editLabels: true })} />
  )

  const captureDefault = view === 'lists' ? 'list' : view === 'tables' ? 'table' : 'text'
  // Offline the capture bar stays in place, inert (nothing moves when the line drops or comes back).
  const canCapture = (view === 'all' || view === 'lists' || view === 'label' || view === 'tables' || view === 'reminders')
  // Over the notes (Classic, Focus, Cards, and every theme on a phone) the editor is a
  // modal dialog: the focus moves in and stays in, the notes behind are inert, and closing puts
  // the focus back on the card. Beside or below the notes (Office, Desk) it is a labelled region
  // and the notes stay usable. The editor's own fields use Escape first (NoteEditor).
  const modal = placement === 'card' || placement === 'popover' || placement === 'sheet'
  const paneRef = useRef<HTMLDivElement>(null)
  useModalFocus(paneRef, !!open && modal, closeNote, { escape: false, inert: true, focusSelf: true })
  const lastOpen = useRef<string | null>(null)
  useEffect(() => {
    const was = lastOpen.current
    lastOpen.current = open
    if (!was || open) return
    // Closed: the focus goes back to the note's card when nothing else took it.
    requestAnimationFrame(() => {
      if (document.activeElement && document.activeElement !== document.body) return
      document.querySelector<HTMLElement>(`.nt-card[data-note="${CSS.escape(was)}"]`)?.focus({ preventScroll: true })
    })
  }, [open])
  const paneName = shownNote.data?.title || (shownNote.data?.kind === 'checklist' ? tr('common.list') : tr('common.note'))
  const pane = open ? (
    <div ref={paneRef} className={`nt-pane nt-pane-${placement}`} data-testid="note-pane"
      role={modal ? 'dialog' : 'region'} aria-modal={modal || undefined} aria-label={paneName}>
      <NoteEditor key={open} id={open} focusItem={route?.item} onClose={closeNote} fresh={fresh === open} />
    </div>
  ) : null

  // Handlers per note that stay the same object across renders (they call the latest functions),
  // so a memoised card is not re-rendered for a new closure.
  const latest = useRef({ openNote, reorder, toggleSelected: ui.toggleSelected, setFocused: ui.setFocused })
  useLayoutEffect(() => {
    latest.current = { openNote, reorder, toggleSelected: ui.toggleSelected, setFocused: ui.setFocused }
  })
  const handlers = useRef(new Map<string, Pick<CardProps, 'onOpen' | 'onSelect' | 'onDropOn' | 'onFocusCard'>>())
  const handlersFor = (id: string) => {
    let h = handlers.current.get(id)
    if (!h) {
      h = {
        onOpen: () => latest.current.openNote(id),
        onSelect: () => latest.current.toggleSelected(id),
        onDropOn: (dragged, where) => latest.current.reorder(dragged, id, where),
        onFocusCard: () => latest.current.setFocused(id),
      }
      handlers.current.set(id, h)
    }
    return h
  }

  const card = (n: Card) => n.kind === 'table' ? (
    <TableCard key={n.id} table={n} focused={ui.focused === n.id} selected={ui.selected.has(n.id)}
      draggable={sort === 'manual' && withPinned && online} onSelect={() => ui.toggleSelected(n.id)}
      onDropOn={(dragged, where) => reorder(dragged, n.id, where)} onFocusCard={() => ui.setFocused(n.id)} />
  ) : (
    <NoteCard key={n.id} note={n} focused={ui.focused === n.id} selected={ui.selected.has(n.id)} open={open === n.id}
      draggable={sort === 'manual' && withPinned && online} {...handlersFor(n.id)} />
  )

  const trashCount = view === 'trash' ? cards.length : 0
  return (
    <section className={`app-frame nt-app nt-theme-${theme.id} nt-detail-${placement}${narrow ? ' nt-narrow' : ''}${open ? ' nt-has-pane' : ''}${ui.leftHidden ? ' nt-left-hidden' : ''}`}
      data-testid="notes-app" aria-label={tr('common.notes')}>
      {!narrow && !ui.leftHidden && sidebar}
      {narrow && drawer && <ModalDrawer className="nt-drawer" scrimClass="nt-drawer-scrim" label={tr('notes.notesApp.notesMenu')} onClose={() => setDrawer(false)}>{sidebar}</ModalDrawer>}
      <div className="nt-center">
        <div className="nt-main-col">
          <header className="nt-head">
            {narrow && <button type="button" className="icon-btn" aria-label={tr('notes.notesApp.notesMenu')} onClick={() => setDrawer(true)}><Menu /></button>}
            <h1>{view === 'label' ? label : view === 'search' ? tr('common.search') : VIEW_TITLE[view]}</h1>
            <span className="spacer" />
            <button type="button" className="icon-btn" aria-label={layout === 'grid' ? tr('notes.notesApp.listLayout') : tr('notes.notesApp.gridLayout')} data-tip={layout === 'grid' ? tr('notes.notesApp.listLayout') : tr('notes.notesApp.gridLayout')}
              onClick={() => {
                const v = layout === 'grid' ? 'list' : 'grid'
                setLayout(v)
                store('mailr.notes.layout', v)
              }}>
              {layout === 'grid' ? <LayoutList /> : <Grid2x2 />}
            </button>
            <button type="button" className="icon-btn" aria-label={tr('notes.notesApp.sort', { label: SORTS.find((s) => s.id === sort)?.label })} data-tip={tr('common.sort')} onClick={(e) => sortA.toggle(e.currentTarget)}><ArrowDownUp /></button>
            <Popover anchor={sortA.anchor} open={sortA.open} onClose={sortA.close} placement="bottom-end" label={tr('common.sort')}>
              {SORTS.map((s) => <MenuItem key={s.id} selected={sort === s.id} onSelect={() => {
                sortA.close()
                pickSort(s.id)
              }}>{s.label}</MenuItem>)}
            </Popover>
            {/* Notes ⋮: Import from Google Keep (ImportDialog). */}
            <button type="button" className="icon-btn" aria-label={tr('notes.notesApp.moreNotesActions')} data-tip={tr('common.more')} aria-haspopup="menu" data-testid="notes-more"
              onClick={(e) => moreA.toggle(e.currentTarget)}><MoreVertical /></button>
            <Popover anchor={moreA.anchor} open={moreA.open} onClose={moreA.close} placement="bottom-end" label={tr('notes.notesApp.moreNotesActions')}>
              <MenuItem icon={<FileUp size={18} />} disabled={!online} onSelect={() => { moreA.close(); setImportKeep(true) }}>{tr('notes.notesApp.importFromGoogleKeep2')}</MenuItem>
            </Popover>
          </header>
          {importKeep && (
            <ImportDialog kind="keep" title={tr('notes.notesApp.importFromGoogleKeep')} onClose={() => setImportKeep(false)} refresh={[noteKeys.all]}
              intro={<>{tr('notes.notesApp.downloadYourNotesFromGoogle')}</>} />
          )}
          {ui.selected.size > 0 && <BulkBar notes={notes.filter((n) => ui.selected.has(n.id))} tables={tables.filter((t) => ui.selected.has(t.id))} trash={view === 'trash'} onClear={() => ui.setSelected(new Set())} />}
          <div className={`nt-scroll${layout === 'list' ? ' nt-layout-list' : ''}`}>
            {canCapture && (
              <div className={`nt-capture-slot${online ? '' : ' off'}`} inert={!online} title={online ? undefined : tr('notes.notesApp.newNotesNeedTheConnection')}>
              <CaptureBar kind={capture} defaultKind={captureDefault} label={view === 'label' ? label : null} myGroups={notesQ.data?.groups ?? []} onKind={setCapture} onTable={() => setNewTable(true)}
                remind={view === 'reminders'} onTemplates={() => ui.set({ templates: true })}
                onCreated={(n, openIt) => {
                  if (openIt) {
                    setFresh(n.id)
                    openNote(n.id)
                  }
                }} />
              </div>
            )}
            {view === 'trash' && notes.length > 0 && (
              <div className="nt-trash-head">
                <span className="muted">{tr('notes.notesApp.notesInTrashAreDeleted')}</span>
                {/* ACT note.purge @web.notes */}
                <button type="button" className="btn text sm danger" onClick={() => setEmptyAsk(true)}>{tr('notes.notesApp.emptyTrash')}</button>
              </div>
            )}
            {loading ? (
              <Masonry min={cardW} gap={gap} list={layout === 'list'}>{[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="nt-card nt-skel"><Skeleton height={14} /><Skeleton height={14} style={{ width: `${80 - i * 7}%` }} /></div>)}</Masonry>
            ) : !cards.length ? (
              <Empty view={view} q={q} />
            ) : (
              <>
                {pinned.length > 0 && (
                  <section aria-label={tr('common.pinned')}>
                    <h2 className="nt-group-head">{tr('common.pinned')}</h2>
                    <Masonry min={cardW} gap={gap} list={layout === 'list'}>{pinned.map(card)}</Masonry>
                  </section>
                )}
                <section aria-label={pinned.length ? tr('notes.notesApp.others') : VIEW_TITLE[view]}>
                  {pinned.length > 0 && others.length > 0 && <h2 className="nt-group-head">{tr('notes.notesApp.others')}</h2>}
                  <Masonry min={cardW} gap={gap} list={layout === 'list'}>{others.slice(0, upTo).map(card)}</Masonry>
                  {others.length > upTo && <div ref={more} className="nt-more" aria-hidden />}
                </section>
              </>
            )}
          </div>
        </div>
        {pane && (placement === 'pane' || placement === 'below') && pane}
      </div>
      {pane && (placement === 'card' || placement === 'popover' || placement === 'sheet') && (
        <div className={`nt-overlay nt-overlay-${placement}`} onMouseDown={(e) => e.target === e.currentTarget && closeNote()}>
          {pane}
        </div>
      )}
      <LabelsPopover notes={notes} tables={tables} />
      {newTable && <NewTableDialog label={view === 'label' ? label : null} onClose={() => setNewTable(false)} />}
      <PurgeDialog />
      <ConfirmDialog open={emptyAsk} danger title={tr('notes.notesApp.deleteNotesForever', { count: trashCount })}
        body={<>
          <p>{tr('notes.notesApp.theyCannotBeRestoredAfterwards')}</p>
          <button type="button" className="btn text sm" onClick={() => downloadText(tr('notes.notesApp.notesFromTrashMd'), notesMarkdown(notes))}>{tr('notes.notesApp.saveAMarkdownCopyFirst')}</button>
          {tables.length > 0 && <p className="muted nt-small">{tr('notes.notesApp.tablesInTrashGoToo')}</p>}
        </>}
        confirmLabel={tr('notes.notesApp.emptyTrash')} onClose={() => setEmptyAsk(false)} onConfirm={() => {
          setEmptyAsk(false)
          void emptyTrash(qc).then((r) => toast({ message: tr('notes.notesApp.notesDeletedForever', { count: (r.purged ?? trashCount) }) }))
        }} />
      {ui.editLabels && <EditLabelsDialog onClose={() => ui.set({ editLabels: false })} />}
      {ui.share && <ShareDialog kind={ui.share.kind} id={ui.share.id} name={ui.share.name} onClose={() => ui.set({ share: null })}
        onLeft={() => open === ui.share?.id && closeNote()} />}
      {ui.templates && <TemplatesDialog label={view === 'label' ? label : null} onClose={() => ui.set({ templates: false })}
        onMade={(made) => {
          if (made.note) {
            setFresh(made.note.id)
            openNote(made.note.id)
          } else if (made.table) openNote(made.table.id)
        }} />}
      {ui.repeatFor && <RepeatDialog note={ui.repeatFor} onClose={() => ui.set({ repeatFor: null })} />}
      {ui.saveTemplate && <SaveTemplateDialog kind={ui.saveTemplate.kind} id={ui.saveTemplate.id} name={ui.saveTemplate.name} onClose={() => ui.set({ saveTemplate: null })} />}
    </section>
  )
}

// ---------------------------------------------------------------- left column

function NavItem({ active, icon, label, count, onClick, onDrop, testid }: { active: boolean; icon: ReactNode; label: string; count?: number; onClick: () => void; onDrop?: (e: DragEvent) => void; testid?: string }) {
  const [over, setOver] = useState(false)
  return (
    <button type="button" className={`nt-nav${active ? ' active' : ''}${over ? ' drop' : ''}`} aria-current={active ? 'page' : undefined} onClick={onClick} data-testid={testid}
      onDragOver={onDrop ? (e) => {
        if (e.dataTransfer.types.includes('application/x-mailr-note')) {
          e.preventDefault()
          setOver(true)
        }
      } : undefined}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop ? (e) => {
        setOver(false)
        onDrop(e)
      } : undefined}>
      <span className="nt-nav-icon" aria-hidden>{icon}</span>
      <span className="nt-nav-label">{label}</span>
      {count !== undefined && count > 0 && <span className="nt-nav-count">{count}</span>}
    </button>
  )
}

function Sidebar({ view, label, labels, onGo, onDropOnLabel, onEditLabels }: {
  view: NotesView; label: string | null; labels: { name: string; color: string | null; count: number }[]
  onGo: (v: NotesView, arg?: string | null) => void; onDropOnLabel: (noteId: string, label: string) => void; onEditLabels: () => void
}) {
  return (
    <nav className="nt-side" aria-label={tr('common.notes')} data-testid="notes-sidebar">
      <ShareNotices />
      <NavItem active={view === 'all'} icon={<StickyNote />} label={tr('common.notes')} onClick={() => onGo('all')} />
      <NavItem active={view === 'lists'} icon={<ListChecks />} label={tr('notes.notesApp.lists')} onClick={() => onGo('lists')} />
      <NavItem active={view === 'tables'} icon={<Table2 />} label={tr('notes.notesApp.tables')} onClick={() => onGo('tables')} />
      {/* The Reminders view: notes with a reminder of this viewer's, next first. */}
      <NavItem active={view === 'reminders'} icon={<Bell />} label={tr('common.reminders')} onClick={() => onGo('reminders')} testid="notes-nav-reminders" />
      <div className="nt-side-head">
        <span>{tr('common.labels')}</span>
        {/* ACT notelabel.rename @web.notes, ACT notelabel.color @web.notes, ACT notelabel.reorder @web.notes, ACT notelabel.delete @web.notes */}
        <button type="button" className="icon-btn sm" aria-label={tr('notes.notesApp.editLabels')} data-tip={tr('notes.notesApp.editLabels')} onClick={onEditLabels}><Pencil size={16} /></button>
      </div>
      {labels.map((l) => (
        <NavItem key={l.name} active={view === 'label' && label === l.name} icon={<Tag style={l.color ? { color: colorHex(l.color) ?? undefined } : undefined} />} label={l.name} count={l.count}
          onClick={() => onGo('label', l.name)}
          onDrop={(e) => {
            const id = e.dataTransfer.getData('application/x-mailr-note')
            if (id) {
              e.preventDefault()
              onDropOnLabel(id, l.name)
            }
          }} />
      ))}
      {!labels.length && <button type="button" className="nt-nav dim" onClick={onEditLabels}><span className="nt-nav-icon" aria-hidden><Plus /></span><span className="nt-nav-label">{tr('notes.notesApp.createALabel')}</span></button>}
      <div className="nt-side-sep" />
      <NavItem active={view === 'shared'} icon={<Share2 />} label={tr('notes.notesApp.sharedWithMe')} onClick={() => onGo('shared')} />
      <NavItem active={view === 'archive'} icon={<Archive />} label={tr('common.archive')} onClick={() => onGo('archive')} />
      <NavItem active={view === 'trash'} icon={<Trash2 />} label={tr('common.trash')} onClick={() => onGo('trash')} testid="notes-nav-trash" />
    </nav>
  )
}

function Empty({ view, q }: { view: NotesView; q: string }) {
  const [icon, title, body]: [ReactNode, string, string] =
    view === 'trash' ? [<Trash2 key="i" />, tr('notes.notesApp.noNotesInTrash'), tr('notes.notesApp.notesInTrashAreDeleted')]
      : view === 'archive' ? [<Archive key="i" />, tr('notes.notesApp.noArchivedNotes'), tr('notes.notesApp.notesYouArchiveAppearHere')]
        : view === 'label' ? [<Tag key="i" />, tr('notes.notesApp.noNotesWithThisLabel'), tr('notes.notesApp.notesYouGiveThisLabel')]
          : view === 'search' ? [<Search key="i" />, tr('notes.notesApp.nothingMatches', { q }), tr('notes.notesApp.searchLooksInTitlesText')]
            : view === 'tables' ? [<Table2 key="i" />, tr('notes.notesApp.noTablesYet'), tr('notes.notesApp.makeOneWithNewTable')]
              : view === 'reminders' ? [<Bell key="i" />, tr('notes.notesApp.noReminders'), tr('notes.notesApp.notesWithAReminderAppear')]
              : view === 'shared' ? [<Share2 key="i" />, tr('notes.notesApp.nothingSharedWithYou'), tr('notes.notesApp.notesAndTablesOtherPeople')]
                : view === 'lists' ? [<ListChecks key="i" />, tr('notes.notesApp.noListsYet'), tr('notes.notesApp.listsYouAddAppearHere')]
                  : [<StickyNote key="i" />, tr('notes.notesApp.notesYouAddAppearHere'), tr('notes.notesApp.takeANoteOrStart')]
  return (
    <div className="nt-empty" data-testid="notes-empty">
      <span className="nt-empty-icon" aria-hidden>{icon}</span>
      <h2>{title}</h2>
      <p className="muted">{body}</p>
    </div>
  )
}

// ---------------------------------------------------------------- capture bar

/** "Take a note…" with New list, New table and "New list in <group>". A click opens an inline card:
 * title and body, or title and an item line. Esc or a click outside saves; an empty card saves
 * nothing. A label view adds its label; a group list belongs to the group (every member edits). */
// ACT note.create @web.notes
function CaptureBar({ kind, defaultKind, label, myGroups, onKind, onTable, onCreated, remind, onTemplates }: {
  kind: 'text' | 'list' | null; defaultKind: 'text' | 'list' | 'table'; label: string | null; myGroups: string[]
  onKind: (k: 'text' | 'list' | null) => void; onTable: () => void; onCreated: (n: Note, open: boolean) => void
  /** The Reminders view: the new note gets a reminder (a text note with a reminder). */
  remind?: boolean
  onTemplates: () => void
}) {
  const qc = useQueryClient()
  const rp = useRemindPrefs()
  const presets = remindPresets(new Date(), rp)
  const [when, setWhen] = useState<string>('tomorrow')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  // "New list in Household": a list of one of the user's groups.
  const [group, setGroup] = useState<string | null>(null)
  const groupA = useAnchor<HTMLButtonElement>()
  const ref = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  const saving = useRef(false)

  const save = async () => {
    if (saving.current || !kind) return
    const t = title.trim()
    const b = body.trim()
    const g = group
    onKind(null)
    setTitle('')
    setBody('')
    setGroup(null)
    if (!t && !b) return
    saving.current = true
    try {
      const { note: n, undo } = await createNoteWithUndo(qc, {
        kind: kind === 'list' ? 'checklist' : 'text', title: t,
        ...(kind === 'list' ? { lines: b } : { body: body.replace(/\s+$/, '') }),
        ...(label ? { labels: [label] } : {}),
        ...(g ? { group: g } : {}),
      }, { quiet: true })
      const at = remind ? (presets.find((x) => x.id === when) ?? presets[0]).at : null
      // ACT note.remind @web.notes: a note taken in the Reminders view gets the reminder chosen in its footer.
      if (at) await setReminders(qc, n.id, [at.toISOString()], tr('notes.notesApp.reminder', { remindLabel: remindLabel(at.toISOString(), new Date(), rp) }))
      // A create toast offers Open and Undo.
      else toast({
        message: g ? tr('notes.notesApp.listAddedTo', { g }) : kind === 'list' ? tr('notes.notesApp.listAdded') : tr('notes.notesApp.noteAdded'), actionLabel: tr('common.open'), onAction: () => onCreated(n, true), duration: 8000,
        ...(undo ? { secondaryLabel: tr('common.undo'), onSecondary: () => void runUndo(qc, undo) } : {}),
      })
    } finally {
      saving.current = false
    }
  }

  useEffect(() => {
    if (!kind) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node) && !(e.target as HTMLElement).closest('.popover, .toast')) void save()
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  })

  if (!kind) {
    return (
      <div className="nt-capture" data-testid="notes-capture">
        <button type="button" className="nt-capture-take" onClick={() => (defaultKind === 'table' ? onTable() : onKind(defaultKind))}>{defaultKind === 'list' ? tr('notes.notesApp.newList') : defaultKind === 'table' ? tr('notes.notesApp.newTable') : tr('notes.notesApp.takeANote')}</button>
        <button type="button" className="icon-btn" aria-label={tr('common.newList')} data-tip={tr('notes.notesApp.newListL')} onClick={() => onKind('list')}><ListChecks /></button>
        {/* ACT table.create @web.notes */}
        <button type="button" className="icon-btn" aria-label={tr('notes.notesApp.newTable2')} data-tip={tr('notes.notesApp.newTable2')} onClick={onTable}><Table2 /></button>
        {/* ACT note.from_template @web.notes: the gallery (templates.tsx) */}
        <button type="button" className="icon-btn" aria-label={tr('notes.notesApp.newFromTemplate')} data-tip={tr('notes.notesApp.newFromTemplate')} data-testid="notes-from-template" onClick={onTemplates}><LayoutTemplate /></button>
        {myGroups.length > 0 && (
          <>
            <button type="button" className="icon-btn" aria-label={tr('notes.notesApp.aListForOneOf')} data-tip={tr('notes.notesApp.newListInAGroup')} data-testid="notes-new-group-list"
              onClick={(e) => groupA.toggle(e.currentTarget)}><Users /></button>
            <Popover anchor={groupA.anchor} open={groupA.open} onClose={groupA.close} placement="bottom-end" label={tr('notes.notesApp.newListInAGroup')}>
              {myGroups.map((g) => (
                <MenuItem key={g} icon={<ListChecks size={16} />} onSelect={() => {
                  groupA.close()
                  setGroup(g)
                  onKind('list')
                }}>{tr('notes.notesApp.newListIn', { g })}</MenuItem>
              ))}
            </Popover>
          </>
        )}
      </div>
    )
  }
  return (
    <div className="nt-capture open" ref={ref} data-testid="notes-capture" data-kind={kind}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          void save()
        }
      }}>
      <input className="nt-capture-title" value={title} placeholder={tr('common.title')} aria-label={tr('common.title')} autoFocus={kind === 'text'} maxLength={500}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            bodyRef.current?.focus()
          }
        }} />
      <textarea ref={bodyRef} className="nt-capture-body" value={body} autoFocus={kind === 'list'} rows={kind === 'list' ? 2 : 3}
        placeholder={kind === 'list' ? tr('notes.notesApp.itemsOnePerLineOr') : tr('notes.notesApp.takeANote')}
        aria-label={kind === 'list' ? tr('notes.notesApp.items') : tr('common.note')} onChange={(e) => setBody(e.target.value)} />
      <div className="nt-capture-foot">
        {group && <span className="nt-label-chip" data-testid="capture-group"><Users size={12} aria-hidden /> {group}</span>}
        {remind && (
          <label className="nt-capture-remind">
            <Bell size={14} aria-hidden />
            <select aria-label={tr('common.remindMe')} value={when} onChange={(e) => setWhen(e.target.value)}>
              {presets.map((x) => <option key={x.id} value={x.id}>{x.label} ({remindLabel(x.at.toISOString(), new Date(), rp)})</option>)}
            </select>
          </label>
        )}
        <span className="muted nt-small">{kind === 'list' ? (group ? tr('notes.notesApp.everyoneInCanEditThis', { group }) : tr('notes.notesApp.quantitiesAndSectionsAreRead')) : ''}</span>
        <span className="spacer" />
        <button type="button" className="btn text sm" onClick={() => void save()}>{tr('common.close')}</button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- selection

/** The bar over a selection: one write per card, and one toast with one Undo for the whole batch
 *  ACT note.pin @web.notes, ACT note.archive @web.notes,
 *  ACT note.color @web.notes, ACT note.delete @web.notes, ACT table.pin @web.notes, ACT table.archive @web.notes,
 *  ACT table.delete @web.notes */
function BulkBar({ notes, tables, trash, onClear }: { notes: Note[]; tables: Table[]; trash: boolean; onClear: () => void }) {
  const qc = useQueryClient()
  const colour = useAnchor<HTMLButtonElement>()
  const setUI = useNotesUI((s) => s.set)
  const all: Card[] = [...notes, ...tables]
  const n = all.length
  const pin = !all.every((c) => c.state.pinned)
  const archive = !all.every((c) => c.state.archived)
  return (
    <div className="nt-bulk" role="toolbar" aria-label={tr('notes.notesApp.selectedNotes')} data-testid="notes-bulk">
      <button type="button" className="icon-btn sm" aria-label={tr('notes.notesApp.clearTheSelection')} onClick={onClear}><X size={18} /></button>
      <span>{tr('notes.notesApp.selected', { n })}</span>
      <span className="spacer" />
      {trash ? (
        <button type="button" className="btn text sm danger" onClick={() => setUI({ purge: all.map((x) => x.id) })}>{tr('common.deleteForever')}</button>
      ) : (
        <>
          <button type="button" className="btn text sm" onClick={() => void batch(qc, all, (c) => bulkWrite.state(qc, c, { pinned: pin }), tr(pin ? 'notes.notesApp.itemsPinned' : 'notes.notesApp.itemsUnpinned', { count: n }))}>{pin ? tr('common.pin') : tr('common.unpin')}</button>
          <button type="button" className="btn text sm" onClick={() => {
            void batch(qc, all, (c) => bulkWrite.state(qc, c, { archived: archive }), tr(archive ? 'notes.notesApp.itemsArchived' : 'notes.notesApp.itemsUnarchived', { count: n }))
            onClear()
          }}>{archive ? tr('common.archive') : tr('common.unarchive')}</button>
          <button type="button" className="btn text sm" onClick={(e) => setUI({ labelsFor: { ids: all.map((x) => x.id), anchor: e.currentTarget } })}>{tr('common.labels')}</button>
          {notes.length > 0 && <button type="button" className="btn text sm" onClick={(e) => colour.toggle(e.currentTarget)}>{tr('common.colour')}</button>}
          <button type="button" className="btn text sm danger" onClick={() => {
            void batch(qc, all, (c) => bulkWrite.trash(qc, c), tr('notes.notesApp.movedToTrash', { count: n }))
            onClear()
          }}>{tr('common.delete2')}</button>
          <Popover anchor={colour.anchor} open={colour.open} onClose={colour.close} role="dialog" label={tr('common.colour')} placement="bottom-end">
            <ColorGrid value={null} onPick={(c) => {
              colour.close()
              void batch(qc, notes, (x) => bulkWrite.color(qc, x, c), c ? tr('notes.notesApp.colour', { colorName: colorName(c) }) : tr('common.defaultColour'))
            }} />
          </Popover>
        </>
      )}
    </div>
  )
}

function LabelsPopover({ notes, tables }: { notes: Note[]; tables: Table[] }) {
  const qc = useQueryClient()
  const labelsFor = useNotesUI((s) => s.labelsFor)
  const set = useNotesUI((s) => s.set)
  const labels = useNoteLabels().data ?? []
  const open = useMemo(() => {
    if (!labelsFor) return []
    const fromGrid = notes.filter((n) => labelsFor.ids.includes(n.id))
    const fromCache = labelsFor.ids.filter((id) => !fromGrid.some((n) => n.id === id)).map((id) => qc.getQueryData<Note>(['notes', 'note', id])).filter(Boolean) as Note[]
    return [...fromGrid, ...fromCache, ...tables.filter((t) => labelsFor.ids.includes(t.id))]
  }, [labelsFor, notes, tables, qc])
  // One card: its own write and toast. Several selected: one toast with one Undo for all of them.
  const labelAll = (cards: (Note | Table)[], body: { add_labels?: string[]; remove_labels?: string[] }, label: string) => {
    if (cards.length === 1) void (cards[0].id.startsWith('t') ? setTableState : setState)(qc, cards[0].id, body, label)
    else void batch(qc, cards, (c) => bulkWrite.state(qc, c, body), label)
  }
  return (
    <Popover anchor={labelsFor?.anchor ?? null} open={!!labelsFor} onClose={() => set({ labelsFor: null })} placement="bottom-start" label={tr('common.labels')} minWidth={240}>
      {labelsFor && <LabelsMenu notes={open} labels={labels}
        onAdd={(name) => labelAll(open, { add_labels: [name] }, tr('notes.notesApp.labelAdded', { itemName: name }))}
        onRemove={(name) => labelAll(open, { remove_labels: [name] }, tr('notes.notesApp.labelRemoved', { itemName: name }))} />}
    </Popover>
  )
}

function PurgeDialog() {
  const qc = useQueryClient()
  const purge = useNotesUI((s) => s.purge)
  const set = useNotesUI((s) => s.set)
  const n = purge?.length ?? 0
  return (
    <ConfirmDialog open={!!purge} danger title={tr('notes.notesApp.deleteNotesForever2', { count: n })}
      body={<>
        <p>{tr('notes.notesApp.itCannotBeRestoredAfterwards')}</p>
        <button type="button" className="btn text sm" onClick={() => {
          const notes = (purge ?? []).map((id) => qc.getQueryData<Note>(['notes', 'note', id])).filter(Boolean) as Note[]
          const cards = (qc.getQueriesData<{ notes: Note[] }>({ queryKey: ['notes', 'list'] }).flatMap(([, d]) => d?.notes ?? [])).filter((x) => purge?.includes(x.id))
          downloadText(tr('notes.notesApp.deletedNotesMd'), notesMarkdown(notes.length ? notes : cards))
        }}>{tr('notes.notesApp.saveAMarkdownCopyFirst')}</button>
      </>}
      confirmLabel={tr('common.deleteForever')} onClose={() => set({ purge: null })} onConfirm={() => {
        const ids = purge ?? []
        set({ purge: null })
        useNotesUI.getState().setSelected(new Set())
        ids.forEach((id) => void (id.startsWith('t') ? purgeTable(qc, id) : purgeNote(qc, id)))
      }} />
  )
}

// ---------------------------------------------------------------- Edit labels

function EditLabelsDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const labels = sortLabels(useNoteLabels().data ?? [])
  const [name, setName] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)
  return (
    <Dialog open onClose={onClose} title={tr('notes.notesApp.editLabels')} footer={<button type="button" className="btn filled" onClick={onClose}>{tr('common.done')}</button>}>
      <form className="nt-label-new" onSubmit={(e) => {
        e.preventDefault()
        const t = name.trim()
        if (!t) return
        // ACT notelabel.create @web.notes
        void createLabel(qc, t)
        setName('')
      }}>
        <Plus size={18} aria-hidden />
        <input value={name} maxLength={50} placeholder={tr('notes.notesApp.createANewLabel')} aria-label={tr('notes.notesApp.newLabel')} onChange={(e) => setName(e.target.value)} />
        <button type="submit" className="btn text sm" disabled={!name.trim()}>{tr('common.create')}</button>
      </form>
      <ul className="nt-label-edit">
        {labels.map((l, i) => <LabelRow key={l.name} label={l} first={i === 0} last={i === labels.length - 1} prev={labels[i - 1]?.name} next={labels[i + 1]?.name} onDelete={() => setDeleting(l.name)} />)}
      </ul>
      <ConfirmDialog open={!!deleting} title={tr('notes.notesApp.deleteTheLabel', { deleting: deleting ?? '' })} body={tr('notes.notesApp.itComesOffEveryNote')} confirmLabel={tr('common.delete2')} danger
        onClose={() => setDeleting(null)} onConfirm={() => {
          const d = deleting!
          setDeleting(null)
          void deleteLabel(qc, d)
        }} />
    </Dialog>
  )
}

function LabelRow({ label, first, last, prev, next, onDelete }: { label: { name: string; color: string | null; count: number }; first: boolean; last: boolean; prev?: string; next?: string; onDelete: () => void }) {
  const qc = useQueryClient()
  const [v, setV] = useState(label.name)
  // The swatches open inside the row: a popover would sit under the dialog.
  const [colours, setColours] = useState(false)
  useEffect(() => setV(label.name), [label.name])
  const rename = () => {
    const t = v.trim()
    if (t && t !== label.name) void patchLabel(qc, label.name, { name: t }, tr('notes.notesApp.labelRenamedTo', { t }))
    else setV(label.name)
  }
  return (
    <li>
      <div className="nt-label-line">
        <button type="button" className="icon-btn sm" aria-label={tr('notes.notesApp.colourOf', { labelName: label.name, colorName: colorName(label.color) })} aria-expanded={colours} data-tip={tr('common.colour')} onClick={() => setColours((c) => !c)}>
          <Tag size={16} style={label.color ? { color: colorHex(label.color) ?? undefined } : undefined} />
        </button>
        <input value={v} maxLength={50} aria-label={tr('notes.notesApp.nameOf', { labelName: label.name })} onChange={(e) => setV(e.target.value)} onBlur={rename} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
        <span className="muted nt-small">{label.count}</span>
        <button type="button" className="icon-btn sm" aria-label={tr('notes.notesApp.moveUp', { labelName: label.name })} disabled={first} onClick={() => prev && void patchLabel(qc, label.name, { before: prev }, tr('notes.notesApp.labelMoved'))}>↑</button>
        <button type="button" className="icon-btn sm" aria-label={tr('notes.notesApp.moveDown', { labelName: label.name })} disabled={last} onClick={() => next && void patchLabel(qc, label.name, { after: next }, tr('notes.notesApp.labelMoved'))}>↓</button>
        <button type="button" className="icon-btn sm" aria-label={tr('notes.notesApp.delete', { labelName: label.name })} data-tip={tr('common.delete2')} onClick={onDelete}><Trash2 size={16} /></button>
      </div>
      {colours && <ColorGrid value={label.color} onPick={(c) => {
        setColours(false)
        void patchLabel(qc, label.name, { color: c }, tr('notes.notesApp.colour', { colorName: colorName(c) }))
      }} />}
    </li>
  )
}
