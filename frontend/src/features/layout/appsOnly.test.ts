import { describe, expect, it } from 'vitest'
import { appsOnlyPath } from './Sidebar'

describe('Classic sidebar in the other apps', () => {
  it('is only the apps where the page has its own navigation', () => {
    expect(appsOnlyPath('/calendar/week')).toBe(true)
    expect(appsOnlyPath('/tasks')).toBe(true)
    expect(appsOnlyPath('/notes/all')).toBe(true)
    expect(appsOnlyPath('/mail/inbox')).toBe(false)
    expect(appsOnlyPath('/settings/you')).toBe(false)
    expect(appsOnlyPath('/calendarx')).toBe(false)
  })
})
