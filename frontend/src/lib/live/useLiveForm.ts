// Live editing for a form with an explicit Save (the memory note and filter dialogs): it opens on
// the stored version, follows changes saved elsewhere (in place with Undo while unchanged here,
// merged while changed), shows the conflict bar on an overlap, and sends base_version with Save.

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { toast } from '../../stores/ui'
import { registerEditor } from './editors'
import { LiveDoc, type ConflictState, type Content } from './machine'
import { same, type MergeMode } from './merge3'
import { byName, type LiveKind } from './store'
import { fetchLive, reportTyping, useLiveItem } from './useLiveItem'
import { t as tr } from '../../i18n'

export interface LiveForm<T extends Content> {
  editing: { by: string } | null
  conflict: ConflictState<T> | null
  /** The version to send as base_version with Save. */
  baseVersion: () => number | null
  onMine: () => void
  onTheirs: () => void
  /** Save failed: a 409 opens the conflict bar (true); anything else is the caller's (false). */
  rejected: (e: unknown) => boolean
}

export function useLiveForm<T extends Content>(opts: {
  kind: Exclude<LiveKind, 'draft'>
  id: string | number | null | undefined
  form: T
  setForm: (f: T) => void
  fromContent: (c: Record<string, unknown>) => T
  textFields?: Record<string, MergeMode>
  /** What the assistant sees of the form (backend agent/screen.py EDITOR_FIELDS). */
  fields?: (f: T) => Record<string, unknown>
}): LiveForm<T> {
  const { kind, id } = opts
  const docRef = useRef<LiveDoc<T>>(new LiveDoc<T>(opts.textFields ?? {}))
  const [conflict, setConflict] = useState<ConflictState<T> | null>(null)
  const optsRef = useRef(opts)
  optsRef.current = opts
  const doc = docRef.current
  const dirty = () => !!doc.base && !same(optsRef.current.form, doc.base.content)

  const onRemote = useCallback((theirs: { version: number | null; content: Record<string, unknown>; by?: string | null }, quiet = false) => {
    const o = optsRef.current
    const form = o.fromContent(theirs.content)
    const d = doc.onRemote({ version: theirs.version, content: form, by: theirs.by }, o.form, dirty())
    if (d.type === 'replace') {
      o.setForm(d.content)
      if (!quiet) toast({ message: tr('lib.live.useLiveForm.updatedBy', { byName: byName(theirs.by) }), actionLabel: tr('common.undo'), duration: 10_000, onAction: () => optsRef.current.setForm(d.before) })
    } else if (d.type === 'merge') {
      o.setForm(d.content)
      if (!quiet) toast({ message: tr('lib.live.useLiveForm.changedThisTooBothChanges', { replace: byName(theirs.by).replace(/^./, (c) => c.toUpperCase()) }) })
    } else if (d.type === 'conflict') setConflict(d.conflict)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc])

  useEffect(() => {
    if (id == null) return
    let alive = true
    fetchLive(kind, id).then((got) => {
      if (!alive || doc.base) return
      const form = optsRef.current.fromContent(got.content)
      const shown = optsRef.current.form
      if (same(form, shown)) doc.setBase(got.version, form)
      else {
        // The copy the form opened with is older than the stored one.
        doc.setBase(null, shown)
        onRemote({ version: got.version, content: got.content, by: got.by }, true)
      }
    }).catch(() => {})
    return () => {
      alive = false
    }
  }, [kind, id, doc, onRemote])

  const editing = useLiveItem({ kind, id, onRemote: (t) => onRemote(t), version: () => doc.version })

  useEffect(() => {
    if (id == null) return
    return registerEditor(`${kind}-form-${id}`, {
      kind, id: () => id, flush: async () => {},
      snapshot: () => ({
        kind, id, base_version: doc.version, dirty: dirty() || !!conflict, focused: true,
        fields: optsRef.current.fields?.(optsRef.current.form) ?? {},
      }),
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, id, doc, conflict])

  useEffect(() => {
    if (id != null && dirty()) reportTyping(kind, id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.form])

  return {
    editing,
    conflict,
    baseVersion: () => doc.version,
    onMine: () => {
      doc.useMine()
      setConflict(null)
    },
    onTheirs: () => {
      const t = doc.useTheirs()
      setConflict(null)
      if (t) optsRef.current.setForm(t)
    },
    rejected: (e: unknown) => {
      if (!(e instanceof ApiError) || e.status !== 409 || !e.body || typeof e.body !== 'object') return false
      const b = e.body as { base?: { version?: number | null; content?: Record<string, unknown> | null }; theirs?: { version?: number | null; content?: Record<string, unknown>; by?: string | null }; fields?: string[] }
      const o = optsRef.current
      setConflict(doc.onRejected({
        base: { version: b.base?.version ?? null, content: b.base?.content ? o.fromContent(b.base.content) : null },
        theirs: { version: b.theirs?.version ?? null, content: b.theirs?.content ? o.fromContent(b.theirs.content) : o.form, by: b.theirs?.by ?? null },
        fields: b.fields,
      }, o.form))
      return true
    },
  }
}
