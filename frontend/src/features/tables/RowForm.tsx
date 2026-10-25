// The row form (on phones too): one field per column in column
// order, hidden ones folded under "Hidden fields"; tabs Fields, History and Links. Each field saves
// on its own (autosave, with the undo toast). A new row (the phone's +) fills the view's filter
// values and offers "Save and add another". History lists the row's changes ("Dana · 3 Oct 14:02 ·
// Weight 80 → 82") with "Restore row to this point"; Earlier values shows one cell's past values
// with "Use this value".

import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronRight, History, Link2, ListTree, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { ApiError } from '../../api/client'
import { useMe } from '../../api/hooks'
import { Popover } from '../../components/Popover'
import { itemKey, useLive } from '../../lib/live/store'
import { EditingBanner } from '../live/LiveBars'
import { useEditingPerson } from '../notes/presence'
import { toast } from '../../stores/ui'
import { addRow, editRow, restoreRows, restoreVersion } from './actions'
import { useRow, useRowHistory } from './api'
import { LinkPicker, OptionChip, RefChip, SelectPicker } from './cells'
import { editText, isEmpty, optionsOf, rawTip, refKey, whoWhen, dateHint } from './model'
import { openRef } from './refs'
import type { CellValue, Column, Filter, LinkRef, Table, ViewSpec } from './types'
import { t, appLocale } from '../../i18n'

export interface RowFormProps {
  table: Table
  columns: Column[]
  spec: ViewSpec
  rowId: string | null
  canEdit: boolean
  /** Open on Earlier values of this cell. */
  earlier?: string | null
  /** A new row: the filters whose values it starts with. */
  filters?: Filter[]
  onClose: () => void
  onCreated?: (id: string) => void
}

type Tab = 'fields' | 'history' | 'links'

export function RowForm({ table, columns, spec, rowId, canEdit, earlier, filters, onClose, onCreated }: RowFormProps) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const row = useRow(table.id, rowId)
  const [tab, setTab] = useState<Tab>(earlier ? 'history' : 'fields')
  const [cellFor, setCellFor] = useState<string | null>(earlier ?? null)
  const [showHidden, setShowHidden] = useState(false)
  const [draft, setDraft] = useState<Record<string, CellValue>>({})
  const hist = useRowHistory(table.id, rowId, tab === 'history' && !!rowId)
  const isNew = !rowId
  const r = row.data
  const refs = r?.refs ?? {}
  const hidden = new Set(spec.hidden)
  const shown = columns.filter((c) => c.id === table.primary_column || !hidden.has(c.id))
  const folded = columns.filter((c) => c.id !== table.primary_column && hidden.has(c.id))
  const title = isNew ? t('common.newRow') : r?.title ?? '…'
  // "Assistant is editing this row…" while the assistant or an agent writes it (live `editing` events).
  const editing = useLive((s) => (rowId ? s.editing[itemKey('row', rowId)] ?? null : null))
  const otherEditing = useEditingPerson(table.id, useMe().data?.user.id)

  useEffect(() => setCellFor(earlier ?? null), [earlier])
  useEffect(() => {
    if (earlier) setTab('history')
  }, [earlier])

  const save = async (col: Column, value: CellValue, parse = false) => {
    if (isNew) {
      setDraft((d) => ({ ...d, [col.id]: value }))
      return
    }
    // A field is read-only until the row has loaded, so a change is never dropped for want of it.
    const cur = r ?? (await row.refetch()).data
    if (!cur) return
    try {
      await editRow(qc, table.id, cur, { [col.id]: value }, { parse })
      void row.refetch()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const body = (e.body ?? {}) as { conflict?: boolean; detail?: { deleted?: boolean } }
        if (body.conflict) toast({ message: t('tables.rowForm.wasChangedBySomeoneElse', { colName: col.name }), actionLabel: t('common.useMine'), onAction: () => void editRow(qc, table.id, cur, { [col.id]: value }, { parse, force: true }) })
        void row.refetch()
      }
    }
  }

  const create = async (another: boolean) => {
    const cells: Record<string, CellValue> = {}
    const parse = Object.entries(draft).some(([k, v]) => typeof v === 'string' && columns.find((c) => c.id === k)?.type !== 'text')
    for (const [k, v] of Object.entries(draft)) if (!isEmpty(v)) cells[k] = v
    // ACT row.add @web.table
    const made = await addRow(qc, table.id, cells, { filters, parse })
    setDraft({})
    if (another) toast({ message: t('tables.rowForm.rowAdded'), duration: 2500 })
    else onCreated?.(made.id)
  }

  const fields = (cols: Column[]) => cols.map((c) => (
    <Field key={c.id} table={table} col={c} value={isNew ? draft[c.id] : r?.cells[c.id]} raw={isNew ? undefined : r?.raw[c.id]} refs={refs} readOnly={!canEdit || (!isNew && !r) || (!!r?.deleted_at)}
      onSave={(v, parse) => void save(c, v, parse)} onEarlier={isNew ? undefined : () => {
        setCellFor(c.id)
        setTab('history')
      }} onOpenRef={(ref) => openRef(nav, ref, refs)} />
  ))

  return (
    <div className="tb-form-panel" role="dialog" aria-label={t('tables.rowForm.row', { title })} data-testid="row-form">
      <header className="tb-form-head">
        <h2>{title}</h2>
        <span className="spacer" />
        <button type="button" className="icon-btn" aria-label={t('common.close')} onClick={onClose}><X /></button>
      </header>
      <EditingBanner kind="row" editing={editing} />
      {/* "Dana is editing": another person's write to this table in the last 10 s. */}
      {otherEditing && <div className="nt-banner" role="status" data-testid="row-presence"><span className="nt-presence">{t('tables.rowForm.isEditing', { otherEditing })}</span></div>}
      {r?.deleted_at && (
        <div className="nt-banner warn" role="status">
          {t('tables.rowForm.deletedThisRow', { deletedByName: r.deleted_by?.name ?? t('common.someone') })}
          {canEdit && <button type="button" className="btn text sm" onClick={() => void restoreRows(qc, table.id, [r.id]).then(() => row.refetch())}>{t('common.restore')}</button>}
        </div>
      )}
      {!isNew && (
        <div className="tb-form-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'fields'} className={tab === 'fields' ? 'on' : ''} onClick={() => setTab('fields')}><ListTree size={16} aria-hidden />{' '}{t('tables.rowForm.fields')}</button>
          <button type="button" role="tab" aria-selected={tab === 'history'} className={tab === 'history' ? 'on' : ''} onClick={() => setTab('history')}><History size={16} aria-hidden />{' '}{t('tables.rowForm.history')}</button>
          <button type="button" role="tab" aria-selected={tab === 'links'} className={tab === 'links' ? 'on' : ''} onClick={() => setTab('links')}><Link2 size={16} aria-hidden />{' '}{t('common.links')}</button>
        </div>
      )}
      <div className="tb-form-body">
        {tab === 'fields' && (
          <>
            {fields(shown)}
            {folded.length > 0 && (
              <>
                <button type="button" className="btn text sm tb-fold" aria-expanded={showHidden} onClick={() => setShowHidden((s) => !s)}>
                  {showHidden ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}{' '}{t('tables.rowForm.hiddenFields', { foldedLength: folded.length })}
                </button>
                {showHidden && fields(folded)}
              </>
            )}
            {/* The row's authors (rows show who wrote them). */}
            {!isNew && r && <p className="muted nt-small tb-meta" data-testid="row-authors">{t('tables.rowForm.addedChanged', { value: r.created_by?.name ? t('tables.rowForm.by', { createdByName: r.created_by.name }) : '', value2: new Date(r.created_at).toLocaleString(appLocale()), value3: r.updated_by?.name ? t('tables.rowForm.by2', { updatedByName: r.updated_by.name }) : '', value4: new Date(r.updated_at).toLocaleString(appLocale()) })}</p>}
          </>
        )}
        {tab === 'history' && !isNew && (
          <RowHistory table={table} columns={columns} rowId={rowId!} canEdit={canEdit} cellFor={cellFor} onCellFor={setCellFor} data={hist.data} loading={hist.isLoading}
            onRestored={() => void row.refetch()} />
        )}
        {tab === 'links' && !isNew && (
          <div className="tb-links">
            <h3>{t('tables.rowForm.linkedFrom')}</h3>
            {r?.linked_from?.length ? (
              <ul>{r.linked_from.map((l) => (
                <li key={`${l.table}:${l.row}:${l.column}`}><button type="button" className="btn text sm" onClick={() => nav(`/tables/t/${encodeURIComponent(l.table)}/r/${encodeURIComponent(l.row)}`)}>{l.title}</button>
                  <span className="muted nt-small"> · {l.table_name}, {l.column}</span></li>
              ))}</ul>
            ) : <p className="muted">{t('tables.rowForm.noRowLinksHere')}</p>}
            {columns.filter((c) => c.type === 'link').map((c) => (
              <div key={c.id}>
                <h3>{c.name}</h3>
                <div className="tb-chips wrap">{((r?.cells[c.id] as LinkRef[] | null) ?? []).map((ref) => <RefChip key={refKey(ref)} r={ref} info={refs[refKey(ref)]} onOpen={() => openRef(nav, ref, refs)} />)}</div>
              </div>
            ))}
          </div>
        )}
      </div>
      {isNew && (
        <footer className="tb-form-foot">
          <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn tonal" onClick={() => void create(true)}>{t('tables.rowForm.saveAndAddAnother')}</button>
          <button type="button" className="btn filled" onClick={() => void create(false)}>{t('common.save')}</button>
        </footer>
      )}
    </div>
  )
}

function RowHistory({ table, columns, rowId, canEdit, cellFor, onCellFor, data, loading, onRestored }: {
  table: Table; columns: Column[]; rowId: string; canEdit: boolean; cellFor: string | null; onCellFor: (c: string | null) => void
  data: ReturnType<typeof useRowHistory>['data']; loading: boolean; onRestored: () => void
}) {
  const qc = useQueryClient()
  const col = columns.find((c) => c.id === cellFor)
  if (loading) return <p className="muted">{t('tables.rowForm.loadingTheHistory')}</p>
  if (!data) return null
  if (col) {
    const vals = data.values[col.id] ?? []
    return (
      <div className="tb-earlier" data-testid="earlier-values">
        <button type="button" className="btn text sm" onClick={() => onCellFor(null)}>{t('tables.rowForm.allChanges')}</button>
        <h3>{t('tables.rowForm.earlierValuesOf', { colName: col.name })}</h3>
        <ul>{vals.map((v, i) => (
          <li key={v.seq}>
            <span className="tb-val">{v.text || '(empty)'}</span>
            <span className="muted nt-small"> {whoWhen(v.who, v.at)}{i === 0 ? ' · now' : ''}</span>
            {/* ACT row.restore_version @web.table */}
            {canEdit && i > 0 && <button type="button" className="btn text sm" onClick={() => void restoreVersion(qc, table.id, rowId, v.seq, col.id).then(onRestored)}>{t('tables.rowForm.useThisValue')}</button>}
          </li>
        ))}</ul>
        {!vals.length && <p className="muted">{t('tables.rowForm.noEarlierValuesInThe')}</p>}
      </div>
    )
  }
  return (
    <ul className="tb-history" data-testid="row-history">
      {/* ACT row.history @web.table */}
      {data.changes.map((ch, i) => (
        <li key={ch.seq}>
          <div><strong>{ch.who}</strong> <span className="muted nt-small">{whoWhen('', ch.at).replace(/^ · /, '')}</span></div>
          <div>{ch.summary}</div>
          {canEdit && i > 0 && ch.kind === 'row' && <button type="button" className="btn text sm" onClick={() => void restoreVersion(qc, table.id, rowId, ch.seq).then(onRestored)}>{t('tables.rowForm.restoreTheRowToThis')}</button>}
        </li>
      ))}
      {!data.changes.length && <li className="muted">{t('tables.rowForm.noChangesInTheLast')}</li>}
      <li className="muted nt-small">{t('tables.rowForm.earlierValuesOfOneCell')}</li>
      <li className="tb-chips wrap">{columns.map((c) => <button key={c.id} type="button" className="btn text sm" onClick={() => onCellFor(c.id)}>{c.name}</button>)}</li>
    </ul>
  )
}

/** One field of the form, typed by its column. */
function Field({ table, col, value, raw, refs, readOnly, onSave, onEarlier, onOpenRef }: {
  table: Table; col: Column; value: CellValue | undefined; raw?: string; refs: Record<string, import('./types').RefInfo>; readOnly: boolean
  onSave: (v: CellValue, parse: boolean) => void; onEarlier?: () => void; onOpenRef: (r: LinkRef) => void
}) {
  const [text, setText] = useState(editText(col, value, raw))
  const [pick, setPick] = useState<HTMLElement | null>(null)
  const id = useMemo(() => `f-${col.id}`, [col.id])
  // The stored value shows again when it changes, unless the field is being edited: a refetch of the
  // table or the row (a new `col` or `value` object) must not wipe what the user is typing.
  // This happens while rendering, not in an effect: the field is read-only until the row loads, and
  // with an effect the first render after the load showed an editable, still empty field, then the
  // effect put the stored text in under the user's first keys ("Mon" + "Monday" = "MonMonday").
  const stored = editText(col, value, raw)
  const [focused, setFocused] = useState(false)
  const [shown, setShown] = useState({ stored, focused })
  if (shown.stored !== stored || shown.focused !== focused) {
    setShown({ stored, focused })
    if (!focused) setText(stored)
  }
  const commit = () => {
    setFocused(false)
    if (text === editText(col, value, raw)) return
    onSave(col.type === 'text' ? text : (text.trim() || null), col.type !== 'text')
  }
  const label = (
    <span className="tb-field-label">
      {col.name}{col.required && <span className="tb-req" aria-label="required">*</span>}
      {onEarlier && <button type="button" className="btn text xs tb-earlier-btn" onClick={onEarlier}>{t('tables.rowForm.earlierValues')}</button>}
    </span>
  )
  let input
  if (col.type === 'checkbox') {
    input = <input id={id} type="checkbox" className="checkbox" checked={!!value} disabled={readOnly} onChange={(e) => onSave(e.target.checked, false)} />
  } else if (col.type === 'select' || col.type === 'multi_select' || col.type === 'link') {
    const chips = col.type === 'link'
      ? ((value as LinkRef[] | null) ?? []).map((r) => <RefChip key={refKey(r)} r={r} info={refs[refKey(r)]} onOpen={() => onOpenRef(r)} />)
      : (Array.isArray(value) ? value as string[] : value ? [value as string] : []).map((o) => <OptionChip key={o} o={optionsOf(col).find((x) => x.id === o)} />)
    input = (
      <button id={id} type="button" className="tb-field-pick" disabled={readOnly} onClick={(e) => setPick(e.currentTarget)}>
        <span className="tb-chips wrap">{chips.length ? chips : <span className="muted">{t('common.choose')}</span>}</span>
      </button>
    )
  } else if (col.type === 'text' && col.config.long) {
    input = <textarea id={id} value={text} rows={4} disabled={readOnly} onFocus={() => setFocused(true)} onChange={(e) => setText(e.target.value)} onBlur={commit} />
  } else {
    input = <input id={id} value={text} disabled={readOnly} inputMode={col.type === 'number' || col.type === 'currency' ? 'decimal' : undefined}
      placeholder={col.type === 'date' ? dateHint() : col.type === 'datetime' ? t('tables.rowForm.fri3pm') : undefined}
      onFocus={() => setFocused(true)} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
  }
  return (
    <div className={`tb-field tb-f-${col.type}${raw && isEmpty(value) ? ' raw' : ''}`}>
      <label htmlFor={id}>{label}</label>
      {input}
      {raw && isEmpty(value) && <span className="tb-field-warn" role="note"><AlertTriangle size={13} aria-hidden /> {rawTip(col, raw)}</span>}
      <Popover anchor={pick} open={!!pick} onClose={() => setPick(null)} role="dialog" label={col.name} placement="bottom-start" minWidth={260}>
        {col.type === 'link' ? (
          <LinkPicker table={table.id} col={col} value={value} refs={refs} onClose={() => setPick(null)} onChange={(v) => onSave(v.length ? v : null, false)} />
        ) : (
          <SelectPicker col={col} value={value} canCreate={!readOnly} onClose={() => setPick(null)} onPick={(v) => onSave(v, false)}
            onCreate={(name) => onSave(col.type === 'multi_select' ? [...((value as string[] | null) ?? []).map((x) => optionsOf(col).find((o) => o.id === x)?.name ?? ''), name].filter(Boolean).join('; ') : name, true)} />
        )}
      </Popover>
    </div>
  )
}
