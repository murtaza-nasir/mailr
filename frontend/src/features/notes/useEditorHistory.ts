// Undo and redo in the note editor. The model is
// lib/editHistory.ts; this hook applies its steps to one open note.
//
// - A text step (the title, the body) sets the field and autosaves like any edit: a new version
//   through the live editing path (base version, merge, the conflict bar).
// - An operation (a tick, an added or deleted item, a move, a section, a colour, a label, a link,
//   text <-> list) is the server's own undo descriptor, taken from the toast it offered
//   (api.ts setUndoRecorder). Undo sends it; its answer carries the inverse, which is the redo; the
//   redo's answer carries the next undo. A step whose answer has no inverse cannot be redone and
//   leaves the redo stack.
// - Steps run one after another, so quick presses of Ctrl+Z keep their order.

import type { QueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { request } from '../../api/client'
import { EditHistory, caretAfter, reapplyText, revertText, type OpStep, type Step, type TextStep } from '../../lib/editHistory'
import { toast, toastError } from '../../stores/ui'
import { UNDO_MS, noteKeys, setUndoRecorder } from './api'
import { rememberVersion } from './actions'
import type { Undo } from './types'
import { t } from '../../i18n'

/** An operation step: the descriptors that take it back, and (once undone) the ones that redo it. */
export interface NoteOp {
  undo: Undo[] | null
  redo: Undo[] | null
}

export type Field = 'title' | 'body'

export interface EditorHistory {
  canUndo: boolean
  canRedo: boolean
  /** A field changed by typing, a paste, a cut, a replacement (the browser's inputType). */
  recordText: (field: Field, before: string, after: string, inputType?: string | null) => void
  seal: () => void
  undo: () => void
  redo: () => void
}

const histories = new Map<string, EditHistory<NoteOp>>()

/** The history of one note, kept while the tab lives (closing and reopening the note starts afresh). */
function historyOf(noteId: string): EditHistory<NoteOp> {
  let h = histories.get(noteId)
  if (!h) {
    h = new EditHistory<NoteOp>()
    histories.set(noteId, h)
  }
  return h
}

const forNote = (noteId: string, u: Undo) => {
  const p = u.path.startsWith('/') ? u.path : `/${u.path}`
  return p === `/notes/${encodeURIComponent(noteId)}` || p.startsWith(`/notes/${encodeURIComponent(noteId)}/`)
}

/** Send descriptors newest first and collect their answers' undos, the inverse of the batch. */
async function send(qc: QueryClient, noteId: string, us: Undo[]): Promise<Undo[] | null> {
  const back: Undo[] = []
  let whole = true
  try {
    for (const u of [...us].reverse()) {
      const r = await request<{ undo?: Undo | null; note?: { version?: number } }>(u.path.startsWith('/') ? u.path : `/${u.path}`, { method: u.method, body: (u.body ?? undefined) as never })
      if (r?.note?.version != null) rememberVersion(noteId, r.note.version)
      if (r?.undo) back.push(r.undo)
      else whole = false
    }
  } finally {
    await qc.invalidateQueries({ queryKey: noteKeys.all })
    if (us.some((u) => u.path.includes('/tables/'))) void qc.invalidateQueries({ queryKey: ['tables'] })
  }
  return whole && back.length ? back : null
}

export function useEditorHistory(opts: {
  qc: QueryClient
  noteId: string
  /** The field's value now. */
  read: (field: Field) => string
  /** Set a field from the history (it autosaves) and put the caret at `caret`. */
  apply: (field: Field, value: string, caret: number) => void
  /** Text steps only apply to a text note's body while the note is a text note. */
  kind: () => 'text' | 'checklist'
  disabled: boolean
}): EditorHistory {
  const { qc, noteId } = opts
  const h = useMemo(() => historyOf(noteId), [noteId])
  const [, setTick] = useState(0)
  const bump = useCallback(() => setTick((n) => n + 1), [])
  const chain = useRef<Promise<unknown>>(Promise.resolve())
  const o = useRef(opts)
  o.current = opts

  const applyText = useCallback((s: TextStep, back: boolean): boolean => {
    if (s.field === 'body' && o.current.kind() !== 'text') return false
    const cur = o.current.read(s.field as Field)
    const next = back ? revertText(cur, s) : reapplyText(cur, s)
    if (next === null) {
      toast({ message: t('notes.editorHistory.textChangedElsewhere'), duration: 4000 })
      return false
    }
    o.current.apply(s.field as Field, next, caretAfter(cur, next))
    return true
  }, [])

  const runOp = useCallback(async (s: OpStep<NoteOp>, back: boolean): Promise<boolean> => {
    const us = back ? s.op.undo : s.op.redo
    if (!us) return false
    try {
      const inverse = await send(qc, noteId, us)
      if (back) s.op.redo = inverse
      else s.op.undo = inverse
      return true
    } catch (e) {
      toastError(e, t('common.couldNotUndo'))
      return false
    }
  }, [qc, noteId])

  /** Take one step back (or forward); a step that cannot be applied leaves the history. */
  const move = useCallback((back: boolean, only?: Step<NoteOp>) => {
    chain.current = chain.current.catch(() => undefined).then(async () => {
      if (only && h.peekUndo() !== only) {
        // The toast's Undo for a step that is no longer the newest: reverse it where it is, as a new step.
        if (only.kind === 'text') {
          const cur = o.current.read(only.field as Field)
          const next = revertText(cur, only)
          if (next !== null) {
            h.recordText(only.field, cur, next, Date.now(), 'historyUndo')
            o.current.apply(only.field as Field, next, caretAfter(cur, next))
          }
        } else if (only.op.undo) {
          await runOp(only, true)
          h.drop(only)
        }
        bump()
        return
      }
      const s = back ? h.undo() : h.redo()
      if (!s) return
      bump()
      const ok = s.kind === 'text' ? applyText(s, back) : await runOp(s, back)
      if (!ok) h.drop(s)
      // An operation whose answer carried no inverse cannot come back the other way.
      else if (s.kind === 'op' && !(back ? s.op.redo : s.op.undo)) h.drop(s)
      bump()
    })
  }, [h, applyText, runOp, bump])

  // Every undo the Notes app offers for this note while the editor is open becomes a step.
  useEffect(() => setUndoRecorder({
    claim: (undos, label) => {
      if (!undos.length || !undos.every((u) => forNote(noteId, u))) return null
      const step = h.recordOp(label || undos[0].label, { undo: undos, redo: null })
      bump()
      return () => move(true, step)
    },
  }), [noteId, h, move, bump])

  const recordText = useCallback((field: Field, before: string, after: string, inputType?: string | null) => {
    const r = h.recordText(field, before, after, Date.now(), inputType)
    if (!r) return
    bump()
    if (r.big) {
      const step = r.step
      toast({ message: t('notes.editorHistory.textRemoved'), actionLabel: t('common.undo'), onAction: () => move(true, step), duration: UNDO_MS })
    }
  }, [h, bump, move])

  return {
    canUndo: !opts.disabled && h.canUndo,
    canRedo: !opts.disabled && h.canRedo,
    recordText,
    seal: () => h.seal(),
    undo: () => move(true),
    redo: () => move(false),
  }
}

/** The editor closed for good (not a re-render): its history goes. */
export function forgetHistory(noteId: string): void {
  histories.delete(noteId)
}
