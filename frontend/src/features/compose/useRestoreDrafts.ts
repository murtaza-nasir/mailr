// Reopen compose text the server never confirmed: after a reload, a crash or a sign-out,
// each local copy (lib/draftStore.ts) comes back as a window that says "Restored unsent draft"
// and saves itself to the server, which is the source of truth from then on.
import { useEffect } from 'react'
import { beat, openFormIds, restorableDrafts, saveLocalDraft, tabId } from '../../lib/draftStore'
import { useCompose } from '../../stores/compose'

export function useRestoreDrafts(owner: number | undefined): void {
  // This tab is alive: other tabs leave its drafts alone.
  useEffect(() => {
    const tab = tabId()
    beat(tab, Date.now())
    const t = setInterval(() => beat(tab, Date.now()), 5000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (owner === undefined) return
    const tab = tabId()
    const compose = useCompose.getState()
    const open = openFormIds()
    for (const w of compose.windows) open.add(w.localId)
    for (const d of restorableDrafts(owner, tab, Date.now())) {
      if (open.has(d.localId)) continue
      // Claim it for this tab before the window opens, so another tab does not take it too.
      saveLocalDraft({ ...d, tab })
      compose.open({ payload: d.payload, draftId: d.draftId, conversationId: d.conversationId, localId: d.localId, restored: true })
    }
  }, [owner])
}
