// The links of a conversation, an event, a task or a recording: the notes and tables linked to it as compact chips that open them, with × to unlink and
// the undo toast; a list item turned into a task shows "From the list …", opening the list on that
// item (checkitem.to_task's round trip). One "Link a note or table" button opens a search over Notes
// whose last row, "New note", makes a text note named after the item, links it and opens it. With
// nothing linked only that button shows. The reader draws the chips on its subject line and puts the
// button in its toolbar. Writes wait while offline.

import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { ListChecks, Plus, StickyNote, Table2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { request } from '../../api/client'
import { Popover, useAnchor } from '../../components/Popover'
import { useOnline } from '../../hooks/useOnline'
import { notePath, tableRoutePath } from '../../lib/routes'
import { toast, toastError } from '../../stores/ui'
import { linkTable, unlinkTable } from '../tables/actions'
import { useLinkedTables } from '../tables/api'
import { createNoteWithUndo, linkNote, unlinkNote } from './actions'
import { runUndo, useLinkedNotes, useNoteSearchAll } from './api'
import { linkedRef, type LinkKind } from './LinkPicker'
import './notes.css'
import { t as tr } from '../../i18n'

export interface LinkedPanelProps {
  kind: LinkKind
  /** The link's target_ref: {conversation_id} | {uid, occurrence?} | {uid} | {connector_id, recording_id}. */
  target: Record<string, unknown>
  /** The item's title, for "New note". */
  title: string
  className?: string
}

/** Both links panels' reads (notes and tables) after a link changed. */
export function refreshLinked(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: ['notes', 'linked'] })
  void qc.invalidateQueries({ queryKey: ['tables', 'linked'] })
}

/** The notes and tables linked to one item, and the writes on them. */
export function useLinked(kind: LinkKind, target: Record<string, unknown>, title: string) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const online = useOnline()
  const ref = linkedRef(kind, target)
  const notes = useLinkedNotes(kind, ref).data ?? []
  const tables = useLinkedTables(kind === 'recording' ? 'recording' : kind, ref).data
  const [busy, setBusy] = useState(false)
  const whole = notes.filter((n) => !n.item_id)
  const fromItems = notes.filter((n) => n.item_id)
  const run = async (p: Promise<unknown>) => {
    try {
      await p
    } finally {
      refreshLinked(qc)
    }
  }
  return {
    ref, online, busy, whole, fromItems,
    tables: tables?.tables ?? [],
    rows: tables?.rows ?? [],
    count: whole.length + fromItems.length + (tables?.tables?.length ?? 0) + (tables?.rows?.length ?? 0),
    exclude: new Set([...whole.map((n) => n.id), ...(tables?.tables ?? []).map((t) => t.id)]),
    open: (path: string) => nav(path),
    unlinkNote: (noteId: string, linkId: number) => void run(unlinkNote(qc, noteId, linkId)),
    unlinkTable: (tableId: string, linkId: number) => void run(unlinkTable(qc, tableId, linkId)),
    link: (id: string, isTable: boolean) => void run(isTable ? linkTable(qc, id, kind, target) : linkNote(qc, id, kind, target)),
    /** A text note named after the item, linked to it, then opened. */
    newNote: async () => {
      if (busy) return
      setBusy(true)
      try {
        const { note: n, undo } = await createNoteWithUndo(qc, { kind: 'text', title }, { quiet: true })
        await request(`/notes/${encodeURIComponent(n.id)}/links`, { method: 'POST', body: { target_kind: kind, target_ref: target } as never })
        refreshLinked(qc)
        // The note opens at once; Undo takes it away again.
        toast({ message: tr('notes.linkedPanel.noteAddedAndLinked'), ...(undo ? { secondaryLabel: tr('common.undo'), onSecondary: () => void runUndo(qc, undo).then(() => refreshLinked(qc)) } : {}), duration: 7000 })
        nav(notePath(n.id))
      } catch (e) {
        toastError(e, tr('notes.linkedPanel.couldNotMakeTheNote'))
      } finally {
        setBusy(false)
      }
    },
  }
}
export type Linked = ReturnType<typeof useLinked>

/** The linked notes, list items, tables and table rows as chips; nothing when none is linked.
 *  `as="span"` keeps them inline, in a line of other chips (the reader's subject line). */
export function LinkedChips({ linked: l, as: Tag = 'div', className, testId = 'linked-chips' }: { linked: Linked; as?: 'div' | 'span'; className?: string; testId?: string }) {
  if (!l.count) return null
  return (
    <Tag className={`nt-linked-chips${className ? ` ${className}` : ''}`} role="list" aria-label={tr('notes.linkedPanel.linkedNotesAndTables')} data-testid={testId}>
      {l.whole.map((n) => {
        const name = n.title || (n.kind === 'checklist' ? tr('common.untitledList') : tr('common.untitledNote'))
        return (
          <span key={`n:${n.link_id}`} role="listitem" className="nt-link-chip" data-testid="linked-note">
            <button type="button" title={name} onClick={() => l.open(notePath(n.id))}>
              {n.kind === 'checklist' ? <ListChecks size={14} aria-hidden /> : <StickyNote size={14} aria-hidden />} <span className="nt-link-name">{name}</span>
            </button>
            {l.online && <button type="button" aria-label={tr('notes.linkedPanel.unlink', { title: n.title || tr('notes.linkedPanel.theNote') })} data-tip={tr('notes.linkedPanel.unlink2')} onClick={() => l.unlinkNote(n.id, n.link_id)}><X size={12} /></button>}
          </span>
        )
      })}
      {l.fromItems.map((n) => (
        <span key={`i:${n.link_id}`} role="listitem" className="nt-link-chip" data-testid="linked-list-item">
          <button type="button" onClick={() => l.open(notePath(n.id, n.item_id))}><ListChecks size={14} aria-hidden /> <span className="nt-link-name">{tr('notes.linkedPanel.fromTheList', { title: n.title || tr('common.untitledList') })}</span></button>
        </span>
      ))}
      {l.tables.map((t) => (
        <span key={`t:${t.link_id}`} role="listitem" className="nt-link-chip" data-testid="linked-table">
          <button type="button" title={t.name || tr('common.untitledTable')} onClick={() => l.open(tableRoutePath(t.id))}><Table2 size={14} aria-hidden /> <span className="nt-link-name">{t.name || tr('common.untitledTable')}</span></button>
          {l.online && <button type="button" aria-label={tr('notes.linkedPanel.unlink3', { itemName: t.name || tr('notes.linkedPanel.theTable') })} data-tip={tr('notes.linkedPanel.unlink2')} onClick={() => l.unlinkTable(t.id, t.link_id)}><X size={12} /></button>}
        </span>
      ))}
      {l.rows.map((r) => (
        <span key={`r:${r.table}:${r.row}:${r.column}`} role="listitem" className="nt-link-chip" title={`${r.table_name} · ${r.column}`}>
          <button type="button" onClick={() => l.open(tableRoutePath(r.table, null, r.row))}><Table2 size={14} aria-hidden /> <span className="nt-link-name">{r.title}<span className="muted"> · {r.table_name}</span></span></button>
        </span>
      ))}
    </Tag>
  )
}

/** "Link a note or table": the search over Notes in a popover. `withNew` adds the "New note" row. */
export function LinkPickerPopover({ linked: l, anchor, open, onClose, withNew }: {
  linked: Linked; anchor: HTMLElement | DOMRect | null; open: boolean; onClose: () => void; withNew?: string
}) {
  return (
    <Popover anchor={anchor} open={open} onClose={onClose} role="dialog" label={tr('notes.linkedPanel.linkANoteOrTable')} placement="bottom-start" className="nt-link-pop">
      <NotePicker exclude={l.exclude} onClose={onClose}
        onPick={(id, isTable) => {
          onClose()
          l.link(id, isTable)
        }}
        onNew={withNew != null ? () => { onClose(); void l.newNote() } : undefined} newTitle={withNew} newBusy={l.busy} />
    </Popover>
  )
}

/** The links of an event, a task or a recording: the chips and one "Link a note or table" button,
 *  on one wrapping line. */
export function LinkedPanel({ kind, target, title, className }: LinkedPanelProps) {
  const l = useLinked(kind, target, title)
  const pickA = useAnchor<HTMLButtonElement>()
  if (l.ref == null) return null
  return (
    <div className={`nt-linked${className ? ` ${className}` : ''}`} data-testid="linked-panel">
      <LinkedChips linked={l} />
      {l.online && (
        <button type="button" className="nt-link-add" aria-haspopup="dialog" aria-expanded={pickA.open} onClick={(e) => pickA.toggle(e.currentTarget)}>
          <Plus size={14} aria-hidden />{' '}{tr('notes.linkedPanel.linkANoteOrTable')}
        </button>
      )}
      <LinkPickerPopover linked={l} anchor={pickA.anchor} open={pickA.open} onClose={pickA.close} withNew={title} />
    </div>
  )
}

/** A search over the user's notes and tables, for "Link a note or table". The pointer moves the
 *  highlight only when it moves (LinkPicker.tsx); a click or Enter links. */
function NotePicker({ exclude, onPick, onClose, onNew, newTitle, newBusy }: {
  exclude: Set<string>; onPick: (id: string, isTable: boolean) => void; onClose: () => void
  /** "New note": a text note named after the item, linked to it. */
  onNew?: () => void; newTitle?: string; newBusy?: boolean
}) {
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  useEffect(() => {
    const t = window.setTimeout(() => setQ(text.trim()), 250)
    return () => window.clearTimeout(t)
  }, [text])
  const res = useNoteSearchAll(q, 8, !!q)
  const options = [
    ...(res.data?.notes ?? []).map((n) => ({ id: n.id, table: false, kind: n.kind as string, title: n.title || (n.kind === 'checklist' ? tr('common.untitledList') : tr('common.untitledNote')) })),
    ...(res.data?.tables ?? []).map((t) => ({ id: t.id, table: true, kind: 'table', title: t.name || tr('common.untitledTable') })),
  ].filter((o) => !exclude.has(o.id))
  useEffect(() => setSel(0), [options.length])
  return (
    <div className="nt-link-picker" data-testid="note-link-picker">
      <input className="input" type="search" value={text} autoFocus data-autofocus placeholder={tr('notes.linkedPanel.searchNotesAndTables')} aria-label={tr('notes.linkedPanel.searchNotesAndTables')}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setSel((s) => Math.min(options.length - 1, s + 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setSel((s) => Math.max(0, s - 1))
          } else if (e.key === 'Enter') {
            e.preventDefault()
            const o = options[sel]
            if (o) onPick(o.id, o.table)
          } else if (e.key === 'Escape') {
            e.preventDefault()
            e.stopPropagation()
            onClose()
          }
        }} />
      <ul className="nt-link-results" role="listbox" aria-label={tr('common.notesAndTables')}>
        {options.map((o, i) => (
          <li key={o.id} role="presentation">
            <button type="button" role="option" aria-selected={i === sel} className={`nt-link-opt${i === sel ? ' active' : ''}`}
              onMouseMove={() => i !== sel && setSel(i)} onClick={() => onPick(o.id, o.table)}>
              {o.table ? <Table2 size={14} /> : o.kind === 'checklist' ? <ListChecks size={14} /> : <StickyNote size={14} />}
              <span className="nt-link-opt-title">{o.title}</span>
            </button>
          </li>
        ))}
      </ul>
      <p className="muted nt-small" aria-live="polite">
        {!q ? tr('notes.linkedPanel.typeWordsFromATitle') : res.isFetching && !options.length ? tr('notes.linkedPanel.searching') : !options.length ? tr('common.nothingMatches') : ''}
      </p>
      {onNew && (
        <button type="button" className="nt-link-opt nt-link-new" disabled={newBusy} onClick={onNew}>
          <Plus size={14} aria-hidden />
          <span className="nt-link-opt-title">{tr('notes.linkedPanel.newNote')}{newTitle ? <span className="muted"> · {newTitle}</span> : null}</span>
        </button>
      )}
    </div>
  )
}
