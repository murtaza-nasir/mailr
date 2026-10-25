// Mail actions made offline (archive, read, labels, star, trash and their opposites) wait here
// and are sent when the connection is back, in order, each with the idempotency key it got when
// it was made, so a change the server already took is never applied twice. Like Android's
// outbox (MailRepository.drain): a network failure stops the pass, a 401 pauses until sign-in,
// a server error retries later with a backoff and holds only the changes to the same
// conversations, a conversation that is gone (404) drops its change, and a refusal turns the
// change "failed" in Pending changes (Retry or Discard).
//
// Storage is IndexedDB ("mailr-offline", one record per change, per user), separate from the
// compose drafts (draftStore.ts, localStorage). Without IndexedDB (some private windows) the
// queue lives in memory for the session.
import { create } from 'zustand'
import type { ActionName, ActionRequest } from '../api/types'
import { t as tr } from '../i18n'

export interface PendingOp {
  /** The idempotency key, also the record's key. */
  id: string
  user: number
  at: number
  req: ActionRequest
  /** "Archive · 2 conversations": what Pending changes shows. */
  text: string
  state: 'queued' | 'failed'
  attempts: number
  error?: string
  /** Not before this time (a server error's backoff). */
  nextAt?: number
}

/** The actions that may wait for the connection. */
export const QUEUEABLE = new Set<ActionName>([
  'archive', 'inbox', 'trash', 'untrash', 'read', 'unread', 'star', 'unstar', 'add_labels', 'remove_labels', 'important', 'not_important',
])

export const MAX_ATTEMPTS = 5

const OPPOSITE: Partial<Record<ActionName, ActionName>> = {
  star: 'unstar', unstar: 'star', read: 'unread', unread: 'read', important: 'not_important', not_important: 'important',
  archive: 'inbox', inbox: 'archive', trash: 'untrash', untrash: 'trash', add_labels: 'remove_labels', remove_labels: 'add_labels',
}

const VERBS: Partial<Record<ActionName, string>> = {
  archive: tr('common.archive'), inbox: tr('common.moveToInbox'), trash: tr('common.delete2'), untrash: tr('common.restore'), read: tr('common.markAsRead'), unread: tr('common.markAsUnread'),
  star: tr('common.star'), unstar: tr('common.removeStar'), add_labels: tr('lib.offlineQueue.addLabel'), remove_labels: tr('common.removeLabel'), important: tr('common.markImportant'), not_important: tr('lib.offlineQueue.markNotImportant'),
}

export function describe(req: ActionRequest): string {
  const n = req.ids.length
  return `${VERBS[req.action] ?? req.action} · ${tr('lib.offlineQueue.conversations', { count: n })}`
}

export function queueable(req: ActionRequest, scope: string): boolean {
  return scope === 'conversations' && !req.query && req.ids.length > 0 && QUEUEABLE.has(req.action)
}

function sameSet(a: number[] = [], b: number[] = []): boolean {
  if (a.length !== b.length) return false
  const s = new Set(a)
  return b.every((x) => s.has(x))
}

/**
 * The queue after adding `op`. A change that undoes a waiting one for the same conversations
 * (star, then unstar) removes both: the server never needs to hear of either. The same change
 * twice is kept once.
 */
export function coalesce(ops: PendingOp[], op: PendingOp): PendingOp[] {
  const queued = ops.filter((o) => o.state === 'queued' && o.user === op.user && sameSet(o.req.ids, op.req.ids))
  const labelsMatch = (o: PendingOp) => sameSet(o.req.label_ids, op.req.label_ids)
  const last = queued[queued.length - 1]
  if (last && OPPOSITE[last.req.action] === op.req.action && labelsMatch(last)) return ops.filter((o) => o.id !== last.id)
  if (last && last.req.action === op.req.action && labelsMatch(last)) return ops
  return [...ops, op]
}

/** Exponential backoff for a server error: 5 s, 10 s, 20 s … up to 5 minutes. */
export function backoff(attempt: number): number {
  return Math.min(5 * 60_000, 5_000 * 2 ** Math.max(0, attempt - 1))
}

// ---------- storage ----------

const DB = 'mailr-offline'
const STORE = 'ops'
let memory: PendingOp[] = []
let dbp: Promise<IDBDatabase | null> | null = null

function openDb(): Promise<IDBDatabase | null> {
  if (dbp) return dbp
  dbp = new Promise((resolve) => {
    try {
      if (typeof indexedDB === 'undefined') return resolve(null)
      const r = indexedDB.open(DB, 1)
      r.onupgradeneeded = () => {
        if (!r.result.objectStoreNames.contains(STORE)) r.result.createObjectStore(STORE, { keyPath: 'id' }).createIndex('user', 'user')
      }
      r.onsuccess = () => resolve(r.result)
      r.onerror = () => resolve(null)
      r.onblocked = () => resolve(null)
    } catch {
      resolve(null)
    }
  })
  return dbp
}

function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T> | void): Promise<T | undefined> {
  return openDb().then(
    (db) =>
      new Promise<T | undefined>((resolve) => {
        if (!db) return resolve(undefined)
        try {
          const t = db.transaction(STORE, mode)
          const req = fn(t.objectStore(STORE))
          t.oncomplete = () => resolve(req ? (req.result as T) : undefined)
          t.onerror = () => resolve(undefined)
          t.onabort = () => resolve(undefined)
        } catch {
          resolve(undefined)
        }
      }),
  )
}

async function readAll(user: number): Promise<PendingOp[]> {
  const db = await openDb()
  if (!db) return memory.filter((o) => o.user === user)
  const all = ((await tx<PendingOp[]>('readonly', (s) => s.index('user').getAll(user))) ?? []) as PendingOp[]
  return all.sort((a, b) => a.at - b.at)
}

async function write(ops: PendingOp[], removed: string[]): Promise<void> {
  const db = await openDb()
  if (!db) {
    memory = [...memory.filter((o) => !removed.includes(o.id) && !ops.some((x) => x.id === o.id)), ...ops]
    return
  }
  await tx('readwrite', (s) => {
    for (const id of removed) s.delete(id)
    for (const o of ops) s.put(o)
  })
}

// ---------- the store ----------

interface PendingState {
  user: number | null
  ops: PendingOp[]
  draining: boolean
  /** The server said signed out while sending: the queue waits, whole, for sign-in. */
  paused: boolean
  open: boolean
  setOpen: (open: boolean) => void
}

export const usePending = create<PendingState>((set) => ({
  user: null,
  ops: [],
  draining: false,
  paused: false,
  open: false,
  setOpen: (open) => set({ open }),
}))

/** Load this user's waiting changes (the shell calls it once the session is known). */
export async function loadQueue(user: number): Promise<void> {
  const ops = await readAll(user)
  usePending.setState({ user, ops, paused: false })
}

/** Keep a change for later. Returns the queue entry (or null when it cancelled a waiting one). */
export async function enqueue(user: number, req: ActionRequest, id: string, now = Date.now()): Promise<PendingOp | null> {
  const op: PendingOp = { id, user, at: now, req, text: describe(req), state: 'queued', attempts: 0 }
  const before = usePending.getState().ops
  const after = coalesce(before, op)
  const removed = before.filter((o) => !after.includes(o)).map((o) => o.id)
  const added = after.filter((o) => !before.includes(o))
  usePending.setState({ ops: after })
  await write(added, removed)
  return added.includes(op) ? op : null
}

/** Pending changes → Discard (or Undo of a change that is still waiting). */
export async function discard(id: string): Promise<void> {
  usePending.setState((s) => ({ ops: s.ops.filter((o) => o.id !== id) }))
  await write([], [id])
}

/** Pending changes → Retry: back in the queue with a fresh count. */
export async function retry(id: string): Promise<void> {
  const op = usePending.getState().ops.find((o) => o.id === id)
  if (!op) return
  const next: PendingOp = { ...op, state: 'queued', attempts: 0, error: undefined, nextAt: undefined }
  usePending.setState((s) => ({ ops: s.ops.map((o) => (o.id === id ? next : o)), paused: false }))
  await write([next], [])
}

/** Forget every change of this user (sign-out after they were sent, or a different user). */
export async function clearQueue(user: number): Promise<void> {
  const ids = (await readAll(user)).map((o) => o.id)
  usePending.setState((s) => ({ ops: s.ops.filter((o) => o.user !== user) }))
  await write([], ids)
}

export type Send = (op: PendingOp) => Promise<void>
export interface SendError {
  status: number
}

export interface DrainResult {
  sent: number
  dropped: number
  failed: number
  /** Left waiting: offline, paused or backing off. */
  waiting: number
}

/**
 * Send the waiting changes in order. `send` throws an error with `status` (0 for the network).
 * A change whose conversations are held by an earlier one that is backing off waits too.
 */
export async function drain(send: Send, now: () => number = Date.now): Promise<DrainResult> {
  const st = usePending.getState()
  const res: DrainResult = { sent: 0, dropped: 0, failed: 0, waiting: 0 }
  if (st.draining || st.user == null) return res
  usePending.setState({ draining: true, paused: false })
  const blocked = new Set<number>()
  try {
    for (const op of usePending.getState().ops.filter((o) => o.state === 'queued' && o.user === st.user)) {
      if (op.req.ids.some((x) => blocked.has(x)) || (op.nextAt && op.nextAt > now())) {
        op.req.ids.forEach((x) => blocked.add(x))
        res.waiting++
        continue
      }
      try {
        await send(op)
        await discard(op.id)
        res.sent++
      } catch (e) {
        const status = (e as SendError).status ?? 0
        if (status === 0) {
          // Offline again: the rest waits for the next pass.
          res.waiting += usePending.getState().ops.filter((o) => o.state === 'queued').length
          break
        }
        if (status === 401) {
          usePending.setState({ paused: true })
          res.waiting += usePending.getState().ops.filter((o) => o.state === 'queued').length
          break
        }
        if (status === 404 || status === 410) {
          // The conversations are gone (deleted elsewhere): nothing left to change.
          await discard(op.id)
          res.dropped++
          continue
        }
        const message = (e as Error).message || tr('lib.offlineQueue.error', { status })
        const retryable = status >= 500 || status === 429 || status === 408
        const attempts = op.attempts + 1
        const next: PendingOp = retryable && attempts < MAX_ATTEMPTS
          ? { ...op, attempts, error: message, nextAt: now() + backoff(attempts) }
          : { ...op, attempts, error: message, state: 'failed' }
        usePending.setState((s) => ({ ops: s.ops.map((o) => (o.id === op.id ? next : o)) }))
        await write([next], [])
        op.req.ids.forEach((x) => blocked.add(x))
        if (next.state === 'failed') res.failed++
        else res.waiting++
      }
    }
  } finally {
    usePending.setState({ draining: false })
  }
  return res
}
