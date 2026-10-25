// Audio of a browser recording, kept in IndexedDB while it is being made (recording.record), so a
// closed or crashed tab loses at most the last couple of seconds. Each session has a row with its
// start, length, a heartbeat and the dialog's form; its audio is a run of chunks. A session is
// deleted after its upload or when the user discards it. Without IndexedDB (a private window in
// some browsers) every call quietly does nothing and the recording lives in memory only.

import type { RecorderForm } from './recorderStore'

const DB = 'mailr-recorder'
const VERSION = 1

export interface SessionRow {
  id: string
  startedAt: number
  /** The last chunk's time: a session written to within LIVE_MS belongs to a tab that is still recording. */
  heartbeat: number
  durationMs: number
  bytes: number
  mime: string
  /** The recording is finished (stopped) and waits for its upload. */
  stopped: boolean
  form: RecorderForm | null
}

export const LIVE_MS = 15_000

let dbp: Promise<IDBDatabase | null> | null = null

function db(): Promise<IDBDatabase | null> {
  dbp ??= new Promise((resolve) => {
    try {
      if (typeof indexedDB === 'undefined') return resolve(null)
      const req = indexedDB.open(DB, VERSION)
      req.onupgradeneeded = () => {
        const d = req.result
        if (!d.objectStoreNames.contains('sessions')) d.createObjectStore('sessions', { keyPath: 'id' })
        if (!d.objectStoreNames.contains('chunks')) {
          const c = d.createObjectStore('chunks', { autoIncrement: true })
          c.createIndex('session', 'session')
        }
      }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => resolve(null)
      req.onblocked = () => resolve(null)
    } catch {
      resolve(null)
    }
  })
  return dbp
}

function done(tx: IDBTransaction): Promise<boolean> {
  return new Promise((resolve) => {
    tx.oncomplete = () => resolve(true)
    tx.onerror = () => resolve(false)
    tx.onabort = () => resolve(false)
  })
}

async function run(stores: string[], mode: IDBTransactionMode, fn: (tx: IDBTransaction) => void): Promise<boolean> {
  const d = await db()
  if (!d) return false
  try {
    const tx = d.transaction(stores, mode)
    fn(tx)
    return await done(tx)
  } catch {
    return false
  }
}

export function newSessionId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

export function putSession(row: SessionRow): Promise<boolean> {
  return run(['sessions'], 'readwrite', (tx) => tx.objectStore('sessions').put(row))
}

export async function patchSession(id: string, patch: Partial<SessionRow>): Promise<boolean> {
  const row = await getSession(id)
  if (!row) return false
  return putSession({ ...row, ...patch })
}

export async function getSession(id: string): Promise<SessionRow | null> {
  const d = await db()
  if (!d) return null
  return new Promise((resolve) => {
    try {
      const req = d.transaction('sessions').objectStore('sessions').get(id)
      req.onsuccess = () => resolve((req.result as SessionRow | undefined) ?? null)
      req.onerror = () => resolve(null)
    } catch {
      resolve(null)
    }
  })
}

export async function listSessions(): Promise<SessionRow[]> {
  const d = await db()
  if (!d) return []
  return new Promise((resolve) => {
    try {
      const req = d.transaction('sessions').objectStore('sessions').getAll()
      req.onsuccess = () => resolve((req.result as SessionRow[]) ?? [])
      req.onerror = () => resolve([])
    } catch {
      resolve([])
    }
  })
}

/** One more piece of audio, with the session's heartbeat, length and size in the same write. */
export function appendChunk(id: string, seq: number, blob: Blob, durationMs: number, bytes: number): Promise<boolean> {
  return run(['sessions', 'chunks'], 'readwrite', (tx) => {
    tx.objectStore('chunks').add({ session: id, seq, blob })
    const s = tx.objectStore('sessions')
    const req = s.get(id)
    req.onsuccess = () => {
      const row = req.result as SessionRow | undefined
      if (row) s.put({ ...row, heartbeat: Date.now(), durationMs, bytes })
    }
  })
}

/** The session's audio, its chunks in order. */
export async function readAudio(id: string, mime: string): Promise<Blob | null> {
  const d = await db()
  if (!d) return null
  return new Promise((resolve) => {
    try {
      const req = d.transaction('chunks').objectStore('chunks').index('session').getAll(id)
      req.onsuccess = () => {
        const rows = ((req.result as { seq: number; blob: Blob }[]) ?? []).sort((a, b) => a.seq - b.seq)
        resolve(rows.length ? new Blob(rows.map((r) => r.blob), { type: mime }) : null)
      }
      req.onerror = () => resolve(null)
    } catch {
      resolve(null)
    }
  })
}

export function deleteSession(id: string): Promise<boolean> {
  return run(['sessions', 'chunks'], 'readwrite', (tx) => {
    tx.objectStore('sessions').delete(id)
    const idx = tx.objectStore('chunks').index('session')
    const req = idx.openKeyCursor(IDBKeyRange.only(id))
    req.onsuccess = () => {
      const c = req.result
      if (!c) return
      tx.objectStore('chunks').delete(c.primaryKey)
      c.continue()
    }
  })
}
