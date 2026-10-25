// ACT-SURFACES: web.table
// The table routes (lib/routes.ts), where a table citation opens (features/rail/assistant/citations.ts) and
// the open table in the assistant's screen context (assistant-context/appContext.ts). ACT table.search
import { describe, expect, it } from 'vitest'
import { appContext } from '../assistant-context/appContext'
import { appFromPath, parseTablePath, tableFormPath, tableRoutePath } from '../../lib/routes'
import { scopeForPath } from '../../lib/shortcuts'
import { citationRoute } from '../rail/assistant/citations'
import { useTableUI } from './store'

describe('table routes', () => {
  it('parses and builds the three forms', () => {
    expect(parseTablePath('/tables/t/tabc123')).toEqual({ id: 'tabc123', view: null, row: null })
    expect(parseTablePath('/tables/t/tabc123/v/v9/r/0192-ab')).toEqual({ id: 'tabc123', view: 'v9', row: '0192-ab' })
    expect(parseTablePath('/notes/tables')).toBeNull()
    // The members-only "Add a row" form.
    expect(parseTablePath('/tables/t/tabc123/add')).toEqual({ id: 'tabc123', view: null, row: null, form: true })
    expect(tableFormPath('tabc123')).toBe('/tables/t/tabc123/add')
    expect(tableRoutePath('tabc123', 'v9', 'r1')).toBe('/tables/t/tabc123/v/v9/r/r1')
    expect(tableRoutePath('tabc123')).toBe('/tables/t/tabc123')
  })
  it('belongs to the Notes app and its keys', () => {
    expect(appFromPath('/tables/t/tabc123')).toBe('notes')
    expect(scopeForPath('/tables/t/tabc123')).toBe('notes')
  })
  it('opens a cited table or row', () => {
    const base = { conversation_id: null, message_id: null, quote: null, verified: true }
    expect(citationRoute({ ...base, kind: 'table', table: 'tabc', row: null })).toBe('/tables/t/tabc')
    expect(citationRoute({ ...base, kind: 'table', table: 'tabc', row: 'r1' })).toBe('/tables/t/tabc/r/r1')
  })
  it('tells the assistant what the open table shows', () => {
    useTableUI.getState().set({ id: 'tabc', view: 'v1', filters: [{ col: 'c1', op: 'is', value: 'o1' }], sort: [], focused_row: 'r2', focused_column: 'c3', selected_rows: ['r2', 'r3'] })
    expect(appContext('/tables/t/tabc', '')).toEqual({ table: {
      id: 'tabc', view: 'v1', filters: [{ col: 'c1', op: 'is', value: 'o1' }], sort: [], focused_row: 'r2', focused_column: 'c3', selected_rows: ['r2', 'r3'],
    } })
    expect(appContext('/tables/t/tabc/r/r9', '').table?.focused_row).toBe('r9')
    expect(appContext('/tables/t/tother', '').table?.selected_rows).toEqual([])
  })
})
