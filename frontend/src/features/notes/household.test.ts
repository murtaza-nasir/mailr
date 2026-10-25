// ACT-SURFACES: web.note web.notes web.table web.calendar
// Household lists on the web (offline lists, presence, repeats, quick notes, table dates and the local-only switch): the offline list queue
// (offline.ts: coalescing, the overlay, the drain), the repeat rule and its words (repeatModel.ts,
// note.repeat), presence (note.presence and table.presence: who is in a list or a table, ticks by others), "Share with Cleo"
// (note.share_quick), the locked Local models only switch (note.local_only, table.local_only), a
// table's dates as calendar chips (table.on_calendar) and the "Add a row" link (table.form_link).
import { beforeEach, describe, expect, it } from 'vitest'
import { asChip } from '../calendar/tableDates'
import { dateColumns, formLink } from '../tables/householdMenu'
import { localOnlyLock } from './localOnly'
import {
  addOp, applyPending, checkOp, coalesce, deleteOp, drain, editOp, enqueue, loadNotesQueue, pendingItems, useNotesQueue, type NoteOp,
} from './offline'
import { hereLine, hereNames, itemTouched, onPresence, resetPresence, usePresence } from './presence'
import { quickShareTargets } from './quickShare'
import { repeatLine } from './repeat'
import { listRepeat, repeatFromWords, repeatRule, repeatWords } from './repeatModel'
import type { Item, Note } from './types'

const item = (id: string, text: string, extra: Partial<Item> = {}): Item => ({
  id, section_id: null, text, qty: null, extra: null, checked: false, checked_at: null, order: id, rank: null, added_at: '2026-10-07T00:00:00Z', version: 1, ...extra,
})
const list = (items: Item[]): Note => ({
  id: 'n1', kind: 'checklist', title: 'Groceries', body: '', color: null, shopping: true, checked_at_bottom: true, suggest_usual: false, local_only: false,
  created_at: '', updated_at: '', deleted_at: null, version: 3, level: 'owner', state: { pinned: false, archived: false, labels: [], order: null }, items,
})

describe('the offline list queue (offline.ts)', () => {
  const milk = item('i-milk', 'milk')
  const eggs = item('i-eggs', 'eggs')

  it('lays waiting ticks, edits, adds and deletes over the server copy', () => {
    const ops = [
      checkOp(1, 'n1', milk, true, 'a', 1000),
      addOp(1, 'n1', [item('i-new', 'bread')], 'b', 2000),
      editOp(1, 'n1', eggs, { text: 'brown eggs' }, 'c', 3000),
    ]
    const out = applyPending(list([milk, eggs]), ops)
    expect(out.items!.map((i) => [i.text, i.checked])).toEqual([['milk', true], ['brown eggs', false], ['bread', false]])
    expect(applyPending(list([milk, eggs]), [deleteOp(1, 'n1', milk, 'd')]).items!.map((i) => i.id)).toEqual(['i-eggs'])
    // Another list's changes leave this one alone.
    expect(applyPending(list([milk]), [checkOp(1, 'n2', milk, true, 'e')]).items![0].checked).toBe(false)
    expect([...pendingItems(ops, 'n1')].sort()).toEqual(['i-eggs', 'i-milk', 'i-new'])
  })

  it('keeps only the last tap of an item, and folds edits into an add that has not left', () => {
    let q: NoteOp[] = [checkOp(1, 'n1', milk, true, 'a', 1)]
    q = coalesce(q, checkOp(1, 'n1', milk, false, 'b', 2))
    expect(q.map((o) => [o.id, o.body?.checked])).toEqual([['b', false]])
    const bread = item('i-bread', 'bread')
    q = coalesce([], addOp(1, 'n1', [bread, item('i-jam', 'jam')], 'add', 1))
    q = coalesce(q, editOp(1, 'n1', bread, { text: 'rye bread' }, 'ed', 2))
    q = coalesce(q, checkOp(1, 'n1', bread, true, 'ck', 3))
    expect(q).toHaveLength(1)
    expect(q[0].body?.items).toEqual([
      { id: 'i-bread', text: 'rye bread', qty: null, section_id: null, checked: true },
      { id: 'i-jam', text: 'jam', qty: null, section_id: null },
    ])
    q = coalesce(q, deleteOp(1, 'n1', bread, 'del', 4))
    expect(q[0].items!.map((i) => i.id)).toEqual(['i-jam'])
    expect(coalesce(q, deleteOp(1, 'n1', item('i-jam', 'jam'), 'del2', 5))).toEqual([])
  })

  describe('drain', () => {
    beforeEach(async () => {
      useNotesQueue.setState({ user: null, ops: [], draining: false, paused: false })
      await loadNotesQueue(1)
      useNotesQueue.setState({ ops: [] })
    })

    it('sends in order with each change once, and stops while the network is down', async () => {
      await enqueue(checkOp(1, 'n1', milk, true, 'k1', 1))
      await enqueue(addOp(1, 'n1', [item('i-x', 'bread')], 'k2', 2))
      const sent: string[] = []
      let net = false
      const r1 = await drain(async (op) => {
        if (!net) throw Object.assign(new Error('offline'), { status: 0 })
        sent.push(op.id)
      })
      expect(r1.sent).toBe(0)
      expect(useNotesQueue.getState().ops).toHaveLength(2)
      net = true
      const r2 = await drain(async (op) => {
        sent.push(op.id)
      })
      expect(r2.sent).toBe(2)
      expect(sent).toEqual(['k1', 'k2'])
      expect(useNotesQueue.getState().ops).toEqual([])
    })

    it('drops a change to an item that is gone, and turns a clash into a failed change', async () => {
      await enqueue(deleteOp(1, 'n1', milk, 'gone', 1))
      await enqueue(editOp(1, 'n2', eggs, { text: 'eggs' }, 'clash', 2))
      const r = await drain(async (op) => {
        throw Object.assign(new Error('x'), { status: op.id === 'gone' ? 404 : 409 })
      })
      expect([r.dropped, r.failed]).toEqual([1, 1])
      const [f] = useNotesQueue.getState().ops
      expect([f.id, f.state, f.error]).toEqual(['clash', 'failed', 'Changed elsewhere meanwhile.'])
    })
  })
})

describe('repeating lists (repeatModel.ts, repeat.tsx)', () => {
  it('reads the two shapes a list takes, and nothing else', () => {
    expect(listRepeat('FREQ=WEEKLY;BYDAY=SU')).toEqual({ freq: 'weekly', day: 'SU', interval: 1 })
    expect(listRepeat('FREQ=DAILY;INTERVAL=3')).toEqual({ freq: 'daily', interval: 3 })
    expect(listRepeat('FREQ=MONTHLY;BYMONTHDAY=1')).toBeNull()
    expect(listRepeat('FREQ=WEEKLY;BYDAY=MO,TU')).toBeNull()
    expect(repeatRule({ freq: 'weekly', day: 'FR', interval: 2 })).toBe('FREQ=WEEKLY;BYDAY=FR;INTERVAL=2')
    expect(repeatRule({ freq: 'daily', interval: 1 })).toBe('FREQ=DAILY;INTERVAL=1')
  })
  it('takes typed words with the task grammar', () => {
    const now = new Date('2026-10-07T12:00:00Z') // a Wednesday
    expect(repeatFromWords('every sunday', 'UTC', now)).toEqual({ freq: 'weekly', day: 'SU', interval: 1 })
    expect(repeatFromWords('sunday', 'UTC', now)).toEqual({ freq: 'weekly', day: 'SU', interval: 1 })
    expect(repeatFromWords('every 3 days', 'UTC', now)).toEqual({ freq: 'daily', interval: 3 })
    expect(repeatFromWords('weekly', 'UTC', now)).toEqual({ freq: 'weekly', day: 'WE', interval: 1 })
    expect(repeatFromWords('monthly', 'UTC', now)).toBeNull()
    expect(repeatFromWords('', 'UTC', now)).toBeNull()
  })
  it('says the repeat and the next reset', () => {
    expect(repeatWords('FREQ=WEEKLY;BYDAY=SU')).toBe('Weekly on Sunday')
    expect(repeatWords('FREQ=DAILY;INTERVAL=3')).toBe('Every 3 days')
    expect(repeatLine({ repeat: { rule: 'FREQ=WEEKLY;BYDAY=SU', next: '2026-10-11T09:00:00Z', last: null } }, 'UTC')).toMatch(/^Repeats weekly on Sunday · next Sun.* 11 Oct|^Repeats weekly on Sunday · next Sun.*Oct 11/)
    expect(repeatLine({ repeat: null })).toBe('')
  })
})

describe('presence (presence.ts)', () => {
  beforeEach(() => resetPresence())
  it('shows who is in a list until their beat runs out or they leave', () => {
    const t0 = 1_000_000
    expect(onPresence({ kind: 'note', id: 'n1', person: { id: 2, name: 'Cleo Example' }, state: 'here', hello: true }, t0)).toBe(true)
    onPresence({ kind: 'note', id: 'n1', person: { id: 3, name: 'Dana' }, state: 'here' }, t0)
    expect(hereNames('n1', 1, t0 + 1000)).toEqual(['Cleo', 'Dana'])
    expect(hereNames('n1', 2, t0 + 1000)).toEqual(['Dana'])
    expect(hereNames('n1', 1, t0 + 41_000)).toEqual([])
    onPresence({ kind: 'note', id: 'n1', person: { id: 3, name: 'Dana' }, state: 'left' }, t0 + 2000)
    expect(hereNames('n1', 1, t0 + 3000)).toEqual(['Cleo'])
    expect(hereLine(['Cleo'], 'list')).toBe('Cleo is in this list')
    expect(hereLine(['Cleo', 'Dana'], 'table')).toBe('Cleo and Dana are in this table')
    expect(hereLine(['A', 'B', 'C', 'D'], 'note')).toBe('A, B and 2 more are in this note')
    expect(hereLine([], 'list')).toBe('')
  })
  it("marks the row another person ticked, by first name", () => {
    itemTouched('i1', { id: 2, name: 'Cleo Example' })
    expect(usePresence.getState().touched.i1.name).toBe('Cleo')
    itemTouched('i2', null)
    expect(usePresence.getState().touched.i2).toBeUndefined()
  })
})

describe('share with a household member (quickShare.tsx)', () => {
  it('offers the candidates in your groups, two at most, by first name', () => {
    const st = {
      candidates: [{ id: 2, name: 'Cleo Example', email: 'cleo@example.com' }, { id: 3, name: 'Dana', email: 'd@example.com' }, { id: 4, name: 'Gus', email: 'g@example.com' }, { id: 5, name: 'Olive', email: 'o@example.com' }],
      household: [2, 4, 5],
    }
    expect(quickShareTargets(st)).toEqual([{ id: 2, name: 'Cleo' }, { id: 4, name: 'Gus' }])
    expect(quickShareTargets({ candidates: st.candidates, household: [] })).toEqual([])
    expect(quickShareTargets(undefined)).toEqual([])
  })
})

describe('Local models only (localOnly.tsx)', () => {
  it('locks the switch for an editor once it is on, with the reason', () => {
    expect(localOnlyLock({ local_only: true, can_lower_local_only: false, level: 'edit' })).toBe('Only the owner can turn this off.')
    expect(localOnlyLock({ local_only: false, can_lower_local_only: false, level: 'edit' })).toBeNull()
    expect(localOnlyLock({ local_only: true, can_lower_local_only: true, level: 'owner' })).toBeNull()
    expect(localOnlyLock({ local_only: false, level: 'view' })).toMatch(/view/)
    expect(localOnlyLock({ local_only: false, level: 'owner' }, false)).toMatch(/offline/)
  })
})

describe('tables on the calendar and the form (tableDates.tsx, householdMenu.tsx)', () => {
  it('makes a dated row a date-only chip that opens the row', () => {
    const c = asChip({ table: 't1', table_name: 'Birthdays', color: null, row: 'r1', title: 'Ana', date: '2026-10-12', column_name: 'Day' })
    expect([c.due, c.due_all_day, c.summary, c.table_row?.row]).toEqual(['2026-10-12', true, 'Ana', 'r1'])
  })
  it('offers date columns only, and a sign-in link to the form', () => {
    const cols = [{ id: 'a', name: 'Name', type: 'text' }, { id: 'b', name: 'Day', type: 'date' }, { id: 'c', name: 'When', type: 'datetime' }] as never
    expect(dateColumns({ columns: cols })).toEqual([{ id: 'b', name: 'Day' }, { id: 'c', name: 'When' }])
    expect(formLink('t1', 'https://mail.example.com')).toBe('https://mail.example.com/tables/t/t1/add')
  })
})
