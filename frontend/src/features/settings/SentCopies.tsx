import { CheckCircle2 } from 'lucide-react'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { Connector, SentCopies } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { toast, toastError } from '../../stores/ui'
import { t } from '../../i18n'

/* Mail sent from mailr saved in the provider's Sent folder (backend connectors/sentcopy.py), for
 * mirror-mode IMAP accounts: the setting in the account's edit form, the copy of past sent mail, and
 * the Retry on the account's warning. */

export type SaveSent = SentCopies['setting']

/** A run of the job sent.backfill (backend sentcopy.job_out). */
export interface SentBackfillJob {
  job: number
  status: 'queued' | 'running' | 'done' | 'failed'
  dry_run: boolean
  since: string | null
  done: number
  total: number | null
  result: { total: number; present: number; copied: number; failed: number } | null
  error: string
}

export const SAVE_SENT_CHOICES: SaveSent[] = ['auto', 'on', 'off']

export function saveSentLabel(v: SaveSent): string {
  return v === 'on' ? t('settings.sentCopies.on') : v === 'off' ? t('settings.sentCopies.off') : t('settings.sentCopies.automatic')
}

/** What the chosen setting does for this account, in one line. */
export function saveSentHint(sc: SentCopies, v: SaveSent): string {
  if (v === 'off') return t('settings.sentCopies.hintOff', { provider: sc.provider })
  if (v === 'on') return sc.provider_saves ? t('settings.sentCopies.hintOnProviderSaves', { provider: sc.provider }) : t('settings.sentCopies.hintOn', { provider: sc.provider })
  return sc.provider_saves ? t('settings.sentCopies.hintAutoProviderSaves', { provider: sc.provider }) : t('settings.sentCopies.hintOn', { provider: sc.provider })
}

/** The body of POST /connectors/{id}/sent-backfill: a count (dry run) or the copy, from a date or all. */
export function backfillBody(dryRun: boolean, since: string): { dry_run: boolean; since: string | null } {
  return { dry_run: dryRun, since: since.trim() || null }
}

/** One line on a finished count or copy. */
export function backfillSummary(j: SentBackfillJob, provider: string): string {
  const r = j.result
  if (!r) return j.error || t('settings.sentCopies.stopped')
  if (j.dry_run) {
    if (r.copied === 0) return t('settings.sentCopies.nothingToCopy', { count: r.total, provider })
    return t('settings.sentCopies.toCopy', { count: r.copied, total: r.total, provider })
  }
  return [
    t('settings.sentCopies.copied', { count: r.copied, provider }),
    r.present ? t('settings.sentCopies.alreadyThere', { count: r.present }) : '',
    r.failed ? t('settings.sentCopies.notCopied', { count: r.failed }) : '',
  ].filter(Boolean).join(' ')
}

/** The warning beside the Retry: sent messages that could not be saved at the provider, or null. */
export function sentCopiesWarning(sc: SentCopies | null | undefined): string | null {
  if (!sc?.failed) return null
  return t('settings.sentCopies.failedLine', { count: sc.failed, provider: sc.provider })
}

const running = (j?: SentBackfillJob) => !j || j.status === 'queued' || j.status === 'running'

/** The setting and the backfill button inside the account's edit form. */
export function SentCopiesFields({ c, value, onChange }: { c: Connector; value: SaveSent; onChange: (v: SaveSent) => void }) {
  const [backfill, setBackfill] = useState(false)
  const sc = c.sent_copies
  if (!sc) return null
  return (
    <div className="field">
      <label htmlFor="ec-save-sent">{t('settings.sentCopies.saveSentMailAtTheProvider')}</label>
      {/* ACT settings.account_save_sent @web.settings */}
      <select id="ec-save-sent" className="select" value={value} onChange={(e) => onChange(e.target.value as SaveSent)} data-testid="save-sent">
        {SAVE_SENT_CHOICES.map((v) => (
          <option key={v} value={v}>
            {saveSentLabel(v)}
          </option>
        ))}
      </select>
      <span className="hint">{saveSentHint(sc, value)}</span>
      <div>
        <button type="button" className="btn text sm" style={{ paddingInline: 0 }} onClick={() => setBackfill(true)} data-testid="sent-backfill-open">
          {t('settings.sentCopies.copyPastSentMailTo', { provider: sc.provider })}
        </button>
      </div>
      {backfill && <SentBackfillDialog c={c} onClose={() => setBackfill(false)} />}
    </div>
  )
}

/** "Copy past sent mail to <provider>": count first, then copy, with progress.
 *  ACT settings.account_sent_backfill @web.settings */
export function SentBackfillDialog({ c, onClose }: { c: Connector; onClose: () => void }) {
  const qc = useQueryClient()
  const provider = c.sent_copies?.provider ?? c.name
  const [since, setSince] = useState('')
  const [job, setJob] = useState<number | null>(null)
  const start = useMutation({
    mutationFn: (dry: boolean) => api.post<SentBackfillJob>(`/connectors/${c.id}/sent-backfill`, backfillBody(dry, since)),
    onSuccess: (j) => setJob(j.job),
    onError: (e) => toastError(e, t('settings.sentCopies.couldNotStart')),
  })
  const q = useQuery({
    queryKey: ['sent-backfill', c.id, job],
    queryFn: () => api.get<SentBackfillJob>(`/connectors/${c.id}/sent-backfill/${job}`),
    enabled: job != null,
    refetchInterval: (s) => (running(s.state.data) ? 1000 : false),
  })
  const j = q.data
  const busy = start.isPending || (job != null && running(j))
  const counted = j && !running(j) && j.dry_run && j.result
  const finished = j && !running(j) && !j.dry_run
  const copy = () =>
    start.mutate(false, {
      onSuccess: () => {
        toast({ message: t('settings.sentCopies.copyStarted') })
        void qc.invalidateQueries({ queryKey: ['connectors'] })
      },
    })
  const total = j?.total ?? 0
  return (
    <Dialog
      open
      modal
      onClose={onClose}
      title={t('settings.sentCopies.copyPastSentMailTo', { provider })}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {busy && job != null ? t('settings.sentCopies.closeItGoesOn') : t('common.close')}
          </button>
          {!finished && (
            counted && j.result!.copied > 0 ? (
              <button type="button" className="btn filled" disabled={busy} onClick={copy} data-testid="sent-backfill-copy">
                {t('settings.sentCopies.copyMessages', { count: j.result!.copied })}
              </button>
            ) : (
              <button type="button" className="btn filled" disabled={busy} onClick={() => start.mutate(true)} data-testid="sent-backfill-count">
                {t('settings.sentCopies.count')}
              </button>
            )
          )}
        </>
      }
    >
      <div className="stack" data-testid="sent-backfill">
        <p style={{ margin: 0 }}>{t('settings.sentCopies.backfillIntro', { provider, name: c.name })}</p>
        <div className="field">
          <label htmlFor="sb-since">{t('settings.sentCopies.sentSince')}</label>
          <input id="sb-since" type="date" className="input" value={since} disabled={busy} onChange={(e) => { setSince(e.target.value); setJob(null) }} />
          <span className="hint">{t('settings.sentCopies.leaveEmptyForAll')}</span>
        </div>
        {job != null && running(j) && (
          <div className="mig-progress" aria-live="polite">
            <div>{j?.dry_run === false ? t('settings.sentCopies.copying') : t('settings.sentCopies.counting')}</div>
            <div className="mig-bar" role="progressbar" aria-label={t('settings.sentCopies.progress')} aria-valuemin={0} aria-valuemax={total || 1} aria-valuenow={j?.done ?? 0}>
              <span style={{ width: `${total ? Math.round(((j?.done ?? 0) / total) * 100) : 4}%` }} />
            </div>
            {total > 0 && <div className="muted">{t('settings.sentCopies.ofMessages', { done: Math.min(j?.done ?? 0, total), count: total })}</div>}
          </div>
        )}
        {j && !running(j) && (
          <div className={`banner${j.status === 'failed' ? ' error' : ''}`} role="status">
            <CheckCircle2 />
            <span>{backfillSummary(j, provider)}</span>
          </div>
        )}
      </div>
    </Dialog>
  )
}

/** The Retry on an account whose sent messages could not be saved at the provider.
 *  ACT settings.account_sent_retry @web.settings */
export function SentCopiesRetry({ c }: { c: Connector }) {
  const qc = useQueryClient()
  const retry = useMutation({
    mutationFn: () => api.post<{ queued: number }>(`/connectors/${c.id}/sent-copies/retry`),
    onSuccess: (r) => {
      toast({ message: t('settings.sentCopies.retrying', { count: r.queued }) })
      void qc.invalidateQueries({ queryKey: ['connectors'] })
    },
    onError: (e) => toastError(e),
  })
  if (!c.sent_copies?.failed) return null
  return (
    <button type="button" className="btn text sm" style={{ padding: 0, height: 'auto' }} disabled={retry.isPending} onClick={() => retry.mutate()} data-testid="sent-copies-retry">
      {t('settings.sentCopies.retry')}
    </button>
  )
}
