// The Notes app's pure helpers: the display order of unchecked items, the
// grid's sort and Pinned group, Trash days, quantities, the Markdown copy (it parses back to the same
// items through the item grammar), light Markdown, the paste summary and link parsing.
// ACT-SURFACES: web.notes web.note
import { describe, expect, it } from 'vitest'
import { parseItems } from '../../lib/itemadd'
import { pasteSummary } from './Checklist'
import { parseLink } from './NoteEditor'
import { checklistView, mdInline, noteMarkdown, noteText, qtyLabel, sortGroup, sortNotes, splitPinned, stepQty, trashLine, uuid7 } from './model'
import type { Item, Note, Section } from './types'

const it0 = (p: Partial<Item>): Item => ({
  id: p.id ?? Math.random().toString(36).slice(2), section_id: null, text: 'x', qty: null, extra: null, checked: false, checked_at: null,
  order: 'a', rank: null, added_at: '2026-10-01T00:00:00Z', ...p,
})
const sec = (id: string, name: string, order: string, mode: 'learned' | 'manual' = 'manual'): Section => ({ id, name, order, order_mode: mode, collapsed: false })

describe('display order', () => {
  it('manual mode sorts by the order key', () => {
    const out = sortGroup([it0({ id: 'b', order: 'b' }), it0({ id: 'a', order: 'a' }), it0({ id: 'c', order: 'c' })], 'manual')
    expect(out.map((i) => i.id)).toEqual(['a', 'b', 'c'])
  })

  it('learned mode: ranked first by rank (ties by order key), then unranked in the order they were added', () => {
    const out = sortGroup([
      it0({ id: 'new', rank: null, added_at: '2026-10-03T00:00:00Z' }),
      it0({ id: 'late', rank: 0.9 }),
      it0({ id: 'old', rank: null, added_at: '2026-10-01T00:00:00Z' }),
      it0({ id: 'early-b', rank: 0.1, order: 'b' }),
      it0({ id: 'early-a', rank: 0.1, order: 'a' }),
    ], 'learned')
    expect(out.map((i) => i.id)).toEqual(['early-a', 'early-b', 'late', 'old', 'new'])
  })

  it('the top group first, then sections by their order; checked items fold, newest check first', () => {
    const sections = [sec('s2', 'Deli', 'm'), sec('s1', 'Bakery', 'i')]
    const items = [
      it0({ id: 'top', text: 'milk' }),
      it0({ id: 'bread', section_id: 's1' }),
      it0({ id: 'ham', section_id: 's2' }),
      it0({ id: 'c1', checked: true, checked_at: '2026-10-01T10:00:00Z' }),
      it0({ id: 'c2', checked: true, checked_at: '2026-10-02T10:00:00Z', section_id: 's1' }),
    ]
    const v = checklistView({ checked_at_bottom: true, top: { order_mode: 'manual', collapsed: false, trips: 0, unchecked: 1 } }, sections, items)
    expect(v.groups.map((g) => g.section?.name ?? null)).toEqual([null, 'Bakery', 'Deli'])
    expect(v.groups.map((g) => g.items.map((i) => i.id))).toEqual([['top'], ['bread'], ['ham']])
    expect(v.checked.map((i) => i.id)).toEqual(['c2', 'c1'])
    // Without the fold, checked items keep their place.
    const inPlace = checklistView({ checked_at_bottom: false }, sections, items)
    expect(inPlace.checked).toEqual([])
    expect(inPlace.groups[0].items.map((i) => i.id)).toContain('c1')
    // A check that lingers (600 ms) stays in its group.
    const linger = checklistView({ checked_at_bottom: true }, sections, items, new Set(['c1']))
    expect(linger.groups[0].items.map((i) => i.id)).toEqual(['top', 'c1'])
  })

  it('an item whose section is gone sits in the top group', () => {
    const v = checklistView({ checked_at_bottom: true }, [], [it0({ id: 'orphan', section_id: 'gone' })])
    expect(v.groups[0].items.map((i) => i.id)).toEqual(['orphan'])
  })
})

describe('the grid', () => {
  const n = (id: string, p: Partial<Note> = {}, state: Partial<Note['state']> = {}): Note => ({
    id, kind: 'text', title: id, body: '', color: null, shopping: false, checked_at_bottom: true, suggest_usual: false, local_only: false,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', deleted_at: null, version: 1, level: 'owner',
    state: { pinned: false, archived: false, labels: [], order: null, ...state }, ...p,
  })
  it('sorts by hand, by edit, by creation and by title', () => {
    const notes = [n('b', { updated_at: '2026-10-03T00:00:00Z' }, { order: 'm' }), n('a', { created_at: '2026-10-05T00:00:00Z' }, { order: 'z' }), n('c', {}, { order: 'a' })]
    expect(sortNotes(notes, 'manual').map((x) => x.id)).toEqual(['c', 'b', 'a'])
    expect(sortNotes(notes, 'edited')[0].id).toBe('b')
    expect(sortNotes(notes, 'created')[0].id).toBe('a')
    expect(sortNotes(notes, 'title').map((x) => x.id)).toEqual(['a', 'b', 'c'])
  })
  it('pinned first, except where the view has no Pinned group', () => {
    const notes = [n('a'), n('b', {}, { pinned: true })]
    expect(splitPinned(notes, true)).toEqual({ pinned: [notes[1]], others: [notes[0]] })
    expect(splitPinned(notes, false).pinned).toEqual([])
  })
  it('Trash says how long a note stays', () => {
    const now = Date.parse('2026-10-05T12:00:00Z')
    expect(trashLine({ deleted_at: '2026-10-02T12:00:00Z', purge_at: '2026-11-01T12:00:00Z' }, now)).toBe('Deleted 3 days ago · 27 days left')
    expect(trashLine({ deleted_at: '2026-10-05T11:00:00Z', purge_at: null }, now)).toBe('Deleted today · 30 days left')
  })
})

describe('quantities', () => {
  it('a count of 1 shows no chip; units show with the amount', () => {
    expect(qtyLabel({ amount: '1', unit: null })).toBe('')
    expect(qtyLabel({ amount: '2', unit: null })).toBe('2')
    expect(qtyLabel({ amount: '500', unit: 'g' })).toBe('500 g')
  })
  it('− and + change a count by one; below one the chip goes', () => {
    expect(stepQty({ amount: '2', unit: null }, 1)).toEqual({ amount: '3', unit: null })
    expect(stepQty({ amount: '2', unit: null }, -1)).toEqual({ amount: '1', unit: null })
    expect(stepQty({ amount: '1', unit: null }, -1)).toBeNull()
    expect(stepQty(null, 1)).toEqual({ amount: '2', unit: null })
  })
})

describe('the Markdown copy', () => {
  it('parses back to the same items through the item grammar', () => {
    const note = {
      id: 'n1', kind: 'checklist', title: 'Shopping', body: '', sections: [sec('s1', 'Costco', 'i')],
      items: [it0({ text: 'milk', qty: { amount: '2', unit: null } }), it0({ text: 'flour', qty: { amount: '500', unit: 'g' }, section_id: 's1', checked: true })],
    } as unknown as Note
    const md = noteMarkdown(note)
    expect(md).toBe('# Shopping\n\n- [ ] milk ×2\n\nCostco:\n- [x] flour 500 g\n')
    const back = parseItems(md.split('\n').slice(2).join('\n'), { mode: 'list', list: { id: 'n1', name: 'Shopping', sections: ['Costco'] } })
    expect(back.items.map((i) => [i.text, i.qty?.amount, i.qty?.unit ?? null, i.section, i.checked])).toEqual([
      ['milk', '2', null, null, false], ['flour', '500', 'g', 'Costco', true],
    ])
  })
})

describe('the text of a note (Create from note)', () => {
  it('a checklist gives its open items under their sections; a text note its body', () => {
    const note = {
      id: 'n1', kind: 'checklist', title: 'Shopping', body: '', sections: [sec('s1', 'Costco', 'i')],
      items: [it0({ text: 'milk', qty: { amount: '2', unit: null } }), it0({ text: 'flour', section_id: 's1' }), it0({ text: 'eggs', section_id: 's1', checked: true })],
    } as unknown as Note
    expect(noteText(note)).toBe('- milk (2)\n\nCostco:\n- flour')
    expect(noteText({ kind: 'text', body: ' Call the plumber \n' } as unknown as Note)).toBe('Call the plumber')
  })
})

describe('bits', () => {
  it('light Markdown: bold, italics, links', () => {
    expect(mdInline('**Lisbon** in *spring*, see https://example.org.')).toEqual([
      { kind: 'bold', text: 'Lisbon' }, { kind: 'text', text: ' in ' }, { kind: 'italic', text: 'spring' }, { kind: 'text', text: ', see ' },
      { kind: 'link', text: 'https://example.org', href: 'https://example.org' }, { kind: 'text', text: '.' },
    ])
  })
  it('the paste summary names the sections', () => {
    expect(pasteSummary(['Costco', 'Corner market', 'Corner market', null], 'Shopping')).toBe('Add 4 items to Shopping (Costco 1, Corner market 2)?')
    expect(pasteSummary([null], 'Packing')).toBe('Add 1 item to Packing?')
  })
  it('a link names an email, an event, a task or a recording by its Mailr address', () => {
    expect(parseLink('https://mail.example.org/mail/inbox/c123')?.kind).toBe('conversation')
    expect(parseLink('/calendar/event/abc%40x/2026-10-02')).toEqual({ kind: 'event', ref: { uid: 'abc@x', occurrence: '2026-10-02' } })
    expect(parseLink('/tasks/today/task/t-1')).toEqual({ kind: 'task', ref: { uid: 't-1' } })
    expect(parseLink('/recordings/2/41')).toEqual({ kind: 'recording', ref: { connector_id: 2, recording_id: 41 } })
    expect(parseLink('https://example.org/elsewhere')).toBeNull()
  })
  it('item ids are UUID v7', () => {
    expect(uuid7()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  })
})
