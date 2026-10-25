// A local copy of compose text the server has not confirmed yet. Each open compose form
// writes its payload here while a save is pending or failing; a successful server save removes
// it, so the server draft stays the source of truth once saved. After a reload, a crash or a
// sign-out, the shell reopens what is left here as "Restored unsent draft".
//
// Storage is localStorage, and every access is wrapped: private windows and full quotas throw.
// Records belong to one user (owner) and one tab; another tab's records are only restored once
// that tab has stopped beating (closed or crashed).

import type { ComposePayload } from '../api/types'

export interface LocalDraft {
  localId: string
  /** The signed-in user's id: a shared browser never shows one user's text to another. */
  owner: number
  /** The tab that wrote it (tabId()), so two open tabs do not restore each other's drafts. */
  tab: string
  draftId: number | null
  conversationId: number | null
  payload: ComposePayload
  updatedAt: number
}

export type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

export const DRAFTS_KEY = 'mailr.localDrafts.v1'
export const TABS_KEY = 'mailr.localDraftTabs.v1'
const TAB_ID_KEY = 'mailr.tabId'
/** A tab that has not beaten for this long is closed or crashed. */
export const TAB_STALE_MS = 20_000
/** Local copies older than this are dropped. */
export const MAX_AGE_MS = 14 * 86_400_000

function local(): DraftStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

function readJson<T>(s: DraftStorage | null, key: string): T | null {
  if (!s) return null
  try {
    const raw = s.getItem(key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

function writeJson(s: DraftStorage | null, key: string, value: unknown): boolean {
  if (!s) return false
  try {
    s.setItem(key, JSON.stringify(value))
    return true
  } catch {
    return false
  }
}

export function readLocalDrafts(s: DraftStorage | null = local()): Record<string, LocalDraft> {
  const all = readJson<Record<string, LocalDraft>>(s, DRAFTS_KEY)
  return all && typeof all === 'object' && !Array.isArray(all) ? all : {}
}

export function hasLocalDraft(localId: string | undefined, s: DraftStorage | null = local()): boolean {
  return !!localId && localId in readLocalDrafts(s)
}

/** Write (or replace) one record. False when storage is unavailable or full. */
export function saveLocalDraft(d: LocalDraft, s: DraftStorage | null = local()): boolean {
  const all = readLocalDrafts(s)
  all[d.localId] = d
  return writeJson(s, DRAFTS_KEY, all)
}

export function removeLocalDraft(localId: string, s: DraftStorage | null = local()): void {
  const all = readLocalDrafts(s)
  if (!(localId in all)) return
  delete all[localId]
  if (Object.keys(all).length) writeJson(s, DRAFTS_KEY, all)
  else {
    try {
      s?.removeItem(DRAFTS_KEY)
    } catch {
      /* ignore */
    }
  }
}

/** Record that tab `tab` is alive now. */
export function beat(tab: string, now: number, s: DraftStorage | null = local()): void {
  const tabs = readJson<Record<string, number>>(s, TABS_KEY) ?? {}
  for (const [k, t] of Object.entries(tabs)) if (now - t > TAB_STALE_MS * 3) delete tabs[k]
  tabs[tab] = now
  writeJson(s, TABS_KEY, tabs)
}

/** The user's records this tab may reopen, oldest first: its own, and those of tabs that stopped
 * beating. Expired records are pruned on the way. */
export function restorableDrafts(owner: number, tab: string, now: number, s: DraftStorage | null = local()): LocalDraft[] {
  const all = readLocalDrafts(s)
  const tabs = readJson<Record<string, number>>(s, TABS_KEY) ?? {}
  let pruned = false
  const out: LocalDraft[] = []
  for (const [id, d] of Object.entries(all)) {
    if (!d || typeof d !== 'object' || !d.payload || now - (d.updatedAt ?? 0) > MAX_AGE_MS) {
      delete all[id]
      pruned = true
      continue
    }
    if (d.owner !== owner) continue
    const last = tabs[d.tab]
    const alive = d.tab !== tab && last !== undefined && now - last < TAB_STALE_MS
    if (!alive) out.push(d)
  }
  if (pruned) writeJson(s, DRAFTS_KEY, all)
  return out.sort((a, b) => a.updatedAt - b.updatedAt)
}

let memTabId: string | null = null

/** This tab's id: kept in sessionStorage, so it survives a reload of the same tab. */
export function tabId(): string {
  try {
    const have = sessionStorage.getItem(TAB_ID_KEY)
    if (have) return have
    const id = newLocalId()
    sessionStorage.setItem(TAB_ID_KEY, id)
    return id
  } catch {
    memTabId ??= newLocalId()
    return memTabId
  }
}

export function newLocalId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `l${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`
  }
}

// ---------- open forms ----------

export interface OpenForm {
  /** True while the form holds text the server has not confirmed. */
  unsaved: () => boolean
  /** Write the local copy now: only unsaved text, or (all) any text the form holds. */
  flush: (all: boolean) => void
}

const forms = new Map<string, OpenForm>()
let guardInstalled = false

function installUnloadGuard() {
  if (guardInstalled || typeof window === 'undefined') return
  guardInstalled = true
  window.addEventListener('beforeunload', (e) => {
    flushAllDrafts(false)
    if (!hasUnsavedDrafts()) return
    e.preventDefault()
    // Older browsers show the prompt only when returnValue is set.
    e.returnValue = ''
  })
  window.addEventListener('pagehide', () => flushAllDrafts(false))
}

export function registerOpenForm(localId: string, form: OpenForm): () => void {
  installUnloadGuard()
  forms.set(localId, form)
  return () => {
    if (forms.get(localId) === form) forms.delete(localId)
  }
}

export function flushAllDrafts(all: boolean): void {
  for (const f of forms.values()) {
    try {
      f.flush(all)
    } catch {
      /* a broken form must not stop the others */
    }
  }
}

export function hasUnsavedDrafts(): boolean {
  for (const f of forms.values()) if (f.unsaved()) return true
  return false
}

export function openFormIds(): Set<string> {
  return new Set(forms.keys())
}
