// The notes side panel (web.panel): a quick glance from mail. Pinned
// checklists with their unchecked items (a checkbox checks in place, with the undo toast), a quick-add
// line bound to one chosen list (the item grammar in capture mode, so `#Other list` still works), and
// "Open Notes" in the panel header.
// ACT note.create @web.panel, ACT note.add_items @web.panel, ACT checkitem.add @web.panel, ACT checkitem.check @web.panel, ACT checkitem.uncheck @web.panel

import { useQueryClient } from '@tanstack/react-query'
import { ListChecks } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { EmptyState, Skeleton } from '../../components/bits'
import { notePath } from '../../lib/routes'
import { useUI } from '../../stores/ui'
import { capture, patchItem } from '../notes/actions'
import { useNotes } from '../notes/api'
import { qtyLabel } from '../notes/model'
import type { Item, Note } from '../notes/types'
import '../notes/notes.css'
import { t as tr } from '../../i18n'

const LIST_KEY = 'mailr.notes.panelList'

function loadList(): string | null {
  try {
    return localStorage.getItem(LIST_KEY)
  } catch {
    return null
  }
}

export function NotesPanel() {
  const qc = useQueryClient()
  const lists = useNotes('lists', null, 'manual')
  const all = useMemo(() => (lists.data?.notes ?? []).filter((n) => n.kind === 'checklist' && !n.deleted_at && !n.state.archived), [lists.data])
  const pinned = all.filter((n) => n.state.pinned)
  const [listId, setListId] = useState<string | null>(loadList)
  const target = all.find((n) => n.id === listId) ?? pinned[0] ?? all[0] ?? null
  const [text, setText] = useState('')
  const close = () => useUI.getState().setPanel(null)

  const add = async () => {
    const t = text.trim()
    if (!t) return
    setText('')
    const r = await capture(qc, t, target?.id ?? null).catch(() => null)
    if (r?.note?.id) {
      setListId(r.note.id)
      try {
        localStorage.setItem(LIST_KEY, r.note.id)
      } catch {
        /* private mode */
      }
    }
  }

  return (
    <div className="nt-panel" data-testid="notes-panel">
      <div className="nt-panel-add">
        <select aria-label={tr('rail.notesPanel.addToList')} value={target?.id ?? ''} onChange={(e) => {
          setListId(e.target.value)
          try {
            localStorage.setItem(LIST_KEY, e.target.value)
          } catch {
            /* private mode */
          }
        }}>
          {!all.length && <option value="">{tr('common.newList')}</option>}
          {all.map((n) => <option key={n.id} value={n.id}>{n.title || tr('common.untitledList')}</option>)}
        </select>
        <input value={text} dir="auto" placeholder={target ? tr('rail.notesPanel.addToMilkEggs', { targetTitle: target.title || tr('rail.notesPanel.theList') }) : tr('rail.notesPanel.newListMilkEggs')} aria-label={tr('rail.notesPanel.addItems')}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              void add()
            }
          }} />
      </div>
      {lists.isLoading ? <Skeleton height={60} /> : !pinned.length ? (
        <EmptyState icon={<ListChecks />} title={tr('rail.notesPanel.noPinnedLists')}>{tr('rail.notesPanel.pinAListInNotes')}</EmptyState>
      ) : pinned.map((n) => <PanelList key={n.id} note={n} onOpen={close} onCheck={(i) => void patchItem(qc, n.id, i, { checked: true })} />)}
    </div>
  )
}

function PanelList({ note, onOpen, onCheck }: { note: Note; onOpen: () => void; onCheck: (i: Item) => void }) {
  const items = note.preview?.items ?? []
  return (
    <section className={`nt-panel-list${note.color ? ` nt-c-${note.color}` : ''}`} aria-label={note.title || tr('common.list')}>
      <h3><Link to={notePath(note.id)} onClick={onOpen}>{note.title || tr('common.untitledList')}</Link></h3>
      <ul>
        {items.map((i) => (
          <li key={i.id} className="nt-card-item">
            <input type="checkbox" className="checkbox" aria-label={tr('rail.notesPanel.check', { text: i.text })} onChange={() => onCheck({ id: i.id, checked: false } as Item)} />
            {qtyLabel(i.qty) && <span className="nt-qty">{qtyLabel(i.qty)}</span>}
            {/* The section follows the text inline, so a long item wraps with it and it is never cut. */}
            <span className="nt-item-text" dir="auto">{i.text}{i.section && <span className="muted nt-small nt-item-section" dir="auto">{' ·\u00a0'}{i.section}</span>}</span>
          </li>
        ))}
        {!items.length && <li className="muted">{tr('rail.notesPanel.nothingLeftToGet')}</li>}
        {!!note.preview?.more && <li className="nt-card-more"><Link to={notePath(note.id)} onClick={onOpen}>{tr('rail.notesPanel.more', { more: note.preview.more })}</Link></li>}
      </ul>
    </section>
  )
}
