import { describe, expect, it } from 'vitest'
import type { TaskListRef } from '../../lib/routes'
import {
  buildSections, bulkPatch, bulkReport, computeCounts, deriveTags, dueChip, filterForView, icalPriority, inSmartList,
  inverseOf, isEmptyQuery, matchesQuery, midpoint, needsRenumber, moveBack, nestRows, parseTaskQuery, placeOf, priorityLevel, shiftDue,
  type TaskItem, type TaskList,
} from './model'

const zone = 'America/Chicago'
// Thu 1 Oct 2026, 10:00 in Chicago.
const now = new Date('2026-10-01T15:00:00Z')
const prefs = { zone, hour12: true }
const o = { now, zone }
const lists: TaskList[] = [
  { id: 'https://dav/work/', calendar_id: 1, connector_id: 1, name: 'Work' },
  { id: 'https://dav/home/', calendar_id: 2, connector_id: 1, name: 'Home' },
]

let seq = 0
function task(p: Partial<TaskItem>): TaskItem {
  seq++
  return { uid: `t${seq}`, summary: `Task ${seq}`, status: 'needs-action', due: null, list: lists[0].id, created: `2026-09-${String(seq).padStart(2, '0')}T00:00:00+00:00`, ...p }
}

describe('priority', () => {
  it('maps iCalendar values to the four levels and back', () => {
    expect([0, 1, 4, 5, 6, 9].map(priorityLevel)).toEqual([0, 3, 3, 2, 1, 1])
    expect([0, 1, 2, 3].map((l) => icalPriority(l as 0))).toEqual([0, 9, 5, 1])
  })
})

describe('smart lists (client fallback)', () => {
  const overdue = task({ due: '2026-09-29' })
  const today = task({ due: '2026-10-01' })
  const todayTimed = task({ due: '2026-10-01T20:00:00+00:00' })
  const tomorrow = task({ due: '2026-10-02' })
  const started = task({ start: '2026-09-30', start_all_day: true })
  const startedFarDue = task({ start: '2026-09-30', start_all_day: true, due: '2026-10-20' })
  const deferred = task({ start: '2026-10-05', start_all_day: true, due: '2026-10-06' })
  const nodate = task({})
  const high = task({ priority: 1 })
  const fromMail = task({ conversation_id: 'c-1' })
  const done = task({ status: 'completed', completed: true, due: '2026-09-29', completed_at: '2026-10-01T14:00:00+00:00' })
  const all = [overdue, today, todayTimed, tomorrow, started, startedFarDue, deferred, nodate, high, fromMail, done]
  const ids = (list: Parameters<typeof inSmartList>[1], extra = {}) => all.filter((t) => inSmartList(t, list, { ...o, ...extra })).map((t) => t.uid)

  it('Today: due by today, or started with no due or a due within 7 days; never deferred', () => {
    expect(ids('today')).toEqual([overdue.uid, today.uid, todayTimed.uid, started.uid])
  })
  it('Upcoming: due or starting after today', () => {
    expect(ids('upcoming')).toEqual([tomorrow.uid, startedFarDue.uid, deferred.uid])
  })
  it('Scheduled, No date, High, From email, Completed', () => {
    expect(ids('scheduled')).toEqual([overdue.uid, today.uid, todayTimed.uid, tomorrow.uid, startedFarDue.uid, deferred.uid])
    expect(ids('nodate')).toEqual([nodate.uid, high.uid, fromMail.uid])
    expect(ids('high')).toEqual([high.uid])
    expect(ids('email')).toEqual([fromMail.uid])
    expect(ids('completed')).toEqual([done.uid])
  })
  it('All hides deferred tasks unless Show deferred is on', () => {
    expect(ids('all')).not.toContain(deferred.uid)
    expect(ids('all', { showDeferred: true })).toContain(deferred.uid)
  })
  it('counts open tasks per smart list, list and tag; overdue separately', () => {
    const c = computeCounts([...all, task({ categories: ['phone'], list: lists[1].id })], o)
    expect(c.smart.today).toBe(4)
    expect(c.smart.overdue).toBe(1)
    expect(c.smart.all).toBe(10)
    expect(c.lists[lists[1].id]).toBe(1)
    expect(c.tags.phone).toBe(1)
  })
  it('filters a user list and a tag view', () => {
    const home = task({ list: lists[1].id, categories: ['Errand'] })
    expect(filterForView([...all, home], { kind: 'list', id: '2' }, lists, o).map((t) => t.uid)).toEqual([home.uid])
    expect(filterForView([...all, home], { kind: 'tag', id: 'errand' }, lists, o).map((t) => t.uid)).toEqual([home.uid])
  })
  it('derives tags with open counts', () => {
    expect(deriveTags([task({ categories: ['b', 'A'] }), task({ categories: ['a'], status: 'completed' })])).toEqual([
      { name: 'A', count: 1 }, { name: 'b', count: 1 },
    ])
  })
})

describe('sections', () => {
  const g = { ...o, lists, sort: 'auto' as const, group: 'auto' as const, weekStart: 1 as const }
  const ref = (id: string): TaskListRef => ({ kind: 'smart', id: id as 'today' })

  it('Today: Overdue (oldest first), Today (timed by time, then priority), Started', () => {
    const a = task({ due: '2026-09-30' })
    const b = task({ due: '2026-09-28' })
    const c = task({ due: '2026-10-01', priority: 1 })
    const d = task({ due: '2026-10-01T16:00:00+00:00' })
    const e = task({ due: '2026-10-01', priority: 9 })
    const f = task({ start: '2026-09-30', start_all_day: true })
    const s = buildSections([a, b, c, d, e, f], ref('today'), g)
    expect(s.map((x) => x.title)).toEqual(['Overdue', 'Today', 'Started'])
    expect(s[0].tone).toBe('danger')
    expect(s[0].tasks.map((t) => t.uid)).toEqual([b.uid, a.uid])
    expect(s[1].tasks.map((t) => t.uid)).toEqual([d.uid, c.uid, e.uid])
    expect(s[1].prefill?.due).toBe('2026-10-01')
  })
  it('Upcoming: the next seven days (kept when empty), later this month, then months', () => {
    const fri = task({ due: '2026-10-02' })
    const later = task({ due: '2026-10-20' })
    const nov = task({ due: '2026-11-04' })
    const jan = task({ due: '2027-01-04' })
    const deferred = task({ start: '2026-10-03', start_all_day: true, due: '2026-10-30' })
    const s = buildSections([fri, later, nov, jan, deferred], ref('upcoming'), g)
    expect(s.map((x) => x.title)).toEqual(['Tomorrow', 'Sat 3 Oct', 'Sun 4 Oct', 'Mon 5 Oct', 'Tue 6 Oct', 'Wed 7 Oct', 'Thu 8 Oct', 'Later this month', 'November', 'January 2027'])
    expect(s[0].tasks).toEqual([fri])
    expect(s[1].tasks).toEqual([deferred])
    expect(s[1].dropDay).toBe('2026-10-03')
    expect(s[2].tasks).toEqual([])
  })
  it('Completed: Today, Yesterday, This week, Earlier, newest first', () => {
    const t1 = task({ status: 'completed', completed_at: '2026-10-01T14:00:00+00:00' })
    const t2 = task({ status: 'completed', completed_at: '2026-10-01T15:00:00+00:00' })
    const y = task({ status: 'completed', completed_at: '2026-09-30T14:00:00+00:00' })
    const w = task({ status: 'completed', completed_at: '2026-09-28T18:00:00+00:00' })
    const e = task({ status: 'completed', completed_at: '2026-09-01T14:00:00+00:00' })
    const s = buildSections([t1, t2, y, w, e], ref('completed'), g)
    expect(s.map((x) => x.title)).toEqual(['Today', 'Yesterday', 'This week', 'Earlier'])
    expect(s[0].tasks.map((t) => t.uid)).toEqual([t2.uid, t1.uid])
  })
  it('All groups by list; an explicit grouping and sort replace the view\'s own', () => {
    const a = task({ list: lists[1].id, summary: 'b' })
    const b = task({ list: lists[0].id, summary: 'a', priority: 1 })
    expect(buildSections([a, b], ref('all'), g).map((x) => x.title)).toEqual(['Work', 'Home'])
    const byPri = buildSections([a, b], ref('all'), { ...g, group: 'priority' })
    expect(byPri.map((x) => x.title)).toEqual(['High priority', 'No priority'])
    const flat = buildSections([a, b], ref('all'), { ...g, group: 'none', sort: 'title' })
    expect(flat[0].tasks.map((t) => t.summary)).toEqual(['a', 'b'])
  })
  it('a user list keeps the manual order', () => {
    const a = task({ sort_order: 20 })
    const b = task({ sort_order: 10 })
    const c = task({})
    expect(buildSections([a, b, c], { kind: 'list', id: '1' }, g)[0].tasks.map((t) => t.uid)).toEqual([b.uid, a.uid, c.uid])
  })
})

describe('subtasks', () => {
  it('nests one level, flattens deeper trees under the top parent and shows orphans alone', () => {
    const p = task({})
    const c = task({ parent: p.uid })
    const gc = task({ parent: c.uid })
    const orphan = task({ parent: 'gone' })
    const rows = nestRows([p, c, gc, orphan], 'all')
    expect(rows.map((r) => [r.task.uid, r.depth, !!r.nested, !!r.orphan])).toEqual([
      [p.uid, 0, false, false], [c.uid, 1, false, false], [gc.uid, 1, true, false], [orphan.uid, 0, false, true],
    ])
    expect(nestRows([p, c], new Set()).map((r) => r.task.uid)).toEqual([p.uid])
    const done = task({ parent: p.uid, status: 'completed' })
    expect(nestRows([p], new Set([p.uid]), { [p.uid]: [done] }).map((r) => r.task.uid)).toEqual([p.uid, done.uid])
  })
})

describe('due chip', () => {
  const chip = (p: Partial<TaskItem>) => dueChip(task(p), now, prefs)
  it('says how long a task is overdue, in red', () => {
    expect(chip({ due: '2026-09-29' })).toMatchObject({ text: 'Overdue 2d', tone: 'overdue' })
    expect(chip({ due: '2026-10-01T13:00:00+00:00' })).toMatchObject({ text: 'Overdue 2h', tone: 'overdue' })
  })
  it('marks today, tomorrow and the day after as urgent; later days are plain', () => {
    expect(chip({ due: '2026-10-01T20:00:00+00:00' })).toMatchObject({ text: 'Today 3 PM', tone: 'urgent' })
    expect(chip({ due: '2026-10-01' })).toMatchObject({ text: 'Today', tone: 'urgent' })
    expect(chip({ due: '2026-10-02' })).toMatchObject({ text: 'Tomorrow', tone: 'urgent' })
    expect(chip({ due: '2026-10-03' })).toMatchObject({ text: 'Sat', tone: 'urgent' })
    expect(chip({ due: '2026-10-04' })).toMatchObject({ text: 'Sun', tone: 'normal' })
    expect(chip({ due: '2026-10-12' })).toMatchObject({ text: '12 Oct', tone: 'normal' })
    expect(chip({ due: '2027-01-12' })).toMatchObject({ text: '12 Jan 2027' })
  })
  it('shows "Starts Thu" (muted) for a deferred task, and nothing without dates', () => {
    expect(chip({ start: '2026-10-08', start_all_day: true, due: '2026-10-09' })).toMatchObject({ text: 'Starts 8 Oct', tone: 'deferred' })
    expect(chip({ start: '2026-10-03', start_all_day: true })).toMatchObject({ text: 'Starts Sat', tone: 'deferred' })
    expect(chip({})).toBeNull()
  })
})

describe('search operators', () => {
  const work = task({ summary: 'Call Dana', categories: ['phone'], priority: 1, due: '2026-09-30', conversation_id: 'c1' })
  const home = task({ summary: 'Buy paint', notes: 'white matte', list: lists[1].id, due: '2026-10-05' })
  const m = (q: string) => [work, home].filter((t) => matchesQuery(t, parseTaskQuery(q, now, zone), { ...o, lists })).map((t) => t.summary)
  it('matches free text in title, notes and tags', () => {
    expect(m('dana')).toEqual(['Call Dana'])
    expect(m('matte')).toEqual(['Buy paint'])
    expect(m('phone')).toEqual(['Call Dana'])
  })
  it('understands list:, tag:/@, due:, is:, has: and pri:', () => {
    expect(m('list:home')).toEqual(['Buy paint'])
    expect(m('@phone')).toEqual(['Call Dana'])
    expect(m('tag:phone')).toEqual(['Call Dana'])
    expect(m('due:<fri')).toEqual(['Call Dana'])
    expect(m('due:>fri')).toEqual(['Buy paint'])
    expect(m('is:overdue')).toEqual(['Call Dana'])
    expect(m('has:email')).toEqual(['Call Dana'])
    expect(m('pri:high')).toEqual(['Call Dana'])
    expect(isEmptyQuery(parseTaskQuery('  ', now, zone))).toBe(true)
  })
})

describe('manual order', () => {
  it('places a task between neighbours', () => {
    expect(midpoint(10, 20)).toBe(15)
    expect(midpoint(null, 20)).toBe(20 - 1024)
    expect(midpoint(10, null)).toBe(10 + 1024)
    expect(needsRenumber([task({ sort_order: 1 }), task({ sort_order: 1 })])).toBe(true)
    expect(needsRenumber([task({ sort_order: 1 }), task({ sort_order: 2 })])).toBe(false)
  })

  // ACT task.reorder, ACT task.reparent: the Undo of a move goes back through the same POST /move.
  it('undoes a move to the old parent and neighbours', () => {
    const [a, b, c] = [task({ uid: 'a', sort_order: 1 }), task({ uid: 'b', sort_order: 2 }), task({ uid: 'c', sort_order: 3 })]
    expect(placeOf(b, [a, b, c])).toEqual({ parent: '', above: ['a'], below: ['c'] })
    expect(placeOf(c, [a, b, c])).toEqual({ parent: '', above: ['b', 'a'], below: [] })
    // A reorder: back after its old neighbour above, or before the one below when it was first.
    expect(moveBack(b, { after: 'c', sort_order: 4 }, placeOf(b, [a, b, c]))).toEqual({ after: 'a', sort_order: 2 })
    expect(moveBack(a, { after: 'c', sort_order: 4 }, placeOf(a, [a, b, c]))).toEqual({ before: 'b', sort_order: 1 })
    // An indent: the old parent ("" for the top level) and the old place.
    expect(moveBack(b, { parent: 'a' }, placeOf(b, [a, b, c]))).toEqual({ parent: '', after: 'a', sort_order: 2 })
    const sub = task({ uid: 's', parent: 'a', sort_order: 5 })
    expect(moveBack(sub, { parent: '' }, { parent: 'a', above: [], below: [] })).toEqual({ parent: 'a', sort_order: 5 })
    // Several rows dragged together: a neighbour not yet back in place is skipped.
    expect(moveBack(c, { before: 'a' }, placeOf(c, [a, b, c]), new Set(['b', 'c']))).toEqual({ after: 'a', sort_order: 3 })
    expect(moveBack(a, { before: 'c' }, placeOf(a, [a, b, c]), new Set(['a', 'b']))).toEqual({ before: 'c', sort_order: 1 })
    // A list move puts the list back; nothing changed: nothing to undo.
    expect(moveBack(task({ uid: 'x', list: 'L1' }), { list: 'L2' })).toEqual({ list: 'L1' })
    expect(moveBack(b, { parent: '' }, { parent: '', above: [], below: [] })).toBeNull()
  })
})

describe('bulk', () => {
  it('builds each task\'s patch and the inverse that undoes it', () => {
    const t = task({ due: '2026-10-02', categories: ['a'], priority: 5 })
    expect(bulkPatch('tag', t, { tag: 'A' })).toBeNull()
    expect(bulkPatch('tag', t, { tag: 'b' })).toEqual({ categories: ['a', 'b'] })
    expect(bulkPatch('reschedule', t, { due: null })).toEqual({ clear_due: true })
    expect(inverseOf('reschedule', t)).toEqual({ patch: { due: '2026-10-02' } })
    expect(inverseOf('reschedule', task({}))).toEqual({ patch: { clear_due: true } })
    expect(inverseOf('complete', t)).toEqual({ patch: { completed: false } })
    expect(inverseOf('priority', t)).toEqual({ patch: { priority: 5 } })
    expect(inverseOf('delete', t)).toEqual({ recreate: t })
  })
  it('shifts dues by days and keeps the time', () => {
    expect(shiftDue(task({ due: '2026-10-02' }), 7, zone)).toBe('2026-10-09')
    expect(shiftDue(task({ due: '2026-10-02T14:30:00+00:00' }), 1, zone)).toBe('2026-10-03T14:30:00.000Z')
  })
  it('reports partial failures', () => {
    expect(bulkReport('Completed', 12, [])).toBe('Completed 12 tasks')
    expect(bulkReport('Completed', 11, [{ conflict: true }])).toBe('11 of 12 done · 1 changed elsewhere')
  })
})
