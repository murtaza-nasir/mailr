// ACT-SURFACES: web.notes web.note web.table
// Sharing on the web (notes/sharing.tsx, presence.ts): the lines that say who else sees a note or a
// table, the initials on a card, the authors of items, and "Dana is editing" (note.share,
// note.leave, table.share).
import { afterEach, describe, expect, it } from 'vitest'
import { authorLine } from './model'
import { editingName, noteUnshared, noteWriter, PRESENCE_MS, resetPresence, usePresence } from './presence'
import { initials, policyNote, sharedLine, viewOnlyLine } from './sharing'

describe('sharedLine', () => {
  it('names the owner for a sharee, the group for a group note, and counts people for the owner', () => {
    expect(sharedLine({ level: 'edit', owner: { kind: 'user', name: 'Olive' } })).toBe('Shared by Olive')
    expect(sharedLine({ level: 'edit', owner: { kind: 'group', name: 'Household' } })).toBe('In the Household group')
    expect(sharedLine({ level: 'owner', owner: { kind: 'user', name: 'Me' }, sharing: { count: 0, people: [] } })).toBe('')
    expect(sharedLine({ level: 'owner', sharing: { count: 1, people: [{ kind: 'user', name: 'Dana' }] } })).toBe('Shared with 1 person')
    expect(sharedLine({ level: 'owner', sharing: { count: 2, people: [{ kind: 'user', name: 'Dana' }, { kind: 'group', name: 'Family' }] } })).toBe('Shared with 2 people')
    expect(sharedLine({ level: 'owner', sharing: { count: 1, people: [{ kind: 'group', name: 'Family' }] } })).toBe('Shared with the Family group')
  })
  it('says view only with who shared it', () => {
    expect(viewOnlyLine({ owner: { kind: 'user', name: 'Olive' } })).toBe('View only · shared by Olive')
    expect(viewOnlyLine({ owner: { kind: 'group', name: 'Household' } })).toBe('View only · shared by the Household group')
  })
  it('names the policy only when it narrows the choice', () => {
    const s = { own: true, owner: { kind: 'user' as const, id: 1, name: 'O', email: 'o@example.org' }, noun: 'list' as const, can_share: true }
    expect(policyNote({ ...s, policy: 'anyone' })).toBe('')
    expect(policyNote({ ...s, policy: 'groups' })).toBe('Only people in your groups can be added on this server.')
    expect(policyNote({ ...s, policy: 'nobody' })).toMatch(/turned off/)
    expect(policyNote({ ...s, policy: 'groups', can_share: false })).toBe('')
  })
})

describe('initials', () => {
  it('shows the owner and the other people, at most four', () => {
    expect(initials({ level: 'view', owner: { kind: 'user', name: 'Olive Park' }, sharing: { count: 1, people: [{ kind: 'user', name: 'Dana' }] } })).toEqual(['OP', 'D'])
    expect(initials({ level: 'owner', sharing: { count: 0, people: [] } })).toEqual([])
    const many = Array.from({ length: 6 }, (_, i) => ({ kind: 'user' as const, name: `P${i} X` }))
    expect(initials({ level: 'owner', sharing: { count: 6, people: many } })).toHaveLength(4)
  })
})

describe('authorLine', () => {
  it('names another person who added or checked an item, never the viewer', () => {
    expect(authorLine({ added_by: { id: 2, name: 'Dana' }, checked: false }, 1)).toBe('Added by Dana')
    expect(authorLine({ added_by: { id: 1, name: 'Me' }, checked: false }, 1)).toBe('')
    expect(authorLine({ added_by: { id: 1, name: 'Me' }, checked: true, checked_by: { id: 2, name: 'Dana' } }, 1)).toBe('Checked by Dana')
    expect(authorLine({ added_by: { id: 2, name: 'Dana' }, checked: false }, undefined)).toBe('')
  })
})

describe('presence', () => {
  afterEach(() => resetPresence())
  it('"Dana is editing" for 10 s after another person\'s write, never for the viewer', () => {
    const t0 = Date.now()
    expect(editingName('nabc', 1, t0)).toBeNull()
    noteWriter('nabc', { id: 2, name: 'Dana' })
    expect(editingName('nabc', 1, t0 + 1000)).toBe('Dana')
    expect(editingName('nabc', 1, t0 + PRESENCE_MS + 1000)).toBeNull()
    expect(editingName('nabc', 2, t0 + 1000)).toBeNull()
    noteWriter('nabc', null)
    expect(editingName('nabc', 1, t0 + 1000)).toBe('Dana')
  })
  it('remembers who stopped sharing an item', () => {
    noteUnshared('nx', 'Olive')
    expect(usePresence.getState().unshared.nx).toBe('Olive')
  })
})
