// Actions on a recording, shared by the sheet and the event detail: the follow-up draft
// (recording.follow_up) and the action-item review list (recording.to_tasks). The draft opens in compose and is never
// sent from here; ticked items become tasks linked to the recording.

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { Spinner } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { useCompose } from '../../stores/compose'
import { toast, toastError } from '../../stores/ui'
import { createTasks, followUp, proposeActionItems, type ActionItem } from './api'
import { fmtT } from './format'
import { t as tr } from '../../i18n'

export interface EventRef {
  uid: string
  occurrence?: string | null
}

// ACT recording.follow_up @web.calendar @web.recording
export async function startFollowUp(cid: number, rid: number, mode: 'summary' | 'assistant', ev?: EventRef | null): Promise<boolean> {
  try {
    const d = await followUp(cid, rid, { mode, event_uid: ev?.uid, occurrence: ev?.occurrence ?? undefined })
    useCompose.getState().open({ draftId: d.draft_id, payload: d.payload, conversationId: d.conversation_id ?? undefined })
    toast({ message: d.reply ? tr('speakr.actions.followUpDraftedAsA') : tr('speakr.actions.followUpDraftedNothingIs') })
    return true
  } catch (e) {
    toastError(e, tr('speakr.actions.noFollowUpDraft'))
    return false
  }
}

/** "Follow-up email" with its two modes. */
export function FollowUpButtons({ cid, rid, ev, disabled }: { cid: number; rid: number; ev?: EventRef | null; disabled?: boolean }) {
  const [busy, setBusy] = useState<null | 'summary' | 'assistant'>(null)
  const go = async (mode: 'summary' | 'assistant') => {
    setBusy(mode)
    await startFollowUp(cid, rid, mode, ev)
    setBusy(null)
  }
  return (
    <span className="rec-followup" role="group" aria-label={tr('speakr.actions.followUpEmail')}>
      <button type="button" className="btn tonal sm" disabled={disabled || !!busy} onClick={() => void go('summary')}
        data-tip={tr('speakr.actions.speakrsSummaryAsWrittenTo')}>
        {busy === 'summary' ? <Spinner small /> : null}{' '}{tr('speakr.actions.followUpEmail')}
      </button>
      <button type="button" className="btn text sm" disabled={disabled || !!busy} onClick={() => void go('assistant')}
        data-tip={tr('speakr.actions.writesItFromTheSummary', { name: assistantNameNow() })}>
        {busy === 'assistant' ? <Spinner small /> : null}{' '}{tr('speakr.actions.draftWith', { name: assistantNameNow() })}
      </button>
    </span>
  )
}

// ACT recording.to_tasks @web.recording
export function ActionItemsDialog({ cid, rid, title, ev, onClose }: { cid: number; rid: number; title: string; ev?: EventRef | null; onClose: () => void }) {
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['speakr', 'action-items', cid, rid],
    queryFn: () => proposeActionItems(cid, rid),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })
  const [ticked, setTicked] = useState<Record<number, boolean>>({})
  const items = q.data?.items ?? []
  const isOn = (i: number, it: ActionItem) => (ticked[i] ?? it.checked) && !it.already_added
  const chosen = items.filter((it, i) => isOn(i, it))
  const add = useMutation({
    mutationFn: () => createTasks(cid, rid, { items: chosen.map((it) => ({ text: it.text, due: it.due, t: it.t })), event_uid: ev?.uid, occurrence: ev?.occurrence }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ['speakr'] })
      void qc.invalidateQueries({ queryKey: ['tasks'] })
      toast({ message: tr('speakr.actions.tasksAdded', { count: r.created.length, value: r.pending ? tr('speakr.actions.waitingForApproval', { pending: r.pending }) : '' }) })
      onClose()
    },
    onError: (e) => toastError(e, tr('speakr.actions.tasksNotAdded')),
  })
  return (
    <Dialog
      open
      onClose={onClose}
      title={tr('speakr.actions.tasksFrom', { title })}
      wide
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
          <button type="button" className="btn filled" disabled={!chosen.length || add.isPending} onClick={() => add.mutate()}>
            {chosen.length ? tr('speakr.actions.addTasks', { count: chosen.length }) : tr('common.addTasks')}
          </button>
        </>
      }
    >
      {q.isLoading ? (
        <div className="rec-wait"><Spinner /> <span className="muted">{tr('speakr.actions.readingTheMeeting')}</span></div>
      ) : q.isError ? (
        <p className="error-text">{(q.error as Error).message}</p>
      ) : !items.length ? (
        <p className="muted">{q.data?.note ?? tr('speakr.actions.noActionItemsWereFound')}</p>
      ) : (
        <ul className="rec-items" aria-label={tr('speakr.actions.proposedTasks')}>
          {items.map((it, i) => (
            <li key={`${i}-${it.text}`} className={it.already_added ? 'done' : ''}>
              <label className="choice">
                <input type="checkbox" className="checkbox" checked={isOn(i, it)} disabled={it.already_added}
                  onChange={(e) => setTicked((t) => ({ ...t, [i]: e.target.checked }))} />
                <span>
                  {it.text}
                  <span className="desc">
                    {[it.mine ? tr('speakr.actions.me') : it.owner || tr('speakr.actions.noOwner'), it.due && tr('speakr.actions.due', { due: it.due }), it.already_added && tr('speakr.actions.alreadyAdded')].filter(Boolean).join(' · ')}
                    {it.t !== null && (
                      <> · <a href={it.url} target="_blank" rel="noreferrer">{fmtT(it.t)} <ExternalLink size={11} aria-hidden /></a></>
                    )}
                  </span>
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  )
}
