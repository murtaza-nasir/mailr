// What has not left yet: the send states the thread shows on a message (Sending, Sent,
// Not sent, Waiting for your approval), the Outbox list, and the actions on them. One query, GET
// /outbox?active=true, feeds all of them; the live `outbox` event refreshes it.
//
// ACT draft.retry_send @web.reader @web.list, ACT draft.outbox @web.nav @web.list

import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { AlertCircle, Check, Clock, Pencil, RotateCw, Send, ShieldCheck, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { qk } from '../../api/hooks'
import type { ComposePayload, Message, Outbox } from '../../api/types'
import { EmptyState } from '../../components/bits'
import { formatListDate } from '../../lib/format'
import { useCompose } from '../../stores/compose'
import { toast, toastError, useUI } from '../../stores/ui'
import { RowSkeleton } from './ConversationRow'
import { appNavigate } from '../../lib/appNav'
import { mailPath } from '../../lib/views'
import { t as tr } from '../../i18n'

export type OutboxRow = Outbox & { payload?: ComposePayload }

export const activeOutboxKey = ['outbox', 'active'] as const

/** Queued to go within a minute, sending, waiting for approval, failed this week, sent in the last two
 * minutes. Polled while something is on its way, since a worker's answer can come without an event. */
export function useActiveOutbox() {
  return useQuery<OutboxRow[]>({
    queryKey: activeOutboxKey,
    queryFn: ({ signal }) => api.get<OutboxRow[]>('/outbox', { active: 'true' }, signal),
    staleTime: 10_000,
    refetchInterval: (q) => ((q.state.data ?? []).some((o) => o.status === 'queued' || o.status === 'sending') ? 5000 : 60_000),
  })
}

/** Did not leave, or may not have: `uncertain` is a send whose connection dropped after the message
 * was handed over, which the user decides about (Retry or Discard). */
export const notSent = (o: Pick<Outbox, 'status'>) => o.status === 'failed' || o.status === 'uncertain'

/** The rows the Outbox lists, and whose number its nav entry shows: not left yet, or not sent. */
export const inOutbox = (o: OutboxRow) => o.status === 'queued' || o.status === 'sending' || notSent(o)

/** The Outbox nav entry exists only while something is queued or failed (no new
 * permanent entry). */
export function useOutboxCount(): { total: number; failed: number } {
  const rows = useActiveOutbox().data ?? []
  const mine = rows.filter(inOutbox)
  return { total: mine.length, failed: mine.filter(notSent).length }
}

export type SendState = { kind: 'sending' | 'not_sent' | 'approval' | 'sent'; row: OutboxRow }

const bare = (id: string | null | undefined) => (id ?? '').replace(/^<|>$/g, '').trim().toLowerCase()

/** A message's send state: a saved draft that is on its way, failed or waiting for approval, or a
 * message that left a moment ago. */
export function sendStateFor(m: Message, rows: OutboxRow[]): SendState | null {
  if (m.draft) {
    const row = rows.find((o) => o.draft_id === m.id && o.status !== 'sent')
    if (!row) return null
    if (notSent(row)) return { kind: 'not_sent', row }
    if (row.status === 'awaiting_approval') return { kind: 'approval', row }
    return { kind: 'sending', row }
  }
  if (m.sent_by_me && m.message_id) {
    const row = rows.find((o) => o.status === 'sent' && o.sent_message_id && bare(o.sent_message_id) === bare(m.message_id))
    if (row) return { kind: 'sent', row }
  }
  return null
}

/** A send error in plain words; the raw text stays behind Details. */
export function plainSendError(error: string): string {
  const e = error.toLowerCase()
  if (!e) return tr('mail.outbox.theMailServerDidNot')
  if (e.includes('interrupted')) return tr('mail.outbox.sendingWasInterruptedItMay')
  if (/recipient|5\.1\.[0-9]|user unknown|no such user|mailbox unavailable|does not exist/.test(e)) return tr('mail.outbox.theMailServerRefusedAn')
  if (/auth|535|password|credential|login|sign.?in/.test(e)) return tr('mail.outbox.theMailServerRefusedThe')
  if (/timed? ?out|timeout|connect|unreachable|network|resolve|dns/.test(e)) return tr('mail.outbox.theMailServerCouldNot')
  if (/too large|size|552/.test(e)) return tr('mail.outbox.theMessageIsTooLarge')
  if (/spam|block|policy|reject/.test(e)) return tr('mail.outbox.theMailServerRejectedThe')
  return tr('mail.outbox.theMailServerDidNot')
}

function refresh(qc: QueryClient) {
  void qc.invalidateQueries({ queryKey: ['outbox'] })
  void qc.invalidateQueries({ queryKey: qk.conversations })
}

export function useOutboxActions() {
  const qc = useQueryClient()
  const openCompose = useCompose((s) => s.open)
  return {
    // ACT draft.retry_send @web.reader @web.list
    retry: async (o: OutboxRow) => {
      try {
        await api.post(`/outbox/${o.id}/retry`)
        toast({ message: tr('mail.outbox.sendingAgain'), duration: 3000 })
      } catch (e) {
        toastError(e, tr('mail.outbox.couldNotRetry'))
      } finally {
        refresh(qc)
      }
    },
    /** Take it back to the composer (a queued one is unsent, a failed one cancelled). */
    edit: async (o: OutboxRow) => {
      try {
        const r = await api.post<{ payload: ComposePayload; draft_id?: number | null }>(`/outbox/${o.id}/cancel`)
        openCompose({ payload: r.payload ?? o.payload, draftId: r.draft_id ?? null, conversationId: o.conversation_id ?? undefined })
      } catch (e) {
        toastError(e, tr('mail.outbox.tooLateToEditIt'))
      } finally {
        refresh(qc)
      }
    },
    discard: async (o: OutboxRow) => {
      try {
        await api.post(`/outbox/${o.id}/cancel`)
        toast({ message: notSent(o) ? tr('mail.outbox.takenOutOfTheOutbox') : tr('mail.outbox.sendingCancelledASavedDraft') })
      } catch (e) {
        toastError(e, tr('mail.outbox.couldNotCancel'))
      } finally {
        refresh(qc)
      }
    },
    /** Withdraw a send without a toast of its own (opening Ambrose's draft to edit it). */
    discardQuiet: async (o: OutboxRow) => {
      await api.post(`/outbox/${o.id}/cancel`).catch(() => undefined)
      refresh(qc)
    },
    // ACT approval.approve @web.reader: the send Ambrose asked for, from the draft in the thread.
    approve: async (o: OutboxRow) => {
      try {
        await api.post(`/outbox/${o.id}/approve`)
        toast({ message: tr('mail.outbox.approvedSendingTo', { who: who(o) }), duration: 4000 })
      } catch (e) {
        toastError(e, tr('mail.outbox.couldNotApprove'))
      } finally {
        refresh(qc)
        void qc.invalidateQueries({ queryKey: ['approvals'] })
      }
    },
  }
}

/** "Marco Silva" or "marco@example.com and 2 others". */
export function who(o: Pick<Outbox, 'recipients'>): string {
  const r = o.recipients ?? []
  if (!r.length) return tr('mail.outbox.noOne')
  return r.length === 1 ? r[0] : tr('mail.outbox.and', { r: r[0], value: r.length - 1, value2: r.length === 2 ? 'other' : 'others' })
}

/** Seconds left in the undo window of a queued send, ticking. */
function useSecondsLeft(sendAt: string | undefined): number {
  const [now, setNow] = useState(() => Date.now())
  const at = sendAt ? new Date(sendAt).getTime() : 0
  useEffect(() => {
    if (at <= now) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [at, now])
  return Math.max(0, Math.ceil((at - now) / 1000))
}

/** The line on a message in the thread: Sending / Sent / Not sent (Retry, Edit) / Waiting for your
 * approval (Approve, Edit). Its height is fixed so it never moves the message below. */
export function SendStatusLine({ state, onEditDraft }: { state: SendState; onEditDraft?: () => void }) {
  const a = useOutboxActions()
  const o = state.row
  const left = useSecondsLeft(o.status === 'queued' ? o.send_at : undefined)
  if (state.kind === 'sent')
    return (
      <div className="send-status sent" role="status" data-testid="send-status">
        <Check size={14} aria-hidden />{' '}{tr('common.sent')}
      </div>
    )
  if (state.kind === 'sending')
    return (
      <div className="send-status sending" role="status" data-testid="send-status">
        <Send size={14} aria-hidden /> <span>{tr('common.sending')}</span>
        {o.status === 'queued' && left > 0 && (
          <button type="button" className="btn text sm" onClick={() => void a.edit(o)}>{tr('mail.outbox.undo', { left })}</button>
        )}
      </div>
    )
  if (state.kind === 'approval')
    return (
      <div className="send-status approval" role="status" data-testid="send-status">
        <ShieldCheck size={14} aria-hidden /> <span>{tr('mail.outbox.waitingForYourApprovalTo', { who: who(o) })}</span>
        <button type="button" className="btn tonal sm" onClick={() => void a.approve(o)}>{tr('mail.outbox.approve')}</button>
        {onEditDraft && <button type="button" className="btn text sm" onClick={onEditDraft}>{tr('common.edit2')}</button>}
      </div>
    )
  return (
    <div className="send-status failed" role="alert" data-testid="send-status">
      <AlertCircle size={14} aria-hidden /> <span><b>{o.status === 'uncertain' ? tr('mail.outbox.mayHaveBeenSent') : tr('mail.outbox.notSent')}</b> {plainSendError(o.error)}</span>
      <button type="button" className="btn tonal sm" onClick={() => void a.retry(o)}>{tr('common.retry')}</button>
      <button type="button" className="btn text sm" onClick={() => void a.edit(o)}>{tr('common.edit2')}</button>
      {o.error && (
        <details className="send-status-details">
          <summary>{tr('common.details')}</summary>
          <code>{o.error}</code>
        </details>
      )}
    </div>
  )
}

/** The Outbox view (/mail/outbox): what is on its way and what was not sent, with Retry, Edit and
 * Discard. Waiting-for-approval sends live in the approvals popover. */
export function OutboxList() {
  const q = useActiveOutbox()
  const a = useOutboxActions()
  if (q.isLoading) return <RowSkeleton count={2} />
  const rows = (q.data ?? []).filter(inOutbox)
  if (!rows.length)
    return (
      <EmptyState icon={<Check />} title={tr('mail.outbox.everythingHasBeenSent')}>
        <p>{tr('mail.outbox.messagesThatAreOnTheir')}</p>
      </EmptyState>
    )
  return (
    <ul className="scheduled-list outbox-list" aria-label={tr('common.outbox')}>
      {rows.map((o) => (
        <li key={o.id} className={`scheduled-row outbox-row${notSent(o) ? ' failed' : ''}`} data-testid="outbox-row">
          <div className="who" title={o.recipients.join(', ')}>{tr('mail.outbox.to', { who: who(o) })}</div>
          <div className="what">
            <span className="subject" dir="auto">{o.subject || tr('common.noSubject')}</span>
            {notSent(o) && (
              <span className="outbox-why">
                {' · '}{plainSendError(o.error)}
                {o.error && (
                  <details className="send-status-details">
                    <summary>{tr('common.details')}</summary>
                    <code>{o.error}</code>
                  </details>
                )}
              </span>
            )}
          </div>
          <div className={`when${notSent(o) ? ' bad' : ''}`}>
            {notSent(o) ? <><AlertCircle size={14} aria-hidden />{' '}{o.status === 'uncertain' ? tr('mail.outbox.mayHaveBeenSent') : tr('common.notSent')}</> : <><Clock size={14} aria-hidden />{' '}{tr('common.sending')}</>}
            {o.created_at && <span className="muted"> · {formatListDate(o.created_at)}</span>}
          </div>
          <div className="actions">
            {notSent(o) && (
              <button type="button" className="icon-btn sm" aria-label={tr('common.retry')} data-tip={tr('common.retry')} onClick={() => void a.retry(o)}><RotateCw /></button>
            )}
            {o.status !== 'sending' && (
              <button type="button" className="icon-btn sm" aria-label={tr('common.edit2')} data-tip={tr('common.edit2')} onClick={() => void a.edit(o)}><Pencil /></button>
            )}
            {o.status !== 'sending' && (
              <button type="button" className="icon-btn sm" aria-label={notSent(o) ? tr('common.discard') : tr('mail.outbox.cancelSend')} data-tip={notSent(o) ? tr('common.discard') : tr('mail.outbox.cancelSend')} onClick={() => void a.discard(o)}><Trash2 /></button>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}

// ---------------------------------------------------------------- the send toast

interface Watch {
  toastId: number
  onSent: (o: OutboxRow) => void
  onFailed: (o: OutboxRow) => void
  timer: ReturnType<typeof setTimeout> | null
  until: number
}
const watching = new Map<number, Watch>()

/** Follow one send until it has left or failed: the composer's "Sending…" toast becomes "Message sent"
 * only then. Polls from the end of the undo window, and the live `outbox` event (outboxEvent)
 * answers at once. */
export function watchSend(o: Outbox, toastId: number, onSent: (o: OutboxRow) => void, onFailed: (o: OutboxRow) => void) {
  const w: Watch = { toastId, onSent, onFailed, timer: null, until: new Date(o.send_at).getTime() + 120_000 }
  watching.set(o.id, w)
  const wait = Math.max(500, new Date(o.send_at).getTime() - Date.now() + 500)
  w.timer = setTimeout(() => void poll(o.id), wait)
}

async function poll(id: number) {
  const w = watching.get(id)
  if (!w) return
  try {
    const rows = await api.get<OutboxRow[]>('/outbox', { active: 'true' })
    const row = rows.find((r) => r.id === id)
    if (settle(id, row)) return
  } catch {
    /* try again below */
  }
  if (Date.now() > w.until) {
    // No answer within two minutes: the "Sending…" toast gives way to one that points at the Outbox,
    // where the message's state is shown.
    watching.delete(id)
    useUI.getState().dismissToast(w.toastId)
    toast({ message: tr('mail.outbox.stillSending'), actionLabel: tr('mail.outbox.openOutbox'), onAction: () => appNavigate(mailPath('outbox')), duration: 10_000 })
    return
  }
  w.timer = setTimeout(() => void poll(id), 1500)
}

function settle(id: number, row?: OutboxRow): boolean {
  const w = watching.get(id)
  if (!w) return true
  if (!row || !['sent', 'failed', 'uncertain', 'rejected'].includes(row.status)) return false
  watching.delete(id)
  if (w.timer) clearTimeout(w.timer)
  useUI.getState().dismissToast(w.toastId)
  if (row.status === 'sent') w.onSent(row)
  else w.onFailed(row)
  return true
}

/** Stop following a send (its Undo cancelled it): no more polling, no late "Message sent". */
export function unwatchSend(id: number) {
  const w = watching.get(id)
  if (!w) return
  if (w.timer) clearTimeout(w.timer)
  watching.delete(id)
  useUI.getState().dismissToast(w.toastId)
}

/** The live `outbox` event (api/events.ts): a watched send settles at once. Returns true when the
 * composer's toast will speak for it, so the generic failure toast stays away. */
export function outboxEvent(ev: { id?: unknown; status?: unknown }): boolean {
  const id = Number(ev.id)
  if (!watching.has(id)) return false
  if (ev.status === 'sent' || ev.status === 'failed' || ev.status === 'uncertain') void poll(id)
  return ev.status === 'failed' || ev.status === 'uncertain'
}
