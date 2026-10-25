// Other people on a shared note or table: "Dana is
// editing" shows while another person's write arrived within the last 10 s, and "Dana stopped sharing
// this note" replaces a note that was unshared while open. api/events.ts feeds both from the live
// stream: the `author` of an item event, and the `unshared` notes event.

import { useEffect, useState } from 'react'
import { create } from 'zustand'
import { t as tr } from '../../i18n'

/** How long "Dana is editing" stays after their last write. */
export const PRESENCE_MS = 10_000

interface Presence {
  /** Item id (a note's or a table's public id) → the last other writer and when. */
  writers: Record<string, { id: number; name: string; at: number }>
  /** Item id → the name of the person who stopped sharing it with this viewer. */
  unshared: Record<string, string>
  /** Item id → the other people who have it open (presence events): person id → name, last beat. */
  here: Record<string, Record<number, { name: string; at: number }>>
  /** Checklist item id → the other person whose change to it just arrived ("Cleo" on the row). */
  touched: Record<string, { name: string; at: number }>
}

export const usePresence = create<Presence>(() => ({ writers: {}, unshared: {}, here: {}, touched: {} }))

/** How long a "here" lasts without the next beat (the server's TTL_S; notes/presence.py). */
export const HERE_MS = 40_000
/** How often an open shared item says it is still open. */
export const BEAT_MS = 15_000
/** How long another person's change marks an item row. */
export const TOUCH_MS = 4_000

/** A write by `author` to the note or table `id` arrived now. */
export function noteWriter(id: string, author: { id?: number; name?: string } | null | undefined): void {
  if (!id || !author?.id) return
  usePresence.setState((s) => ({ writers: { ...s.writers, [id]: { id: author.id!, name: author.name || tr('common.someone'), at: Date.now() } } }))
}

/** Another person's change to a checklist item arrived: the row shows their name for a moment. */
export function itemTouched(itemId: string, author: { id?: number; name?: string } | null | undefined): void {
  if (!itemId || !author?.id) return
  usePresence.setState((s) => ({ touched: { ...s.touched, [itemId]: { name: firstName(author.name || tr('common.someone')), at: Date.now() } } }))
}

export function firstName(name: string): string {
  return name.trim().split(/\s+/)[0] || name
}

export interface PresenceEvent {
  kind: 'note' | 'table'
  id: string
  person: { id: number; name: string }
  state: 'here' | 'left'
  hello?: boolean
}

/** A presence event from the live stream (api/events.ts). Returns true when it greets this viewer
 *  (a `hello`): the open item answers with its own beat at once. */
export function onPresence(ev: PresenceEvent, now = Date.now()): boolean {
  if (!ev?.id || !ev.person?.id) return false
  usePresence.setState((s) => {
    const cur = { ...(s.here[ev.id] ?? {}) }
    if (ev.state === 'left') delete cur[ev.person.id]
    else cur[ev.person.id] = { name: ev.person.name || tr('common.someone'), at: now }
    return { here: { ...s.here, [ev.id]: cur } }
  })
  return ev.state === 'here' && !!ev.hello
}

/** The other people who have the item open now, by first name (pure). */
export function hereNames(id: string, me: number | undefined, now = Date.now()): string[] {
  const m = usePresence.getState().here[id] ?? {}
  return Object.entries(m).filter(([pid, v]) => Number(pid) !== me && now - v.at < HERE_MS).map(([, v]) => firstName(v.name)).sort()
}

/** "Cleo is in this list", "Cleo and Dana are in this list", or ''. */
export function hereLine(names: string[], noun: 'list' | 'note' | 'table'): string {
  if (!names.length) return ''
  const who = names.length === 1 ? names[0] : names.length === 2 ? tr('notes.presence.and', { names: names[0], names2: names[1] }) : tr('notes.presence.andMore', { names: names[0], names2: names[1], value: names.length - 2 })
  return tr('notes.presence.areInThis', { count: names.length, who, noun })
}

export function noteUnshared(id: string, by: string): void {
  usePresence.setState((s) => ({ unshared: { ...s.unshared, [id]: by } }))
}

/** The other person writing the item now, or null (pure: the hook below re-renders when it ends). */
export function editingName(id: string, me: number | undefined, now = Date.now()): string | null {
  const w = usePresence.getState().writers[id]
  return w && w.id !== me && now - w.at < PRESENCE_MS ? w.name : null
}

/** "Dana is editing" for the open item, or null: someone else wrote it within PRESENCE_MS. */
export function useEditingPerson(id: string | null | undefined, me: number | undefined): string | null {
  const w = usePresence((s) => (id ? s.writers[id] : undefined))
  const [, tick] = useState(0)
  const live = !!w && w.id !== me && Date.now() - w.at < PRESENCE_MS
  useEffect(() => {
    if (!live || !w) return
    const t = window.setTimeout(() => tick((n) => n + 1), Math.max(50, PRESENCE_MS - (Date.now() - w.at)))
    return () => window.clearTimeout(t)
  }, [live, w])
  return live ? w!.name : null
}

export function useUnsharedBy(id: string | null | undefined): string | null {
  return usePresence((s) => (id ? s.unshared[id] ?? null : null))
}

/** Test helper. */
export function resetPresence(): void {
  usePresence.setState({ writers: {}, unshared: {}, here: {}, touched: {} })
}
