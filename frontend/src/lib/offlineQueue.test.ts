import { beforeEach, describe, expect, it } from 'vitest'
import type { ActionRequest } from '../api/types'
import { backoff, clearQueue, coalesce, drain, enqueue, loadQueue, MAX_ATTEMPTS, queueable, retry, usePending, type PendingOp } from './offlineQueue'

const op = (id: string, req: ActionRequest, state: PendingOp['state'] = 'queued'): PendingOp => ({ id, user: 1, at: 0, req, text: '', state, attempts: 0 })

describe('coalesce', () => {
  it('a change that undoes the last waiting one for the same conversations removes both', () => {
    const q = [op('a', { ids: [1], action: 'star' })]
    expect(coalesce(q, op('b', { ids: [1], action: 'unstar' }))).toEqual([])
    expect(coalesce([op('a', { ids: [1, 2], action: 'archive' })], op('b', { ids: [2, 1], action: 'inbox' }))).toEqual([])
  })
  it('keeps changes for other conversations, other labels and repeated changes once', () => {
    const q = [op('a', { ids: [1], action: 'star' })]
    expect(coalesce(q, op('b', { ids: [2], action: 'unstar' }))).toHaveLength(2)
    expect(coalesce(q, op('b', { ids: [1], action: 'star' }))).toHaveLength(1)
    const lab = [op('a', { ids: [1], action: 'add_labels', label_ids: [5] })]
    expect(coalesce(lab, op('b', { ids: [1], action: 'remove_labels', label_ids: [6] }))).toHaveLength(2)
    expect(coalesce(lab, op('b', { ids: [1], action: 'remove_labels', label_ids: [5] }))).toHaveLength(0)
  })
  it('a failed change is not cancelled by a new one', () => {
    expect(coalesce([op('a', { ids: [1], action: 'star' }, 'failed')], op('b', { ids: [1], action: 'unstar' }))).toHaveLength(2)
  })
})

describe('queueable', () => {
  it('takes the list actions on loaded rows, not whole-list jobs or snooze', () => {
    expect(queueable({ ids: [1], action: 'archive' }, 'conversations')).toBe(true)
    expect(queueable({ ids: [1], action: 'add_labels', label_ids: [2] }, 'conversations')).toBe(true)
    expect(queueable({ ids: [1], action: 'snooze', until: 'x' }, 'conversations')).toBe(false)
    expect(queueable({ ids: [], action: 'archive', query: { view: 'inbox', q: '' } }, 'conversations')).toBe(false)
    expect(queueable({ ids: [1], action: 'read' }, 'messages')).toBe(false)
  })
})

describe('drain', () => {
  beforeEach(async () => {
    await loadQueue(1)
    await clearQueue(1)
    usePending.setState({ draining: false, paused: false })
  })

  const err = (status: number) => Object.assign(new Error(`status ${status}`), { status })

  it('sends in order, drops changes to deleted conversations, fails refusals', async () => {
    await enqueue(1, { ids: [1], action: 'archive' }, 'k1')
    await enqueue(1, { ids: [2], action: 'star' }, 'k2')
    await enqueue(1, { ids: [3], action: 'read' }, 'k3')
    const seen: string[] = []
    const r = await drain(async (o) => {
      seen.push(o.id)
      if (o.id === 'k2') throw err(404)
      if (o.id === 'k3') throw err(400)
    })
    expect(seen).toEqual(['k1', 'k2', 'k3'])
    expect(r).toMatchObject({ sent: 1, dropped: 1, failed: 1 })
    const left = usePending.getState().ops
    expect(left.map((o) => [o.id, o.state])).toEqual([['k3', 'failed']])
    await retry('k3')
    expect(usePending.getState().ops[0].state).toBe('queued')
  })

  it('stops on a network error and pauses on 401, keeping everything', async () => {
    await enqueue(1, { ids: [1], action: 'archive' }, 'k1')
    await enqueue(1, { ids: [2], action: 'archive' }, 'k2')
    const r = await drain(async () => {
      throw err(0)
    })
    expect(r.sent).toBe(0)
    expect(usePending.getState().ops).toHaveLength(2)
    await drain(async () => {
      throw err(401)
    })
    expect(usePending.getState().paused).toBe(true)
    expect(usePending.getState().ops).toHaveLength(2)
  })

  it('a server error backs off and holds later changes to the same conversation only', async () => {
    await enqueue(1, { ids: [1], action: 'archive' }, 'k1')
    await enqueue(1, { ids: [1], action: 'star' }, 'k2')
    await enqueue(1, { ids: [2], action: 'star' }, 'k3')
    const seen: string[] = []
    const r = await drain(async (o) => {
      seen.push(o.id)
      if (o.id === 'k1') throw err(503)
    }, () => 1000)
    expect(seen).toEqual(['k1', 'k3'])
    expect(r).toMatchObject({ sent: 1, waiting: 2 })
    const k1 = usePending.getState().ops.find((o) => o.id === 'k1')!
    expect(k1.nextAt).toBe(1000 + backoff(1))
    expect(k1.attempts).toBe(1)
  })

  it(`gives up after ${MAX_ATTEMPTS} server errors`, async () => {
    await enqueue(1, { ids: [1], action: 'archive' }, 'k1')
    let t = 0
    for (let i = 0; i < MAX_ATTEMPTS; i++) {
      t += 10 * 60_000
      await drain(async () => {
        throw err(500)
      }, () => t)
    }
    expect(usePending.getState().ops[0].state).toBe('failed')
  })
})
