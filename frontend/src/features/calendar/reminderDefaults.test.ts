import { describe, expect, it } from 'vitest'
import { toWrite } from './actions'
import { defaultReminders, normalizeEvent, reminderField, userDefaultReminders } from './data'
import { initialForm } from './EventEditor'
import type { EditorInit } from './store'
import type { ApiEvent, Collection } from './types'

const settings = { events: true, default_timed_event: 1440, default_all_day_event: 900 }

describe('default reminders of a new event', () => {
  it('uses the user default for timed and all-day events', () => {
    expect(userDefaultReminders(settings, false)).toEqual([1440])
    expect(userDefaultReminders(settings, true)).toEqual([900])
  })
  it('gives none for a default of none, reminders off, or settings not loaded', () => {
    expect(userDefaultReminders({ ...settings, default_timed_event: null }, false)).toEqual([])
    expect(userDefaultReminders({ ...settings, events: false }, true)).toEqual([])
    expect(userDefaultReminders(undefined, false)).toEqual([])
  })
  it("prefers the calendar's own defaults", () => {
    const c = { default_reminders: { timed: [30] } } as unknown as Collection
    expect(defaultReminders(c, false) ?? userDefaultReminders(settings, false)).toEqual([30])
    expect(defaultReminders(c, true) ?? userDefaultReminders(settings, true)).toEqual([900])
  })
})

describe('"no reminders" from the editor', () => {
  const zone = 'America/Chicago'
  const ctx = { zone, collections: new Map() }
  const defaults = { calendar: '/cal/', duration: 60, now: new Date('2026-10-05T14:00:00Z') }
  const ev = (extra: Partial<ApiEvent>) => normalizeEvent({ uid: 'u1', summary: 'Sync', start: '2026-10-06T15:00:00Z', end: '2026-10-06T16:00:00Z', ...extra } as ApiEvent, ctx)!

  it('leaves reminders out while the default chips are untouched', () => {
    const f = initialForm({ mode: 'new' } as EditorInit, zone, defaults)
    expect(f.remindersAuto).toBe(true)
    expect(reminderField(f)).toBeUndefined()
  })
  it('sends [] (silent) once every chip is removed, and a changed list as it is', () => {
    const f = initialForm({ mode: 'new' } as EditorInit, zone, defaults)
    // ReminderChips' onChange: set({ reminders: r, remindersAuto: false })
    expect(reminderField({ ...f, reminders: [], remindersAuto: false })).toEqual([])
    expect(reminderField({ ...f, reminders: [30], remindersAuto: false })).toEqual([30])
  })
  it('a duplicate of an event without reminders starts from the defaults; a silent one stays silent', () => {
    expect(initialForm({ mode: 'duplicate', event: ev({ reminders: [] }) } as EditorInit, zone, defaults).remindersAuto).toBe(true)
    const silent = initialForm({ mode: 'duplicate', event: ev({ reminders: [], no_reminders: true }) } as EditorInit, zone, defaults)
    expect(reminderField(silent)).toEqual([])
    expect(reminderField(initialForm({ mode: 'duplicate', event: ev({ reminders: [15] }) } as EditorInit, zone, defaults))).toEqual([15])
    // Editing keeps the event's own list (only a change is sent).
    expect(initialForm({ mode: 'edit', event: ev({ reminders: [] }) } as EditorInit, zone, defaults).remindersAuto).toBeUndefined()
  })
  it('a copy payload (Keep both, Restore as new) leaves reminders out unless the event has some or is silent', () => {
    expect(toWrite({ summary: 'x', reminders: [] }).reminders).toBeUndefined()
    expect(toWrite({ summary: 'x', reminders: [], no_reminders: true }).reminders).toEqual([])
    expect(toWrite({ summary: 'x', reminders: [10] }).reminders).toEqual([10])
  })
})
