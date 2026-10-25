// A table on a phone: a card per row with the primary value in bold
// and the view's phone columns under it, view chips at the top, the active filters as chips (×
// clears one), the footers in a bar at the bottom, and + for a new row. A tap opens the row form.

import { Plus, X } from 'lucide-react'
import { cellText, filterText, footerText } from './model'
import type { Column, RowsAnswer, Table, TableView, ViewSpec } from './types'
import { t as tr } from '../../i18n'

export function PhoneList({ table, columns, data, spec, views, view, canEdit, onView, onSpec, onOpenRow, onNew }: {
  table: Table; columns: Column[]; data: RowsAnswer; spec: ViewSpec; views: TableView[]; view: TableView | undefined; canEdit: boolean
  onView: (id: string) => void; onSpec: (p: Partial<ViewSpec>) => void; onOpenRow: (id: string) => void; onNew: () => void
}) {
  const byId = new Map(columns.map((c) => [c.id, c]))
  const primary = byId.get(table.primary_column ?? '') ?? columns[0]
  const under = (spec.phone_columns.length ? spec.phone_columns : columns.filter((c) => c.id !== primary?.id).slice(0, 1).map((c) => c.id))
    .map((id) => byId.get(id)).filter((c): c is Column => !!c)
  const footers = Object.entries(data.footers ?? {}).map(([k, f]) => (byId.get(k) ? `${byId.get(k)!.name}: ${footerText(byId.get(k)!, f)}` : '')).filter(Boolean)
  return (
    <div className="tb-phone" data-testid="table-phone">
      {views.length > 1 && (
        <div className="tb-phone-chips" role="tablist" aria-label={tr('common.views')}>
          {views.map((v) => <button key={v.id} type="button" role="tab" aria-selected={v.id === view?.id} className={`tb-chip-btn${v.id === view?.id ? ' on' : ''}`} onClick={() => onView(v.id)}>{v.name}</button>)}
        </div>
      )}
      {spec.filters.length > 0 && (
        <div className="tb-phone-chips" aria-label={tr('tables.phoneList.activeFilters')}>
          {spec.filters.map((f, i) => (
            <span key={i} className="tb-fchip">{filterText(byId.get(f.col), f, data.refs)}
              <button type="button" aria-label={tr('tables.phoneList.clearTheFilter')} onClick={() => onSpec({ filters: spec.filters.filter((_, j) => j !== i) })}><X size={12} /></button>
            </span>
          ))}
        </div>
      )}
      <ul className="tb-phone-list">
        {data.rows.map((r) => (
          <li key={r.id}>
            <button type="button" className="tb-phone-row" onClick={() => onOpenRow(r.id)}>
              <strong dir="auto">{primary ? (cellText(primary, r.cells[primary.id], data.refs) || r.raw[primary.id] || tr('tables.phoneList.untitledRow')) : tr('tables.phoneList.row')}</strong>
              {under.map((c) => {
                const t = cellText(c, r.cells[c.id], data.refs) || r.raw[c.id]
                return t ? <span key={c.id} className="muted"><span className="tb-phone-label"><bdi>{c.name}</bdi></span> <bdi>{t}</bdi></span> : null
              })}
            </button>
          </li>
        ))}
        {!data.rows.length && <li className="muted tb-phone-empty">{data.total ? tr('tables.phoneList.noRowsMatchTheseFilters') : tr('tables.phoneList.noRowsYet')}</li>}
      </ul>
      {footers.length > 0 && <div className="tb-phone-foot" role="status">{footers.join(' · ')}</div>}
      {canEdit && <button type="button" className="tb-fab" aria-label={tr('common.newRow')} onClick={onNew}><Plus /></button>}
    </div>
  )
}
