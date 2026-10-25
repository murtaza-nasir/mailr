// ACT-SURFACES: web.editor
// group.expand in recipient fields: adding a group, the summary, removing one member or the group.
import { describe, expect, it } from 'vitest'
import { addGroup, groupLabel, groupOptions, partition, prune, removeGroup } from './groupChips'

const a = (email: string, name = '') => ({ email, name })

describe('group chips (group.expand)', () => {
  it('adds the members once and remembers which addresses the group added', () => {
    const before = [a('Dana@x.example', 'Dana')]
    const { value, expanded } = addGroup(before, { id: 7, name: 'Committee' }, [a('dana@x.example'), a('eve@x.example'), a('fay@x.example')])
    expect(value.map((v) => v.email)).toEqual(['Dana@x.example', 'eve@x.example', 'fay@x.example'])
    expect(expanded).toEqual({ id: 7, name: 'Committee', emails: ['eve@x.example', 'fay@x.example'], total: 3 })
  })

  it('shows a summary per group and the other addresses apart', () => {
    const { value, expanded } = addGroup([a('lone@x.example')], { id: 1, name: 'Team' }, [a('t1@x.example'), a('t2@x.example')])
    const p = partition(value, [expanded])
    expect(p.groups.map((g) => g.members.map((m) => m.email))).toEqual([['t1@x.example', 't2@x.example']])
    expect(p.loose.map((l) => l.email)).toEqual(['lone@x.example'])
    expect(groupLabel(expanded, 2)).toBe('Team · 2')
  })

  it('removing one member keeps the group; removing the group keeps earlier addresses', () => {
    const { value, expanded } = addGroup([a('keep@x.example')], { id: 1, name: 'Team' }, [a('keep@x.example'), a('t1@x.example'), a('t2@x.example')])
    const one = value.filter((v) => v.email !== 't1@x.example')
    const p = partition(one, [expanded])
    expect(p.groups[0].members.map((m) => m.email)).toEqual(['t2@x.example'])
    expect(groupLabel(expanded, 1)).toBe('Team · 1 of 3')
    expect(removeGroup(one, expanded).map((v) => v.email)).toEqual(['keep@x.example'])
    expect(prune(removeGroup(one, expanded), [expanded])).toEqual([])
  })

  it('offers matching groups, prefix matches first, not the chosen ones or empty groups', () => {
    const gs = [{ id: 1, name: 'Book club', count: 3 }, { id: 2, name: 'Club board', count: 2 }, { id: 3, name: 'Empty club', count: 0 }]
    expect(groupOptions(gs, 'club', []).map((g) => g.id)).toEqual([2, 1])
    expect(groupOptions(gs, 'club', [{ id: 2, name: 'Club board', emails: [], total: 2 }]).map((g) => g.id)).toEqual([1])
    expect(groupOptions(gs, '', [])).toEqual([])
  })
})
