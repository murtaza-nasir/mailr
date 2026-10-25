// The household entries of a table's ⋮ menu: Local models only, a date column on your own calendar, and the members-only
// "Add a row" form. Each appears only where it applies: the calendar choice only for a table with a
// date column, the form only for someone who can add rows.

import { useQueryClient } from '@tanstack/react-query'
import { CalendarDays, FormInput, Link2 } from 'lucide-react'
import { MenuHeader, MenuItem, MenuSep } from '../../components/Popover'
import { tableFormPath } from '../../lib/routes'
import { toast } from '../../stores/ui'
import { LocalOnlyItem, localOnlyLabel } from '../notes/localOnly'
import { patchTable, setTableState } from './actions'
import type { Table } from './types'
import { t as tr } from '../../i18n'

/** The columns a table can show on the calendar: dates, and dates with a time (by their date). */
export function dateColumns(t: Pick<Table, 'columns'>): { id: string; name: string }[] {
  return (t.columns ?? []).filter((c) => c.type === 'date' || c.type === 'datetime').map((c) => ({ id: c.id, name: c.name }))
}

/** The link to the form, as a person pastes it into a chat. */
export function formLink(id: string, origin = typeof window === 'undefined' ? '' : window.location.origin): string {
  return `${origin}${tableFormPath(id)}`
}

export function TableHouseholdItems({ table, online, onDone, onOpenForm }: { table: Table; online: boolean; onDone: () => void; onOpenForm: () => void }) {
  const qc = useQueryClient()
  const dates = dateColumns(table)
  const onCal = table.state.calendar_column ?? null
  const canAdd = table.level !== 'view' && !table.deleted_at
  const setCal = (col: string | null, name?: string) => {
    onDone()
    // ACT table.on_calendar @web.table
    void setTableState(qc, table.id, { calendar_column: col }, col ? tr('tables.householdMenu.datesShowOnYourCalendar', { itemName: name }) : tr('tables.householdMenu.datesOffYourCalendar'))
      .then(() => qc.invalidateQueries({ queryKey: ['tables', 'calendar'] }))
  }
  return (
    <>
      <MenuSep />
      <LocalOnlyItem item={table} noun="table" online={online} onToggle={() => {
        onDone()
        void patchTable(qc, table, { local_only: !table.local_only }, localOnlyLabel(!table.local_only)).catch(() => undefined)
      }} />
      {dates.length > 0 && !table.deleted_at && (
        <>
          <MenuHeader>{tr('tables.householdMenu.onYourCalendar')}</MenuHeader>
          <MenuItem icon={<CalendarDays size={16} />} selected={!onCal} disabled={!online} onSelect={() => setCal(null)}>{tr('tables.householdMenu.notOnTheCalendar')}</MenuItem>
          {dates.map((c) => (
            <MenuItem key={c.id} icon={<CalendarDays size={16} />} selected={onCal === c.id} disabled={!online} tip={tr('tables.householdMenu.allDayChipsOnYour')}
              onSelect={() => setCal(c.id, c.name)}>{dates.length === 1 ? tr('tables.householdMenu.showDates', { itemName: c.name }) : c.name}</MenuItem>
          ))}
        </>
      )}
      {canAdd && (
        <>
          <MenuSep />
          {/* ACT table.form_link @web.table: never public; the people who can edit it sign in first. */}
          <MenuItem icon={<FormInput size={16} />} onSelect={() => {
            onDone()
            onOpenForm()
          }}>{tr('tables.householdMenu.openTheAddARow')}</MenuItem>
          <MenuItem icon={<Link2 size={16} />} tip={tr('tables.householdMenu.forThePeopleWhoShare')} onSelect={() => {
            onDone()
            const link = formLink(table.id)
            void navigator.clipboard?.writeText(link).then(
              () => toast({ message: tr('tables.householdMenu.linkCopiedOnlyThePeople') }),
              () => toast({ message: link, duration: 10_000 }),
            )
          }}>{tr('tables.householdMenu.copyTheAddARow')}</MenuItem>
        </>
      )}
    </>
  )
}
