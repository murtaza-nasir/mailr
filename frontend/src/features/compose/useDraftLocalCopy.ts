import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react'
import type { ComposePayload } from '../../api/types'
import { newLocalId, registerOpenForm, removeLocalDraft, saveLocalDraft, tabId } from '../../lib/draftStore'
import { canon } from '../../lib/live/merge3'

/** A draft payload as the form compares it with the server's copy: keys in a fixed order, and an
 *  empty value ('', null, [], missing) left out. The server answers a merged save with its own
 *  payload, whose keys come in another order and which carries empty fields the form leaves out;
 *  a plain JSON.stringify called equal drafts different, and "Draft saved" never showed. */
export function draftJson(p: object): string {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0)) continue
    out[k] = v
  }
  return canon(out)
}

interface Opts {
  givenLocalId: string | undefined
  /** The form opened a local copy that is newer than the server's (it counts as touched). */
  carried: boolean
  owner: number | null
  conversationId: number | null | undefined
  draftIdRef: MutableRefObject<number | null>
  payload: (withSignature: boolean) => ComposePayload
  hasContent: boolean
  dirty: boolean
}

/** The local copy: text the server has not confirmed survives a reload or a crash
 *  (lib/draftStore.ts). The refs are shared with the form's saving and sending. */
export function useDraftLocalCopy({ givenLocalId, carried, owner, conversationId, draftIdRef, payload, hasContent, dirty }: Opts) {
  const [localId] = useState(() => givenLocalId ?? newLocalId())
  /** Set after the form takes the server's copy: the next serialised payload counts as saved. */
  const syncedRef = useRef(false)
  /** The user changed something here (opening a draft and reading it is not a change). */
  const touchedRef = useRef(carried)
  /** The payload the server last confirmed, and the one on screen now (both as draftJson). */
  const serverJson = useRef<string | null>(null)
  const latest = useRef<{ json: string; p: ComposePayload; has: boolean } | null>(null)
  /** Sent or discarded: nothing left to keep. */
  const doneRef = useRef(false)
  /** Signing out or reloading: the local copy must outlive a save still in flight. */
  const pinnedRef = useRef(false)
  const unsavedNow = useCallback(
    () => !doneRef.current && touchedRef.current && !!latest.current?.has && latest.current.json !== serverJson.current,
    [],
  )
  const writeLocal = useCallback(() => {
    const l = latest.current
    if (!l || owner === null || doneRef.current || !touchedRef.current || !l.has) return
    saveLocalDraft({ localId, owner, tab: tabId(), draftId: draftIdRef.current, conversationId: conversationId ?? null, payload: l.p, updatedAt: Date.now() })
  }, [localId, owner, conversationId, draftIdRef])
  useEffect(() => {
    if (dirty) touchedRef.current = true
    const p = payload(false)
    latest.current = { json: draftJson(p), p, has: hasContent }
    if (syncedRef.current) {
      // The form now shows what the server stored (a remote change, or "Use theirs"): saved.
      syncedRef.current = false
      serverJson.current = latest.current.json
    }
    if (!touchedRef.current || doneRef.current) return
    if (!unsavedNow()) {
      removeLocalDraft(localId)
      return
    }
    // Checked again when the timer fires: a save may have landed in between.
    const t = setTimeout(() => {
      if (unsavedNow()) writeLocal()
    }, 300)
    return () => clearTimeout(t)
  }, [payload, hasContent, dirty, localId, unsavedNow, writeLocal])
  useEffect(
    () =>
      registerOpenForm(localId, {
        unsaved: unsavedNow,
        flush: (all) => {
          if (all) pinnedRef.current = true
          if (doneRef.current || !latest.current?.has) return
          if (all ? touchedRef.current || !draftIdRef.current : unsavedNow()) writeLocal()
        },
      }),
    [localId, unsavedNow, writeLocal, draftIdRef],
  )
  /** The form is finished with (sent, discarded, or closed after a save): drop the local copy. */
  const finished = useCallback(() => {
    doneRef.current = true
    removeLocalDraft(localId)
  }, [localId])
  return { localId, syncedRef, touchedRef, serverJson, latest, doneRef, pinnedRef, unsavedNow, writeLocal, finished }
}
