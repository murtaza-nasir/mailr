import { describe, expect, it } from 'vitest'
import { appFromPath, calendarPath, calendarSearchPath, eventPath, parseCalendarPath, parseTasksPath, taskPath, tasksPath } from './routes'

describe('app discriminator', () => {
  it('reads the first segment', () => {
    expect(appFromPath('/mail/inbox')).toBe('mail')
    expect(appFromPath('/calendar/week/2026-10-01')).toBe('calendar')
    expect(appFromPath('/tasks/today')).toBe('tasks')
    expect(appFromPath('/settings/general')).toBe('settings')
    expect(appFromPath('/')).toBe('mail')
  })
})

describe('calendar paths', () => {
  it('view and date', () => {
    expect(parseCalendarPath('/calendar')).toEqual({ view: null, date: null, event: null, search: null })
    expect(parseCalendarPath('/calendar/week/2026-10-01')).toMatchObject({ view: 'week', date: '2026-10-01' })
    expect(parseCalendarPath('/calendar/2026-10-01')).toMatchObject({ view: null, date: '2026-10-01' })
    expect(parseCalendarPath('/calendar/bogus/xx')).toMatchObject({ view: null, date: null })
    expect(parseCalendarPath('/mail/inbox')).toBeNull()
  })
  it('occurrence deep links round-trip, odd UIDs included', () => {
    const uid = 'abc/123@example.com'
    const occ = '2026-10-01T15:00:00+00:00'
    const p = eventPath(uid, occ)
    expect(p).toBe('/calendar/event/abc%2F123%40example.com/2026-10-01T15%3A00%3A00%2B00%3A00')
    expect(parseCalendarPath(p)?.event).toEqual({ uid, occ })
    expect(parseCalendarPath(eventPath('x', '2026-10-25'))?.event).toEqual({ uid: 'x', occ: '2026-10-25' })
  })
  it('search and builders', () => {
    expect(parseCalendarPath('/calendar/search', '?q=dentist')?.search).toBe('dentist')
    expect(calendarSearchPath('a b')).toBe('/calendar/search?q=a%20b')
    expect(calendarPath()).toBe('/calendar')
    expect(calendarPath('month', '2026-10-01')).toBe('/calendar/month/2026-10-01')
  })
})

describe('tasks paths', () => {
  it('lists', () => {
    expect(parseTasksPath('/tasks')).toEqual({ list: null, taskUid: null })
    expect(parseTasksPath('/tasks/today')).toEqual({ list: { kind: 'smart', id: 'today' }, taskUid: null })
    expect(parseTasksPath('/tasks/l/12')?.list).toEqual({ kind: 'list', id: '12' })
    expect(parseTasksPath('/tasks/t/task')?.list).toEqual({ kind: 'tag', id: 'task' })
    expect(parseTasksPath('/calendar')).toBeNull()
  })
  it('task deep links, alone or over a list', () => {
    expect(parseTasksPath('/tasks/task/u-1')).toEqual({ list: null, taskUid: 'u-1' })
    expect(parseTasksPath('/tasks/today/task/u-1')).toEqual({ list: { kind: 'smart', id: 'today' }, taskUid: 'u-1' })
    expect(parseTasksPath(taskPath('a/b@c', { kind: 'tag', id: 'call mum' }))).toEqual({ list: { kind: 'tag', id: 'call mum' }, taskUid: 'a/b@c' })
    expect(tasksPath('upcoming')).toBe('/tasks/upcoming')
    expect(tasksPath({ kind: 'list', id: '7' })).toBe('/tasks/l/7')
    expect(taskPath('u')).toBe('/tasks/task/u')
  })
})
