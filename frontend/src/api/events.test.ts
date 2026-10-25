import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import { connectorNews, FLUSH_MS, flushDelay, invalidateFor, nextStreak } from './events'
import { qk } from './hooks'

function seeded() {
  const qc = new QueryClient()
  const keys = {
    range: qk.calendar.range('2026-10-01T05:00:00Z', '2026-10-08T05:00:00Z'),
    agenda: qk.calendar.agenda('2026-10-01T05:00:00.000Z'),
    event: qk.calendar.event('u1', '2026-10-01T15:00:00+00:00'),
    calendars: qk.calendar.calendars,
    taskList: qk.tasks.list({ smart: 'today' }),
    panelTasks: ['tasks'],
    taskCounts: qk.tasks.counts,
    tasksOnGrid: qk.tasks.onCalendar('a', 'b'),
    active: qk.reminders.active,
    remSettings: qk.reminders.settings,
    labels: qk.labels,
  }
  for (const k of Object.values(keys)) qc.setQueryData(k, [])
  const stale = () => Object.fromEntries(Object.entries(keys).map(([n, k]) => [n, !!qc.getQueryState(k)?.isInvalidated]))
  return { qc, stale }
}

describe('live changes for calendar, tasks and reminders', () => {
  it('an event change refreshes the calendar and the due reminders, nothing else', () => {
    const { qc, stale } = seeded()
    invalidateFor(qc, new Set(['event']))
    expect(stale()).toEqual({
      range: true, agenda: true, event: true, calendars: true, taskList: false, panelTasks: false, taskCounts: false,
      tasksOnGrid: false, active: true, remSettings: false, labels: false,
    })
  })
  it('a task change refreshes every task view, the grid\'s task chips included', () => {
    const { qc, stale } = seeded()
    invalidateFor(qc, new Set(['task']))
    expect(stale()).toMatchObject({ taskList: true, panelTasks: true, taskCounts: true, tasksOnGrid: true, active: true, range: false, labels: false })
  })
  it('a calendar (collection) change refreshes both apps', () => {
    const { qc, stale } = seeded()
    invalidateFor(qc, new Set(['calendar']))
    expect(stale()).toMatchObject({ range: true, calendars: true, taskList: true, active: true, remSettings: true, labels: false })
  })
  it('a reminder change refreshes only reminders', () => {
    const { qc, stale } = seeded()
    invalidateFor(qc, new Set(['reminder']))
    expect(stale()).toMatchObject({ active: true, remSettings: true, range: false, taskList: false })
  })
  it('entities the client does not know are ignored', () => {
    const { qc, stale } = seeded()
    expect(() => invalidateFor(qc, new Set(['journal', 'freebusy']))).not.toThrow()
    expect(Object.values(stale()).every((v) => !v)).toBe(true)
  })
})

describe('live changes for mail', () => {
  function mail() {
    const qc = new QueryClient()
    const keys = {
      list: qk.conversationList({ view: 'inbox' } as never),
      open: qk.conversation(7),
      other: qk.conversation(8),
      labels: qk.labels,
      counts: ['labels', 'sidebar-counts'],
      inboxCount: ['labels', 'inbox-count'],
    }
    for (const k of Object.values(keys)) qc.setQueryData(k, [])
    const stale = () => Object.fromEntries(Object.entries(keys).map(([n, k]) => [n, !!qc.getQueryState(k)?.isInvalidated]))
    return { qc, stale }
  }
  it('a change that names its conversations refreshes only those, and leaves the counts to the counts event', () => {
    const { qc, stale } = mail()
    invalidateFor(qc, new Set(['conversation']), { conversations: [7], countsFollow: true })
    expect(stale()).toEqual({ list: true, open: true, other: false, labels: false, counts: false, inboxCount: false })
  })
  it('without the live stream (catching up) the label numbers refetch, and every conversation', () => {
    const { qc, stale } = mail()
    invalidateFor(qc, new Set(['conversation']))
    expect(stale()).toEqual({ list: true, open: true, other: true, labels: true, counts: true, inboxCount: true })
  })
  it('a label change with the live stream refetches the label list only', () => {
    const { qc, stale } = mail()
    invalidateFor(qc, new Set(['label']), { countsFollow: true })
    expect(stale()).toMatchObject({ labels: true, counts: false, inboxCount: false, list: false })
  })
  it('moved messages may belong to any conversation', () => {
    const { qc, stale } = mail()
    invalidateFor(qc, new Set(['message', 'conversation']), { conversations: [7], countsFollow: true })
    expect(stale()).toMatchObject({ open: true, other: true })
  })
})

describe('connector events', () => {
  it('refetch only for news', () => {
    const qc = new QueryClient()
    qc.setQueryData(['connectors'], [{ id: 3, status: 'idle', status_detail: 'Up to date' }])
    expect(connectorNews(qc, { type: 'connector', id: 3, status: 'idle', detail: 'Up to date' })).toBe(false)
    expect(connectorNews(qc, { type: 'connector', id: 3, status: 'syncing', detail: 'Connecting…' })).toBe(true)
    expect(connectorNews(qc, { type: 'connector', id: 4, status: 'idle', detail: '' })).toBe(true)
  })
})

describe('coalescing a steady stream of changes', () => {
  it('widens the window while refetches follow each other, up to 3 s, and resets after a quiet spell', () => {
    let streak = 0
    const delays: number[] = []
    for (let i = 0; i < 5; i++) {
      delays.push(flushDelay(streak))
      streak = nextStreak(streak, delays[delays.length - 1] + 50)
    }
    expect(delays).toEqual([FLUSH_MS, 800, 1600, 3000, 3000])
    expect(nextStreak(3, 10_000)).toBe(0)
    expect(flushDelay(nextStreak(3, 10_000))).toBe(FLUSH_MS)
  })
})

describe('live changes for notes and tables', () => {
  it('a burst of note and table events refetches each family once', () => {
    const qc = new QueryClient()
    const seen: string[] = []
    qc.invalidateQueries = ((f?: { queryKey?: readonly unknown[] }) => {
      seen.push(String(f?.queryKey?.[0]))
      return Promise.resolve()
    }) as QueryClient['invalidateQueries']
    invalidateFor(qc, new Set(['notes', 'tables']))
    expect(seen.filter((k) => k === 'notes')).toHaveLength(1)
    expect(seen.filter((k) => k === 'tables')).toHaveLength(1)
    expect(seen).not.toContain('conversations')
  })
})
