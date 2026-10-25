// The members-only "Add a row" form of a table: a link,
// /tables/t/<id>/add, that opens only the row form, for a household member to add a row quickly
// (a chore done, a reading taken, a book lent). It is never public: the link opens the app, which asks
// for sign-in first, and the server lets in only the people who can edit the table. Anyone else sees
// why they cannot add a row.

import { ArrowLeft, Check, Table2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { ApiError } from '../../api/client'
import { Spinner } from '../../components/bits'
import { useOnline } from '../../hooks/useOnline'
import { notesPath, tableRoutePath } from '../../lib/routes'
import { useTable } from './api'
import { mergeSpec } from './model'
import { RowForm } from './RowForm'
import './tables.css'
import '../notes/notes.css'
import { useDocumentTitle } from '../../hooks/useDocumentTitle'
import { t as tr } from '../../i18n'

// ACT table.form_link @web.table
export function AddRowPage({ id }: { id: string }) {
  const nav = useNavigate()
  const online = useOnline()
  const t = useTable(id)
  const table = t.data
  const [added, setAdded] = useState(0)
  const [form, setForm] = useState(0)
  useDocumentTitle(table ? tr('tables.addRowPage.addARow', { tableName: table.name }) : tr('tables.addRowPage.addARow2'), tr('common.notes'))
  const frame = (body: React.ReactNode) => (
    <section className="app-frame nt-app tb-app tb-addrow" aria-label={tr('tables.addRowPage.addARow2')} data-testid="add-row-page">{body}</section>
  )
  if (t.isError) {
    const gone = t.error instanceof ApiError && t.error.status === 404
    return frame(
      <div className="nt-empty">
        <span className="nt-empty-icon" aria-hidden><Table2 /></span>
        <h2>{gone ? tr('tables.addRowPage.thisFormIsNotAvailable') : tr('tables.addRowPage.theFormCouldNotBe')}</h2>
        <p className="muted">{gone ? tr('tables.addRowPage.theFormIsForThe') : tr('tables.addRowPage.tryAgainInAMoment')}</p>
        <button type="button" className="btn tonal" onClick={() => nav(notesPath('tables'))}>{tr('common.goToTables')}</button>
      </div>,
    )
  }
  if (!table) return frame(<div className="tb-loading"><Spinner /></div>)
  const view = table.views?.find((v) => v.default) ?? table.views?.[0]
  const canEdit = table.level !== 'view' && !table.deleted_at
  if (!canEdit) {
    return frame(
      <div className="nt-empty" data-testid="add-row-refused">
        <span className="nt-empty-icon" aria-hidden><Table2 /></span>
        <h2>{table.deleted_at ? tr('tables.addRowPage.thisTableIsInTrash') : tr('tables.addRowPage.youCanViewThisTable')}</h2>
        <p className="muted">{table.deleted_at ? tr('tables.addRowPage.itsOwnerCanRestoreIt') : tr('tables.addRowPage.askItsOwnerForEdit')}</p>
        <button type="button" className="btn tonal" onClick={() => nav(tableRoutePath(table.id))}>{tr('tables.addRowPage.openTheTable')}</button>
      </div>,
    )
  }
  return frame(
    <div className="tb-addrow-col">
      <header className="tb-addrow-head">
        <button type="button" className="icon-btn" aria-label={tr('tables.addRowPage.openTheTable')} data-tip={tr('tables.addRowPage.openTheTable')} onClick={() => nav(tableRoutePath(table.id))}><ArrowLeft /></button>
        <div>
          <h1>{table.name || tr('common.untitledTable')}</h1>
          {/* The subtitle says what happened, so nothing moves when a row is added. */}
          <p className="muted nt-small" role="status" data-testid={added ? 'add-row-done' : undefined}>
            {added ? <><Check size={14} aria-hidden /> {tr('tables.addRowPage.rowsAddedAddAnotherBelow', { count: added })}</>
              : tr('tables.addRowPage.addARowOnlyThe')}
          </p>
        </div>
      </header>
      {!online && <div className="nt-banner" role="status">{tr('tables.addRowPage.youAreOfflineRowsAre')}</div>}
      <div className="tb-addrow-card">
        <RowForm key={form} table={table} columns={table.columns ?? []} spec={mergeSpec(view?.spec, null)} rowId={null} canEdit={canEdit && online}
          onClose={() => nav(tableRoutePath(table.id))}
          onCreated={() => {
            setAdded((n) => n + 1)
            setForm((n) => n + 1)
          }} />
      </div>
    </div>,
  )
}
