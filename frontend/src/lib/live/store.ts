// Live editing on the client: the change signal of the items people and
// agents edit at once. api/events.ts feeds the `item` and `editing` events of /events in here;
// editors subscribe to their item, and chat cards read the latest version.

import { assistantNameNow } from '../../features/rail/assistant/useAssistantName'
import { create } from 'zustand'
import { t as tr } from '../../i18n'

// `note`: only as the assistant's chat card; the Notes app follows its own `item` events (api/events.ts).
// `row`: a table row (features/tables).
export type LiveKind = 'draft' | 'event' | 'task' | 'memory' | 'rule' | 'note' | 'row'

export interface ItemEvent {
  kind: LiveKind
  id: string | number
  version: number | null
  by: string
  origin?: string | null
  deleted?: boolean
}

export interface EditingEvent {
  kind: LiveKind
  id: string | number
  by: string
  state: 'start' | 'end'
}

export const itemKey = (kind: LiveKind, id: string | number) => `${kind}:${id}`

/** Window event: the live stream (re)connected; open editors check their item's version. */
export const LIVE_RESYNC = 'mailr:live-resync'

/** How long an "editing" notice stays without its end (a lost event, a crashed write). */
export const EDITING_TIMEOUT_MS = 30_000

interface LiveState {
  versions: Record<string, { version: number | null; by: string; deleted?: boolean }>
  editing: Record<string, { by: string; at: number }>
  /** "Let me finish typing first" (assistant panel): the assistant's writes wait for a pause. */
  waitForTyping: boolean
  /** A turn asked with waitForTyping is running: the editors report typing meanwhile. */
  holding: boolean
}

export const useLive = create<LiveState>(() => ({ versions: {}, editing: {}, waitForTyping: false, holding: false }))

const listeners = new Map<string, Set<(e: ItemEvent) => void>>()
const timers = new Map<string, ReturnType<typeof setTimeout>>()

/** A live `item` event from the server (or a save of this tab, `local`). */
export function onItemEvent(e: ItemEvent) {
  const k = itemKey(e.kind, e.id)
  const cur = useLive.getState().versions[k]
  if (!e.deleted && cur?.version != null && e.version != null && e.version < cur.version) return
  useLive.setState((s) => ({ versions: { ...s.versions, [k]: { version: e.version, by: e.by, deleted: e.deleted } } }))
  for (const fn of listeners.get(k) ?? []) fn(e)
}

/** Record a version this tab saved itself (no event needed for its own windows). */
export function noteVersion(kind: LiveKind, id: string | number, version: number | null, by = 'user') {
  if (version == null) return
  const k = itemKey(kind, id)
  const cur = useLive.getState().versions[k]
  if (cur?.version != null && cur.version >= version) return
  useLive.setState((s) => ({ versions: { ...s.versions, [k]: { version, by } } }))
}

export function onEditingEvent(e: EditingEvent) {
  const k = itemKey(e.kind, e.id)
  const t = timers.get(k)
  if (t) clearTimeout(t)
  timers.delete(k)
  if (e.state === 'start') {
    useLive.setState((s) => ({ editing: { ...s.editing, [k]: { by: e.by, at: Date.now() } } }))
    timers.set(k, setTimeout(() => onEditingEvent({ ...e, state: 'end' }), EDITING_TIMEOUT_MS))
  } else if (useLive.getState().editing[k]) {
    useLive.setState((s) => {
      const editing = { ...s.editing }
      delete editing[k]
      return { editing }
    })
  }
}

export function subscribeItem(kind: LiveKind, id: string | number, fn: (e: ItemEvent) => void): () => void {
  const k = itemKey(kind, id)
  let set = listeners.get(k)
  if (!set) listeners.set(k, (set = new Set()))
  set.add(fn)
  return () => {
    set!.delete(fn)
    if (!set!.size) listeners.delete(k)
  }
}

/** Who is editing the item now ({by}), or null. */
export function useEditing(kind: LiveKind, id: string | number | null | undefined): { by: string } | null {
  return useLive((s) => (id == null ? null : s.editing[itemKey(kind, id)] ?? null))
}

export function useLatestVersion(kind: LiveKind, id: string | number | null | undefined): number | null {
  return useLive((s) => (id == null ? null : s.versions[itemKey(kind, id)]?.version ?? null))
}

/** Ambrose (the assistant's name), "another device", "the agent Claude Desktop", … for a `by` value. */
export function byName(by: string | null | undefined): string {
  if (!by || by === 'assistant') return assistantNameNow()
  if (by.startsWith('agent:')) return tr('lib.live.store.theAgent', { by: by.slice(6) || tr('lib.live.store.withAToken') })
  if (by === 'device') return tr('lib.live.store.anotherDevice')
  if (by === 'user') return tr('lib.live.store.anotherTab')
  return tr('lib.live.store.someoneElse')
}

export const NOUNS: Record<LiveKind, string> = { draft: 'draft', event: 'event', task: 'task', memory: 'memory', rule: 'filter', note: 'note', row: 'row' }

/** The banner text while an agent writes the item: "Ambrose is editing this draft…". */
export function editingText(kind: LiveKind, by: string): string {
  const who = by.startsWith('agent:') ? by.slice(6) || tr('lib.live.store.anAgent') : assistantNameNow()
  return tr('lib.live.store.isEditingThis', { who, nouns: NOUNS[kind] })
}

/** Test helper: forget everything. */
export function resetLive() {
  for (const t of timers.values()) clearTimeout(t)
  timers.clear()
  listeners.clear()
  useLive.setState({ versions: {}, editing: {}, waitForTyping: false, holding: false })
}
