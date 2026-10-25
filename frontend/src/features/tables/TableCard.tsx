// A table as a card in the Notes grid (the Notes view holds notes and tables): its
// name, the first rows of its default view as a small table, the row count, labels and colour.
// Hover actions: Pin, Colour, Labels, Archive and ⋮ (Duplicate, Export CSV, Export to Excel, Delete); in Trash,
// Restore and Delete forever.

import { useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, Link2, Lock, MoreVertical, Palette, Pin, Table2, Tag, Trash2, Undo2 } from 'lucide-react'
import { useCallback, useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router'
import { MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { tableRoutePath } from '../../lib/routes'
import { canLeave, ColorGrid } from '../notes/menus'
import { CardPeople, leaveShared } from '../notes/sharing'
import { colorName, trashLine } from '../notes/model'
import { useNotesUI } from '../notes/store'
import { duplicateTable, exportCsv, patchTable, restoreTable, setTableState, trashTable } from './actions'
import { plural } from './model'
import type { Table } from './types'
import './tables.css'
import { t as tr } from '../../i18n'

export type TableVerb = 'open' | 'pin' | 'archive' | 'labels' | 'duplicate' | 'export' | 'exportXlsx' | 'delete' | 'restore' | 'purge' | 'share' | 'leave'

export function useTableVerbs() {
  const qc = useQueryClient()
  const nav = useNavigate()
  return useCallback(async (t: Table, v: TableVerb, anchor?: HTMLElement | DOMRect | null) => {
    switch (v) {
      case 'open':
        nav(tableRoutePath(t.id))
        break
      // ACT table.pin @web.notes
      case 'pin':
        await setTableState(qc, t.id, { pinned: !t.state.pinned }, t.state.pinned ? tr('common.unpinned') : tr('common.pinned'))
        break
      // ACT table.archive @web.notes
      case 'archive':
        await setTableState(qc, t.id, { archived: !t.state.archived }, t.state.archived ? tr('common.unarchived') : tr('common.archived'))
        break
      // ACT table.label @web.notes
      case 'labels':
        if (anchor) useNotesUI.getState().set({ labelsFor: { ids: [t.id], anchor } })
        break
      // ACT table.duplicate @web.notes
      case 'duplicate':
        await duplicateTable(qc, t.id, true)
        break
      case 'export':
        await exportCsv(t, { scope: 'all' })
        break
      // ACT table.export_xlsx: the whole table, from the card
      case 'exportXlsx':
        await exportCsv(t, { scope: 'all', format: 'xlsx' })
        break
      // ACT table.delete @web.notes
      case 'delete':
        await trashTable(qc, t.id)
        break
      // ACT table.restore @web.notes
      case 'restore':
        await restoreTable(qc, t.id)
        break
      // ACT table.purge @web.notes
      case 'purge':
        useNotesUI.getState().set({ purge: [t.id] })
        break
      // ACT table.share @web.notes: the people dialog (notes/sharing.tsx)
      case 'share':
        useNotesUI.getState().set({ share: { kind: 'table', id: t.id, name: t.name || tr('common.untitledTable') } })
        break
      // ACT table.leave @web.notes
      case 'leave':
        await leaveShared(qc, 'table', t.id)
        break
    }
  }, [qc, nav])
}

export function TableCard({ table, focused, selected, draggable, onSelect, onDropOn, onFocusCard }: {
  table: Table; focused: boolean; selected: boolean; draggable: boolean
  onSelect: () => void; onDropOn: (dragged: string, where: 'before' | 'after') => void; onFocusCard?: () => void
}) {
  const qc = useQueryClient()
  const verb = useTableVerbs()
  const colour = useAnchor<HTMLButtonElement>()
  const more = useAnchor<HTMLButtonElement>()
  const [drop, setDrop] = useState<'before' | 'after' | null>(null)
  const trashed = !!table.deleted_at
  const p = table.preview
  const open = () => void verb(table, 'open')
  const onDragOver = (e: DragEvent) => {
    if (!draggable || !e.dataTransfer.types.includes('application/x-mailr-note')) return
    e.preventDefault()
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setDrop(e.clientY < r.top + r.height / 2 ? 'before' : 'after')
  }
  return (
    <article className={`nt-card${table.color ? ` nt-c-${table.color}` : ''}${focused ? ' focused' : ''}${selected ? ' selected' : ''}${drop ? ` drop-${drop}` : ''}`}
      data-note={table.id} data-kind="table" tabIndex={0} role="button"
      aria-label={tr('tables.tableCard.table', { tableName: table.name || tr('common.untitledTable'), value: table.color ? `, ${colorName(table.color)}` : '', value2: table.state.pinned ? ', pinned' : '' })}
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.setData('application/x-mailr-note', table.id)
        e.dataTransfer.effectAllowed = 'move'
      }}
      onDragOver={onDragOver} onDragLeave={() => setDrop(null)}
      onDrop={(e) => {
        const id = e.dataTransfer.getData('application/x-mailr-note')
        const where = drop
        setDrop(null)
        if (id && id !== table.id && where) {
          e.preventDefault()
          onDropOn(id, where)
        }
      }}
      onClick={(e) => e.currentTarget.contains(e.target as Node) && !trashed && open()}
      onKeyDown={(e) => {
        if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          if (!trashed) open()
        }
      }}
      onFocus={(e) => e.target === e.currentTarget && onFocusCard?.()}>
      <button type="button" className={`nt-card-select icon-btn sm${selected ? ' on' : ''}`} aria-label={selected ? tr('common.deselect') : tr('common.select')} aria-pressed={selected} onClick={(e) => {
        e.stopPropagation()
        onSelect()
      }}><span className="nt-tick" aria-hidden /></button>
      {!trashed && (
        <button type="button" className={`nt-card-pin icon-btn sm${table.state.pinned ? ' on' : ''}`} aria-label={table.state.pinned ? tr('common.unpin') : tr('common.pin')} onClick={(e) => {
          e.stopPropagation()
          void verb(table, 'pin')
        }}><Pin size={18} fill={table.state.pinned ? 'currentColor' : 'none'} /></button>
      )}
      <h3 className="nt-card-title" dir="auto">{table.name || tr('common.untitledTable')}</h3>
      {p && p.columns.length > 0 && (
        <table className="tb-card-table" aria-label={tr('tables.tableCard.firstRows')}>
          <thead><tr>{p.columns.map((c) => <th key={c.id} dir="auto">{c.name}</th>)}</tr></thead>
          <tbody>{p.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} dir="auto">{v}</td>)}</tr>)}</tbody>
        </table>
      )}
      <div className="nt-card-foot">
        <span className="tb-card-kind"><Table2 size={14} aria-hidden /> {plural(table.counts.rows, 'row')}</span>
        {table.state.labels.map((l) => <span key={l} className="nt-label-chip" dir="auto">{l}</span>)}
        <span className="spacer" />
        <CardPeople item={table} />
        {!!table.links?.length && <Link2 size={14} aria-label={tr('tables.tableCard.hasLinks')} />}
        {table.local_only && <Lock size={14} aria-label={tr('common.localOnly2')} />}
      </div>
      {trashed && <div className="nt-card-trash muted">{trashLine(table)}</div>}
      <div className="nt-card-actions" onClick={(e) => e.stopPropagation()}>
        {trashed ? (
          <>
            <button type="button" className="icon-btn sm" aria-label={tr('common.restore')} data-tip={tr('common.restore')} onClick={() => void verb(table, 'restore')}><Undo2 size={18} /></button>
            <button type="button" className="icon-btn sm" aria-label={tr('common.deleteForever')} data-tip={tr('common.deleteForever')} onClick={() => void verb(table, 'purge')}><Trash2 size={18} /></button>
          </>
        ) : (
          <>
            <button type="button" className="icon-btn sm" aria-label={tr('common.colour')} data-tip={tr('common.colour')} onClick={(e) => colour.toggle(e.currentTarget)}><Palette size={18} /></button>
            <button type="button" className="icon-btn sm" aria-label={tr('common.labels')} data-tip={tr('tables.tableCard.labelsT')} onClick={(e) => void verb(table, 'labels', e.currentTarget)}><Tag size={18} /></button>
            <button type="button" className="icon-btn sm" aria-label={table.state.archived ? tr('common.unarchive') : tr('common.archive')} data-tip={table.state.archived ? tr('tables.tableCard.unarchiveE') : tr('tables.tableCard.archiveE')} onClick={() => void verb(table, 'archive')}>
              {table.state.archived ? <ArchiveRestore size={18} /> : <Archive size={18} />}
            </button>
            <button type="button" className="icon-btn sm" aria-label={tr('common.more')} data-tip={tr('common.more')} onClick={(e) => more.toggle(e.currentTarget)}><MoreVertical size={18} /></button>
          </>
        )}
      </div>
      <Popover anchor={colour.anchor} open={colour.open} onClose={colour.close} role="dialog" label={tr('common.colour')} placement="bottom-start">
        <ColorGrid value={table.color} onPick={(c) => {
          colour.close()
          // ACT table.color @web.notes
          void patchTable(qc, { id: table.id, version: null }, { color: c }, c ? tr('tables.tableCard.colour', { colorName: colorName(c) }) : tr('common.defaultColour'))
        }} />
      </Popover>
      <Popover anchor={more.anchor} open={more.open} onClose={more.close} placement="bottom-end" label={tr('common.more')}>
        <MenuItem onSelect={() => {
          more.close()
          open()
        }}>{tr('common.open')}</MenuItem>
        <MenuItem onSelect={() => {
          more.close()
          void verb(table, 'duplicate')
        }}>{tr('common.duplicate')}</MenuItem>
        <MenuItem onSelect={() => {
          more.close()
          void verb(table, 'export')
        }}>{tr('common.exportCsv')}</MenuItem>
        <MenuItem onSelect={() => {
          more.close()
          void verb(table, 'exportXlsx')
        }}>{tr('tables.tableCard.exportToExcelXlsx')}</MenuItem>
        <MenuItem onSelect={() => {
          more.close()
          void verb(table, 'share')
        }}>{table.level === 'owner' || table.level === 'share' ? tr('common.share2') : tr('tables.tableCard.people')}</MenuItem>
        {canLeave(table) && <MenuItem onSelect={() => {
          more.close()
          void verb(table, 'leave')
        }}>{tr('tables.tableCard.removeFromMyNotes')}</MenuItem>}
        {(table.can_delete ?? table.level === 'owner') && <>
          <MenuSep />
          <MenuItem danger onSelect={() => {
            more.close()
            void verb(table, 'delete')
          }}>{tr('common.delete2')}</MenuItem>
        </>}
      </Popover>
    </article>
  )
}
