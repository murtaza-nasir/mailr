// ACT-SURFACES: web.table
// Pure table logic (features/tables/model.ts): cell display, plain text for copy, TSV, focus and
// range moves, paste and fill-down plans (ACT row.bulk_edit), filter chips and footers
// (ACT table.view_edit, ACT table.stats).
import { describe, expect, it } from 'vitest'
import {
  anchorAt, cellText, fillDownPlan, filterText, footerText, formatNumber, inRange, mergeSpec, moveFocus, parseTSV, pastePlan, plainText,
  rawTip, reanchor, resolveFocus, roundDecimal, shiftDecimal, specDiffers, toTSV, visibleColumns,
} from './model'
import type { Column, Row } from './types'

const col = (type: Column['type'], config: Column['config'] = {}, id = 'c1', name = 'Weight'): Column =>
  ({ id, name, type, config, required: false, width: null, order: 'i' })
const sel = col('select', { options: [{ id: 'o1', name: 'Squat', color: 'red' }, { id: 'o2', name: 'Bench', color: 'blue' }] }, 'c2', 'Exercise')

describe('numbers', () => {
  it('rounds exactly and keeps decimals', () => {
    expect(roundDecimal('82.45', 1)).toBe('82.5')
    expect(roundDecimal('82.44', 1)).toBe('82.4')
    expect(roundDecimal('9.995', 2)).toBe('10.00')
    expect(roundDecimal('-0.04', 1)).toBe('0.0')
    expect(roundDecimal('3', 2)).toBe('3.00')
  })
  it('shifts for percent', () => {
    expect(shiftDecimal('0.125', 2)).toBe('12.5')
    expect(shiftDecimal('1', 2)).toBe('100')
  })
  it('formats per column', () => {
    expect(formatNumber('1250.5', { format: 'thousands' })).toBe('1,250.5')
    expect(formatNumber('0.125', { format: 'percent', decimals: 1 })).toBe('12.5%')
    expect(formatNumber('1212', { currency: 'USD', decimals: 2 }, 'currency')).toBe('$1,212.00')
    expect(formatNumber('-5', { currency: 'XYZ' }, 'currency')).toBe('XYZ -5')
  })
})

describe('cells', () => {
  it('shows each type', () => {
    expect(cellText(col('number', { decimals: 1 }), '82')).toBe('82.0')
    expect(cellText(col('date'), '2025-10-03')).toBe('3 Oct 2025')
    expect(cellText(col('checkbox'), true)).toBe('✓')
    expect(cellText(sel, 'o2')).toBe('Bench')
    expect(cellText({ ...sel, type: 'multi_select' }, ['o1', 'o2'])).toBe('Squat, Bench')
    expect(cellText(col('link', { target: 'note' }), [{ kind: 'note', id: 'n1' }], { 'note:n1': { kind: 'note', state: 'hidden', title: 'Not shared with you' } }))
      .toBe('Not shared with you')
    expect(cellText(col('text'), null)).toBe('')
  })
  it('copies plain text', () => {
    expect(plainText({ ...sel, type: 'multi_select' }, ['o1', 'o2'])).toBe('Squat; Bench')
    expect(plainText(col('datetime'), { at: '2026-10-05T14:30', tz: 'UTC' })).toBe('2026-10-05 14:30')
    expect(plainText(col('number'), null, 'about 80')).toBe('about 80')
    expect(rawTip(col('number'), 'about 80')).toBe("Not a number: 'about 80'")
  })
})

describe('TSV', () => {
  it('round-trips quotes, tabs and newlines', () => {
    const m = [['a', 'b\tc'], ['line\nbreak', 'say "hi"'], ['', 'x']]
    expect(parseTSV(toTSV(m))).toEqual(m)
  })
  it('reads spreadsheet text with CRLF', () => {
    expect(parseTSV('1\t2\r\n3\t4\r\n')).toEqual([['1', '2'], ['3', '4']])
  })
})

describe('focus and ranges', () => {
  it('moves and wraps Tab', () => {
    expect(moveFocus({ r: 0, c: 2 }, 'Tab', 3, 3)).toEqual({ r: 1, c: 0 })
    expect(moveFocus({ r: 1, c: 0 }, 'ShiftTab', 3, 3)).toEqual({ r: 0, c: 2 })
    expect(moveFocus({ r: 2, c: 0 }, 'ArrowDown', 3, 3)).toEqual({ r: 2, c: 0 })
    expect(moveFocus({ r: 0, c: 0 }, 'Enter', 3, 3)).toEqual({ r: 1, c: 0 })
  })
  it('checks a range either way', () => {
    expect(inRange({ a: { r: 3, c: 2 }, b: { r: 1, c: 0 } }, { r: 2, c: 1 })).toBe(true)
    expect(inRange({ a: { r: 3, c: 2 }, b: { r: 1, c: 0 } }, { r: 0, c: 1 })).toBe(false)
  })
  it('keeps the focus on its row when the rows move, and takes the place when the row goes', () => {
    const cols = ['c0', 'c1']
    // The focus on Mon (row 0); Alt+Down moves Mon below Thu. Until the refetch lands the grid
    // still draws the old order, and the focus stays on Mon there too (a Shift+Enter in that
    // window added the row below Thu: tables-app e2e, integ run of 10 Oct 2026).
    const a = anchorAt({ r: 0, c: 0 }, ['mon', 'thu', 'fri'], cols)
    expect(resolveFocus(a, ['mon', 'thu', 'fri'], cols)).toEqual({ r: 0, c: 0 })
    expect(resolveFocus(a, ['thu', 'mon', 'fri'], cols)).toEqual({ r: 1, c: 0 })
    // A refetch puts a row above it: the focus goes down with it.
    expect(resolveFocus(a, ['new', 'mon', 'thu', 'fri'], cols)).toEqual({ r: 1, c: 0 })
    // A column moves: the focus stays on its column.
    expect(resolveFocus(anchorAt({ r: 1, c: 1 }, ['mon', 'thu'], cols), ['mon', 'thu'], ['c1', 'c0'])).toEqual({ r: 1, c: 0 })
    // The row is deleted: the row now at its last place takes the focus, inside the grid.
    expect(resolveFocus({ row: 'thu', col: 'c0', r: 1, c: 0 }, ['mon', 'fri'], cols)).toEqual({ r: 1, c: 0 })
    expect(resolveFocus({ row: 'fri', col: 'c0', r: 2, c: 0 }, ['mon', 'thu'], cols)).toEqual({ r: 1, c: 0 })
    expect(reanchor({ row: 'fri', col: 'c0', r: 2, c: 0 }, ['mon', 'thu'], cols)).toEqual({ row: 'thu', col: 'c0', r: 1, c: 0 })
    // A moved row keeps its anchor; only the place is brought up to date.
    expect(reanchor(a, ['thu', 'mon'], cols)).toEqual({ row: 'mon', col: 'c0', r: 1, c: 0 })
    // Nothing changed: the same object, so the grid does not draw again.
    expect(reanchor(a, ['mon', 'thu'], cols)).toBe(a)
    // No rows drawn yet (a view loading): the anchor waits.
    expect(reanchor(a, [], cols)).toBe(a)
  })
  it('plans a paste past the last row', () => {
    const p = pastePlan({ r: 1, c: 1 }, [['a', 'b'], ['c', 'd'], ['e', 'f']], ['r0', 'r1', 'r2'], ['c0', 'c1', 'c2'])
    expect(p.added).toBe(1)
    expect(p.rows).toEqual([{ id: 'r1', cells: { c1: 'a', c2: 'b' } }, { id: 'r2', cells: { c1: 'c', c2: 'd' } }, { cells: { c1: 'e', c2: 'f' } }])
  })
  it('plans a fill down', () => {
    const rows = [{ id: 'a', cells: { c0: '1' } }, { id: 'b', cells: {} }, { id: 'c', cells: { c0: '3' } }] as unknown as Row[]
    expect(fillDownPlan({ a: { r: 0, c: 0 }, b: { r: 2, c: 0 } }, rows, ['c0'])).toEqual([{ id: 'b', cells: { c0: '1' } }, { id: 'c', cells: { c0: '1' } }])
  })
})

describe('views', () => {
  it('writes filter chips', () => {
    expect(filterText(sel, { col: 'c2', op: 'is', value: 'o1' })).toBe('Exercise is Squat')
    expect(filterText(col('number'), { col: 'c1', op: 'gte', value: '80' })).toBe('Weight ≥ 80')
    expect(filterText(col('date', {}, 'c3', 'Date'), { col: 'c3', op: 'last_days', value: 7 })).toBe('Date in the last 7 days')
    expect(filterText(col('checkbox', {}, 'c4', 'Done'), { col: 'c4', op: 'checked' })).toBe('Done is checked')
  })
  it('writes footers', () => {
    expect(footerText(col('number'), { agg: 'sum', value: '315' })).toBe('Sum 315')
    expect(footerText(sel, { agg: 'per_option', value: { top: [{ name: 'Squat', count: 3 }], more: 1 } })).toBe('Squat 3 +1')
    expect(footerText(col('checkbox'), { agg: 'percent_checked', value: 40 })).toBe('40% checked')
  })
  it('tells unsaved changes and keeps the primary column first', () => {
    const saved = mergeSpec(undefined, null)
    expect(specDiffers(saved, { sort: [] })).toBe(false)
    expect(specDiffers(saved, { sort: [{ col: 'c1', dir: 'asc' }] })).toBe(true)
    const cols = [col('number'), sel, col('text', {}, 'c9', 'Notes')]
    expect(visibleColumns(cols, { ...saved, hidden: ['c9', 'c1'] }, 'c9').map((c) => c.id)).toEqual(['c9', 'c2'])
  })
})

describe('dateHint', () => {
  it("takes its example dates from this device's clock (the docs shot showed 12 Oct on Fri 2 Oct)", async () => {
    const { dateHint } = await import('./model')
    // Three examples on Fri 2 Oct: today, tomorrow (sat) and a week ahead (9 oct). The "or" keeps
    // "sat, 9 oct" from reading as one date (the docs shot read as "today is Sat 9 Oct").
    expect(dateHint(new Date(2026, 9, 2, 10, 40))).toBe('today, sat or 9 oct')
  })
})
