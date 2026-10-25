// The table's dialogs: History by session,
// Deleted rows, Deleted columns, Restore to a point in time with its preview, CSV import with the
// detected types, CSV export, the column builder of a new table, and the grid's key help.

import { useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Plus, Trash2, Upload } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { Dialog } from '../../components/Dialog'
import { tableRoutePath } from '../../lib/routes'
import { offerUndo, useDeletedColumns, useDeletedRows, useTableHistory } from './api'
import {
  createTable, csvPreview, exportCsv, fileBase64, importCsv, restoreColumn, restorePoint, restorePointPreview, restoreRows,
  type ColumnSpec, type CsvSource,
} from './actions'
import { OptionsEditor, TypePicker } from './columns'
import { TYPE_LABELS, plainText, plural, whoWhen } from './model'
import type { CellValue, Change, ColumnType, CsvPreview, SelectOption, Table, ViewSpec } from './types'
import { t as tr, appLocale } from '../../i18n'

// ---------------------------------------------------------------- history

// ACT table.history @web.table
export interface PointTarget { seq: number; label: string }

export function HistoryDialog({ table, onClose, onRestorePoint }: { table: Table; onClose: () => void; onRestorePoint: (p: PointTarget) => void }) {
  const h = useTableHistory(table.id, true)
  const [open, setOpen] = useState<Set<string>>(new Set())
  return (
    <Dialog open onClose={onClose} title={tr('tables.dialogs.historyOf', { tableName: table.name })} wide footer={<button type="button" className="btn filled" onClick={onClose}>{tr('common.done')}</button>}>
      <p className="muted nt-small">{tr('tables.dialogs.changesOfTheLast30')}</p>
      {h.isLoading && <p className="muted">{tr('common.loading2')}</p>}
      <ul className="tb-sessions" data-testid="table-history">
        {(h.data?.sessions ?? []).map((s, i) => {
          const isOpen = open.has(s.id)
          return (
            <li key={s.id}>
              <div className="tb-session-line">
                <button type="button" className="icon-btn sm" aria-expanded={isOpen} aria-label={isOpen ? tr('tables.dialogs.fold') : tr('tables.dialogs.showTheChanges')} onClick={() => setOpen((o) => {
                  const n = new Set(o)
                  if (n.has(s.id)) n.delete(s.id)
                  else n.add(s.id)
                  return n
                })}>{isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button>
                <span><span className="muted">{whoWhen('', s.end).replace(/^ · /, '')}</span> · {s.summary}</span>
                <span className="spacer" />
                {i > 0 && table.level !== 'view' && <button type="button" className="btn text sm" onClick={() => onRestorePoint({ seq: s.to_seq, label: tr('tables.dialogs.after', { summary: s.summary }) })}>{tr('tables.dialogs.restoreToHere')}</button>}
              </div>
              {isOpen && (
                <ul className="tb-session-changes">{s.changes.map((c) => (
                  <li key={c.seq}><span className="muted nt-small">{c.who}</span> <span className="tb-change-text">{changeText(table, c)}</span>
                    {table.level !== 'view' && <button type="button" className="btn text xs" onClick={() => onRestorePoint({ seq: c.seq - 1, label: tr('tables.dialogs.before', { summary: c.summary }) })}>{tr('tables.dialogs.restoreToBeforeThis')}</button>}
                  </li>
                ))}</ul>
              )}
            </li>
          )
        })}
        {h.data && !h.data.sessions.length && <li className="muted">{tr('tables.dialogs.noChangesInTheLast')}</li>}
      </ul>
    </Dialog>
  )
}

/** A change as History shows it; added, deleted and restored rows are named by their primary value. */
function changeText(table: Table, c: Change): string {
  if (c.kind !== 'row' || !['add', 'delete', 'restore'].includes(c.op)) return c.summary
  const pc = table.columns?.find((x) => x.id === table.primary_column)
  const full = (c.op === 'delete' ? c.before : c.after) as { cells?: Record<string, CellValue> } | null
  const name = pc ? plainText(pc, full?.cells?.[pc.id] ?? null) : ''
  return name ? `${c.summary} ${name}` : c.summary
}

// ---------------------------------------------------------------- restore to a point in time

// ACT table.restore_point @web.table
export function RestorePointDialog({ table, session, onClose }: { table: Table; session: PointTarget | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [when, setWhen] = useState(() => {
    const d = new Date(Date.now() - 3600_000)
    const p = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
  })
  const [preview, setPreview] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const body = session ? { seq: session.seq, label: session.label } : { at: new Date(when).toISOString(), label: new Date(when).toLocaleString(appLocale()) }
  const key = JSON.stringify(body)
  useEffect(() => {
    let live = true
    setPreview(null)
    setError(null)
    restorePointPreview(table.id, JSON.parse(key)).then((p) => live && setPreview(p.summary)).catch((e: Error) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [key, table.id])
  return (
    <Dialog open onClose={onClose} title={tr('tables.dialogs.restoreToAPointIn')} footer={<>
      <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
      <button type="button" className="btn filled" disabled={!preview} data-testid="restore-point-confirm" onClick={() => {
        onClose()
        void restorePoint(qc, table.id, JSON.parse(key))
      }}>{tr('common.restore')}</button>
    </>}>
      {session ? <p>{tr('tables.dialogs.backToTheTableAs', { sessionLabel: session.label })}</p> : (
        <label className="field"><span>{tr('tables.dialogs.theMoment')}</span><input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} aria-label={tr('tables.dialogs.theMoment')} /></label>
      )}
      <p className="tb-preview" role="status" data-testid="restore-point-preview">{error ?? preview ?? tr('tables.dialogs.comparing')}</p>
      <p className="muted nt-small">{tr('tables.dialogs.theRestoreShowsInHistory')}</p>
    </Dialog>
  )
}

// ---------------------------------------------------------------- deleted rows and columns

// ACT row.restore @web.table
export function DeletedRowsDialog({ table, onClose }: { table: Table; onClose: () => void }) {
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const d = useDeletedRows(table.id, q, true)
  const rows = d.data?.rows ?? []
  return (
    <Dialog open onClose={onClose} title={tr('tables.dialogs.deletedRows')} wide footer={<>
      <button type="button" className="btn text" onClick={onClose}>{tr('common.close')}</button>
      <button type="button" className="btn filled" disabled={!sel.size || table.level === 'view'} onClick={() => {
        void restoreRows(qc, table.id, [...sel]).then(() => setSel(new Set()))
      }}>{tr('tables.dialogs.restore', { value: sel.size ? sel.size : '' })}</button>
    </>}>
      <input type="search" className="tb-dialog-search" placeholder={tr('tables.dialogs.searchDeletedRows')} aria-label={tr('tables.dialogs.searchDeletedRows')} value={q} onChange={(e) => setQ(e.target.value)} />
      <ul className="tb-pick-list" data-testid="deleted-rows">
        {rows.map((r) => (
          <li key={r.id}>
            <label className="tb-check-line">
              <input type="checkbox" className="checkbox" checked={sel.has(r.id)} onChange={() => setSel((s) => {
                const n = new Set(s)
                if (n.has(r.id)) n.delete(r.id)
                else n.add(r.id)
                return n
              })} />
              <span>{r.title}</span>
              <span className="muted nt-small">{whoWhen(r.deleted_by?.name ?? tr('common.someone'), r.deleted_at!)}</span>
            </label>
          </li>
        ))}
        {d.data && !rows.length && <li className="muted">{tr('tables.dialogs.noDeletedRowsInThe')}</li>}
      </ul>
    </Dialog>
  )
}

// ACT column.restore @web.table
export function DeletedColumnsDialog({ table, onClose }: { table: Table; onClose: () => void }) {
  const qc = useQueryClient()
  const d = useDeletedColumns(table.id, true)
  return (
    <Dialog open onClose={onClose} title={tr('tables.dialogs.deletedColumns')} footer={<button type="button" className="btn filled" onClick={onClose}>{tr('common.done')}</button>}>
      <ul className="tb-pick-list" data-testid="deleted-columns">
        {(d.data ?? []).map((c) => (
          <li key={c.id} className="tb-pop-line">
            <span>{c.name} <span className="muted nt-small">{TYPE_LABELS[c.type]} · {plural(c.values ?? 0, 'value')}</span></span>
            <span className="spacer" />
            {table.level !== 'view' && <button type="button" className="btn text sm" onClick={() => void restoreColumn(qc, table.id, c.id)}>{tr('common.restore')}</button>}
          </li>
        ))}
        {d.data && !d.data.length && <li className="muted">{tr('tables.dialogs.noDeletedColumnsInThe')}</li>}
      </ul>
    </Dialog>
  )
}

// ---------------------------------------------------------------- CSV

/** CSV into a new table, or rows appended to `table`: read, preview with the detected types
 * ("Number · 98%"), Skip per column, then the result with Undo for 7 s. */
// ACT table.import_csv @web.table @web.notes
export function ImportDialog({ table, onClose }: { table?: Table; onClose: () => void }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const [src, setSrc] = useState<CsvSource | null>(null)
  const [text, setText] = useState('')
  const [pv, setPv] = useState<CsvPreview | null>(null)
  const [cols, setCols] = useState<Record<number, { type?: ColumnType; skip?: boolean; name?: string; col?: string }>>({})
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const key = JSON.stringify(src)
  useEffect(() => {
    if (!src) return
    let live = true
    setErr(null)
    csvPreview(src, table?.id).then((p) => {
      if (!live) return
      setPv(p)
    }).catch((e: Error) => live && setErr(e.message))
    return () => {
      live = false
    }
  }, [key, table?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const run = async () => {
    if (!src || !pv) return
    setBusy(true)
    try {
      const columns = pv.columns.map((c) => {
        const o = cols[c.index] ?? {}
        if (table) return o.skip ? { index: c.index, skip: true } : o.col ? { index: c.index, col: o.col } : { index: c.index }
        return { index: c.index, skip: !!o.skip, type: o.type ?? c.type, name: o.name ?? c.name, currency: c.currency }
      })
      const r = await importCsv(qc, { ...src, delimiter: pv.delimiter, header: pv.header, columns, ...(table ? {} : { name: name || tr('tables.dialogs.importedTable') }) }, table?.id)
      offerUndo(qc, (r as { undo?: never }).undo, r.summary)
      onClose()
      if (r.table) nav(tableRoutePath(r.table.id))
    } catch {
      setBusy(false)
    }
  }
  return (
    <Dialog open onClose={onClose} modal wide title={table ? tr('tables.dialogs.importCsvInto', { tableName: table.name }) : tr('tables.dialogs.importACsvFile')} footer={<>
      <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
      <button type="button" className="btn filled" disabled={!pv || busy} onClick={() => void run()} data-testid="import-run">{tr('tables.dialogs.import', { value: pv ? plural(pv.total, 'row') : '' })}</button>
    </>}>
      {!pv && (
        <div className="tb-import-src">
          <label className="btn tonal"><Upload size={16} aria-hidden />{' '}{tr('tables.dialogs.chooseAFile')}
            <input type="file" accept=".csv,.tsv,.txt,text/csv" hidden aria-label={tr('tables.dialogs.csvFile')} onChange={async (e) => {
              const f = e.target.files?.[0]
              if (!f) return
              if (!name) setName(f.name.replace(/\.(csv|tsv|txt)$/i, ''))
              setSrc({ base64: await fileBase64(f) })
            }} />
          </label>
          <p className="muted nt-small">{tr('tables.dialogs.orPasteTheRowsComma')}</p>
          <textarea rows={6} value={text} aria-label={tr('tables.dialogs.csvText')} onChange={(e) => setText(e.target.value)} />
          <button type="button" className="btn text sm" disabled={!text.trim()} onClick={() => setSrc({ text })}>{tr('common.preview')}</button>
          <p className="muted nt-small">{tr('tables.dialogs.upTo5MbAnd')}</p>
        </div>
      )}
      {err && <p className="nt-banner warn" role="alert">{err}</p>}
      {pv && (
        <div className="tb-import" data-testid="import-preview">
          <div className="tb-row-fields">
            {!table && <label className="field"><span>{tr('tables.dialogs.tableName')}</span><input value={name} onChange={(e) => setName(e.target.value)} aria-label={tr('tables.dialogs.tableName')} /></label>}
            <label className="field"><span>{tr('tables.dialogs.separator')}</span>
              <select value={pv.delimiter} aria-label={tr('tables.dialogs.separator')} onChange={(e) => setSrc({ ...src!, delimiter: e.target.value })}>
                <option value=",">{tr('tables.dialogs.comma')}</option><option value=";">{tr('tables.dialogs.semicolon')}</option><option value={'\t'}>{tr('common.tab')}</option>
              </select>
            </label>
            <label className="tb-check-line"><input type="checkbox" className="checkbox" checked={pv.header} onChange={(e) => setSrc({ ...src!, header: e.target.checked })} />{' '}{tr('tables.dialogs.firstRowIsTheHeader')}</label>
          </div>
          <div className="tb-import-scroll">
            <table className="tb-import-table">
              <thead>
                <tr>{pv.columns.map((c) => {
                  const o = cols[c.index] ?? {}
                  const plan = pv.plan?.find((p) => p.index === c.index)
                  return (
                    <th key={c.index} className={o.skip ? 'skip' : undefined}>
                      <div>{c.name}</div>
                      <div className="muted nt-small" data-testid="import-type">{c.label}</div>
                      {table ? (
                        <div className="nt-small">{plan?.action === 'map' ? `→ ${table.columns?.find((x) => x.id === plan.col)?.name ?? ''}` : plan?.action === 'add' ? tr('tables.dialogs.newColumn') : tr('tables.dialogs.skipped')}</div>
                      ) : (
                        <select value={o.type ?? c.type} aria-label={tr('tables.dialogs.typeOf', { itemName: c.name })} onChange={(e) => setCols({ ...cols, [c.index]: { ...o, type: e.target.value as ColumnType } })}>
                          {(Object.keys(TYPE_LABELS) as ColumnType[]).filter((t) => t !== 'link').map((t) => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
                        </select>
                      )}
                      <label className="tb-check-line nt-small"><input type="checkbox" className="checkbox" checked={!!o.skip} onChange={(e) => setCols({ ...cols, [c.index]: { ...o, skip: e.target.checked } })} />{' '}{tr('tables.dialogs.skip')}</label>
                    </th>
                  )
                })}</tr>
              </thead>
              <tbody>{pv.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{v}</td>)}</tr>)}</tbody>
            </table>
          </div>
          <p className="muted nt-small">{tr('tables.dialogs.valuesThatDoNotConvert', { plural: plural(pv.total, 'row'), encoding: pv.encoding })}</p>
        </div>
      )}
    </Dialog>
  )
}

// ACT table.export_csv @web.table, ACT table.export_xlsx @web.table
export function ExportDialog({ table, view, spec, format = 'csv', onClose }: { table: Table; view: string | null; spec: Partial<ViewSpec> | null; format?: 'csv' | 'xlsx'; onClose: () => void }) {
  const [scope, setScope] = useState<'view' | 'all'>('view')
  const [bom, setBom] = useState(false)
  return (
    <Dialog open onClose={onClose} title={format === 'xlsx' ? tr('tables.dialogs.exportToExcel') : tr('common.exportCsv')} footer={<>
      <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
      <button type="button" className="btn filled" onClick={() => {
        onClose()
        void exportCsv(table, { scope, view, spec, bom, format })
      }}>{tr('tables.dialogs.export')}</button>
    </>}>
      <div className="tb-form">
        <label className="tb-check-line"><input type="radio" name="scope" checked={scope === 'view'} onChange={() => setScope('view')} />{' '}{tr('tables.dialogs.theCurrentViewItsFilters')}</label>
        <label className="tb-check-line"><input type="radio" name="scope" checked={scope === 'all'} onChange={() => setScope('all')} />{' '}{tr('tables.dialogs.theWholeTable')}</label>
        {format === 'csv' ? (
          <label className="tb-check-line"><input type="checkbox" className="checkbox" checked={bom} onChange={(e) => setBom(e.target.checked)} />{' '}{tr('tables.dialogs.forSpreadsheetProgramsAddsA')}</label>
        ) : (
          <p className="muted nt-small">{tr('tables.dialogs.anXlsxWorkbookForExcel')}</p>
        )}
      </div>
    </Dialog>
  )
}

// ---------------------------------------------------------------- a new table

interface BuilderCol { name: string; type: ColumnType; options: SelectOption[] }
const TEMPLATES: Record<string, BuilderCol[]> = {
  log: [{ name: tr('common.date'), type: 'date', options: [] }, { name: tr('tables.dialogs.item'), type: 'text', options: [] }, { name: tr('common.amount'), type: 'number', options: [] }],
  inventory: [{ name: tr('tables.dialogs.item'), type: 'text', options: [] }, { name: tr('tables.dialogs.qty'), type: 'number', options: [] }, { name: tr('common.location'), type: 'text', options: [] }],
}

/** The column builder of a new table (table.create): a name, columns with their type
 * and options, or a template (Log, Inventory). One text column "Name" by default. */
// ACT table.create @web.notes
export function NewTableDialog({ initialName = '', label, onClose }: { initialName?: string; label?: string | null; onClose: () => void }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const [name, setName] = useState(initialName)
  const [cols, setCols] = useState<BuilderCol[]>([{ name: tr('common.name'), type: 'text', options: [] }])
  const [busy, setBusy] = useState(false)
  const [importing, setImporting] = useState(false)
  if (importing) return <ImportDialog onClose={onClose} />
  const ok = cols.length > 0 && cols.every((c) => c.name.trim()) && new Set(cols.map((c) => c.name.trim().toLowerCase())).size === cols.length
  const create = async () => {
    setBusy(true)
    try {
      const columns: ColumnSpec[] = cols.map((c) => ({ name: c.name.trim(), type: c.type, ...(c.options.length ? { options: c.options.map((o) => o.name) } : {}) }))
      const t = await createTable(qc, { name: name.trim() || tr('common.untitledTable'), columns, ...(label ? { labels: [label] } : {}) })
      onClose()
      nav(tableRoutePath(t.id), { state: { fresh: true } })
    } catch {
      setBusy(false)
    }
  }
  return (
    <Dialog open onClose={onClose} modal title={tr('tables.dialogs.newTable')} footer={<>
      <button type="button" className="btn text" onClick={() => setImporting(true)}><Upload size={16} aria-hidden />{' '}{tr('tables.dialogs.importCsv')}</button>
      <span className="spacer" />
      <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
      <button type="button" className="btn filled" disabled={!ok || busy} onClick={() => void create()} data-testid="new-table-create">{tr('tables.dialogs.createTable')}</button>
    </>}>
      <div className="tb-form" data-testid="new-table">
        <label className="field"><span>{tr('common.name')}</span><input autoFocus value={name} maxLength={200} placeholder={tr('tables.dialogs.workouts')} aria-label={tr('tables.dialogs.tableName')} onChange={(e) => setName(e.target.value)} /></label>
        <div className="tb-templates">
          <span className="muted nt-small">{tr('tables.dialogs.startFrom')}</span>
          <button type="button" className="btn text sm" onClick={() => setCols(TEMPLATES.log.map((c) => ({ ...c })))}>{tr('tables.dialogs.log')}</button>
          <button type="button" className="btn text sm" onClick={() => setCols(TEMPLATES.inventory.map((c) => ({ ...c })))}>{tr('tables.dialogs.inventory')}</button>
        </div>
        <ol className="tb-builder">
          {cols.map((c, i) => (
            <li key={i}>
              <div className="tb-pop-line">
                <input value={c.name} aria-label={tr('tables.dialogs.columnName', { value: i + 1 })} maxLength={100} onChange={(e) => setCols(cols.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                <select value={c.type} aria-label={tr('tables.dialogs.columnType', { value: i + 1 })} onChange={(e) => setCols(cols.map((x, j) => (j === i ? { ...x, type: e.target.value as ColumnType } : x)))}>
                  {(Object.keys(TYPE_LABELS) as ColumnType[]).filter((t) => t !== 'link').map((t) => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
                </select>
                <button type="button" className="icon-btn sm" aria-label={tr('tables.dialogs.removeColumn', { itemName: c.name })} disabled={cols.length === 1} onClick={() => setCols(cols.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
              </div>
              {(c.type === 'select' || c.type === 'multi_select') && <OptionsEditor opts={c.options} original={[]} onChange={(o) => setCols(cols.map((x, j) => (j === i ? { ...x, options: o } : x)))} />}
            </li>
          ))}
        </ol>
        {cols.length < 50 && <button type="button" className="btn text sm" onClick={() => setCols([...cols, { name: '', type: 'text', options: [] }])}><Plus size={14} aria-hidden />{' '}{tr('tables.dialogs.addAColumn')}</button>}
        <p className="muted nt-small">{tr('tables.dialogs.linkColumnsAreAddedFrom')}</p>
      </div>
    </Dialog>
  )
}

export { TypePicker }

// ---------------------------------------------------------------- keys

const KEYS: [string, string][] = [
  [tr('tables.dialogs.arrows'), tr('tables.dialogs.moveTheFocusedCell')], [tr('tables.dialogs.enterF2'), tr('tables.dialogs.editTheCellEnterAgain')], [tr('tables.dialogs.tabShiftTab'), tr('tables.dialogs.saveAndMoveRightLeft')],
  [tr('tables.dialogs.typing'), tr('tables.dialogs.startEditingWithThatCharacter')], [tr('common.esc'), tr('tables.dialogs.cancelTheEditThenClear')], [tr('common.space'), tr('tables.dialogs.toggleACheckboxOpenA')],
  [tr('tables.dialogs.deleteBackspace'), tr('tables.dialogs.clearTheCellOrThe')], ['Shift+arrows, Shift+click', tr('tables.dialogs.selectARangeOfCells')],
  [tr('tables.dialogs.ctrlCCtrlV'), tr('tables.dialogs.copyAndPasteRangesAs')], ['Ctrl/⌘+D', tr('tables.dialogs.fillDownTheSelectedRange')], ['Shift+Enter', tr('tables.dialogs.aNewRowBelow')],
  [tr('tables.dialogs.ctrlEnterO'), tr('tables.dialogs.openTheRowForm')], ['x', tr('tables.dialogs.selectTheRow')], ['Ctrl/⌘+Shift+Backspace', tr('tables.dialogs.deleteTheSelectedRows')],
  ['Alt+↑ / Alt+↓', tr('tables.dialogs.moveTheRowManualOrder')], ['/', tr('tables.dialogs.searchInTheTable')], [tr('tables.dialogs.zCtrlZ'), tr('common.undo')], ['?', tr('tables.dialogs.thisHelp')],
]

export function KeysDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog open onClose={onClose} title={tr('tables.dialogs.keyboardShortcutsTables')} footer={<button type="button" className="btn filled" onClick={onClose}>{tr('common.done')}</button>}>
      <dl className="tb-keys">{KEYS.map(([k, d]) => <div key={k}><dt><kbd>{k}</kbd></dt><dd>{d}</dd></div>)}</dl>
    </Dialog>
  )
}
