// The view bar: view tabs with the default first, "+ View", Sort,
// Filter, Group, Columns, Footer, the search in the table (`/`), and "Unsaved changes · Save ·
// Reset" when the view differs from its saved form. A viewer at view level changes sort and
// filters for themselves only; saving needs edit.

import { useQueryClient } from '@tanstack/react-query'
import { ArrowDownUp, Calculator, Columns3, Filter as FilterIcon, Group, Plus, Search, Trash2, X } from 'lucide-react'
import { forwardRef, useState } from 'react'
import { Dialog } from '../../components/Dialog'
import { MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { deleteView, editView, saveView } from './actions'
import { OptionChip } from './cells'
import { COMMON_FOOTERS, FOOTERS, NO_VALUE_OPS, OPS, filterText, optionsOf, dateHint } from './model'
import type { Column, Filter, RefInfo, Table, TableView, ViewSpec } from './types'
import { t } from '../../i18n'

export interface ViewBarProps {
  table: Table
  views: TableView[]
  view: TableView | undefined
  spec: ViewSpec
  dirty: boolean
  canEdit: boolean
  columns: Column[]
  refs: Record<string, RefInfo>
  q: string
  onQ: (q: string) => void
  onView: (id: string) => void
  onSpec: (p: Partial<ViewSpec>) => void
  onReset: () => void
  filterFor: Column | null
  onFilterFor: (c: Column | null) => void
}

export const ViewBar = forwardRef<HTMLInputElement, ViewBarProps>(function ViewBar(
  { table, views, view, spec, dirty, canEdit, columns, refs, q, onQ, onView, onSpec, onReset, filterFor, onFilterFor }, searchRef,
) {
  const qc = useQueryClient()
  const sortA = useAnchor<HTMLButtonElement>()
  const filterA = useAnchor<HTMLButtonElement>()
  const groupA = useAnchor<HTMLButtonElement>()
  const colsA = useAnchor<HTMLButtonElement>()
  const footA = useAnchor<HTMLButtonElement>()
  const tabA = useAnchor<HTMLButtonElement>()
  const [tabMenu, setTabMenu] = useState<TableView | null>(null)
  const [naming, setNaming] = useState<{ mode: 'new' | 'rename'; view?: TableView } | null>(null)
  const byId = new Map(columns.map((c) => [c.id, c]))
  const filterOpen = filterA.open || !!filterFor
  const filterAnchor = filterA.anchor ?? (filterFor ? document.querySelector<HTMLElement>('[data-testid="view-filter"]') : null)

  return (
    <div className="tb-viewbar" role="toolbar" aria-label={t('common.view')}>
      <div className="tb-tabs" role="tablist" aria-label={t('common.views')}>
        {views.map((v) => (
          <button key={v.id} type="button" role="tab" aria-selected={v.id === view?.id} className={`tb-tab${v.id === view?.id ? ' on' : ''}`}
            onClick={() => onView(v.id)}
            onContextMenu={(e) => {
              e.preventDefault()
              if (!canEdit) return
              setTabMenu(v)
              tabA.toggle(e.currentTarget as HTMLButtonElement)
            }}>
            {v.name}
          </button>
        ))}
        {canEdit && <button type="button" className="tb-tab add" aria-label={t('tables.viewBar.newView')} onClick={() => setNaming({ mode: 'new' })}><Plus size={14} aria-hidden />{' '}{t('common.view')}</button>}
      </div>
      <span className="spacer" />
      {dirty && (
        <span className="tb-unsaved" role="status">
          {t('tables.viewBar.unsavedChanges')}
          {canEdit && view && <button type="button" className="btn text sm" onClick={() => {
            // ACT table.view_edit @web.table
            void editView(qc, table.id, view.id, { spec }).then(onReset)
          }}>{t('common.save')}</button>}
          {canEdit && <button type="button" className="btn text sm" onClick={() => setNaming({ mode: 'new' })}>{t('tables.viewBar.saveAsView')}</button>}
          <button type="button" className="btn text sm" onClick={onReset}>{t('tables.viewBar.reset')}</button>
        </span>
      )}
      <button type="button" className={`btn text sm${spec.sort.length ? ' on' : ''}`} onClick={(e) => sortA.toggle(e.currentTarget)}><ArrowDownUp size={16} aria-hidden />{' '}{t('tables.viewBar.sort', { value: spec.sort.length ? ` (${spec.sort.length})` : '' })}</button>
      <button type="button" data-testid="view-filter" className={`btn text sm${spec.filters.length ? ' on' : ''}`} onClick={(e) => filterA.toggle(e.currentTarget)}><FilterIcon size={16} aria-hidden />{' '}{t('tables.viewBar.filter', { value: spec.filters.length ? ` (${spec.filters.length})` : '' })}</button>
      <button type="button" className={`btn text sm${spec.group_by ? ' on' : ''}`} onClick={(e) => groupA.toggle(e.currentTarget)}><Group size={16} aria-hidden />{' '}{t('common.group')}</button>
      <button type="button" className={`btn text sm${spec.hidden.length ? ' on' : ''}`} onClick={(e) => colsA.toggle(e.currentTarget)}><Columns3 size={16} aria-hidden />{' '}{t('tables.viewBar.columns')}</button>
      <button type="button" className="btn text sm" onClick={(e) => footA.toggle(e.currentTarget)}><Calculator size={16} aria-hidden />{' '}{t('tables.viewBar.footer')}</button>
      <label className="nt-search tb-search">
        <Search size={16} aria-hidden />
        <input ref={searchRef} type="search" placeholder={t('tables.viewBar.searchInTable')} aria-label={t('tables.viewBar.searchInTable')} value={q} onChange={(e) => onQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              if (q) onQ('')
              else (e.target as HTMLInputElement).blur()
            }
          }} />
      </label>
      {spec.filters.length > 0 && (
        <div className="tb-filter-chips" aria-label={t('tables.viewBar.activeFilters')}>
          {spec.filters.map((f, i) => (
            <span key={i} className="tb-fchip">{filterText(byId.get(f.col), f, refs)}
              <button type="button" aria-label={t('tables.viewBar.clearTheFilter', { filterText: filterText(byId.get(f.col), f, refs) })} onClick={() => onSpec({ filters: spec.filters.filter((_, j) => j !== i) })}><X size={12} /></button>
            </span>
          ))}
        </div>
      )}

      <Popover anchor={sortA.anchor} open={sortA.open} onClose={sortA.close} role="dialog" label={t('common.sort')} placement="bottom-end" minWidth={300}>
        <div className="tb-pop">
          {spec.sort.map((s, i) => (
            <div key={i} className="tb-pop-line">
              <select value={s.col} aria-label={t('tables.viewBar.sortKey', { value: i + 1 })} onChange={(e) => onSpec({ sort: spec.sort.map((x, j) => (j === i ? { ...x, col: e.target.value } : x)) })}>
                {columns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <select value={s.dir} aria-label={t('tables.viewBar.direction')} onChange={(e) => onSpec({ sort: spec.sort.map((x, j) => (j === i ? { ...x, dir: e.target.value as 'asc' } : x)) })}>
                <option value="asc">{t('tables.viewBar.ascending')}</option><option value="desc">{t('tables.viewBar.descending')}</option>
              </select>
              <button type="button" className="icon-btn sm" aria-label={t('tables.viewBar.removeTheSortKey')} onClick={() => onSpec({ sort: spec.sort.filter((_, j) => j !== i) })}><Trash2 size={14} /></button>
            </div>
          ))}
          {!spec.sort.length && <p className="muted nt-small">{t('tables.viewBar.manualOrderRowsMoveBy')}</p>}
          {spec.sort.length < 3 && columns.length > 0 && <button type="button" className="btn text sm" onClick={() => onSpec({ sort: [...spec.sort, { col: columns.find((c) => !spec.sort.some((s) => s.col === c.id))?.id ?? columns[0].id, dir: 'asc' }] })}><Plus size={14} aria-hidden />{' '}{t('tables.viewBar.addASortKey')}</button>}
          {spec.sort.length > 0 && <button type="button" className="btn text sm" onClick={() => onSpec({ sort: [] })}>{t('common.manualOrder')}</button>}
        </div>
      </Popover>

      <Popover anchor={filterAnchor} open={filterOpen && !!filterAnchor} onClose={() => {
        filterA.close()
        onFilterFor(null)
      }} role="dialog" label={t('common.filter')} placement="bottom-end" minWidth={420}>
        <FilterEditor columns={columns} filters={spec.filters} refs={refs} start={filterFor} onChange={(filters) => onSpec({ filters })} />
      </Popover>

      <Popover anchor={groupA.anchor} open={groupA.open} onClose={groupA.close} label={t('common.group')} placement="bottom-end">
        <MenuItem selected={!spec.group_by} onSelect={() => {
          groupA.close()
          onSpec({ group_by: null })
        }}>{t('tables.viewBar.noGrouping')}</MenuItem>
        <MenuSep />
        {columns.filter((c) => ['select', 'multi_select', 'checkbox', 'date', 'datetime'].includes(c.type) || (c.type === 'link' && !c.config.multiple)).flatMap((c) =>
          c.type === 'date' || c.type === 'datetime'
            ? (['day', 'week', 'month', 'year'] as const).map((by) => <MenuItem key={`${c.id}:${by}`} selected={spec.group_by?.col === c.id && spec.group_by?.by === by} onSelect={() => {
              groupA.close()
              onSpec({ group_by: { col: c.id, by } })
            }}>{t('tables.viewBar.by', { itemName: c.name, by })}</MenuItem>)
            : [<MenuItem key={c.id} selected={spec.group_by?.col === c.id} onSelect={() => {
              groupA.close()
              onSpec({ group_by: { col: c.id } })
            }}>{c.name}</MenuItem>])}
      </Popover>

      <Popover anchor={colsA.anchor} open={colsA.open} onClose={colsA.close} role="dialog" label={t('tables.viewBar.columns')} placement="bottom-end" minWidth={260}>
        <div className="tb-pop">
          {columns.map((c) => (
            <label key={c.id} className="tb-check-line">
              <input type="checkbox" className="checkbox" checked={!spec.hidden.includes(c.id)} disabled={c.id === table.primary_column}
                onChange={(e) => onSpec({ hidden: e.target.checked ? spec.hidden.filter((h) => h !== c.id) : [...spec.hidden, c.id] })} />
              {c.name}{c.id === table.primary_column && <span className="muted nt-small">{' '}{t('tables.viewBar.namesTheRows')}</span>}
            </label>
          ))}
          <p className="muted nt-small">{t('tables.viewBar.phoneListOneOrTwo')}</p>
          {columns.filter((c) => c.id !== table.primary_column).map((c) => (
            <label key={`p:${c.id}`} className="tb-check-line">
              <input type="checkbox" className="checkbox" checked={spec.phone_columns.includes(c.id)} disabled={!spec.phone_columns.includes(c.id) && spec.phone_columns.length >= 2}
                onChange={(e) => onSpec({ phone_columns: e.target.checked ? [...spec.phone_columns, c.id] : spec.phone_columns.filter((x) => x !== c.id) })} />
              {t('tables.viewBar.showOnAPhone', { itemName: c.name })}
            </label>
          ))}
        </div>
      </Popover>

      <Popover anchor={footA.anchor} open={footA.open} onClose={footA.close} role="dialog" label={t('tables.viewBar.footer')} placement="bottom-end" minWidth={300}>
        <div className="tb-pop">
          {columns.map((c) => (
            <label key={c.id} className="field tb-pop-line">
              <span>{c.name}</span>
              <select value={spec.footers[c.id] ?? ''} aria-label={t('tables.viewBar.footerOf', { itemName: c.name })} onChange={(e) => {
                const f = { ...spec.footers }
                if (e.target.value) f[c.id] = e.target.value
                else delete f[c.id]
                onSpec({ footers: f })
              }}>
                <option value="">{t('common.none')}</option>
                {[...(FOOTERS[c.type] ?? []), ...COMMON_FOOTERS].map(([a, l]) => <option key={a} value={a}>{l}</option>)}
              </select>
            </label>
          ))}
        </div>
      </Popover>

      <Popover anchor={tabA.anchor} open={tabA.open && !!tabMenu} onClose={tabA.close} label={t('common.view')} placement="bottom-start">
        {tabMenu && <>
          <MenuItem onSelect={() => {
            tabA.close()
            setNaming({ mode: 'rename', view: tabMenu })
          }}>{t('common.rename')}</MenuItem>
          {!tabMenu.default && <MenuItem onSelect={() => {
            tabA.close()
            void editView(qc, table.id, tabMenu.id, { default: true })
          }}>{t('tables.viewBar.makeDefault')}</MenuItem>}
          {views.length > 1 && <MenuItem danger onSelect={() => {
            tabA.close()
            // ACT table.view_delete @web.table
            void deleteView(qc, table.id, tabMenu.id)
          }}>{t('tables.viewBar.deleteView')}</MenuItem>}
        </>}
      </Popover>
      {naming && <ViewNameDialog title={naming.mode === 'new' ? t('tables.viewBar.saveAsANewView') : t('tables.viewBar.renameView')} initial={naming.view?.name ?? ''} onClose={() => setNaming(null)} onSave={(name) => {
        const n = naming
        setNaming(null)
        // ACT table.view_save @web.table
        if (n.mode === 'new') void saveView(qc, table.id, { name, spec }).then((r) => {
          onReset()
          onView(r.view.id)
        })
        else if (n.view) void editView(qc, table.id, n.view.id, { name })
      }} />}
    </div>
  )
})

function ViewNameDialog({ title, initial, onClose, onSave }: { title: string; initial: string; onClose: () => void; onSave: (name: string) => void }) {
  const [v, setV] = useState(initial)
  return (
    <Dialog open onClose={onClose} title={title} footer={<>
      <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
      <button type="button" className="btn filled" disabled={!v.trim()} onClick={() => onSave(v.trim())}>{t('common.save')}</button>
    </>}>
      <label className="field"><span>{t('common.name')}</span><input autoFocus value={v} maxLength={100} aria-label={t('tables.viewBar.viewName')} onChange={(e) => setV(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && v.trim() && onSave(v.trim())} /></label>
    </Dialog>
  )
}

/** The filters: column, operator and value per line; all must match. Date values take the
 * quick-add words ("today", "fri", "12 oct") and stay relative. */
export function FilterEditor({ columns, filters, refs, start, onChange }: { columns: Column[]; filters: Filter[]; refs: Record<string, RefInfo>; start: Column | null; onChange: (f: Filter[]) => void }) {
  const [draft, setDraft] = useState<Filter | null>(start ? { col: start.id, op: OPS[start.type][0][0] } : null)
  const byId = new Map(columns.map((c) => [c.id, c]))
  const lines = draft ? [...filters, draft] : filters
  const set = (i: number, f: Filter) => {
    if (i < filters.length) onChange(filters.map((x, j) => (j === i ? f : x)))
    else if (NO_VALUE_OPS.has(f.op) || (f.value != null && f.value !== '')) {
      onChange([...filters, f])
      setDraft(null)
    } else setDraft(f)
  }
  return (
    <div className="tb-pop">
      {lines.map((f, i) => {
        const c = byId.get(f.col)
        if (!c) return null
        return (
          <div key={i} className="tb-pop-line" data-testid="filter-line">
            <select value={f.col} aria-label={t('tables.viewBar.column')} onChange={(e) => {
              const nc = byId.get(e.target.value)!
              set(i, { col: nc.id, op: OPS[nc.type][0][0] })
            }}>
              {columns.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
            <select value={f.op} aria-label={t('tables.viewBar.operator')} onChange={(e) => set(i, { ...f, op: e.target.value, ...(NO_VALUE_OPS.has(e.target.value) ? { value: undefined } : {}) })}>
              {OPS[c.type].map(([op, words]) => <option key={op} value={op}>{words}</option>)}
            </select>
            {!NO_VALUE_OPS.has(f.op) && <FilterValue col={c} f={f} refs={refs} onValue={(value) => set(i, { ...f, value })} />}
            <button type="button" className="icon-btn sm" aria-label={t('tables.viewBar.removeTheFilter')} onClick={() => {
              if (i < filters.length) onChange(filters.filter((_, j) => j !== i))
              else setDraft(null)
            }}><Trash2 size={14} /></button>
          </div>
        )
      })}
      {!draft && columns.length > 0 && <button type="button" className="btn text sm" onClick={() => setDraft({ col: columns[0].id, op: OPS[columns[0].type][0][0] })}><Plus size={14} aria-hidden />{' '}{t('tables.viewBar.addAFilter')}</button>}
      {filters.length > 0 && <button type="button" className="btn text sm" onClick={() => onChange([])}>{t('tables.viewBar.clearFilters')}</button>}
    </div>
  )
}

function FilterValue({ col, f, refs, onValue }: { col: Column; f: Filter; refs: Record<string, RefInfo>; onValue: (v: unknown) => void }) {
  const [text, setText] = useState(Array.isArray(f.value) ? '' : String(f.value ?? ''))
  if (col.type === 'select' || col.type === 'multi_select') {
    const many = ['any_of', 'none_of', 'has_any', 'has_all', 'has_none'].includes(f.op)
    const cur = Array.isArray(f.value) ? (f.value as string[]) : f.value ? [String(f.value)] : []
    return (
      <span className="tb-chips wrap">
        {optionsOf(col).map((o) => (
          <label key={o.id} className="tb-opt-pick"><input type={many ? 'checkbox' : 'radio'} className="checkbox" name={`fv-${col.id}`} checked={cur.includes(o.id)}
            onChange={() => onValue(many ? (cur.includes(o.id) ? cur.filter((x) => x !== o.id) : [...cur, o.id]) : o.id)} /><OptionChip o={o} /></label>
        ))}
      </span>
    )
  }
  if (col.type === 'link') {
    const ok = Object.entries(refs).filter(([, v]) => v.state === 'ok')
    return (
      <select value={String(f.value ?? '')} aria-label={t('tables.viewBar.value')} onChange={(e) => onValue(e.target.value)}>
        <option value="" disabled>{t('common.choose')}</option>
        {ok.map(([k, v]) => <option key={k} value={k}>{v.title}</option>)}
      </select>
    )
  }
  if (f.op === 'between') {
    const [a, b] = Array.isArray(f.value) ? (f.value as string[]) : ['', '']
    return (
      <span className="tb-between">
        <input value={a ?? ''} aria-label={t('common.from')} onChange={(e) => onValue([e.target.value, b ?? ''])} />
        <span>{t('tables.viewBar.and')}</span>
        <input value={b ?? ''} aria-label={t('common.to')} onChange={(e) => onValue([a ?? '', e.target.value])} />
      </span>
    )
  }
  const hint = col.type === 'date' || col.type === 'datetime' ? (f.op.endsWith('_days') ? '7' : dateHint()) : ''
  return <input value={text} aria-label={t('tables.viewBar.value')} placeholder={hint} onChange={(e) => setText(e.target.value)} onBlur={() => text.trim() && onValue(f.op.endsWith('_days') ? Number(text) || text : text.trim())}
    onKeyDown={(e) => {
      if (e.key === 'Enter' && text.trim()) {
        e.preventDefault()
        onValue(f.op.endsWith('_days') ? Number(text) || text : text.trim())
      }
    }} />
}
