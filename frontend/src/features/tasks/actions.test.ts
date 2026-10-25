// ACT-SURFACES: web.tasks
import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TaskItem } from './model'

// The network: every request is recorded; a PATCH waits until the test lets it through.
const calls: { method: string; path: string; body: Record<string, unknown> | undefined; query?: Record<string, unknown> }[] = []
let releasePatches: () => void = () => undefined
let patchGate: Promise<void> = Promise.resolve()

vi.mock('./api', async (importOriginal) => {
  const real = await importOriginal<typeof import('./api')>()
  return {
    ...real,
    taskRequest: vi.fn(async (method: string, path: string, body?: Record<string, unknown>, query?: Record<string, unknown>) => {
      calls.push({ method, path, body, query })
      if (method === 'PATCH') {
        await patchGate
        const uid = decodeURIComponent(path.split('/').pop()!)
        return { ...task(uid), status: 'needs-action', completed: false, etag: `"${uid}-after-undo"` }
      }
      if (method === 'POST' && path === '/tasks/bulk') {
        const b = body as { uids: string[]; args: { dues: Record<string, string> } }
        return { ok: b.uids, failed: [], items: b.uids.map((u) => ({ ...task(u), due: b.args.dues[u], etag: `"${u}-shifted"` })) }
      }
      if (method === 'DELETE') return null
      if (method === 'POST' && path === '/tasks') {
        const b = body as { summary: string; parent?: string }
        return { ...task(`${b.summary}-new`), parent: b.parent ?? null, etag: `"${b.summary}-new-0"` }
      }
      throw new Error(`unexpected ${method} ${path}`)
    }),
  }
})

const { sendBulk, undoBulk, sendDelete, restoreDeleted } = await import('./actions')
const { findCached } = await import('./api')

function task(uid: string, p: Partial<TaskItem> = {}): TaskItem {
  return { uid, summary: uid, status: 'needs-action', due: '2026-10-08', due_all_day: true, list: 'https://dav/work/', etag: `"${uid}-0"`, ...p }
}

function client(items: TaskItem[]) {
  const qc = new QueryClient()
  qc.setQueryData(['tasks', 'list', 'smart', 'today'], { items })
  return qc
}

beforeEach(() => {
  calls.length = 0
  patchGate = new Promise((r) => { releasePatches = r })
})

describe('a batch after a batch undo', () => {
  it('waits for the undo writes, then sends the versions and dues of the newest copies', async () => {
    // The copies a bulk Complete left behind: completed, with the versions from before it.
    const stale = ['a', 'b', 'c'].map((u) => task(u, { status: 'completed', completed: true }))
    const qc = client(stale)

    const undo = undoBulk(qc, 'complete', stale.map((t) => ({ ...t, status: 'needs-action', completed: false })))
    // The next action starts while the undo is still writing, from the copies the bar holds.
    const next = sendBulk(qc, 'reschedule', stale, { duesFor: (t) => (t.due ? `${t.due}+7` : null) })
    await new Promise((r) => setTimeout(r, 20))
    expect(calls.map((c) => c.method)).toEqual(['PATCH', 'PATCH', 'PATCH'])

    releasePatches()
    await undo
    const { res } = await next
    expect(calls.map((c) => c.method)).toEqual(['PATCH', 'PATCH', 'PATCH', 'POST'])
    const sent = calls[3].body as { args: { etags: Record<string, string>; dues: Record<string, string> } }
    expect(sent.args.etags).toEqual({ a: '"a-after-undo"', b: '"b-after-undo"', c: '"c-after-undo"' })
    expect(sent.args.dues).toEqual({ a: '2026-10-08+7', b: '2026-10-08+7', c: '2026-10-08+7' })
    expect(res.ok).toEqual(['a', 'b', 'c'])
    // The server's copies replace the optimistic ones, versions included.
    expect(findCached(qc, 'a')).toMatchObject({ due: '2026-10-08+7', etag: '"a-shifted"', status: 'needs-action' })
  })

  it('keeps the undo copies when nothing else follows', async () => {
    const qc = client([task('d', { status: 'completed', completed: true })])
    const undo = undoBulk(qc, 'complete', [task('d')])
    releasePatches()
    await undo
    expect(findCached(qc, 'd')).toMatchObject({ status: 'needs-action', etag: '"d-after-undo"' })
  })
})

describe('task.delete in the write queues', () => {
  it('waits for a write still running on the task, then deletes with its new version', async () => {
    const qc = client([task('e', { status: 'completed', completed: true })])
    const undo = undoBulk(qc, 'complete', [task('e')])
    const del = sendDelete(qc, [task('e')])
    await new Promise((r) => setTimeout(r, 20))
    expect(calls.map((c) => c.method)).toEqual(['PATCH'])

    releasePatches()
    await undo
    expect(await del).toEqual([])
    expect(calls.map((c) => c.method)).toEqual(['PATCH', 'DELETE'])
    expect(calls[1].query).toMatchObject({ cascade: 'true', etag: '"e-after-undo"' })
    expect(findCached(qc, 'e')).toBeUndefined()
  })

  it('an undo after the deletes were sent waits for them, then recreates the tasks and their subtasks', async () => {
    const parent = task('p')
    const kid = task('k', { parent: 'p' })
    const qc = client([parent, kid])
    releasePatches()
    const del = sendDelete(qc, [parent], [kid])
    const back = restoreDeleted(qc, [parent], [kid])
    await del
    await back
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(['DELETE /tasks/p', 'POST /tasks', 'POST /tasks'])
    expect(calls[1].body).toMatchObject({ summary: 'p' })
    // The subtask goes under the recreated parent.
    expect(calls[2].body).toMatchObject({ summary: 'k', parent: 'p-new' })
  })
})
