import { AlertTriangle, ArrowRight, CheckCircle2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { Connector } from '../../api/types'
import { Spinner } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { toast, toastError } from '../../stores/ui'
import { t, tx } from '../../i18n'

/** One source collection in a migration report (backend calendar/migrate.py, Collection.as_dict). */
export interface MigrationCollection {
  source: string
  kind: 'calendar' | 'addressbook'
  name: string
  color: string | null
  components: string[]
  target: string | null
  target_name: string
  action: 'create' | 'existing' | 'previous' | 'skipped'
  total: number
  copied: number
  skipped: number
  conflicts: number
  replaced: number
  failed: number
  partial: boolean
  conflict_uids: string[]
  failures: { uid: string; item: string; reason: string }[]
}

export interface MigrationResult {
  dry_run: boolean
  overwrite: boolean
  collections: MigrationCollection[]
  totals: { collections: number; created_collections: number; total: number; copied: number; skipped: number; conflicts: number; replaced: number; failed: number }
  not_migrated: { kind: string; name: string; detail: string; how: string }[]
  errors: string[]
}

/** A run of the job calendar.migrate (backend migrate.job_out). */
export interface MigrationJob {
  job: number
  status: 'queued' | 'running' | 'done' | 'failed'
  dry_run: boolean
  done: number
  total: number | null
  current: string
  result: MigrationResult | null
  error: string
}

type Step = 'preview' | 'review' | 'copy' | 'done'

const running = (j?: MigrationJob) => !j || j.status === 'queued' || j.status === 'running'

function useJob(cid: number, job: number | null) {
  return useQuery({
    queryKey: ['migration', cid, job],
    queryFn: () => api.get<MigrationJob>(`/connectors/${cid}/migration/${job}`),
    enabled: job != null,
    refetchInterval: (q) => (running(q.state.data) ? 1000 : false),
  })
}

/** "Copy into my mailr calendar…" on a connected CalDAV account (Settings → Calendar & tasks):
 *  a preview with counts, the copy with progress, the report, then the optional cut-over.
 *  ACT calendar.migrate @web.settings
 *  ACT calendar.migrate_cutover @web.settings */
export function MigrateDialog({ conn, onClose }: { conn: Connector; onClose: () => void }) {
  const qc = useQueryClient()
  const [step, setStep] = useState<Step>('preview')
  const [previewJob, setPreviewJob] = useState<number | null>(null)
  const [copyJob, setCopyJob] = useState<number | null>(null)
  const [overwrite, setOverwrite] = useState(false)
  const [makeDefault, setMakeDefault] = useState(false)
  const [hide, setHide] = useState(false)
  const [applied, setApplied] = useState<string | null>(null)
  const start = useMutation({
    mutationFn: (dry: boolean) => api.post<MigrationJob>(`/connectors/${conn.id}/migration`, { dry_run: dry, overwrite: !dry && overwrite }),
    onSuccess: (j, dry) => {
      if (dry) setPreviewJob(j.job)
      else {
        setCopyJob(j.job)
        setStep('copy')
      }
    },
    onError: (e) => toastError(e, t('settings.calendarMigration.couldNotStart')),
  })
  // The preview starts when the dialog opens (once, also under StrictMode's double effects).
  const started = useRef(false)
  useEffect(() => {
    if (started.current) return
    started.current = true
    start.mutate(true)
  }, [start])
  const preview = useJob(conn.id, previewJob)
  const copy = useJob(conn.id, copyJob)
  useEffect(() => {
    if (step === 'preview' && preview.data && !running(preview.data)) setStep('review')
  }, [step, preview.data])
  useEffect(() => {
    if (step === 'copy' && copy.data && !running(copy.data)) {
      setStep('done')
      void qc.invalidateQueries({ queryKey: ['calendar'] })
      void qc.invalidateQueries({ queryKey: ['tasks'] })
      void qc.invalidateQueries({ queryKey: ['connectors'] })
    }
  }, [step, copy.data, qc])
  const cutover = useMutation({
    mutationFn: () => api.post<{ hidden: number; defaults: unknown }>(`/connectors/${conn.id}/migration/cutover`, { make_default: makeDefault, hide_copied: hide }),
    onSuccess: (r) => {
      const parts = [makeDefault ? t('settings.calendarMigration.newEventsAndTasksNow') : '', hide ? t('settings.calendarMigration.calendarsOfHiddenInCalendar', { count: r.hidden, connName: conn.name }) : '']
      setApplied(parts.filter(Boolean).join(' '))
      void qc.invalidateQueries({ queryKey: ['calendar'] })
      void qc.invalidateQueries({ queryKey: ['tasks'] })
      toast({ message: t('settings.calendarMigration.changesApplied') })
    },
    onError: (e) => toastError(e, t('settings.calendarMigration.notApplied')),
  })

  const pv = preview.data?.result
  const done = copy.data?.result
  const failed = (step === 'review' && preview.data?.status === 'failed') || (step === 'done' && copy.data?.status === 'failed')
  const failure = step === 'review' ? preview.data?.error : copy.data?.error
  const nothing = pv && pv.totals.copied + pv.totals.replaced === 0 && pv.totals.conflicts === 0
  return (
    <Dialog
      open
      wide
      modal
      onClose={onClose}
      title={t('settings.calendarMigration.copyIntoYourMailrCalendar', { connName: conn.name })}
      footer={
        step === 'review' && !failed ? (
          <>
            <button type="button" className="btn text" onClick={onClose}>
              {t('common.cancel')}
            </button>
            <button type="button" className="btn filled" disabled={start.isPending || !pv || !!nothing} onClick={() => start.mutate(false)}>
              {pv ? t('settings.calendarMigration.copyItems', { count: pv.totals.copied + (overwrite ? pv.totals.conflicts : 0) }) : t('common.copy')}
            </button>
          </>
        ) : step === 'done' && !failed ? (
          <>
            <button type="button" className="btn text" onClick={onClose}>
              {t('common.close')}
            </button>
            <button type="button" className="btn filled" disabled={(!makeDefault && !hide) || cutover.isPending || !!applied} onClick={() => cutover.mutate()}>
              {t('common.apply')}
            </button>
          </>
        ) : (
          <button type="button" className="btn text" onClick={onClose}>
            {step === 'copy' ? t('settings.calendarMigration.closeTheCopyGoesOn') : t('common.close')}
          </button>
        )
      }
    >
      <div className="stack mig" data-testid="migrate-dialog">
        {failed ? (
          <div className="banner error" role="alert">
            <AlertTriangle />
            <span>{t('settings.calendarMigration.nothingOnWasChanged', { failure: failure || t('settings.calendarMigration.theCopyStopped'), connName: conn.name })}</span>
          </div>
        ) : step === 'preview' ? (
          <Progress label={t('settings.calendarMigration.readingAndYourMailrCalendar', { connName: conn.name })} job={preview.data} />
        ) : step === 'copy' ? (
          <Progress label={t('settings.calendarMigration.copying')} job={copy.data} />
        ) : step === 'review' && pv ? (
          <>
            <p className="muted" style={{ margin: 0 }}>
              {t('settings.calendarMigration.everyCalendarTaskListAnd', { connName: conn.name })}
            </p>
            <ReportTable result={pv} dry />
            {pv.totals.conflicts > 0 && (
              <label className="choice">
                <input type="checkbox" className="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
                <span>
                  {tx('settings.calendarMigration.replaceTheWithTheVersion', { value: t('settings.calendarMigration.itemsThatDiffer', { count: pv.totals.conflicts }), connName: conn.name }, { span: (c) => <span className="desc">{c}</span> })}
                </span>
              </label>
            )}
            {nothing && (
              <div className="banner">
                <CheckCircle2 />
                <span>{t('settings.calendarMigration.everythingIsAlreadyInYour')}</span>
              </div>
            )}
            <NotCopied result={pv} />
          </>
        ) : step === 'done' && done ? (
          <>
            <div className="banner" role="status">
              <CheckCircle2 />
              <span>
                {t('settings.calendarMigration.copiedItems', { count: done.totals.copied, value: done.totals.replaced ? t('settings.calendarMigration.replaced', { replaced: done.totals.replaced }) : '', value2: done.totals.skipped ? t('settings.calendarMigration.wereAlreadyThere', { skipped: done.totals.skipped }) : '', value3: done.totals.failed ? t('settings.calendarMigration.couldNotBeCopied', { failed: done.totals.failed }) : '' })}
              </span>
            </div>
            <ReportTable result={done} />
            <Failures result={done} />
            <NotCopied result={done} />
            <div className="mig-cutover" data-testid="migrate-cutover">
              <h3>{t('settings.calendarMigration.switchOverOptional')}</h3>
              <p className="muted">{t('settings.calendarMigration.bothChangeOnlyMailrKeeps', { connName: conn.name })}</p>
              <label className="choice">
                <input type="checkbox" className="checkbox" checked={makeDefault} disabled={!!applied} onChange={(e) => setMakeDefault(e.target.checked)} />
                <span>{t('settings.calendarMigration.useTheMailrCalendarFor')}</span>
              </label>
              <label className="choice">
                <input type="checkbox" className="checkbox" checked={hide} disabled={!!applied} onChange={(e) => setHide(e.target.checked)} />
                <span>
                  {tx('settings.calendarMigration.hideTheCopiedCalendarsOf', { connName: conn.name }, { span: (c) => <span className="desc">{c}</span> })}
                </span>
              </label>
              {applied && <p className="mig-applied">{applied}</p>}
            </div>
          </>
        ) : (
          <Spinner />
        )}
      </div>
    </Dialog>
  )
}

function Progress({ label, job }: { label: string; job?: MigrationJob }) {
  const total = job?.total ?? 0
  const done = Math.min(job?.done ?? 0, total)
  const pct = total ? Math.round((done / total) * 100) : 0
  return (
    <div className="mig-progress" aria-live="polite">
      <div>{label}</div>
      <div className="mig-bar" role="progressbar" aria-label={t('settings.calendarMigration.progress')} aria-valuemin={0} aria-valuemax={total || 1} aria-valuenow={done}>
        <span style={{ width: `${total ? pct : 4}%` }} />
      </div>
      <div className="muted">{total ? t('settings.calendarMigration.ofItems', { done, count: total, value: job?.current ? ` · ${job.current}` : '' }) : t('settings.calendarMigration.listing')}</div>
    </div>
  )
}

function where(c: MigrationCollection): string {
  if (c.action === 'create') return t('settings.calendarMigration.new', { targetName: c.target_name })
  return t('settings.calendarMigration.into', { targetName: c.target_name })
}

function ReportTable({ result, dry }: { result: MigrationResult; dry?: boolean }) {
  const rows = result.collections.filter((c) => c.action !== 'skipped')
  return (
    <div className="table-wrap">
      <table className="table mig-table" aria-label={dry ? t('settings.calendarMigration.whatTheCopyDoes') : t('settings.calendarMigration.whatWasCopied')}>
        <thead>
          <tr>
            <th>{t('common.from')}</th>
            <th aria-label="to" />
            <th>{t('settings.calendarMigration.inMailr')}</th>
            <th>{dry ? t('settings.calendarMigration.toCopy') : t('settings.calendarMigration.copied')}</th>
            <th>{t('settings.calendarMigration.alreadyThere')}</th>
            <th>{dry ? t('settings.calendarMigration.differ') : t('settings.calendarMigration.conflicts')}</th>
            {!dry && <th>{t('common.failed')}</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.source}>
              <td>
                <span className="mig-dot" style={{ background: c.color ?? 'transparent' }} aria-hidden />
                {c.name}
                <div className="muted mig-kind">{c.kind === 'addressbook' ? t('common.addressBook') : c.components.join(' ') === 'VTODO' ? t('settings.calendarMigration.taskList') : 'calendar'}{c.partial ? t('settings.calendarMigration.theServerListedOnlyPart') : ''}</div>
              </td>
              <td aria-hidden>
                <ArrowRight size={14} />
              </td>
              <td>{where(c)}</td>
              <td>{dry ? c.copied : c.copied + c.replaced}</td>
              <td>{c.skipped}</td>
              <td>{dry ? c.conflicts + c.replaced : c.conflicts}</td>
              {!dry && <td>{c.failed}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Failures({ result }: { result: MigrationResult }) {
  const rows = result.collections.flatMap((c) => c.failures.map((f) => ({ ...f, from: c.name })))
  const conflicts = result.collections.filter((c) => c.conflicts > 0)
  if (!rows.length && !conflicts.length) return null
  return (
    <div className="mig-notes">
      {conflicts.length > 0 && (
        <p>
          {t('settings.calendarMigration.betweenAndYourMailrCalendar', { differ: differ(conflicts.reduce((n, c) => n + c.conflicts, 0)), map: conflicts.map((c) => c.name).join(', ') })}
        </p>
      )}
      {rows.length > 0 && (
        <ul aria-label={t('settings.calendarMigration.notCopied')}>
          {rows.slice(0, 50).map((f, i) => (
            <li key={i}>
              <b>{f.from}</b>
              {f.uid ? ` · ${f.uid}` : f.item ? ` · ${f.item}` : ''}: {f.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

const differ = (n: number) => (t('settings.calendarMigration.itemsDiffer', { count: n }))

function NotCopied({ result }: { result: MigrationResult }) {
  if (!result.not_migrated.length) return null
  return (
    <div className="mig-notes" data-testid="migrate-not-copied">
      <h3>{t('settings.calendarMigration.setUpAgainByHand')}</h3>
      <ul>
        {result.not_migrated.map((x, i) => (
          <li key={i}>
            <b>{x.name}</b>: {x.detail} {x.how}
          </li>
        ))}
      </ul>
    </div>
  )
}
