// A section header's Snooze all / Move all to / Label all (web.section): the standalone menus run
// `run({ ids })`, and ids made by idsForList stand for the whole section (mail.snooze, mail.move,
// mail.label act on every conversation of it, not only the loaded rows).
import { describe, expect, it } from 'vitest'
import { idsForList, listTargetOf } from './hooks'

const query = { view: 'inbox', q: '', layout: 'default', tab: 'inbox', sec: 'everything' }

describe('list targets', () => {
  it('an id list made for a section stands for it; equal ordinary lists do not', () => {
    const ids = idsForList({ query, total: 2898 }, [5, 6, 7])
    expect(ids).toEqual([5, 6, 7])
    expect(listTargetOf(ids)).toEqual({ query, total: 2898 })
    expect(listTargetOf([5, 6, 7])).toBeUndefined()
  })
  it('a section with no rows loaded still has its target', () => {
    const ids = idsForList({ query, total: 40 })
    expect(ids).toEqual([])
    expect(listTargetOf(ids)?.total).toBe(40)
    expect(listTargetOf([])).toBeUndefined()
  })
})
