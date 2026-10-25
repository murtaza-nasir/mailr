// ACT-SURFACES: web.settings
// Settings → Activity (ActivityPage.tsx; activity.list, activity.undo): who made a change, the days
// the list is grouped by, and the only requests an entry may send as its Undo.
import { describe, expect, it } from 'vitest'
import { ALLOWED, groupByDay, whoLabel, type ActivityItem } from './ActivityPage'

const item = (id: number, at: string): ActivityItem => ({
  id, at, who: 'you', by: null, app: 'mail', text: 'Archived “x”', target: null, count: 1, undo: null, undone: false, ok: true,
})

describe('Activity', () => {
  it('names who made the change', () => {
    expect(whoLabel({ who: 'you', by: null })).toBe('You')
    expect(whoLabel({ who: 'ambrose', by: null })).toBe('Ambrose')
    expect(whoLabel({ who: 'agent', by: 'claude-code' })).toBe('Agent “claude-code”')
    expect(whoLabel({ who: 'admin', by: 'Sam Lee' })).toBe('Your administrator (Sam Lee)')
    expect(whoLabel({ who: 'admin', by: null })).toBe('Your administrator')
  })

  it('groups by Today, Yesterday and then the date', () => {
    const now = new Date('2026-10-06T15:00:00Z')
    const g = groupByDay([item(3, '2026-10-06T14:00:00Z'), item(2, '2026-10-05T12:00:00Z'), item(1, '2026-10-01T12:00:00Z')], 'UTC', now)
    expect(g.map((x) => [x.label, x.items.length])).toEqual([['Today', 1], ['Yesterday', 1], ['Thu 1 Oct 2026', 1]])
  })

  it('sends only the routes an entry can name as its Undo', () => {
    for (const ok of ['/conversations/actions', '/messages/actions', '/calendar/events', '/calendar/events/abc', '/tasks', '/tasks/u1',
      '/notes/n1', '/notes/n1/restore', '/tables/t1', '/tables/t1/revert', '/calendar/events/e1/revert']) expect(ALLOWED.test(ok), ok).toBe(true)
    for (const bad of ['/me/settings', '/admin/users/1', '/tokens', '/notes/n1/shares/x', '/calendar/events/a/b/c', '//evil']) expect(ALLOWED.test(bad), bad).toBe(false)
  })
})
