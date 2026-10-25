// Data for the Notes app. Reads go through react-query under the 'notes' prefix,
// so a live change of a note (api/events.ts) refreshes every view that shows it. Writes go through
// `write`: it sends the request with this tab's client id, refreshes the notes queries, and offers
// the server's `undo` in the 7 s toast and on `z`.

import { useQuery, type QueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import { ApiError, request } from '../../api/client'
import { applyPending, useNotesQueue } from './offline'
import { toast, toastError, useUI } from '../../stores/ui'
import type { Table } from '../tables/types'
import type { Note, NoteLabel, NoteTemplate, Revision, Undo, UsualItem } from './types'
import { t as tr } from '../../i18n'

export const UNDO_MS = 7000

export const noteKeys = {
  all: ['notes'] as const,
  list: (view: string, label: string | null, sort: string) => ['notes', 'list', view, label ?? '', sort] as const,
  note: (id: string) => ['notes', 'note', id] as const,
  labels: ['notes', 'labels'] as const,
  usual: (id: string) => ['notes', 'usual', id] as const,
  revisions: (id: string) => ['notes', 'revisions', id] as const,
  revision: (id: string, rev: number) => ['notes', 'revision', id, rev] as const,
  search: (q: string, limit: number) => ['notes', 'search', q, limit] as const,
  linked: (kind: string, ref: string) => ['notes', 'linked', kind, ref] as const,
  templates: ['notes', 'templates'] as const,
}

const enc = encodeURIComponent

export const notePathApi = (id: string) => `/notes/${enc(id)}`

// ---------------------------------------------------------------- reads

export function useNotes(view: string, label: string | null, sort: string, enabled = true) {
  return useQuery<{ notes: Note[]; tables?: Table[]; labels?: NoteLabel[]; groups?: string[] }>({
    queryKey: noteKeys.list(view, label, sort),
    queryFn: ({ signal }) => request('/notes', { query: { view, label: label ?? undefined, sort }, signal }),
    enabled,
    staleTime: 5_000,
  })
}

export function useNote(id: string | null) {
  // A list's changes made offline show over every copy until the server has them (offline.ts).
  const ops = useNotesQueue((s) => s.ops)
  const select = useCallback((n: Note) => applyPending(n, ops), [ops])
  return useQuery<Note>({
    queryKey: noteKeys.note(id ?? ''),
    queryFn: ({ signal }) => request(notePathApi(id!), { signal }),
    select,
    enabled: !!id,
    staleTime: 2_000,
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  })
}

export function useNoteLabels() {
  return useQuery<NoteLabel[]>({
    queryKey: noteKeys.labels,
    queryFn: async ({ signal }) => (await request<{ labels: NoteLabel[] }>('/notes/labels', { signal })).labels ?? [],
    staleTime: 30_000,
  })
}

export function useUsual(id: string | null, enabled: boolean) {
  return useQuery<{ items: UsualItem[]; hidden: { key: string; text: string; until: string | null }[]; enabled: boolean }>({
    queryKey: noteKeys.usual(id ?? ''),
    queryFn: ({ signal }) => request(`${notePathApi(id!)}/usual`, { signal }),
    enabled: !!id && enabled,
    staleTime: 30_000,
  })
}

export function useRevisions(id: string | null, enabled: boolean) {
  return useQuery<Revision[]>({
    queryKey: noteKeys.revisions(id ?? ''),
    queryFn: async ({ signal }) => (await request<{ revisions: Revision[] }>(`${notePathApi(id!)}/revisions`, { signal })).revisions ?? [],
    enabled: !!id && enabled,
  })
}

export function useRevision(id: string | null, rev: number | null) {
  return useQuery<Revision>({
    queryKey: noteKeys.revision(id ?? '', rev ?? 0),
    queryFn: ({ signal }) => request(`${notePathApi(id!)}/revisions/${rev}`, { signal }),
    enabled: !!id && rev != null,
  })
}

/** Notes and tables that match (tables find by name, description and row text). */
export function useNoteSearchAll(q: string, limit = 50, enabled = true) {
  return useQuery<{ notes: Note[]; tables: Table[] }>({
    queryKey: noteKeys.search(q, limit),
    queryFn: ({ signal }) => searchNotes(q, limit, signal),
    enabled: enabled && !!q.trim(),
    staleTime: 10_000,
  })
}

async function searchNotes(q: string, limit: number, signal?: AbortSignal): Promise<{ notes: Note[]; tables: Table[] }> {
  const r = await request<{ notes?: Note[]; tables?: Table[] }>('/notes/search', { query: { q, limit }, signal })
  return { notes: r.notes ?? [], tables: r.tables ?? [] }
}

/** One note linked to a conversation, an event, a task or a recording. `item_id` names a checklist
 *  item when the link came from checkitem.to_task. */
export interface LinkedNote { id: string; title: string; kind: string; link_id: number; item_id: string | null }

/** The notes linked to a conversation, an event, a task or a recording (the links panels). A
 *  conversation goes by its id; a recording by "<connector>:<recording>". */
export function useLinkedNotes(kind: string, ref: string | number | null) {
  return useQuery<LinkedNote[]>({
    queryKey: noteKeys.linked(kind, String(ref ?? '')),
    queryFn: async ({ signal }) => (await request<{ notes: LinkedNote[] }>('/notes/linked', {
      query: kind === 'conversation' ? { target_kind: kind, conversation_id: ref as number } : { target_kind: kind, ref: String(ref ?? '') }, signal,
    })).notes ?? [],
    enabled: ref != null && ref !== '',
    staleTime: 30_000,
    retry: false,
  })
}

/** The templates: the built-in starters, then the user's own (notes/templates.py). */
export function useTemplates(enabled = true) {
  return useQuery<NoteTemplate[]>({
    queryKey: noteKeys.templates,
    queryFn: async ({ signal }) => (await request<{ templates: NoteTemplate[] }>('/notes/templates', { signal })).templates ?? [],
    enabled,
    staleTime: 60_000,
  })
}

// ---------------------------------------------------------------- writes

export interface WriteOpts {
  /** Toast text when the answer has no undo label; no toast without either. */
  label?: string
  /** Show no toast (a quiet autosave). */
  quiet?: boolean
  /** Offer the answer's undo (default true). */
  undo?: boolean
}

let lastUndo: Undo | Undo[] | null = null

/**
 * The open note editor's history (useEditorHistory.ts) takes every undo offered for its note: the
 * change becomes a step of the editor's Undo and Redo, and the toast's Undo (and `z`) goes through the
 * history, so the two never take the same change back twice. `claim` answers the run that the toast
 * should use, or null when the undo is not for that note.
 */
export interface UndoRecorder {
  claim(undos: Undo[], label: string): (() => void) | null
}
let recorder: UndoRecorder | null = null
/** The history's run for the last undo offered, when the editor claimed it (`z` uses it too). */
let lastClaim: (() => void) | null = null
export function setUndoRecorder(r: UndoRecorder): () => void {
  recorder = r
  return () => {
    if (recorder === r) recorder = null
  }
}

/** Send an undo descriptor (a toast's Undo, or `z`); a batch's descriptors go one after another,
 *  newest first, with one "Undone". */
export async function runUndo(qc: QueryClient, u: Undo | Undo[]): Promise<void> {
  lastUndo = null
  lastClaim = null
  const all = Array.isArray(u) ? [...u].reverse() : [u]
  try {
    for (const one of all) await request(one.path.startsWith('/') ? one.path : `/${one.path}`, { method: one.method, body: (one.body ?? undefined) as never })
    toast({ message: tr('common.undone'), duration: 2500 })
  } catch (e) {
    toastError(e, tr('common.couldNotUndo'))
  }
  await qc.invalidateQueries({ queryKey: noteKeys.all })
  if (all.some((one) => one.path.includes('/tables/'))) await qc.invalidateQueries({ queryKey: ['tables'] })
}

/** The last action's undo, for `z` in the Notes app. Returns false when there is none. */
export function undoLast(qc: QueryClient): boolean {
  if (lastClaim) {
    const c = lastClaim
    lastClaim = null
    lastUndo = null
    c()
    return true
  }
  if (!lastUndo) return false
  void runUndo(qc, lastUndo)
  return true
}

/** One toast and one `z` for a batch on several selected cards (a
 *  batch has one undo): every write's own undo, run together. */
export function offerUndoAll(qc: QueryClient, undos: (Undo | null | undefined)[], label: string): void {
  const all = undos.filter((u): u is Undo => !!u)
  if (!all.length) {
    toast({ message: label, duration: 3000 })
    return
  }
  lastUndo = all
  const claimed = recorder?.claim(all, label) ?? null
  lastClaim = claimed
  const run = () => {
    useUI.getState().setUndo(null)
    if (claimed) {
      lastUndo = null
      lastClaim = null
      claimed()
    } else void runUndo(qc, all)
  }
  useUI.getState().setUndo(run)
  toast({ message: label, actionLabel: tr('common.undo'), onAction: run, duration: UNDO_MS })
}

/** Run one write per target with no toast of its own, then one toast with one Undo for all. */
export async function batch<T>(qc: QueryClient, targets: T[], one: (t: T) => Promise<{ undo?: Undo | null } | unknown>, label: string): Promise<void> {
  const res = await Promise.allSettled(targets.map(one))
  const undos = res.map((r) => (r.status === 'fulfilled' ? (r.value as { undo?: Undo | null } | undefined)?.undo : null))
  const failed = res.filter((r) => r.status === 'rejected').length
  offerUndoAll(qc, undos, failed ? tr('notes.api.couldNotChange', { label, failed }) : label)
}

export function offerUndo(qc: QueryClient, u: Undo | null | undefined, fallback?: string): void {
  if (!u) {
    if (fallback) toast({ message: fallback, duration: 3000 })
    return
  }
  lastUndo = u
  const claimed = recorder?.claim([u], u.label || fallback || '') ?? null
  lastClaim = claimed
  const run = () => {
    useUI.getState().setUndo(null)
    if (claimed) {
      lastUndo = null
      lastClaim = null
      claimed()
    } else void runUndo(qc, u)
  }
  useUI.getState().setUndo(run)
  toast({ message: u.label || fallback || tr('common.done'), actionLabel: tr('common.undo'), onAction: run, duration: UNDO_MS })
}

/** One write: the request, a refresh of the notes queries, and the undo toast. Errors toast and rethrow. */
export async function write<T extends object>(
  qc: QueryClient, method: string, path: string, body?: unknown, opts: WriteOpts = {},
): Promise<T> {
  try {
    const res = await request<T>(path, { method, body: body as never })
    const undo = (res as { undo?: Undo | null } | undefined)?.undo
    if (!opts.quiet && opts.undo !== false) offerUndo(qc, undo, opts.label)
    else if (!opts.quiet && opts.label) toast({ message: opts.label, duration: 3000 })
    void qc.invalidateQueries({ queryKey: noteKeys.all })
    return res
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 409)) toastError(e)
    void qc.invalidateQueries({ queryKey: noteKeys.all })
    throw e
  }
}

/** Put a note into every cached copy (list views and the open note), so a change shows at once. */
export function patchCached(qc: QueryClient, id: string, fn: (n: Note) => Note): void {
  qc.setQueriesData<{ notes: Note[] }>({ queryKey: ['notes', 'list'] }, (d) => (d ? { ...d, notes: d.notes.map((n) => (n.id === id ? fn(n) : n)) } : d))
  qc.setQueryData<Note>(noteKeys.note(id), (n) => (n ? fn(n) : n))
}
