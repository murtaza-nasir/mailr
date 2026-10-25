// "Share with Cleo" in a list's ⋮ menu (the household hand-off): one step to share a
// list, a note or a table with a member of your household, at the Edit level. The people dialog
// (sharing.tsx) stays for anything else. The entries appear only when the viewer may share and a
// household member does not have it yet; at most two, the rest are in "Share…".

import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { UserPlus } from 'lucide-react'
import { MenuItem } from '../../components/Popover'
import { toast, toastError } from '../../stores/ui'
import { shareApi, shareKey, type ShareKind, type ShareState } from './sharing'
import type { AccessLevel } from './types'
import { t } from '../../i18n'

const MAX = 2

/** The household members a quick share offers (pure): candidates in one of your groups, by name. */
export function quickShareTargets(st: Pick<ShareState, 'candidates' | 'household'> | undefined): { id: number; name: string }[] {
  if (!st?.household?.length) return []
  const ids = new Set(st.household)
  return st.candidates.filter((c) => ids.has(c.id)).slice(0, MAX).map((c) => ({ id: c.id, name: c.name.split(/\s+/)[0] || c.name }))
}

export async function quickShare(qc: QueryClient, kind: ShareKind, id: string, person: { id: number; name: string }): Promise<void> {
  try {
    const st = await shareApi.set(kind, id, String(person.id), 'edit')
    qc.setQueryData(shareKey(kind, id), st)
    void qc.invalidateQueries({ queryKey: kind === 'note' ? ['notes'] : ['tables'] })
    toast({
      message: t('notes.quickShare.sharedWith', { personName: person.name }),
      actionLabel: t('common.undo'),
      onAction: () => void shareApi.stop(kind, id, String(person.id)).then((s) => {
        qc.setQueryData(shareKey(kind, id), s)
        void qc.invalidateQueries({ queryKey: kind === 'note' ? ['notes'] : ['tables'] })
      }).catch((e) => toastError(e, t('notes.quickShare.couldNotStopSharing'))),
    })
  } catch (e) {
    toastError(e, t('notes.quickShare.couldNotShareWith', { personName: person.name }))
  }
}

/** The menu entries: "Share with Cleo", "Share with Dana". */
// ACT note.share_quick @web.note @web.notes
export function QuickShareItems({ kind, id, level, onDone }: { kind: ShareKind; id: string; level: AccessLevel; onDone?: () => void }) {
  const qc = useQueryClient()
  const may = level === 'owner' || level === 'share'
  const q = useQuery({ queryKey: shareKey(kind, id), queryFn: () => shareApi.get(kind, id), enabled: may, staleTime: 60_000 })
  if (!may) return null
  return (
    <>
      {quickShareTargets(q.data).map((p) => (
        <MenuItem key={p.id} icon={<UserPlus size={16} />} tip={t('notes.quickShare.canAddTickAndChange', { itemName: p.name })} onSelect={() => {
          onDone?.()
          void quickShare(qc, kind, id, p)
        }}>{t('notes.quickShare.shareWith', { itemName: p.name })}</MenuItem>
      ))}
    </>
  )
}
