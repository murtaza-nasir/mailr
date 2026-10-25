import { afterEach, describe, expect, it } from 'vitest'
import { useCalUI } from '../calendar/store'
import { useTasksUI } from '../tasks/store'
import { appContext } from './appContext'

afterEach(() => {
  useCalUI.getState().set({ focus: null, slot: null })
  useTasksUI.getState().clearSelection()
  useTasksUI.getState().setFocused(null)
})

describe('appContext', () => {
  it('is empty outside the Calendar and Tasks apps', () => {
    expect(appContext('/mail/inbox', '')).toEqual({})
  })
  it('names the focused event ("this meeting"), the view and the slot', () => {
    useCalUI.getState().set({ focus: 'abc@x.org|2026-10-02T15:00:00+00:00', slot: { day: '2026-10-02', min: 570 } })
    const { calendar } = appContext('/calendar/week/2026-10-01', '')
    expect(calendar).toMatchObject({
      view: 'week', date: '2026-10-01',
      focused_event: { uid: 'abc@x.org', occurrence: '2026-10-02T15:00:00+00:00' },
      selected_slot: { start: '2026-10-02T09:30', all_day: false },
    })
    expect(appContext('/calendar/event/u1/2026-10-05', '').calendar?.focused_event).toEqual({ uid: 'u1', occurrence: '2026-10-05' })
  })
  it('names the list and the selected tasks ("these tasks"), at most 50', () => {
    useTasksUI.getState().setSelection(Array.from({ length: 60 }, (_, i) => `t${i}`))
    useTasksUI.getState().setFocused('t3')
    const { tasks } = appContext('/tasks/today', '')
    expect(tasks).toMatchObject({ smart: 'today', focused: 't3' })
    expect((tasks?.selected as string[]).length).toBe(50)
    expect(appContext('/tasks/t/phone/task/u9', '').tasks).toMatchObject({ tag: 'phone', focused: 'u9' })
  })
})
