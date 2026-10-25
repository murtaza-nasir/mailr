// The Notes app's writes: one function per action id, each through `write`
// (the request, the refresh and the undo toast). The functions take the query client, so the grid,
// the editor, the side panel and the palette share them.

import type { QueryClient } from '@tanstack/react-query'
import { ApiError, getCsrf, request } from '../../api/client'
import { CLIENT_ID } from '../../lib/live/clientId'
import { toast } from '../../stores/ui'
import { noteKeys, notePathApi, offerUndo, patchCached, write } from './api'
import { uuid7 } from './model'
import { parseItems } from '../../lib/itemadd'
import { addOp, checkOp, deleteOp, editOp, enqueue, hasWaiting, isNetwork, needsConnection, offlineNow, sendNotesQueue, useNotesQueue, type NoteOp } from './offline'
import type { Item, Note, NoteKind, NoteLink, NoteTemplate, Qty, Section, Undo } from './types'
import { t } from '../../i18n'

type R<T = object> = T & { undo?: Undo | null }
const P = notePathApi
const enc = encodeURIComponent

// ---------------------------------------------------------------- serial writes per note

// Writes to one note go one after another, each with the version the previous one returned, so
// quick edits never race each other into a conflict.
const queues = new Map<string, Promise<unknown>>()
function serial<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const run = (queues.get(key) ?? Promise.resolve()).catch(() => undefined).then(fn)
  queues.set(key, run)
  void run.finally(() => queues.get(key) === run && queues.delete(key)).catch(() => undefined)
  return run
}

const versions = new Map<string, number>()
/** The newest version of a note this tab knows (a save's answer, or the last read). */
export function noteVersion(qc: QueryClient, id: string): number | undefined {
  return versions.get(id) ?? qc.getQueryData<Note>(noteKeys.note(id))?.version
}
export function rememberVersion(id: string, v: number | undefined) {
  if (v != null) versions.set(id, Math.max(v, versions.get(id) ?? 0))
}

// ---------------------------------------------------------------- notes

export interface CreateIn {
  kind: NoteKind
  title?: string
  body?: string
  lines?: string
  color?: string | null
  labels?: string[]
  shopping?: boolean
  pinned?: boolean
  /** A list of one of the user's groups ("New list in Household"): every member edits it. */
  group?: string
}

// ACT note.create
export async function createNote(qc: QueryClient, body: CreateIn, opts: { quiet?: boolean } = {}): Promise<Note> {
  return (await createNoteWithUndo(qc, body, opts)).note
}

/** note.create, with the server's undo, for a caller that makes its own toast (Open and Undo). */
export async function createNoteWithUndo(qc: QueryClient, body: CreateIn, opts: { quiet?: boolean } = {}): Promise<{ note: Note; undo: Undo | null }> {
  const r = await write<R<{ note: Note }>>(qc, 'POST', '/notes', body, { quiet: opts.quiet })
  rememberVersion(r.note.id, r.note.version)
  return { note: r.note, undo: r.undo ?? null }
}

/** A text note from a recording, written by the server from what mailr mirrors (title, date,
 *  people, summary, action items) and linked to it; local-only when the recording is.
 *  ACT note.from_recording */
export async function noteFromRecording(qc: QueryClient, ref: { connector_id: number; recording_id: number }): Promise<Note> {
  const r = await write<R<{ note: Note }>>(qc, 'POST', '/notes/from-recording', ref, { quiet: true })
  rememberVersion(r.note.id, r.note.version)
  return r.note
}

export type NoteFields = Partial<Pick<Note, 'title' | 'body' | 'color' | 'shopping' | 'checked_at_bottom' | 'suggest_usual' | 'local_only' | 'top_order_mode'>>

/** PATCH /notes/{id} with the version the editor started from. A 409 is thrown (the editor shows the bar). */
// ACT note.edit, ACT note.color
export function patchNote(qc: QueryClient, id: string, fields: NoteFields, opts: { label?: string; quiet?: boolean; force?: boolean } = {}): Promise<Note> {
  // A note's own fields need the connection (only a list's items wait offline, offline.ts).
  if (offlineNow()) {
    if (!opts.quiet) needsConnection(t('notes.actions.thisChangeIsMade'))
    return Promise.reject(new ApiError(0, t('notes.actions.youAreOffline'), undefined, 'network'))
  }
  patchCached(qc, id, (n) => ({ ...n, ...fields }))
  return serial(id, async () => {
    const body = { ...fields, ...(opts.force ? {} : { base_version: noteVersion(qc, id) }) }
    const r = await write<R<{ note: Note }>>(qc, 'PATCH', P(id), body, { quiet: opts.quiet, label: opts.label, undo: !opts.quiet })
    rememberVersion(id, r.note.version)
    qc.setQueryData(noteKeys.note(id), (old: Note | undefined) => (old ? { ...old, ...r.note, items: r.note.items ?? old.items, sections: r.note.sections ?? old.sections } : r.note))
    return r.note
  })
}

/** A list repeats (weekly on a day, every N days) or stops; the toast offers Undo (notes/repeat.py).
 *  ACT note.repeat */
export function setRepeat(qc: QueryClient, id: string, rule: string | null): Promise<Note> {
  if (offlineRefuses(t('notes.actions.repeatIsSet'))) return Promise.reject(new ApiError(0, t('notes.actions.youAreOffline'), undefined, 'network'))
  return serial(id, async () => {
    const label = rule ? t('notes.actions.repeatSet') : t('notes.actions.repeatOff')
    const r = await write<R<{ note: Note }>>(qc, 'PATCH', P(id), { repeat: rule }, { label })
    rememberVersion(id, r.note.version)
    qc.setQueryData(noteKeys.note(id), (old: Note | undefined) => (old ? { ...old, ...r.note, items: r.note.items ?? old.items, sections: r.note.sections ?? old.sections } : r.note))
    return r.note
  })
}

export interface StateIn {
  pinned?: boolean
  archived?: boolean
  labels?: string[]
  add_labels?: string[]
  remove_labels?: string[]
  before?: string
  after?: string
  seen_version?: number
  /** Opened now: the "changed since you looked" dot goes. */
  seen?: boolean
}

// ACT note.pin, ACT note.archive, ACT note.label, ACT note.reorder
export async function setState(qc: QueryClient, id: string, body: StateIn, label?: string): Promise<void> {
  patchCached(qc, id, (n) => ({
    ...n,
    state: {
      ...n.state,
      ...(body.pinned !== undefined ? { pinned: body.pinned, archived: body.pinned ? false : n.state.archived } : {}),
      ...(body.archived !== undefined ? { archived: body.archived, pinned: body.archived ? false : n.state.pinned } : {}),
      ...(body.labels ? { labels: body.labels } : {}),
      ...(body.add_labels ? { labels: [...new Set([...n.state.labels, ...body.add_labels])] } : {}),
      ...(body.remove_labels ? { labels: n.state.labels.filter((l) => !body.remove_labels!.includes(l)) } : {}),
    },
  }))
  await write(qc, 'PUT', `${P(id)}/state`, body, { label })
  if (body.labels || body.add_labels) void qc.invalidateQueries({ queryKey: noteKeys.labels })
}

// ACT note.delete
export const trashNote = (qc: QueryClient, id: string) => write(qc, 'DELETE', P(id), undefined, { label: t('notes.actions.noteMovedToTrash') })
// ACT note.restore
export const restoreNote = (qc: QueryClient, id: string) => write(qc, 'POST', `${P(id)}/restore`, undefined, { label: t('notes.actions.noteRestored') })
// ACT note.purge
export const purgeNote = (qc: QueryClient, id: string) => write(qc, 'DELETE', `${P(id)}?forever=true`, undefined, { label: t('notes.actions.noteDeletedForever'), undo: false })
export const emptyTrash = (qc: QueryClient) => write<{ purged: number }>(qc, 'POST', '/notes/trash/empty', {}, { undo: false, quiet: true })
// ACT note.duplicate
export async function duplicateNote(qc: QueryClient, id: string): Promise<Note> {
  return (await write<R<{ note: Note }>>(qc, 'POST', `${P(id)}/duplicate`, {}, { label: t('notes.actions.copyMade') })).note
}

/** Text ↔ checklist. Needs the current version: an old base answers 409 and the user may try again. */
// ACT note.to_checklist, ACT note.to_text
export function convertNote(qc: QueryClient, id: string, to: NoteKind): Promise<Note | null> {
  return serial(id, async () => {
    try {
      const r = await write<R<{ note: Note }>>(qc, 'POST', `${P(id)}/convert`, { to, base_version: noteVersion(qc, id) }, { label: to === 'checklist' ? t('notes.actions.madeItAList') : t('notes.actions.madeItText') })
      rememberVersion(id, r.note.version)
      qc.setQueryData(noteKeys.note(id), r.note)
      return r.note
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        void qc.invalidateQueries({ queryKey: noteKeys.note(id) })
        toast({ message: t('notes.actions.thisNoteChangedMeanwhile'), actionLabel: t('common.tryAgain'), onAction: () => void convertNote(qc, id, to), duration: 10_000 })
        return null
      }
      throw e
    }
  })
}

// ---------------------------------------------------------------- offline (lists)

/** Does this change wait for the connection? Offline, or an earlier change to the note waits. */
function waits(noteId: string): boolean {
  return useNotesQueue.getState().user != null && (offlineNow() || hasWaiting(noteId))
}

/** Keep the change for later and send what waits when the line is up (offline.ts). */
async function queue(qc: QueryClient, op: NoteOp): Promise<void> {
  await enqueue(op)
  if (!offlineNow()) void sendNotesQueue(qc)
}

const me = () => useNotesQueue.getState().user ?? 0

/** Refuse a change that needs the connection while offline (it says so). True: refused. */
function offlineRefuses(what: string): boolean {
  if (!offlineNow()) return false
  needsConnection(what)
  return true
}

// ---------------------------------------------------------------- items

export interface ItemIn {
  id?: string
  text: string
  qty?: Qty | null
  section_id?: string | null
  extra?: string | null
  checked?: boolean
}

export interface AddResult {
  items: Item[]
  merged?: { id: string; text: string; qty: Qty | null }[]
  rechecked?: string[]
  duplicates?: { text: string; qty?: Qty | null; section: string | null; section_id?: string | null }[]
  new_sections?: string[]
  undo?: Undo | null
}

/** Items from typed or pasted lines (the server parses them with the item grammar), or from item objects. */
// ACT note.add_items, ACT checkitem.add
export async function addItems(qc: QueryClient, noteId: string, body: { lines?: string; items?: ItemIn[]; usual?: string[]; section_id?: string | null; after?: string | null; force?: boolean; keep?: string[] }): Promise<AddResult> {
  const items = body.items?.map((i) => ({ id: i.id ?? uuid7(), ...i }))
  if (!body.usual?.length && waits(noteId)) return addOffline(qc, noteId, body, items)
  let res: AddResult
  try {
    res = await serial(noteId, () => write<AddResult>(qc, 'POST', `${P(noteId)}/items`, { ...body, ...(items ? { items } : {}) }, { quiet: true }))
  } catch (e) {
    if (!body.usual?.length && isNetwork(e) && useNotesQueue.getState().user != null) return addOffline(qc, noteId, body, items)
    throw e
  }
  // Duplicates are not added: the toast names them and offers "Add anyway".
  if (res.duplicates?.length && !body.force) {
    const d = res.duplicates[0]
    const name = d.text.charAt(0).toUpperCase() + d.text.slice(1)
    toast({
      message: t('notes.actions.isAlreadyOnTheList', { itemName: name, value: d.section ? ` (${d.section})` : '' }),
      actionLabel: t('notes.actions.addAnyway'),
      onAction: () => void addItems(qc, noteId, { items: res.duplicates!.map((x) => ({ text: x.text, qty: x.qty ?? null, section_id: x.section_id ?? null })), force: true }),
      duration: 7000,
    })
  } else if (res.undo) {
    const n = res.items.length
    const merged = res.merged?.length ?? 0
    const label = res.undo.label || (merged && !n ? t('notes.actions.quantityAdded') : t('notes.actions.itemsAdded', { count: n }))
    offerUndo(qc, { ...res.undo, label })
  }
  return res
}

/** Items added with no connection: read here with the grammar (the list's sections known), shown at
 *  once, and sent later with their ids. A new section (a "## Bakery" line) waits for the connection. */
async function addOffline(qc: QueryClient, noteId: string, body: { lines?: string; items?: ItemIn[]; section_id?: string | null }, given?: (ItemIn & { id: string })[]): Promise<AddResult> {
  const note = qc.getQueryData<Note>(noteKeys.note(noteId))
  const sections = note?.sections ?? []
  const now = new Date().toISOString()
  const made: Item[] = []
  let order = 0
  const push = (text: string, qty: Qty | null, sectionId: string | null, checked: boolean, id = uuid7()) =>
    made.push({ id, section_id: sectionId, text, qty, extra: null, checked, checked_at: checked ? now : null, order: `~${Date.now()}${String(order++).padStart(4, '0')}`, rank: null, added_at: now })
  if (given) for (const i of given) push(i.text, i.qty ?? null, i.section_id ?? body.section_id ?? null, !!i.checked, i.id)
  else {
    const parsed = parseItems(body.lines ?? '', { mode: 'list', list: { id: noteId, name: note?.title ?? '', sections: sections.map((x) => x.name) } })
    for (const p of parsed.items) {
      const sid = p.section ? sections.find((x) => x.name === p.section)?.id ?? null : body.section_id ?? null
      push(p.text, p.qty ? { amount: p.qty.amount, unit: p.qty.unit } : null, sid, p.checked)
    }
    if (parsed.new_sections.length) needsConnection(t('notes.actions.newSectionsAreAdded'))
  }
  if (!made.length) return { items: [] }
  await queue(qc, addOp(me(), noteId, made, uuid7()))
  toast({ message: t('notes.actions.itemsAddedTheyAreSent', { count: made.length }), duration: 3000 })
  return { items: made }
}

export interface ItemPatch {
  checked?: boolean
  text?: string
  parse?: boolean
  qty?: Qty | null
  extra?: string | null
}

// ACT checkitem.check, ACT checkitem.uncheck, ACT checkitem.edit
export async function patchItem(qc: QueryClient, noteId: string, item: Item, patch: ItemPatch, opts: { quiet?: boolean; label?: string } = {}): Promise<Item | null> {
  const now = new Date().toISOString()
  patchCached(qc, noteId, (n) => ({
    ...n,
    items: n.items?.map((i) => (i.id === item.id ? { ...i, ...(patch.qty !== undefined ? { qty: patch.qty } : {}), ...(patch.text !== undefined && !patch.parse ? { text: patch.text } : {}), ...(patch.extra !== undefined ? { extra: patch.extra } : {}), ...(patch.checked !== undefined ? { checked: patch.checked, checked_at: patch.checked ? now : null } : {}) } : i)),
    preview: n.preview && patch.checked ? { ...n.preview, items: n.preview.items.filter((i) => i.id !== item.id), checked: n.preview.checked + 1 } : n.preview,
  }))
  const body = { ...patch, base_version: item.version, ...(patch.checked !== undefined ? { device_at: now } : {}) }
  const label = opts.label ?? (patch.checked === true ? t('common.checked') : patch.checked === false ? t('notes.actions.unchecked') : undefined)
  const later = () => queue(qc, patch.checked !== undefined && Object.keys(patch).length === 1
    ? checkOp(me(), noteId, item, patch.checked, uuid7())
    : editOp(me(), noteId, item, offlineEdit(patch), uuid7())).then(() => null)
  if (waits(noteId)) return later()
  try {
    const r = await write<R<{ item: Item }>>(qc, 'PATCH', `${P(noteId)}/items/${enc(item.id)}`, body, { quiet: opts.quiet, label })
    return r.item ?? null
  } catch (e) {
    if (isNetwork(e) && useNotesQueue.getState().user != null) return later()
    throw e
  }
}

/** An edit made offline: typed text is read with the grammar here, so the list shows "milk" ×2 at once. */
function offlineEdit(patch: ItemPatch): ItemPatch {
  if (patch.text === undefined || !patch.parse) return patch
  const p = parseItems(patch.text, { mode: 'edit' }).items[0]
  return p ? { ...patch, text: p.text, qty: p.qty ? { amount: p.qty.amount, unit: p.qty.unit } : null, parse: false } : patch
}

// ACT checkitem.delete
export async function deleteItem(qc: QueryClient, noteId: string, item: Item): Promise<void> {
  patchCached(qc, noteId, (n) => ({ ...n, items: n.items?.filter((i) => i.id !== item.id) }))
  const later = () => queue(qc, deleteOp(me(), noteId, item, uuid7()))
  if (waits(noteId)) return later()
  try {
    await write(qc, 'DELETE', `${P(noteId)}/items/${enc(item.id)}`, undefined, { label: t('notes.actions.itemDeleted') })
  } catch (e) {
    if (isNetwork(e) && useNotesQueue.getState().user != null) return later()
    throw e
  }
}

export type MoveIn = { before?: string; after?: string; section_id?: string | null } | { direction: 'up' | 'down' | 'prev_section' | 'next_section' }

// ACT checkitem.move
export const moveItem = (qc: QueryClient, noteId: string, itemId: string, body: MoveIn, opts: { quiet?: boolean } = {}) =>
  offlineRefuses(t('notes.actions.itemsAreMoved')) ? Promise.resolve({} as R<{ item: Item }>)
    : serial(noteId, () => write<R<{ item: Item }>>(qc, 'POST', `${P(noteId)}/items/${enc(itemId)}/move`, body, { label: t('notes.actions.itemMoved'), quiet: opts.quiet }))

// ACT note.clear_checked
export const clearChecked = (qc: QueryClient, noteId: string) => offlineRefuses(t('notes.actions.checkedItemsAreCleared')) ? Promise.resolve({ count: 0 }) :
  write<R<{ count: number }>>(qc, 'POST', `${P(noteId)}/items/clear-checked`, {}, { label: t('notes.actions.checkedItemsCleared') })

// ACT note.uncheck_all
export const uncheckAll = (qc: QueryClient, noteId: string) => offlineRefuses(t('notes.actions.uncheckingEveryItemWorks')) ? Promise.resolve({ count: 0 }) :
  write<R<{ count: number }>>(qc, 'POST', `${P(noteId)}/items/uncheck-all`, {}, { label: t('notes.actions.everyItemUnchecked') })

// ACT checkitem.to_task
export const itemToTask = (qc: QueryClient, noteId: string, itemId: string) => offlineRefuses(t('notes.actions.tasksAreMade')) ? Promise.resolve({ task_uid: '' }) :
  write<R<{ task_uid: string }>>(qc, 'POST', `${P(noteId)}/items/${enc(itemId)}/to-task`, {}, { label: t('notes.actions.taskMade') })

// ---------------------------------------------------------------- sections

// ACT checksection.create
export const createSection = (qc: QueryClient, noteId: string, name: string, after?: string | null, opts: { quiet?: boolean } = {}) =>
  offlineRefuses(t('notes.actions.sectionsAreAdded')) ? Promise.reject(new Error('offline')) : serial(noteId, () => write<R<{ section: Section }>>(qc, 'POST', `${P(noteId)}/sections`, { name, ...(after ? { after } : {}) }, { label: t('notes.actions.sectionAdded', { itemName: name }), quiet: opts.quiet }))

/** The items whose text starts with "<section name>:": typed before the section existed. */
export function prefixedItems(items: Item[], name: string): Item[] {
  const squash = (x: string) => x.toLowerCase().replace(/\s+/g, '')
  const want = squash(name)
  return items.filter((i) => {
    const c = i.text.indexOf(':')
    return c > 0 && !i.checked && squash(i.text.slice(0, c)) === want && i.text.slice(c + 1).trim() !== ''
  })
}

/** Move items typed as "Costco: 2 milk" into the section Costco, without the prefix, the quantity read
 * again. One toast with one Undo for the batch. ACT checkitem.move */
export async function adoptPrefixed(qc: QueryClient, noteId: string, section: Section, items: Item[]): Promise<void> {
  const before = items.map((i) => ({ id: i.id, text: i.text, qty: i.qty ?? null, section_id: i.section_id ?? null }))
  const moved: Item[] = []
  for (const i of items) {
    const r = await moveItem(qc, noteId, i.id, { section_id: section.id }, { quiet: true }).catch(() => null)
    const cur = r?.item ?? null
    if (!cur) continue
    const rest = i.text.slice(i.text.indexOf(':') + 1).trim()
    const done = await patchItem(qc, noteId, cur, { text: rest, parse: true }, { quiet: true }).catch(() => null)
    moved.push(done ?? cur)
  }
  if (!moved.length) return
  toast({
    message: t('notes.actions.movedInto', { value: t('notes.actions.items', { count: moved.length }), sectionName: section.name }),
    actionLabel: t('common.undo'),
    duration: 6000,
    onAction: () => void (async () => {
      for (const m of moved) {
        const b = before.find((x) => x.id === m.id)
        if (!b) continue
        const r = await moveItem(qc, noteId, m.id, { section_id: b.section_id }, { quiet: true }).catch(() => null)
        if (r?.item) await patchItem(qc, noteId, r.item, { text: b.text, qty: b.qty }, { quiet: true }).catch(() => null)
      }
    })(),
  })
}

export interface SectionPatch {
  name?: string
  base_name?: string
  before?: string
  after?: string
  direction?: 'up' | 'down'
  order_mode?: 'learned' | 'manual'
  collapsed?: boolean
  reset_learned?: boolean
}

// ACT checksection.rename, ACT checksection.reorder, ACT checksection.order_mode
export function patchSection(qc: QueryClient, noteId: string, sid: string, body: SectionPatch, label?: string) {
  if (body.collapsed !== undefined)
    patchCached(qc, noteId, (n) => ({ ...n, sections: n.sections?.map((s) => (s.id === sid ? { ...s, collapsed: body.collapsed! } : s)) }))
  // Offline, folding a section stays on this device; any other section change needs the connection.
  if (offlineNow() && (Object.keys(body).length === 1 && body.collapsed !== undefined)) return Promise.resolve({} as R<{ section: Section }>)
  if (offlineRefuses(t('notes.actions.sectionsAreChanged'))) return Promise.resolve({} as R<{ section: Section }>)
  return serial(noteId, () => write<R<{ section: Section }>>(qc, 'PATCH', `${P(noteId)}/sections/${enc(sid)}`, body, { label, quiet: body.collapsed !== undefined && !label }))
}

// ACT checksection.delete
export const deleteSection = (qc: QueryClient, noteId: string, sid: string, items: 'keep' | 'delete') =>
  offlineRefuses(t('notes.actions.sectionsAreDeleted')) ? Promise.resolve({}) : write(qc, 'DELETE', `${P(noteId)}/sections/${enc(sid)}?items=${items}`, undefined, { label: t('notes.actions.sectionDeleted') })

// ---------------------------------------------------------------- usual items

// ACT checkitem.usual_add
export const addUsual = (qc: QueryClient, noteId: string, keys: string[]) =>
  offlineRefuses(t('notes.actions.usualItemsAreAdded')) ? Promise.resolve({ items: [] } as AddResult) : addItems(qc, noteId, { usual: keys })

// ACT checkitem.usual_dismiss
export const dismissUsual = (qc: QueryClient, noteId: string, key: string, forWhat: 'interval' | 'never') =>
  write(qc, 'POST', `${P(noteId)}/usual/${enc(key)}/dismiss`, { for: forWhat }, { label: forWhat === 'never' ? t('notes.actions.notSuggestedAgain') : t('notes.actions.hiddenForNow') })

export const undismissUsual = (qc: QueryClient, noteId: string, key: string) =>
  write(qc, 'DELETE', `${P(noteId)}/usual/${enc(key)}/dismiss`, undefined, { label: t('notes.actions.suggestedAgain'), undo: false })

// ---------------------------------------------------------------- history

// ACT note.restore_version
export const restoreVersion = (qc: QueryClient, noteId: string, rev: number) =>
  write<R<{ note: Note }>>(qc, 'POST', `${P(noteId)}/revisions/${rev}/restore`, {}, { label: t('notes.actions.versionRestored') })

/** The editor closed: the editing session ends (a revision). */
export function checkpoint(noteId: string): void {
  void fetch(`/api/v1/notes/${enc(noteId)}/revisions/checkpoint`, { method: 'POST', credentials: 'same-origin', keepalive: true, headers: { 'X-CSRF-Token': getCsrf(), 'X-Mailr-Client': CLIENT_ID } }).catch(() => undefined)
}

// ---------------------------------------------------------------- links

// ACT note.link
export const linkNote = (qc: QueryClient, noteId: string, target_kind: NoteLink['target_kind'], target_ref: Record<string, unknown>) =>
  write<R<{ link: NoteLink }>>(qc, 'POST', `${P(noteId)}/links`, { target_kind, target_ref }, { label: t('notes.actions.linkAdded') })

// ACT note.unlink
export const unlinkNote = (qc: QueryClient, noteId: string, linkId: number) =>
  write(qc, 'DELETE', `${P(noteId)}/links/${linkId}`, undefined, { label: t('notes.actions.linkRemoved') })

// ACT note.from_email
export async function noteFromEmail(qc: QueryClient, conversationId: number, selection: string | null, kind: NoteKind): Promise<{ note: Note; undo?: Undo | null }> {
  return write<R<{ note: Note }>>(qc, 'POST', '/notes/from-email', { conversation_id: conversationId, selection: selection || undefined, kind }, { quiet: true })
}

// ---------------------------------------------------------------- capture (palette, side panel)

/** "milk, eggs #shopping": the grammar in capture mode on the server. */
// ACT note.add_items
export async function capture(qc: QueryClient, text: string, defaultList?: string | null): Promise<{ note: { id: string; title: string }; items: Item[]; created: boolean }> {
  return write<{ note: { id: string; title: string }; items: Item[]; created: boolean }>(qc, 'POST', '/notes/capture', { text, ...(defaultList ? { default_list: defaultList } : {}) }, { label: t('notes.actions.added') })
}

/** "New note…" from anywhere: `#Shopping Costco: milk` adds to a list, anything else is a
 *  text note (notes/routes.py `quick`). The toast offers Open and Undo.
 *  ACT note.quick */
export async function quickNote(qc: QueryClient, text: string): Promise<{ kind: 'note' | 'items'; note: { id: string; title: string }; undo?: Undo | null; created?: boolean; items?: Item[] }> {
  const r = await write<{ kind: 'note' | 'items'; note: Note | { id: string; title: string }; undo?: Undo | null; created?: boolean; items?: Item[] }>(qc, 'POST', '/notes/quick', { text }, { quiet: true })
  return { ...r, note: { id: r.note.id, title: r.note.title } }
}

// ---------------------------------------------------------------- labels

// ACT notelabel.create
export const createLabel = (qc: QueryClient, name: string, color?: string | null) =>
  write(qc, 'POST', '/notes/labels', { name, ...(color ? { color } : {}) }, { label: t('notes.actions.labelCreated', { itemName: name }) })

// ACT notelabel.rename, ACT notelabel.color, ACT notelabel.reorder
export const patchLabel = (qc: QueryClient, name: string, body: { name?: string; color?: string | null; before?: string; after?: string }, label?: string) =>
  write(qc, 'PATCH', `/notes/labels/${enc(name)}`, body, { label })

// ACT notelabel.delete
export const deleteLabel = (qc: QueryClient, name: string) =>
  write(qc, 'DELETE', `/notes/labels/${enc(name)}`, undefined, { label: t('notes.actions.labelDeleted', { itemName: name }) })

/** The viewer opened a shared note or table: the dot goes (no toast, no undo). */
export async function markSeen(qc: QueryClient, kind: 'note' | 'table', id: string): Promise<void> {
  const clear = <T extends { id: string; changed?: boolean }>(x: T): T => (x.id === id ? { ...x, changed: false } : x)
  qc.setQueriesData<{ notes: Note[]; tables?: { id: string; changed?: boolean }[] }>({ queryKey: ['notes', 'list'] },
    (d) => (d ? { ...d, notes: d.notes.map(clear), ...(d.tables ? { tables: d.tables.map(clear) } : {}) } : d))
  try {
    await request(`/${kind}s/${encodeURIComponent(id)}/state`, { method: 'PUT', body: { seen: true } })
  } catch {
    /* the dot comes back on the next refetch; nothing else depends on it */
  }
}

// ---------------------------------------------------------------- reminders

/** The viewer's reminders on a note become `at` (ISO times); [] removes them. The toast offers Undo.
 *  ACT note.remind */
export const setReminders = (qc: QueryClient, noteId: string, at: string[], label?: string) =>
  write<R<{ reminders: Note['reminders']; next: string | null }>>(qc, 'PUT', `${P(noteId)}/reminders`, { at }, { label })

// ---------------------------------------------------------------- templates

/** ACT note.save_template: the note (a text note's title and text, a list's sections and items). */
export const saveTemplate = (qc: QueryClient, noteId: string, name: string) =>
  write<R<{ template: NoteTemplate }>>(qc, 'POST', `${P(noteId)}/save-template`, { name }, { label: t('notes.actions.templateSaved', { itemName: name }) })

/** ACT table.save_template: the table's columns, with no rows. */
export const saveTableTemplate = (qc: QueryClient, tableId: string, name: string) =>
  write<R<{ template: NoteTemplate }>>(qc, 'POST', `/tables/${enc(tableId)}/save-template`, { name }, { label: t('notes.actions.templateSaved', { itemName: name }) })

/** ACT note.from_template: a new note, list or table; the answer holds `note` or `table`. */
export async function fromTemplate(qc: QueryClient, template: string, extra: { title?: string; labels?: string[]; group?: string } = {}): Promise<{ note?: Note; table?: { id: string; name: string } }> {
  const r = await write<R<{ note?: Note; table?: { id: string; name: string } }>>(qc, 'POST', '/notes/from-template', { template, ...extra })
  if (r.note) rememberVersion(r.note.id, r.note.version)
  void qc.invalidateQueries({ queryKey: ['tables'] })
  return r
}

/** ACT note.delete_template: one of the user's own templates; the toast offers Undo. */
export const deleteTemplate = (qc: QueryClient, id: string, name: string) =>
  write(qc, 'DELETE', `/notes/templates/${enc(id)}`, undefined, { label: t('notes.actions.templateDeleted', { itemName: name }) })
