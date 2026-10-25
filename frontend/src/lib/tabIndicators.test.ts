import { describe, expect, it } from 'vitest'
import { detailRoom, fitDetail, pickIndicators, stripRoom, tabIndicators } from './tabIndicators'

const now = new Date(2026, 9, 1, 15, 0) // Thu 1 Oct 2026
const tab = (indicators: Array<'unread' | 'all' | 'due'>) => ({ indicators, badge: 'none' as const })

describe('tabIndicators', () => {
  it('says "Due Oct 4 · 2" for a later deadline, with the full text for the tooltip', () => {
    const [due] = tabIndicators(tab(['due']), { due_count: 2, earliest_due: '2026-10-04', earliest_due_from: 'Wells Fargo' }, now)
    expect(due).toMatchObject({ kind: 'due', text: 'Due Oct 4 · 2', tone: 'warning', title: '2 due · earliest Sun 4 Oct (Wells Fargo)' })
  })
  it('turns red within two days and says "Overdue" once the date has passed', () => {
    expect(tabIndicators(tab(['due']), { due_count: 1, earliest_due: '2026-10-03' }, now)[0]).toMatchObject({ text: 'Due Oct 3', tone: 'urgent' })
    expect(tabIndicators(tab(['due']), { due_count: 3, earliest_due: '2026-09-29' }, now)[0]).toMatchObject({ text: 'Overdue · 3', tone: 'urgent' })
  })
  it('shows "N new" for unread and the plain number for all, with the caps of the list totals', () => {
    const c = { unread: 1000, all: 200_001 }
    expect(tabIndicators(tab(['unread', 'all']), c, now).map((i) => i.text)).toEqual(['999+ new', '200,000+'])
    expect(tabIndicators(tab(['all']), { all: 1234 }, now)[0].text).toBe('1,234')
    expect(tabIndicators(tab([]), c, now)).toEqual([])
    expect(tabIndicators(tab(['unread']), { unread: 0 }, now)).toEqual([])
  })
  it('reads a tab from before indicators by its badge', () => {
    expect(tabIndicators({ badge: 'unread' }, { unread: 3 }, now)[0].text).toBe('3 new')
  })
})

describe('the order and the room', () => {
  const c = { unread: 5, all: 40, due_count: 1, earliest_due: '2026-10-09' }
  const urgent = { ...c, earliest_due: '2026-10-02' }
  const all3 = tab(['unread', 'all', 'due'])
  it('puts an urgent deadline first, then unread, a later deadline, then all', () => {
    expect(tabIndicators(all3, c, now).map((i) => i.kind)).toEqual(['unread', 'due', 'all'])
    expect(tabIndicators(all3, urgent, now).map((i) => i.kind)).toEqual(['due', 'unread', 'all'])
  })
  it('keeps two pills in a full theme, one in a narrow pane, one compact in Desk and on a phone', () => {
    const list = tabIndicators(all3, urgent, now)
    expect(stripRoom('full', false, false)).toEqual({ max: 2, form: 'pill' })
    expect(pickIndicators(list, stripRoom('full', false, false)).map((i) => i.kind)).toEqual(['due', 'unread'])
    expect(pickIndicators(list, stripRoom('full', true, false)).map((i) => i.kind)).toEqual(['due'])
    expect(stripRoom('compact', false, false)).toEqual({ max: 1, form: 'compact' })
    expect(stripRoom('full', false, true)).toEqual({ max: 1, form: 'compact' })
    expect(pickIndicators(tabIndicators(all3, c, now), stripRoom('compact', false, false)).map((i) => i.short)).toEqual(['5'])
  })
})

describe('show more on tabs when there is room (layout.tab_detail)', () => {
  it('lets a tab show every indicator as pills (Desk too), and keeps a phone minimal', () => {
    expect(detailRoom('full', false)).toEqual({ max: 3, form: 'pill' })
    expect(detailRoom('compact', false)).toEqual({ max: 3, form: 'pill' })
    expect(detailRoom('full', true)).toBeNull()
    expect(detailRoom('minimal', false)).toBeNull()
    const list = tabIndicators(tab(['unread', 'all', 'due']), { unread: 3, all: 12, due_count: 1, earliest_due: '2026-10-04' }, now)
    expect(pickIndicators(list, detailRoom('full', false)!).map((i) => i.text)).toEqual(['3 new', 'Due Oct 4', '12'])
  })

  const w = (compact: number, detail: number) => ({ compact, detail })

  it('gives every tab its fuller form on a wide strip', () => {
    expect(fitDetail(1000, [w(150, 260), w(150, 220), w(150, 150)], 0)).toEqual([true, true, true])
  })
  it('keeps the usual form on a strip that has no spare room', () => {
    expect(fitDetail(450, [w(150, 260), w(150, 220), w(150, 150)], 0)).toEqual([false, false, true])
    // Already wider than the strip (it scrolls): nothing grows.
    expect(fitDetail(300, [w(150, 160), w(150, 160), w(150, 150)], 0)).toEqual([false, false, false])
  })
  it('falls back per tab from the left: a smaller later one still fits', () => {
    // Room for 450 + 80 more: tab 0 wants +110 (no), tab 1 +70 (yes), tab 2 +40 (no, 10 left).
    expect(fitDetail(530, [w(150, 260), w(150, 220), w(150, 190)], 0)).toEqual([false, true, false])
  })
  it('counts the gaps between tabs and allows half a pixel of rounding', () => {
    // 100 + 20 + 100 = 220; each fuller form adds 50.
    expect(fitDetail(319.6, [w(100, 150), w(100, 150)], 20)).toEqual([true, true])
    expect(fitDetail(319.4, [w(100, 150), w(100, 150)], 20)).toEqual([true, false])
    expect(fitDetail(269.4, [w(100, 150), w(100, 150)], 20)).toEqual([false, false])
  })
})
