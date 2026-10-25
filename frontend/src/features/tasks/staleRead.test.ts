// ACT-SURFACES: web.tasks
// The tasks-app e2e race (10 Oct 2026): a list read that started before "complete" came back
// landed after it with the old copy, so the Undo carried the old ETag and met a 409 against this
// tab's own write ("changed on another device"), and the task stayed completed.
import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TaskItem } from './model'

const calls: { method: string; etag?: unknown; body?: Record<string, unknown> }[] = []
// What the server holds of task x: its version and status.
let server: TaskItem

vi.mock('./api', async (importOriginal) => {
  const real = await importOriginal<typeof import('./api')>()
  return {
    ...real,
    taskRequest: vi.fn(async (method: string, _path: string, body?: Record<string, unknown>) => {
      calls.push({ method, etag: body?.etag, body })
      if (method !== 'PATCH') throw new Error(`unexpected ${method}`)
      if (body?.etag && body.etag !== server.etag) throw new real.ConflictError('The task changed on another device.', server)
      const n = Number(/x-(\d+)/.exec(server.etag ?? '')![1]) + 1
      const done = body?.completed === true
      server = { ...server, status: done ? 'completed' : 'needs-action', completed: done, etag: `"x-${n}"`, version: (server.version ?? 0) + 1 }
      return server
    }),
  }
})

const { patchTask } = await import('./actions')
const { findCached, freshen, upsertCached, writeMark } = await import('./api')
const { useTasksUI } = await import('./store')

const LIST = ['tasks', 'list', 'smart', 'today']
const x0: TaskItem = { uid: 'x', summary: 'E2E Finish me', status: 'needs-action', due: '2026-10-10', due_all_day: true, list: 'https://dav/l/', etag: '"x-1"' }

beforeEach(() => {
  calls.length = 0
  server = { ...x0 }
  useTasksUI.getState().setConflict(null)
})

describe('a list read from before a write', () => {
  it('takes the written copy of the tasks written since the read started', () => {
    const since = writeMark()
    const qc = new QueryClient()
    upsertCached(qc, { ...x0, status: 'completed', etag: '"x-2"' })
    // The read started before the write came back: its copy of x is the old one.
    expect(freshen([x0, { ...x0, uid: 'y' }], since).map((t) => t.etag)).toEqual(['"x-2"', '"x-1"'])
    // A read that started after the write is the server's newer word.
    expect(freshen([{ ...x0, etag: '"x-3"' }], writeMark())[0].etag).toBe('"x-3"')
  })
})

describe('Undo of complete right after a stale read landed', () => {
  it('goes through: a 409 against this tab own write is sent again on that version', async () => {
    const qc = new QueryClient()
    qc.setQueryData(LIST, { items: [x0] })
    const done = await patchTask(qc, x0, { completed: true })
    expect(done?.etag).toBe('"x-2"')
    // A read that read x before the complete committed lands now and puts the old copy back.
    qc.setQueryData(LIST, { items: [x0] })
    const back = await patchTask(qc, findCached(qc, 'x') ?? done!, { completed: false })
    expect(calls.map((c) => c.etag)).toEqual(['"x-1"', '"x-1"', '"x-2"'])
    expect(back?.status).toBe('needs-action')
    expect(server.status).toBe('needs-action')
    expect(useTasksUI.getState().conflict).toBeNull()
  })

  it('a real change from elsewhere still opens the conflict sheet', async () => {
    const qc = new QueryClient()
    qc.setQueryData(LIST, { items: [x0] })
    await patchTask(qc, x0, { completed: true })
    // Another device writes x after this tab did.
    server = { ...server, summary: 'Renamed elsewhere', etag: '"x-9"' }
    const back = await patchTask(qc, findCached(qc, 'x')!, { completed: false })
    expect(back).toBeNull()
    expect(calls.map((c) => c.etag)).toEqual(['"x-1"', '"x-2"'])
    expect(useTasksUI.getState().conflict?.uid).toBe('x')
    expect(server.status).toBe('completed')
  })
})

describe('two pane edits, the second made before the first came back', () => {
  it('sends the second from the version the first returned (its own write is no conflict)', async () => {
    const qc = new QueryClient()
    server = { ...x0, version: 5 }
    qc.setQueryData(LIST, { items: [server] })
    // Both are made from version 5: the pane has not heard back from the first yet.
    const a = patchTask(qc, server, { completed: true, base_version: 5 })
    const b = patchTask(qc, server, { completed: false, base_version: 5 })
    await Promise.all([a, b])
    expect(calls.map((c) => c.body?.base_version)).toEqual([5, 6])
    expect(useTasksUI.getState().conflict).toBeNull()
  })
})
