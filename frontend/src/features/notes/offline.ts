// Lists that work offline in the web app and the installed PWA. Ticks,
// new items and item edits made with no connection wait here and are sent when it is back, in order,
// each with the idempotency key it got when it was made, as the mail queue does (lib/offlineQueue.ts):
// a network failure stops the pass, a 401 pauses until sign-in, a server error retries later with a
// backoff, an item or a list that is gone (404, 410) drops its change, and a refusal or a clash turns
// the change "failed" (Retry or Discard in the list's banner).
//
// While a change waits, the list shows it: `applyPending` lays the waiting changes over every copy
// the app reads (the server's answer, the service worker's stored copy), so a refetch never takes a
// tick back. New items carry ids made here (UUID v7), so a replay adds each once. A check carries the
// time of the tap (`device_at`): the server keeps the later tap.
//
// What needs the connection (moving items, sections, clearing checked items, usual items, the
// title) says so instead of failing later. Storage is IndexedDB ("mailr-notes-offline", one record
// per change, per user); without IndexedDB the queue lives in memory for the session.

import type { QueryClient } from '@tanstack/react-query'
import { create } from 'zustand'
import { ApiError, request } from '../../api/client'
import { toast } from '../../stores/ui'
import type { Item, Note, Qty } from './types'
import { t as tr } from '../../i18n'

export type NoteOpKind = 'check' | 'edit' | 'add' | 'delete'

export interface NoteOp {
  /** The idempotency key, also the record's key. */
  id: string
  user: number
  at: number
  note: string
  kind: NoteOpKind
  /** The item it changes (check, edit, delete). */
  item?: string
  method: 'POST' | 'PATCH' | 'DELETE'
  path: string
  body?: Record<string, unknown>
  /** "Tick milk": what the banner lists. */
  text: string
  state: 'queued' | 'failed'
  attempts: number
  error?: string
  nextAt?: number
  /** add: the items as the list shows them until the server has them. */
  items?: Item[]
}

export const MAX_ATTEMPTS = 5

export function backoff(attempt: number): number {
  return Math.min(5 * 60_000, 5_000 * 2 ** Math.max(0, attempt - 1))
}

const enc = encodeURIComponent

// ---------------------------------------------------------------- the overlay (pure)

/** The note as it will be once its waiting changes are through. */
export function applyPending(note: Note, ops: NoteOp[]): Note {
  const mine = ops.filter((o) => o.note === note.id)
  if (!mine.length || !note.items) return note
  let items = [...note.items]
  for (const op of mine) {
    if (op.kind === 'add') {
      for (const it of op.items ?? []) if (!items.some((i) => i.id === it.id)) items.push(it)
    } else if (op.kind === 'delete') {
      items = items.filter((i) => i.id !== op.item)
    } else {
      items = items.map((i) => (i.id !== op.item ? i : patched(i, op)))
    }
  }
  return { ...note, items }
}

/** One item with a waiting tick or edit applied. */
function patched(i: Item, op: NoteOp): Item {
  const b = op.body ?? {}
  return {
    ...i,
    ...(b.checked !== undefined ? { checked: !!b.checked, checked_at: b.checked ? String(b.device_at ?? new Date(op.at).toISOString()) : null } : {}),
    ...(typeof b.text === 'string' ? { text: b.text } : {}),
    ...(b.qty !== undefined ? { qty: (b.qty as Qty | null) ?? null } : {}),
    ...(b.extra !== undefined ? { extra: (b.extra as string | null) ?? null } : {}),
  }
}

/** The items with a change still waiting (the dashed edge). */
export function pendingItems(ops: NoteOp[], noteId: string): Set<string> {
  const out = new Set<string>()
  for (const o of ops) {
    if (o.note !== noteId) continue
    if (o.item) out.add(o.item)
    for (const i of o.items ?? []) out.add(i.id)
  }
  return out
}

/**
 * The queue after adding `op` (pure). A tick of an item whose tick is still waiting replaces it (the
 * server needs only the last tap); an edit or a delete of an item added offline changes the waiting
 * add instead, so the server hears of the item once, as it is.
 */
export function coalesce(ops: NoteOp[], op: NoteOp): NoteOp[] {
  const queued = (o: NoteOp) => o.state === 'queued' && o.user === op.user && o.note === op.note
  if (op.item) {
    const add = ops.find((o) => queued(o) && o.kind === 'add' && o.items?.some((i) => i.id === op.item))
    if (add && (op.kind === 'edit' || op.kind === 'check' || op.kind === 'delete')) {
      const items = op.kind === 'delete'
        ? (add.items ?? []).filter((i) => i.id !== op.item)
        : (add.items ?? []).map((i) => (i.id === op.item ? patched(i, op) : i))
      const body = { ...(add.body ?? {}), items: items.map(itemBody) }
      const next = { ...add, items, body, text: addText(items) }
      return items.length ? ops.map((o) => (o.id === add.id ? next : o)) : ops.filter((o) => o.id !== add.id)
    }
    if (op.kind === 'check') {
      const last = [...ops].reverse().find((o) => queued(o) && o.kind === 'check' && o.item === op.item)
      if (last) return [...ops.filter((o) => o.id !== last.id), op]
    }
  }
  return [...ops, op]
}

function itemBody(i: Item): Record<string, unknown> {
  return { id: i.id, text: i.text, qty: i.qty ? { amount: i.qty.amount, unit: i.qty.unit } : null, section_id: i.section_id, ...(i.checked ? { checked: true } : {}) }
}

function addText(items: Item[]): string {
  return items.length === 1 ? tr('notes.offline.add', { text: items[0].text }) : tr('notes.offline.addItems', { count: items.length })
}

// ---------------------------------------------------------------- making a change

export function checkOp(user: number, noteId: string, item: Item, checked: boolean, id: string, now = Date.now()): NoteOp {
  return {
    id, user, at: now, note: noteId, kind: 'check', item: item.id, method: 'PATCH',
    path: `/notes/${enc(noteId)}/items/${enc(item.id)}`,
    body: { checked, device_at: new Date(now).toISOString() },
    text: `${checked ? tr('notes.offline.tick') : tr('notes.offline.untick')} ${item.text}`, state: 'queued', attempts: 0,
  }
}

export function editOp(user: number, noteId: string, item: Item, patch: { text?: string; parse?: boolean; qty?: Qty | null; extra?: string | null }, id: string, now = Date.now()): NoteOp {
  return {
    id, user, at: now, note: noteId, kind: 'edit', item: item.id, method: 'PATCH',
    path: `/notes/${enc(noteId)}/items/${enc(item.id)}`,
    body: { ...patch, ...(item.version != null ? { base_version: item.version } : {}) },
    text: tr('notes.offline.change', { patchText: patch.text ?? item.text }), state: 'queued', attempts: 0,
  }
}

export function deleteOp(user: number, noteId: string, item: Item, id: string, now = Date.now()): NoteOp {
  return {
    id, user, at: now, note: noteId, kind: 'delete', item: item.id, method: 'DELETE',
    path: `/notes/${enc(noteId)}/items/${enc(item.id)}`, text: tr('notes.offline.delete', { text: item.text }), state: 'queued', attempts: 0,
  }
}

export function addOp(user: number, noteId: string, items: Item[], id: string, now = Date.now()): NoteOp {
  return {
    id, user, at: now, note: noteId, kind: 'add', method: 'POST', path: `/notes/${enc(noteId)}/items`,
    body: { items: items.map(itemBody) }, items, text: addText(items), state: 'queued', attempts: 0,
  }
}

// ---------------------------------------------------------------- storage

const DB = 'mailr-notes-offline'
const STORE = 'ops'
let memory: NoteOp[] = []
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

async function readAll(user: number): Promise<NoteOp[]> {
  const db = await openDb()
  if (!db) return memory.filter((o) => o.user === user)
  return new Promise((resolve) => {
    try {
      const req = db.transaction(STORE, 'readonly').objectStore(STORE).index('user').getAll(user)
      req.onsuccess = () => resolve(((req.result ?? []) as NoteOp[]).sort((a, b) => a.at - b.at))
      req.onerror = () => resolve([])
    } catch {
      resolve([])
    }
  })
}

async function write(put: NoteOp[], removed: string[]): Promise<void> {
  const db = await openDb()
  if (!db) {
    memory = [...memory.filter((o) => !removed.includes(o.id) && !put.some((x) => x.id === o.id)), ...put]
    return
  }
  await new Promise<void>((resolve) => {
    try {
      const t = db.transaction(STORE, 'readwrite')
      const s = t.objectStore(STORE)
      for (const id of removed) s.delete(id)
      for (const o of put) s.put(o)
      t.oncomplete = () => resolve()
      t.onerror = () => resolve()
      t.onabort = () => resolve()
    } catch {
      resolve()
    }
  })
}

// ---------------------------------------------------------------- the store

interface NotesQueue {
  user: number | null
  ops: NoteOp[]
  draining: boolean
  paused: boolean
}

export const useNotesQueue = create<NotesQueue>(() => ({ user: null, ops: [], draining: false, paused: false }))

/** This user's waiting list changes (the shell loads them once the session is known). */
export async function loadNotesQueue(user: number): Promise<void> {
  const ops = await readAll(user)
  useNotesQueue.setState({ user, ops, paused: false })
}

/** Is a change to this note still waiting? Then the next one waits behind it, in order. */
export function hasWaiting(noteId: string): boolean {
  return useNotesQueue.getState().ops.some((o) => o.note === noteId && o.state === 'queued')
}

/** Keep a change for later. */
export async function enqueue(op: NoteOp): Promise<void> {
  const before = useNotesQueue.getState().ops
  const after = coalesce(before, op)
  const removed = before.filter((o) => !after.includes(o)).map((o) => o.id)
  const put = after.filter((o) => !before.includes(o))
  useNotesQueue.setState({ ops: after })
  await write(put, removed)
}

export async function discard(id: string): Promise<void> {
  useNotesQueue.setState((s) => ({ ops: s.ops.filter((o) => o.id !== id) }))
  await write([], [id])
}

/** Retry: back in the queue with a fresh count. A clashing edit goes again without its base. */
export async function retry(id: string): Promise<void> {
  const op = useNotesQueue.getState().ops.find((o) => o.id === id)
  if (!op) return
  const body = op.body ? { ...op.body } : undefined
  if (body && 'base_version' in body) delete body.base_version
  const next: NoteOp = { ...op, body, state: 'queued', attempts: 0, error: undefined, nextAt: undefined }
  useNotesQueue.setState((s) => ({ ops: s.ops.map((o) => (o.id === id ? next : o)), paused: false }))
  await write([next], [])
}

export interface DrainResult { sent: number; dropped: number; failed: number; waiting: number }

export type Send = (op: NoteOp) => Promise<void>

/** Send the waiting changes in order. `send` throws an error with `status` (0 for the network). A
 *  change to a note held by an earlier one that is backing off waits too. */
export async function drain(send: Send, now: () => number = Date.now): Promise<DrainResult> {
  const st = useNotesQueue.getState()
  const res: DrainResult = { sent: 0, dropped: 0, failed: 0, waiting: 0 }
  if (st.draining || st.user == null) return res
  useNotesQueue.setState({ draining: true, paused: false })
  const blocked = new Set<string>()
  try {
    for (const op of useNotesQueue.getState().ops.filter((o) => o.state === 'queued' && o.user === st.user)) {
      if (!useNotesQueue.getState().ops.some((o) => o.id === op.id)) continue
      if (blocked.has(op.note) || (op.nextAt && op.nextAt > now())) {
        blocked.add(op.note)
        res.waiting++
        continue
      }
      try {
        await send(op)
        await discard(op.id)
        res.sent++
      } catch (e) {
        const status = (e as { status?: number }).status ?? 0
        if (status === 0) {
          res.waiting += useNotesQueue.getState().ops.filter((o) => o.state === 'queued').length
          break
        }
        if (status === 401) {
          useNotesQueue.setState({ paused: true })
          res.waiting += useNotesQueue.getState().ops.filter((o) => o.state === 'queued').length
          break
        }
        if (status === 404 || status === 410) {
          await discard(op.id)
          res.dropped++
          continue
        }
        const message = status === 409 ? tr('notes.offline.itChangedElsewhereMeanwhile') : (e as Error).message || tr('notes.offline.error', { status })
        const retryable = status >= 500 || status === 429 || status === 408
        const attempts = op.attempts + 1
        const next: NoteOp = retryable && attempts < MAX_ATTEMPTS
          ? { ...op, attempts, error: message, nextAt: now() + backoff(attempts) }
          : { ...op, attempts, error: message, state: 'failed' }
        useNotesQueue.setState((s) => ({ ops: s.ops.map((o) => (o.id === op.id ? next : o)) }))
        await write([next], [])
        blocked.add(op.note)
        if (next.state === 'failed') res.failed++
        else res.waiting++
      }
    }
  } finally {
    useNotesQueue.setState({ draining: false })
  }
  return res
}

/** Send now (back online, back in view, every 10 s while something waits). */
export async function sendNotesQueue(qc: QueryClient): Promise<DrainResult> {
  const r = await drain((op) => request(op.path, { method: op.method, body: op.body as never, idempotencyKey: op.id }).then(() => undefined))
  if (r.sent || r.dropped || r.failed) void qc.invalidateQueries({ queryKey: ['notes'] })
  if (r.failed) toast({ message: tr('notes.offline.listChangesCouldNotBe', { count: r.failed }), error: true })
  else if (r.sent) toast({ message: tr('notes.offline.offlineListChangesWereSent', { count: r.sent }) })
  return r
}

/** Was this failure the network (offline, a dropped line)? Then the change waits. */
export function isNetwork(e: unknown): boolean {
  return e instanceof ApiError && e.status === 0 && e.kind !== 'timeout'
}

/** The words for a change that needs the connection. */
export function needsConnection(what: string): void {
  toast({ message: tr('notes.offline.youAreOfflineWhenYou', { what }), duration: 6000 })
}

export function offlineNow(): boolean {
  return typeof navigator !== 'undefined' && navigator.onLine === false
}
