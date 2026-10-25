// A column's ⌄ menu and its dialogs (column.* actions): sort, filter,
// group, hide, rename, change type with the preview of what converts, the options editor, the
// column's settings, insert left or right, make primary, delete with the count of values and a CSV
// copy offered first.

import { useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3, EyeOff, Filter, Group, KeyRound, Pencil, Plus, Settings2, Shapes, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { MenuItem, MenuSep, Popover } from '../../components/Popover'
import { useTables } from './api'
import { addColumn, deleteColumn, exportCsv, patchColumn, patchTable, retypePreview } from './actions'
import { OptionChip, TYPE_ICON } from './cells'
import { LINK_TARGET_LABELS, OPTION_COLORS, TYPES, TYPE_LABELS, plural } from './model'
import type { Column, ColumnType, SelectOption, Table, ViewSpec } from './types'
import { t as tr } from '../../i18n'

type Mode = 'add' | 'rename' | 'retype' | 'options' | 'config' | 'delete'
const GROUPABLE = new Set(['select', 'multi_select', 'checkbox', 'date', 'datetime', 'link'])

export function ColumnMenu({ table, col, anchor, spec, canEdit, onSpec, onClose, onFilter }: {
  table: Table; col: Column; anchor: HTMLElement; spec: ViewSpec; canEdit: boolean; onSpec: (p: Partial<ViewSpec>) => void; onClose: () => void; onFilter?: (col: Column) => void
}) {
  const qc = useQueryClient()
  const [mode, setMode] = useState<{ m: Mode; where?: 'before' | 'after' } | null>(col.id ? null : { m: 'add' })
  const primary = col.id === table.primary_column
  if (mode) return <ColumnDialog table={table} col={col} mode={mode.m} where={mode.where} onClose={onClose} />
  const sortBy = (dir: 'asc' | 'desc') => {
    onSpec({ sort: [{ col: col.id, dir }, ...spec.sort.filter((s) => s.col !== col.id)].slice(0, 3) })
    onClose()
  }
  return (
    <Popover anchor={anchor} open onClose={onClose} label={tr('tables.columns.column', { colName: col.name })} placement="bottom-start" minWidth={220}>
      <MenuItem icon={<ArrowUp size={16} />} onSelect={() => sortBy('asc')}>{tr('tables.columns.sortAscending')}</MenuItem>
      <MenuItem icon={<ArrowDown size={16} />} onSelect={() => sortBy('desc')}>{tr('tables.columns.sortDescending')}</MenuItem>
      <MenuItem icon={<Filter size={16} />} onSelect={() => {
        onClose()
        onFilter?.(col)
      }}>{tr('common.filter')}</MenuItem>
      {GROUPABLE.has(col.type) && !(col.type === 'link' && col.config.multiple) && (
        <MenuItem icon={<Group size={16} />} selected={spec.group_by?.col === col.id} onSelect={() => {
          onSpec({ group_by: spec.group_by?.col === col.id ? null : { col: col.id, ...(col.type === 'date' || col.type === 'datetime' ? { by: 'month' as const } : {}) } })
          onClose()
        }}>{tr('common.groupBy')}</MenuItem>
      )}
      {!primary && <MenuItem icon={<EyeOff size={16} />} onSelect={() => {
        onSpec({ hidden: [...new Set([...spec.hidden, col.id])] })
        onClose()
      }}>{tr('common.hide')}</MenuItem>}
      {canEdit && <>
        <MenuSep />
        <MenuItem icon={<Pencil size={16} />} onSelect={() => setMode({ m: 'rename' })}>{tr('common.rename')}</MenuItem>
        {col.type !== 'link' && <MenuItem icon={<Shapes size={16} />} onSelect={() => setMode({ m: 'retype' })}>{tr('tables.columns.changeType')}</MenuItem>}
        {(col.type === 'select' || col.type === 'multi_select') && <MenuItem icon={<ArrowUpDown size={16} />} onSelect={() => setMode({ m: 'options' })}>{tr('tables.columns.options')}</MenuItem>}
        <MenuItem icon={<Settings2 size={16} />} onSelect={() => setMode({ m: 'config' })}>{tr('common.settings')}</MenuItem>
        <MenuItem icon={<Columns3 size={16} />} onSelect={() => setMode({ m: 'add', where: 'before' })}>{tr('tables.columns.insertLeft')}</MenuItem>
        <MenuItem icon={<Columns3 size={16} />} onSelect={() => setMode({ m: 'add', where: 'after' })}>{tr('tables.columns.insertRight')}</MenuItem>
        {!primary && <MenuItem icon={<KeyRound size={16} />} onSelect={() => {
          onClose()
          // ACT column.set_primary @web.table
          void patchTable(qc, table, { primary_column: col.id }, tr('tables.columns.namesTheRowsNow', { colName: col.name }))
        }}>{tr('tables.columns.makePrimary')}</MenuItem>}
        <MenuSep />
        <MenuItem icon={<Trash2 size={16} />} danger onSelect={() => setMode({ m: 'delete' })}>{tr('common.delete2')}</MenuItem>
      </>}
    </Popover>
  )
}

export function ColumnDialog({ table, col, mode, where, onClose }: { table: Table; col: Column; mode: Mode; where?: 'before' | 'after'; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(mode === 'add' ? '' : col.name)
  const [type, setType] = useState<ColumnType>(mode === 'add' ? 'text' : col.type)
  const [cfg, setCfg] = useState<Column['config']>(mode === 'add' ? {} : col.config)
  const [required, setRequired] = useState(col.required)
  const [opts, setOpts] = useState<SelectOption[]>(col.config.options ?? [])
  const [merge, setMerge] = useState<Record<string, string>>({})
  const [preview, setPreview] = useState<{ convert: number; fail: number; examples: string[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const tables = useTables('tables', null, mode === 'add')
  const values = table.counts.rows

  useEffect(() => {
    if (mode !== 'retype' || type === col.type) {
      setPreview(null)
      return
    }
    let live = true
    void retypePreview(table.id, col.id, type).then((p) => live && setPreview(p)).catch(() => undefined)
    return () => {
      live = false
    }
  }, [mode, type, col.id, col.type, table.id])

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      onClose()
    } catch {
      setBusy(false)
    }
  }

  if (mode === 'delete') {
    return (
      <ConfirmDialog open danger title={tr('tables.columns.deleteColumn', { colName: col.name })}
        body={<>
          <p>{tr('tables.columns.deleteColumnHoldAPlace', { colName: col.name, plural: plural(values, 'row') })}</p>
          <button type="button" className="btn text sm" onClick={() => void exportCsv(table, { scope: 'all' })}>{tr('tables.columns.saveACsvCopyFirst')}</button>
        </>}
        confirmLabel={tr('tables.columns.deleteColumn2')} onClose={onClose} onConfirm={() => void run(() => deleteColumn(qc, table.id, col.id))} />
    )
  }

  const title = { add: tr('tables.columns.newColumn'), rename: tr('tables.columns.rename', { colName: col.name }), retype: tr('tables.columns.changeTheTypeOf', { colName: col.name }), options: tr('tables.columns.optionsOf', { colName: col.name }), config: tr('tables.columns.settingsOf', { colName: col.name }) }[mode]
  const submit = () => {
    if (mode === 'add') {
      const config: Column['config'] = { ...cfg }
      // ACT column.add @web.table
      return run(() => addColumn(qc, table.id, {
        name: name.trim(), type, config, ...(type === 'select' || type === 'multi_select' ? { options: opts.map((o) => o.name) } : {}),
        ...(where === 'before' ? { before: col.id } : where === 'after' ? { after: col.id } : {}),
      }))
    }
    // ACT column.rename @web.table
    if (mode === 'rename') return run(() => patchColumn(qc, table.id, col.id, { name: name.trim(), base_name: col.name }, { label: tr('tables.columns.renamedTo', { itemName: name.trim() }) }))
    // ACT column.retype @web.table
    if (mode === 'retype') return run(() => patchColumn<{ converted: number; kept_as_text: number }>(qc, table.id, col.id, { type }, { label: tr('tables.columns.changedTo', { colName: col.name, typeLabels: TYPE_LABELS[type].toLowerCase() }) }))
    // ACT column.options @web.table
    if (mode === 'options') return run(() => patchColumn(qc, table.id, col.id, { options: opts, merge }, { label: tr('tables.columns.optionsOfChanged', { colName: col.name }) }))
    // ACT column.config @web.table
    return run(() => patchColumn(qc, table.id, col.id, { config: cfg, required }, { label: tr('tables.columns.settingsOfChanged', { colName: col.name }) }))
  }
  const ok = mode === 'add' || mode === 'rename' ? !!name.trim() && (type !== 'link' || !!cfg.target) && !(type === 'link' && cfg.target === 'table' && !cfg.table) : true
  return (
    <Dialog open onClose={onClose} title={title} modal footer={<>
      <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
      <button type="button" className="btn filled" disabled={!ok || busy} onClick={() => void submit()} data-testid="column-dialog-save">
        {mode === 'add' ? tr('tables.columns.addColumn') : mode === 'retype' ? tr('tables.columns.changeType') : tr('common.save')}
      </button>
    </>}>
      <div className="tb-form">
        {(mode === 'add' || mode === 'rename') && (
          <label className="field"><span>{tr('common.name')}</span><input value={name} autoFocus maxLength={100} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && ok && void submit()} aria-label={tr('tables.columns.columnName')} /></label>
        )}
        {(mode === 'add' || mode === 'retype') && (
          <TypePicker value={type} onChange={setType} exclude={mode === 'retype' ? ['link'] : []} />
        )}
        {mode === 'retype' && type !== col.type && (
          <p className="tb-preview" role="status" data-testid="retype-preview">
            {preview ? tr('tables.columns.convertDoNotKeptAs', { count: preview.fail, plural: plural(preview.convert, 'cell') }) : tr('tables.columns.countingTheCellsThatConvert')}
            {preview?.examples.length ? <span className="muted"> · {preview.examples.slice(0, 3).map((x) => `“${x}”`).join(', ')}</span> : null}
          </p>
        )}
        {mode === 'add' && type === 'link' && (
          <>
            <label className="field"><span>{tr('tables.columns.linksTo')}</span>
              <select value={cfg.target ?? ''} onChange={(e) => setCfg({ ...cfg, target: e.target.value as Column['config']['target'] })} aria-label={tr('tables.columns.linksTo')}>
                <option value="" disabled>{tr('common.choose')}</option>
                {Object.entries(LINK_TARGET_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
            {cfg.target === 'table' && (
              <label className="field"><span>{tr('common.table')}</span>
                <select value={cfg.table ?? ''} onChange={(e) => setCfg({ ...cfg, table: e.target.value })} aria-label={tr('common.table')}>
                  <option value="" disabled>{tr('common.choose')}</option>
                  <option value="self">{tr('tables.columns.thisTable')}</option>
                  {(tables.data ?? []).filter((t) => t.id !== table.id).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </label>
            )}
            <label className="tb-check-line"><input type="checkbox" className="checkbox" checked={!!cfg.multiple} onChange={(e) => setCfg({ ...cfg, multiple: e.target.checked })} />{' '}{tr('tables.columns.allowSeveralLinks')}</label>
          </>
        )}
        {((mode === 'add' && (type === 'select' || type === 'multi_select')) || mode === 'options') && (
          <OptionsEditor opts={opts} onChange={setOpts} merge={mode === 'options' ? merge : undefined} onMerge={setMerge} original={col.config.options ?? []} />
        )}
        {(mode === 'config' || mode === 'add') && <ConfigFields type={mode === 'add' ? type : col.type} cfg={cfg} onCfg={setCfg} />}
        {mode === 'config' && (
          <label className="tb-check-line"><input type="checkbox" className="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />{' '}{tr('tables.columns.requiredAnEmptyCellShows')}</label>
        )}
      </div>
    </Dialog>
  )
}

export function TypePicker({ value, onChange, exclude = [] }: { value: ColumnType; onChange: (t: ColumnType) => void; exclude?: ColumnType[] }) {
  return (
    <div className="tb-types" role="radiogroup" aria-label={tr('tables.columns.type')}>
      {TYPES.filter((t) => !exclude.includes(t)).map((t) => (
        <button key={t} type="button" role="radio" aria-checked={value === t} className={`tb-type-btn${value === t ? ' on' : ''}`} onClick={() => onChange(t)}>
          {TYPE_ICON[t]} {TYPE_LABELS[t]}
        </button>
      ))}
    </div>
  )
}

function ConfigFields({ type, cfg, onCfg }: { type: ColumnType; cfg: Column['config']; onCfg: (c: Column['config']) => void }) {
  if (type === 'text') return <label className="tb-check-line"><input type="checkbox" className="checkbox" checked={!!cfg.long} onChange={(e) => onCfg({ ...cfg, long: e.target.checked })} />{' '}{tr('tables.columns.longTextOpensABox')}</label>
  if (type === 'link') return <label className="tb-check-line"><input type="checkbox" className="checkbox" checked={!!cfg.multiple} onChange={(e) => onCfg({ ...cfg, multiple: e.target.checked })} />{' '}{tr('tables.columns.allowSeveralLinks')}</label>
  if (type !== 'number' && type !== 'currency') return null
  return (
    <div className="tb-row-fields">
      <label className="field"><span>{tr('tables.columns.decimals')}</span>
        <select value={cfg.decimals ?? ''} aria-label={tr('tables.columns.decimals')} onChange={(e) => onCfg({ ...cfg, decimals: e.target.value === '' ? undefined : Number(e.target.value) })}>
          <option value="">{tr('tables.columns.asTyped')}</option>
          {[0, 1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      {type === 'number' ? (
        <label className="field"><span>{tr('tables.columns.format')}</span>
          <select value={cfg.format ?? 'plain'} aria-label={tr('tables.columns.format')} onChange={(e) => onCfg({ ...cfg, format: e.target.value as 'plain' })}>
            <option value="plain">{tr('tables.columns.plain')}</option><option value="thousands">{tr('tables.columns.thousandsSeparators')}</option><option value="percent">{tr('tables.columns.percent')}</option>
          </select>
        </label>
      ) : (
        <label className="field"><span>{tr('common.currency')}</span>
          <input value={cfg.currency ?? ''} maxLength={3} placeholder="USD" aria-label={tr('common.currency')} onChange={(e) => onCfg({ ...cfg, currency: e.target.value.toUpperCase() })} />
        </label>
      )}
    </div>
  )
}

/** Add, rename, recolour, reorder, merge and delete options. A deleted option's cells become empty
 * (or take the option it is merged into); the history keeps them. */
export function OptionsEditor({ opts, onChange, merge, onMerge, original }: {
  opts: SelectOption[]; onChange: (o: SelectOption[]) => void; merge?: Record<string, string>; onMerge?: (m: Record<string, string>) => void; original: SelectOption[]
}) {
  const [name, setName] = useState('')
  const add = () => {
    const n = name.trim()
    if (!n || opts.some((o) => o.name.toLowerCase() === n.toLowerCase())) return
    onChange([...opts, { id: '', name: n, color: OPTION_COLORS[opts.length % OPTION_COLORS.length] }])
    setName('')
  }
  const gone = original.filter((o) => !opts.some((x) => x.id === o.id))
  return (
    <div className="tb-options">
      <ul>
        {opts.map((o, i) => (
          <li key={o.id || `new-${i}`}>
            <select className={`tb-swatch tb-opt-${o.color}`} value={o.color} aria-label={tr('tables.columns.colourOf', { itemName: o.name })} onChange={(e) => onChange(opts.map((x, j) => (j === i ? { ...x, color: e.target.value } : x)))}>
              {OPTION_COLORS.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <input value={o.name} aria-label={tr('tables.columns.nameOfOption', { value: i + 1 })} maxLength={100} onChange={(e) => onChange(opts.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
            <button type="button" className="icon-btn sm" aria-label={tr('tables.columns.moveUp', { itemName: o.name })} disabled={i === 0} onClick={() => onChange(swap(opts, i, i - 1))}>↑</button>
            <button type="button" className="icon-btn sm" aria-label={tr('tables.columns.moveDown', { itemName: o.name })} disabled={i === opts.length - 1} onClick={() => onChange(swap(opts, i, i + 1))}>↓</button>
            <button type="button" className="icon-btn sm" aria-label={tr('tables.columns.delete', { itemName: o.name })} onClick={() => onChange(opts.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
          </li>
        ))}
      </ul>
      <div className="tb-option-add">
        <input value={name} placeholder={tr('tables.columns.newOption')} aria-label={tr('tables.columns.newOption')} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            add()
          }
        }} />
        <button type="button" className="btn text sm" onClick={add} disabled={!name.trim()}><Plus size={14} aria-hidden />{' '}{tr('common.add')}</button>
      </div>
      {merge && onMerge && gone.filter((o) => o.id).length > 0 && (
        <div className="tb-merge">
          <p className="muted nt-small">{tr('tables.columns.cellsHoldingADeletedOption')}</p>
          {gone.map((o) => (
            <label key={o.id} className="tb-merge-line"><OptionChip o={o} /> →
              <select value={merge[o.id] ?? ''} aria-label={tr('tables.columns.mergeInto', { itemName: o.name })} onChange={(e) => onMerge({ ...merge, [o.id]: e.target.value })}>
                <option value="">{tr('tables.columns.emptyTheCells')}</option>
                {opts.filter((x) => x.id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

function swap<T>(a: T[], i: number, j: number): T[] {
  const b = [...a]
  ;[b[i], b[j]] = [b[j], b[i]]
  return b
}
