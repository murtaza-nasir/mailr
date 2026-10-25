import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, Pencil, Send, X } from 'lucide-react'
import { api } from '../../api/client'
import { qk } from '../../api/hooks'
import type { ComposePayload, Outbox } from '../../api/types'
import { EmptyState } from '../../components/bits'
import { formatOptionTime } from '../../lib/snooze'
import { useCompose } from '../../stores/compose'
import { toast, toastError } from '../../stores/ui'
import { RowSkeleton } from './ConversationRow'
import { t } from '../../i18n'

type Scheduled = Outbox & { payload?: ComposePayload }

/** Messages waiting to be sent later. Listed from the outbox (not from conversations), so a
 *  scheduled NEW message shows here too, and each can be edited, cancelled or sent now. */
export function ScheduledList({ account = null }: { account?: number | null }) {
  const qc = useQueryClient()
  const openCompose = useCompose((s) => s.open)
  // An account's own Scheduled folder lists the sends from its addresses (GET /outbox?account=).
  const q = useQuery<Scheduled[]>({
    queryKey: account ? [...qk.outbox('scheduled'), account] : qk.outbox('scheduled'),
    queryFn: ({ signal }) => api.get<Scheduled[]>('/outbox', { scheduled: 'true', ...(account ? { account: String(account) } : {}) }, signal),
    refetchInterval: 60_000,
  })

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['outbox'] })
    void qc.invalidateQueries({ queryKey: ['me'] })
    void qc.invalidateQueries({ queryKey: qk.conversations })
  }

  const cancel = async (o: Scheduled, reopen: boolean) => {
    try {
      const r = await api.post<{ payload: ComposePayload; draft_id?: number | null }>(`/outbox/${o.id}/cancel`)
      if (reopen) openCompose({ payload: r.payload ?? o.payload, draftId: r.draft_id ?? null, conversationId: o.conversation_id ?? undefined })
      toast({ message: reopen ? t('mail.scheduledList.scheduledSendCancelledEditAnd') : t('mail.scheduledList.scheduledSendCancelled') })
    } catch (e) {
      toastError(e, t('mail.scheduledList.couldNotCancel'))
    } finally {
      refresh()
    }
  }

  const sendNow = async (o: Scheduled) => {
    try {
      await api.post(`/outbox/${o.id}/send-now`)
      toast({ message: t('mail.scheduledList.sendingNow') })
    } catch (e) {
      toastError(e, t('mail.scheduledList.couldNotSend'))
    } finally {
      refresh()
    }
  }

  if (q.isLoading) return <RowSkeleton count={4} />
  if (q.isError)
    return (
      <EmptyState title={t('mail.scheduledList.couldntLoadScheduledMessages')}>
        <p>{(q.error as Error).message}</p>
        <button type="button" className="btn tonal" onClick={() => void q.refetch()}>
          {t('common.retry')}
        </button>
      </EmptyState>
    )
  const items = q.data ?? []
  if (!items.length)
    return (
      <EmptyState icon={<Clock />} title={t('mail.scheduledList.nothingScheduled')}>
        <p>{t('mail.scheduledList.messagesYouScheduleWillBe')}</p>
      </EmptyState>
    )
  return (
    <ul className="scheduled-list" aria-label={t('mail.scheduledList.scheduledMessages')}>
      {items.map((o) => (
        <li key={o.id} className="scheduled-row">
          <div className="who" title={o.recipients.join(', ')}>
            {t('mail.scheduledList.to', { recipients: o.recipients.slice(0, 3).join(', '), value: o.recipients.length > 3 ? ` +${o.recipients.length - 3}` : '' })}
          </div>
          <div className="what">
            <span className="subject" dir="auto">{o.subject || t('common.noSubject')}</span>
          </div>
          <div className="when">
            <Clock size={14} aria-hidden /> {formatOptionTime(new Date(o.send_at))}
          </div>
          <div className="actions">
            <button type="button" className="icon-btn sm" aria-label={t('common.edit2')} data-tip={t('common.edit2')} onClick={() => void cancel(o, true)}>
              <Pencil />
            </button>
            <button type="button" className="icon-btn sm" aria-label={t('common.sendNow')} data-tip={t('common.sendNow')} onClick={() => void sendNow(o)}>
              <Send />
            </button>
            <button type="button" className="icon-btn sm" aria-label={t('mail.scheduledList.cancelSend')} data-tip={t('mail.scheduledList.cancelSend')} onClick={() => void cancel(o, false)}>
              <X />
            </button>
          </div>
        </li>
      ))}
    </ul>
  )
}
