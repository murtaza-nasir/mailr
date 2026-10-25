// A table's dates on the calendar (tables/calendar.py). The rows
// of the tables whose date column the viewer put on their calendar show on their day as all-day chips
// with the row's title. They ride with the date-only tasks of the all-day strip and the month cells
// (TaskChip hands them to TableDateChip), are read-only here, and a tap opens the row in its table.

import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Table2 } from 'lucide-react'
import type { CSSProperties } from 'react'
import { useNavigate } from 'react-router'
import { request } from '../../api/client'
import { tableRoutePath } from '../../lib/routes'
import type { ApiTask } from './types'
import { t as tr } from '../../i18n'

export interface TableDate {
  table: string
  table_name: string
  color: string | null
  row: string
  title: string
  date: string
  column_name: string
}

/** The server's longest range (tables/calendar.py MAX_DAYS). */
const MAX_DAYS = 62

function days(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000)
}

/** The dated rows from `first` to `last` (ISO days), as chips the calendar lays out with date-only tasks. */
export function useTableDates(first: string, last: string, enabled = true) {
  return useQuery<ApiTask[]>({
    queryKey: ['tables', 'calendar', first, last],
    queryFn: async ({ signal }) => (await request<{ items: TableDate[] }>('/tables/calendar', { query: { start: first, end: last }, signal })).items.map(asChip),
    enabled: enabled && days(first, last) <= MAX_DAYS,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: false,
  })
}

/** One dated row as a calendar item: a date-only "due", and the row it opens. */
export function asChip(d: TableDate): ApiTask {
  return {
    uid: `table:${d.table}:${d.row}`, summary: d.title, due: d.date, due_all_day: true, status: 'table',
    table_row: { table: d.table, row: d.row, table_name: d.table_name, column: d.column_name, color: d.color },
  }
}

// ACT table.on_calendar @web.calendar
export function TableDateChip({ t, style }: { t: ApiTask; style?: CSSProperties }) {
  const nav = useNavigate()
  const r = t.table_row!
  return (
    <div className={`cal-task cal-tabledate${r.color ? ` nt-c-${r.color}` : ''}`} style={style} data-testid="cal-table-date">
      <Table2 className="cal-tabledate-icon" aria-hidden />
      <button type="button" className="cal-task-title" dir="auto" data-tip={`${r.table_name} · ${r.column}`} aria-label={`${t.summary}, ${r.table_name}`}
        onClick={() => nav(tableRoutePath(r.table, null, r.row))}>
        {t.summary || tr('calendar.tableDates.untitledRow')}
      </button>
    </div>
  )
}
