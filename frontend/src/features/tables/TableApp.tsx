// The table screen (web.table): the header (name
// renamed inline, colour, labels, links, ⋮), the view bar, the grid (a card list on a phone), the
// row form where the theme opens details, and the dialogs of ⋮. Routes: /tables/t/<id>,
// /tables/t/<id>/v/<view>, /tables/t/<id>/r/<row>.

import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Link2, Lock, MoreVertical, Palette, Pin, Tag, Table2, Users } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { ApiError } from '../../api/client'
import { useMe } from '../../api/hooks'
import { ConfirmDialog } from '../../components/Dialog'
import { MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { Spinner } from '../../components/bits'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { useOnline } from '../../hooks/useOnline'
import { notesPath, parseTablePath, tableFormPath, tableRoutePath } from '../../lib/routes'
import { useThemeDef } from '../../themes/useUITheme'
import { useNoteLabels } from '../notes/api'
import { markSeen } from '../notes/actions'
import { canLeave, ColorGrid, LabelsMenu } from '../notes/menus'
import { colorName } from '../notes/model'
import { LinkChip, LinkForm, linkTarget } from '../notes/NoteEditor'
import { useEditingPerson, useUnsharedBy } from '../notes/presence'
import { leaveShared, ShareDialog, sharedLine, viewOnlyLine } from '../notes/sharing'
import { useNotesUI } from '../notes/store'
import { SaveTemplateDialog } from '../notes/templates'
import '../notes/notes.css'
import { duplicateTable, exportCsv, linkTable, patchTable, setTableState, trashTable, unlinkTable } from './actions'
import { useRows, useTable } from './api'
import { DeletedColumnsDialog, DeletedRowsDialog, ExportDialog, HistoryDialog, ImportDialog, KeysDialog, RestorePointDialog, type PointTarget } from './dialogs'
import { Grid, type GridHandle } from './Grid'
import { mergeSpec, specDiffers, visibleColumns } from './model'
import { PhoneList } from './PhoneList'
import { RowForm } from './RowForm'
import { useTableUI } from './store'
import type { Column, RowsAnswer, Table, TableView, ViewSpec } from './types'
import { ViewBar } from './ViewBar'
import { AddRowPage } from './AddRowPage'
import { TableHouseholdItems } from './householdMenu'
import { hereLine } from '../notes/presence'
import { useHere, usePresenceBeacon } from '../notes/presenceBeacon'
import './tables.css'
import { useDocumentTitle } from '../../hooks/useDocumentTitle'
import { t as tr, appLocale } from '../../i18n'

type DialogKind = 'history' | 'deletedRows' | 'deletedCols' | 'restorePoint' | 'import' | 'export' | 'exportXlsx' | 'keys' | 'delete' | 'template' | null

export default function TableApp() {
  const loc = useLocation()
  const r = parseTablePath(loc.pathname)
  // /tables/t/<id>/add: the members-only "Add a row" form on its own.
  if (r?.form) return <AddRowPage id={r.id} />
  return <TableScreen />
}

function TableScreen() {
  const loc = useLocation()
  const nav = useNavigate()
  const qc = useQueryClient()
  const route = parseTablePath(loc.pathname)
  const theme = useThemeDef()
  const narrow = useMediaQuery('(max-width: 760px)')
  const landscape = useMediaQuery('(max-height: 500px) and (orientation: landscape)')
  const online = useOnline()
  const id = route?.id ?? null
  const t = useTable(id)
  const table = t.data
  useDocumentTitle(table?.name || tr('common.table'), tr('common.notes'))
  // Sharing (notes/sharing.tsx): "Dana is editing", "Olive stopped sharing this table", the dot.
  const me = useMe().data?.user.id
  const editingPerson = useEditingPerson(id, me)
  const unsharedBy = useUnsharedBy(id)
  const shareFor = useNotesUI((s) => s.share)
  const setNotesUI = useNotesUI((s) => s.set)
  const tableId = table?.id
  const changed = !!table?.changed
  const shared = !!table && (table.level !== 'owner' || (table.sharing?.count ?? 0) > 0)
  // Who else has it open.
  usePresenceBeacon('table', tableId, shared && !table?.deleted_at && online)
  const here = useHere(tableId, me)
  useEffect(() => {
    if (tableId && changed) void markSeen(qc, 'table', tableId)
  }, [tableId, changed, qc])
  useEffect(() => () => {
    if (tableId && shared) void markSeen(qc, 'table', tableId)
  }, [tableId]) // eslint-disable-line react-hooks/exhaustive-deps
  const views = useMemo(() => table?.views ?? [], [table?.views])
  const view = views.find((v) => v.id === route?.view) ?? views.find((v) => v.default) ?? views[0]
  const [draft, setDraft] = useState<Partial<ViewSpec> | null>(null)
  const [q, setQ] = useState('')
  const [dq, setDq] = useState('')
  const [dialog, setDialog] = useState<DialogKind>(null)
  const [session, setSession] = useState<PointTarget | null>(null)
  const [earlier, setEarlier] = useState<{ row: string; col: string } | null>(null)
  const [newRow, setNewRow] = useState(false)
  const [filterFor, setFilterFor] = useState<Column | null>(null)
  const [name, setName] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const grid = useRef<GridHandle>(null)
  const more = useAnchor<HTMLButtonElement>()
  const colour = useAnchor<HTMLButtonElement>()
  const labelsA = useAnchor<HTMLButtonElement>()
  const linkA = useAnchor<HTMLButtonElement>()
  const labels = useNoteLabels().data ?? []

  useEffect(() => setDraft(null), [view?.id])
  useEffect(() => setName(table?.name ?? ''), [table?.name])
  useEffect(() => {
    const h = setTimeout(() => setDq(q.trim()), 250)
    return () => clearTimeout(h)
  }, [q])

  const spec = mergeSpec(view?.spec, draft)
  const dirty = specDiffers(view?.spec, draft)
  const rows = useRows(id, view?.id ?? null, draft, dq)
  const data = rows.data
  const columns = useMemo(() => table?.columns ?? [], [table?.columns])
  const shown = useMemo(() => visibleColumns(columns, spec, table?.primary_column ?? null), [columns, spec, table?.primary_column])
  const canEdit = !!table && table.level !== 'view' && !table.deleted_at && online
  const placement = narrow ? 'sheet' : theme.apps.noteDetail
  const rowOpen = route?.row ?? null
  const fresh = (loc.state as { fresh?: boolean } | null)?.fresh

  const onSpec = useCallback((p: Partial<ViewSpec>) => setDraft((d) => ({ ...(d ?? {}), ...p })), [])
  const openRow = (rid: string) => id && nav(tableRoutePath(id, route?.view, rid))
  const closeRow = () => {
    setEarlier(null)
    setNewRow(false)
    if (id) nav(tableRoutePath(id, route?.view))
    setTimeout(() => grid.current?.focusGrid(), 30)
  }

  const setUI = useTableUI((s) => s.set)
  useEffect(() => {
    setUI({ id, view: view?.id ?? null, filters: spec.filters, sort: spec.sort })
  }, [id, view?.id, spec.filters, spec.sort, setUI])
  const onFocusChange = useCallback((row: string | null, col: string | null, selected: string[]) => setUI({ focused_row: row, focused_column: col, selected_rows: selected }), [setUI])

  useEffect(() => {
    if (fresh && table && canEdit && data && !data.total) setTimeout(() => grid.current?.focusGrid(), 50)
  }, [fresh, table, canEdit, data])

  if (!route) return null
  if (t.isError) {
    const gone = t.error instanceof ApiError && t.error.status === 404
    return (
      <section className="app-frame nt-app tb-app" aria-label={tr('common.table')}>
        <div className="nt-empty"><span className="nt-empty-icon" aria-hidden><Table2 /></span><h2>{gone && unsharedBy ? tr('tables.tableApp.stoppedSharingThisTable', { unsharedBy }) : gone ? tr('tables.tableApp.thisTableIsNotHere') : tr('tables.tableApp.theTableCouldNotBe')}</h2>
          <p className="muted">{gone ? tr('tables.tableApp.itWasDeletedOrIt') : tr('tables.tableApp.tryAgainInAMoment')}</p>
          <button type="button" className="btn tonal" onClick={() => nav(notesPath('tables'))}>{tr('common.goToTables')}</button></div>
      </section>
    )
  }
  if (!table || !data) return <section className="app-frame nt-app tb-app" aria-label={tr('common.table')}><div className="tb-loading"><Spinner /></div></section>

  const rename = () => {
    const n = name.trim()
    if (n && n !== table.name) void patchTable(qc, table, { name: n }, tr('tables.tableApp.renamedTo', { n }))
    else setName(table.name)
  }
  const atLimit = table.counts.rows >= (table.limits?.rows ?? 10000)
  const form = (rowOpen || newRow) && (
    <div className={`nt-pane nt-pane-${placement} tb-form-pane`} data-testid="row-pane">
      <RowForm key={rowOpen ?? 'new'} table={table} columns={columns} spec={spec} rowId={rowOpen} canEdit={canEdit}
        earlier={earlier && earlier.row === rowOpen ? earlier.col : null} filters={spec.filters} onClose={closeRow}
        onCreated={(rid) => {
          setNewRow(false)
          openRow(rid)
        }} />
    </div>
  )

  return (
    <section className={`app-frame nt-app tb-app nt-theme-${theme.id} nt-detail-${placement}${narrow ? ' nt-narrow' : ''}${form ? ' nt-has-pane' : ''}`}
      data-testid="table-app" aria-label={tr('tables.tableApp.table', { tableName: table.name })}>
      <div className="nt-center">
        <div className="nt-main-col">
          <header className={`nt-head tb-head-bar${table.color ? ` nt-c-${table.color}` : ''}`}>
            <button type="button" className="icon-btn" aria-label={tr('tables.tableApp.backToTables')} data-tip={tr('tables.tableApp.tables')} onClick={() => nav(notesPath('tables'))}><ArrowLeft /></button>
            {/* ACT table.rename @web.table */}
            <input className="tb-name" dir="auto" value={name} aria-label={tr('tables.tableApp.tableName')} maxLength={200} disabled={!canEdit} onChange={(e) => setName(e.target.value)} onBlur={rename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                if (e.key === 'Escape') {
                  setName(table.name)
                  ;(e.target as HTMLInputElement).blur()
                }
              }} />
            {table.local_only && <Lock size={16} aria-label={tr('common.localOnly2')} />}
            {!narrow && table.state.labels.map((l) => <span key={l} className="nt-label-chip" dir="auto">{l}</span>)}
            <span className="spacer" />
            {/* ACT table.unlink @web.table: the chip's × */}
            {!narrow && (table.links ?? []).map((l) => <LinkChip key={l.id} link={l} readOnly={!canEdit} onOpen={() => nav(linkTarget(l))} onRemove={() => void unlinkTable(qc, table.id, l.id)} />)}
            {/* ACT table.pin @web.table */}
            {!narrow && <>
            <button type="button" className={`icon-btn${table.state.pinned ? ' on' : ''}`} aria-label={table.state.pinned ? tr('common.unpin') : tr('common.pin')} data-tip={table.state.pinned ? tr('common.unpin') : tr('common.pin')}
              onClick={() => void setTableState(qc, table.id, { pinned: !table.state.pinned }, table.state.pinned ? tr('common.unpinned') : tr('common.pinned'))}><Pin size={18} fill={table.state.pinned ? 'currentColor' : 'none'} /></button>
            {/* ACT table.color @web.table */}
            <button type="button" className="icon-btn" aria-label={tr('common.colour')} data-tip={tr('common.colour')} disabled={!canEdit} onClick={(e) => colour.toggle(e.currentTarget)}><Palette size={18} /></button>
            {/* ACT table.label @web.table */}
            <button type="button" className="icon-btn" aria-label={tr('common.labels')} data-tip={tr('common.labels')} onClick={(e) => labelsA.toggle(e.currentTarget)}><Tag size={18} /></button>
            {/* ACT table.link @web.table */}
            <button type="button" className="icon-btn" aria-label={tr('tables.tableApp.addALink')} data-tip={tr('tables.tableApp.addALink')} disabled={!canEdit} onClick={(e) => linkA.toggle(e.currentTarget)}><Link2 size={18} /></button>
            </>}
            {/* ACT table.share @web.table: the people dialog (notes/sharing.tsx) */}
            {!table.deleted_at && (
              <button type="button" className="icon-btn" aria-label={table.level === 'owner' || table.level === 'share' ? tr('common.share') : tr('common.people')} data-tip={sharedLine(table) || tr('common.share')} data-testid="table-share"
                onClick={() => setNotesUI({ share: { kind: 'table', id: table.id, name: table.name || tr('common.untitledTable') } })}><Users size={18} /></button>
            )}
            <button type="button" className="icon-btn" aria-label={tr('common.more')} data-tip={tr('common.more')} onClick={(e) => more.toggle(e.currentTarget)} data-testid="table-more"><MoreVertical /></button>
          </header>
          {table.deleted_at && <div className="nt-banner warn" role="status">{tr('tables.tableApp.thisTableIsInTrash')}</div>}
          {table.level === 'view' && <div className="nt-banner" role="status" data-testid="table-view-only">{viewOnlyLine(table)}</div>}
          {table.level !== 'view' && table.level !== 'owner' && !table.deleted_at && <div className="nt-banner" role="status">{sharedLine(table)}</div>}
          {(editingPerson || here.length > 0) && <div className="nt-banner" role="status" data-testid="table-presence"><span className="nt-presence">{editingPerson ? tr('tables.tableApp.isEditing', { editingPerson }) : hereLine(here, 'table')}</span></div>}
          {!online && <div className="nt-banner" role="status">{tr('tables.tableApp.youAreOfflineTheTable')}</div>}
          {atLimit && <div className="nt-banner warn" role="status">{tr('tables.tableApp.thisTableHasRowsThe', { rows: (table.limits?.rows ?? 10000).toLocaleString(appLocale()) })}</div>}
          {narrow && !landscape ? (
            <PhoneList table={table} columns={columns} data={data} spec={spec} views={views} view={view} canEdit={canEdit}
              onView={(v) => nav(tableRoutePath(table.id, v === views.find((x) => x.default)?.id ? null : v))} onSpec={onSpec} onOpenRow={openRow}
              onNew={() => setNewRow(true)} />
          ) : (
            <>
              <ViewBarHost table={table} views={views} view={view} spec={spec} dirty={dirty} canEdit={canEdit && table.level !== 'view'} columns={columns}
                data={data} q={q} setQ={setQ} onSpec={onSpec} onReset={() => setDraft(null)} searchRef={searchRef} filterFor={filterFor} setFilterFor={setFilterFor}
                onView={(v) => nav(tableRoutePath(table.id, v === views.find((x) => x.default)?.id ? null : v))} />
              <Grid table={table} columns={shown} data={data} spec={spec} canEdit={canEdit} handle={grid}
                onOpenRow={openRow} openRow={rowOpen} onSpec={onSpec} onFocusChange={onFocusChange} onFilter={(c) => setFilterFor(c)}
                onEarlier={(row, col) => {
                  setEarlier({ row, col })
                  openRow(row)
                }}
                onSearch={() => searchRef.current?.focus()} onHelp={() => setDialog('keys')} onClearFilters={() => onSpec({ filters: [] })} />
            </>
          )}
        </div>
        {form && (placement === 'pane' || placement === 'below') && form}
      </div>
      {form && (placement === 'card' || placement === 'popover' || placement === 'sheet') && (
        <div className={`nt-overlay nt-overlay-${placement}`} onMouseDown={(e) => e.target === e.currentTarget && closeRow()}>{form}</div>
      )}

      <Popover anchor={colour.anchor} open={colour.open} onClose={colour.close} role="dialog" label={tr('common.colour')} placement="bottom-end">
        <ColorGrid value={table.color} onPick={(c) => {
          colour.close()
          void patchTable(qc, table, { color: c }, c ? tr('tables.tableApp.colour', { colorName: colorName(c) }) : tr('common.defaultColour'))
        }} />
      </Popover>
      <Popover anchor={labelsA.anchor} open={labelsA.open} onClose={labelsA.close} placement="bottom-end" label={tr('common.labels')} minWidth={240}>
        <LabelsMenu notes={[table]} labels={labels} onAdd={(n) => void setTableState(qc, table.id, { add_labels: [n] }, tr('tables.tableApp.labelAdded', { n }))}
          onRemove={(n) => void setTableState(qc, table.id, { remove_labels: [n] }, tr('tables.tableApp.labelRemoved', { n }))} />
      </Popover>
      <Popover anchor={linkA.anchor} open={linkA.open} onClose={linkA.close} role="dialog" label={tr('tables.tableApp.addALink')} placement="bottom-end">
        <LinkForm onClose={linkA.close} onLink={(kind, ref) => {
          linkA.close()
          void linkTable(qc, table.id, kind, ref)
        }} />
      </Popover>
      <Popover anchor={more.anchor} open={more.open} onClose={more.close} placement="bottom-end" label={tr('tables.tableApp.tableMenu')}>
        {narrow && <>
          <MenuItem onSelect={() => {
            more.close()
            void setTableState(qc, table.id, { pinned: !table.state.pinned }, table.state.pinned ? tr('common.unpinned') : tr('common.pinned'))
          }}>{table.state.pinned ? tr('common.unpin') : tr('common.pin')}</MenuItem>
          {canEdit && <MenuItem onSelect={() => {
            const a = more.anchor
            more.close()
            if (a) colour.toggle(a)
          }}>{tr('tables.tableApp.colour2')}</MenuItem>}
          <MenuItem onSelect={() => {
            const a = more.anchor
            more.close()
            if (a) labelsA.toggle(a)
          }}>{tr('tables.tableApp.labels')}</MenuItem>
          <MenuSep />
        </>}
        {/* ACT table.duplicate @web.table */}
        <MenuItem onSelect={() => {
          more.close()
          void duplicateTable(qc, table.id, true).then((c) => nav(tableRoutePath(c.id)))
        }}>{tr('common.duplicate')}</MenuItem>
        {/* ACT table.save_template @web.table: the columns, with no rows (notes/templates.tsx) */}
        <MenuItem onSelect={() => {
          more.close()
          setDialog('template')
        }}>{tr('common.saveAsTemplate')}</MenuItem>
        {canEdit && <MenuItem onSelect={() => {
          more.close()
          setDialog('import')
        }}>{tr('tables.tableApp.importCsv')}</MenuItem>}
        <MenuItem onSelect={() => {
          more.close()
          setDialog('export')
        }}>{tr('common.exportCsv')}</MenuItem>
        <MenuItem onSelect={() => {
          more.close()
          setDialog('exportXlsx')
        }}>{tr('tables.tableApp.exportToExcelXlsx')}</MenuItem>
        <MenuSep />
        <MenuItem onSelect={() => {
          more.close()
          setDialog('history')
        }}>{tr('tables.tableApp.history')}</MenuItem>
        <MenuItem onSelect={() => {
          more.close()
          setDialog('deletedRows')
        }}>{tr('tables.tableApp.deletedRows')}</MenuItem>
        <MenuItem onSelect={() => {
          more.close()
          setDialog('deletedCols')
        }}>{tr('tables.tableApp.deletedColumns')}</MenuItem>
        {canEdit && <MenuItem onSelect={() => {
          more.close()
          setSession(null)
          setDialog('restorePoint')
        }}>{tr('tables.tableApp.restoreToAPointIn')}</MenuItem>}
        <MenuItem onSelect={() => {
          more.close()
          void setTableState(qc, table.id, { archived: !table.state.archived }, table.state.archived ? tr('common.unarchived') : tr('common.archived'))
        }}>{table.state.archived ? tr('common.unarchive') : tr('common.archive')}</MenuItem>
        {/* Local models only, the dates on your calendar, the "Add a row" form (householdMenu.tsx). */}
        <TableHouseholdItems table={table} online={online} onDone={more.close} onOpenForm={() => nav(tableFormPath(table.id))} />
        {/* ACT table.leave @web.table */}
        {canLeave(table) && <MenuItem onSelect={() => {
          more.close()
          void leaveShared(qc, 'table', table.id).then((ok) => ok && nav(notesPath('tables')))
        }}>{tr('tables.tableApp.removeFromMyNotes')}</MenuItem>}
        {(table.can_delete ?? table.level === 'owner') && <>
          <MenuSep />
          <MenuItem danger onSelect={() => {
            more.close()
            setDialog('delete')
          }}>{tr('common.delete2')}</MenuItem>
        </>}
      </Popover>

      {dialog === 'history' && <HistoryDialog table={table} onClose={() => setDialog(null)} onRestorePoint={(s) => {
        setSession(s)
        setDialog('restorePoint')
      }} />}
      {dialog === 'restorePoint' && <RestorePointDialog table={table} session={session} onClose={() => setDialog(null)} />}
      {dialog === 'deletedRows' && <DeletedRowsDialog table={table} onClose={() => setDialog(null)} />}
      {dialog === 'deletedCols' && <DeletedColumnsDialog table={table} onClose={() => setDialog(null)} />}
      {dialog === 'import' && <ImportDialog table={table} onClose={() => setDialog(null)} />}
      {dialog === 'export' && <ExportDialog table={table} view={view?.id ?? null} spec={draft} onClose={() => setDialog(null)} />}
      {dialog === 'exportXlsx' && <ExportDialog format="xlsx" table={table} view={view?.id ?? null} spec={draft} onClose={() => setDialog(null)} />}
      {dialog === 'keys' && <KeysDialog onClose={() => setDialog(null)} />}
      {dialog === 'template' && <SaveTemplateDialog kind="table" id={table.id} name={table.name} onClose={() => setDialog(null)} />}
      {shareFor && <ShareDialog kind={shareFor.kind} id={shareFor.id} name={shareFor.name} onClose={() => setNotesUI({ share: null })}
        onLeft={() => nav(notesPath('tables'))} />}
      {/* ACT table.delete @web.table */}
      <ConfirmDialog open={dialog === 'delete'} danger title={tr('tables.tableApp.moveToTrash', { tableName: table.name })}
        body={<>
          <p>{tr('tables.tableApp.itStaysInTrashFor')}</p>
          <button type="button" className="btn text sm" onClick={() => void exportCsv(table, { scope: 'all' })}>{tr('tables.tableApp.saveACsvCopyFirst')}</button>
        </>}
        confirmLabel={tr('common.moveToTrash')} onClose={() => setDialog(null)} onConfirm={() => {
          setDialog(null)
          void trashTable(qc, table.id).then(() => nav(notesPath('tables')))
        }} />
    </section>
  )
}

function ViewBarHost({ table, views, view, spec, dirty, canEdit, columns, data, q, setQ, onSpec, onReset, onView, searchRef, filterFor, setFilterFor }: {
  table: Table; views: TableView[]; view: TableView | undefined; spec: ViewSpec; dirty: boolean; canEdit: boolean; columns: Column[]; data: RowsAnswer
  q: string; setQ: (q: string) => void; onSpec: (p: Partial<ViewSpec>) => void; onReset: () => void; onView: (id: string) => void
  searchRef: React.RefObject<HTMLInputElement | null>; filterFor: Column | null; setFilterFor: (c: Column | null) => void
}) {
  return <ViewBar ref={searchRef} table={table} views={views} view={view} spec={spec} dirty={dirty} canEdit={canEdit} columns={columns} refs={data.refs ?? {}}
    q={q} onQ={setQ} onView={onView} onSpec={onSpec} onReset={onReset} filterFor={filterFor} onFilterFor={setFilterFor} />
}

