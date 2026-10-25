// Mail search with events and tasks (event.search, task.search): which parts of a mail query
// reach the calendar search.
import { describe, expect, it } from 'vitest'
import { toCalendarQuery } from './SearchGlance'

describe('toCalendarQuery', () => {
  it('keeps free words and phrases', () => {
    expect(toCalendarQuery('budget "q4 review"')).toBe('budget "q4 review"')
  })
  it('turns people into with:', () => {
    expect(toCalendarQuery('from:dana@x.org budget')).toBe('with:dana@x.org budget')
    expect(toCalendarQuery('-to:bob lunch')).toBe('-with:bob lunch')
  })
  it('drops mail-only operators', () => {
    expect(toCalendarQuery('in:inbox is:unread has:attachment label:Work offsite')).toBe('offsite')
    expect(toCalendarQuery('is:starred')).toBe('')
  })
})
