// ACT-SURFACES: web.calendar web.settings
// Labels for group calendars (backend calendar/groups.py) and for calendars people share
// (calendar/sharing.py): the member's or sharee's line under a shared calendar, the owner's line,
// the admin's access summary, and the Share dialog's helpers. ACT settings.group_calendars
// ACT calendar.user_shared ACT calendar.share
import { describe, expect, it } from 'vitest'
import { accessLabel } from '../settings/GroupCalendars'
import { normalizeEvent } from './data'
import { levelChoices, matching, policyNote } from './ShareDialog'
import { ownerNote, sharedLabel, sharingLabel, throughLabel } from './types'

describe('group calendar labels', () => {
  it('names the group, and says view only when the member cannot write', () => {
    expect(sharedLabel({ shared: { kind: 'group', group: 'Family', access: 'write' }, read_only: false })).toBe('Shared with the Family group')
    expect(sharedLabel({ shared: { kind: 'group', group: 'Family', access: 'read' }, read_only: true })).toBe('Shared with the Family group · view only')
    // An editor of a read-only calendar writes, so no "view only".
    expect(sharedLabel({ shared: { kind: 'group', group: 'Family', access: 'read', editor: true }, read_only: false })).toBe('Shared with the Family group')
    expect(sharedLabel({ shared: null, read_only: true })).toBe('')
  })

  it('sums up access for the admin', () => {
    expect(accessLabel({ access: 'write', editors: [] })).toBe('Members can edit')
    expect(accessLabel({ access: 'read', editors: [] })).toBe('Members can view')
    expect(accessLabel({ access: 'read', editors: [{ id: 1, name: 'Rosa', email: 'r@northwind.example' }, { id: 2, name: 'Sam', email: 's@northwind.example' }] })).toBe('Members can view · 2 editors')
  })
})

describe('calendars people share', () => {
  it('names the owner, and says view only when the sharee cannot write', () => {
    expect(sharedLabel({ shared: { kind: 'user', owner: 'Rosa', level: 'edit', access: 'write' }, read_only: false })).toBe('Shared by Rosa')
    expect(sharedLabel({ shared: { kind: 'user', owner: 'Rosa', level: 'view', access: 'read' }, read_only: true })).toBe('Shared by Rosa · view only')
    expect(ownerNote({ shared: { kind: 'user', owner: 'Rosa', level: 'view', access: 'read' } })).toBe('Only Rosa can rename or delete it. You can hide it.')
    expect(ownerNote({ shared: { kind: 'group', group: 'Family', access: 'read' } })).toMatch(/administrator/)
  })

  it('says free/busy only, and the access a group gives', () => {
    expect(sharedLabel({ shared: { kind: 'user', owner: 'Rosa', level: 'freebusy', access: 'freebusy' }, read_only: true })).toBe('Shared by Rosa · free/busy only')
    expect(sharedLabel({ shared: { kind: 'user', owner: 'Rosa', level: 'edit', access: 'write', own_level: 'view', via: ['Family'] }, read_only: false }))
      .toBe('Shared by Rosa · can edit (through Family)')
    expect(throughLabel('share', ['Family', 'Team'])).toBe('can edit and share (through Family, Team)')
  })

  it('offers the levels the server lists, and keeps a level it no longer offers visible', () => {
    expect(levelChoices(['freebusy', 'view', 'edit', 'share']).map((l) => l.id)).toEqual(['freebusy', 'view', 'edit', 'share'])
    expect(levelChoices(['view', 'edit', 'share']).map((l) => l.id)).toEqual(['view', 'edit', 'share'])
    expect(levelChoices(['view', 'edit', 'share'], 'freebusy').map((l) => l.id)).toEqual(['freebusy', 'view', 'edit', 'share'])
    expect(levelChoices(undefined).map((l) => l.id)).toEqual(['view', 'edit', 'share'])
  })

  it('reads a free/busy-only block as "Busy", read-only', () => {
    const url = 'https://mail.example/dav/cal/rosa/rota/'
    const col = { url, name: 'Rota', read_only: true, shared: { kind: 'user' as const, owner: 'Rosa', level: 'freebusy' as const, access: 'freebusy' as const } }
    const e = normalizeEvent({ uid: 'mailr-busy-1@mailr', summary: 'Beschäftigt', start: '2026-10-30T17:00:00Z', end: '2026-10-30T18:00:00Z',
      calendar: url, shared_busy: true }, { zone: 'UTC', collections: new Map([[url, col as never]]) })
    expect(e?.summary).toBe('Busy')
    expect(e?.readOnly).toBe(true)
  })

  it("sums up the owner's shares", () => {
    expect(sharingLabel({ sharing: { people: 1, others: 0 } })).toBe('Shared with 1 person')
    expect(sharingLabel({ sharing: { people: 3, others: 1 } })).toBe('Shared with 3 people and a group')
    expect(sharingLabel({ sharing: { people: 0, others: 1 } })).toBe('Shared with a group')
    expect(sharingLabel({ sharing: null })).toBe('')
  })

  it('explains the policy and filters people', () => {
    const owner = { id: 1, name: 'Rosa', email: 'rosa@northwind.example' }
    expect(policyNote({ policy: 'anyone', own: true, owner })).toBe('You can share with anyone on this server.')
    expect(policyNote({ policy: 'groups', own: false, owner })).toBe('Owned by Rosa; you can share it. You can share with people who are in one of your groups.')
    expect(policyNote({ policy: 'nobody', own: true, owner })).toMatch(/turned off.*stop sharing/)
    const people = [owner, { id: 2, name: 'Sam Lee', email: 'sam@northwind.example' }, { id: 3, name: 'Tess', email: 'tess@contoso.example' }]
    expect(matching(people, 'northwind').map((p) => p.id)).toEqual([1, 2])
    expect(matching(people, ' LEE ').map((p) => p.id)).toEqual([2])
    expect(matching(people, '', 2).map((p) => p.id)).toEqual([1, 2])
  })
})
