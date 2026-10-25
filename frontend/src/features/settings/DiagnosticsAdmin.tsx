// Admin → Diagnostics: every readiness check of the server (backend core/health.py,
// rendered from whatever the server lists, so a new check needs no change here), the backup, push,
// the background work, each mail account's last sync, the model tasks and endpoints, and the
// reverse proxy headers. GET /admin/diagnostics; "Run checks now" asks for a fresh run.

import { AlertTriangle, CheckCircle2, Cpu, Globe, HardDrive, Info, RefreshCw } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useApi } from '../../api/hooks'
import type { AccountHealth, LastOutcome } from '../../api/types'
import { Skeleton } from '../../components/bits'
import { formatRelative } from '../../lib/format'
import { toastError } from '../../stores/ui'
import { Section } from './common'
import { t as tr, tx, appLocale } from '../../i18n'

export interface DiagCheck {
  name: string
  label: string
  ok: boolean
  detail: string
  required: boolean
  /** Nothing to watch: the feature is turned off on the server (e.g. the mail export). The check passes. */
  off?: boolean
}

/** A user whose daily cleanup of deleted mail held back (backend core/purge.py): most of their mail
 *  looks deleted, as after a mail store lost its data. */
export interface PurgeHeld {
  user: string
  rows: number
  files: number
  at: string | null
}

export interface Diagnostics {
  ok: boolean
  checked_at: string | null
  checks: DiagCheck[]
  backup: { mail_export?: boolean; store_backup_check?: boolean; purge_held?: PurgeHeld[]; target_configured: boolean; target_problem: string | null; marker: string; users: { user: string; exported_at: string | null; copied_at: string | null }[] }
  push: { ntfy_configured: boolean; devices: Record<string, number>; transports: Record<string, LastOutcome | null> }
  queue: { queued: number; running: number; due: number; oldest_due_seconds: number | null; failed_last_day: number }
  connectors: { id: number; name: string; user: string; kind: string; mode: string; status: string; detail: string; last_sync_at: string | null; health: AccountHealth }[]
  models: { tasks: { task: string; label: string; health: LastOutcome | null }[]; endpoints: { id: number; name: string; enabled: boolean; health: LastOutcome | null }[] }
  proxy: { trusted: string[]; source: 'set' | 'derived'; untrusted_seen_at: string | null }
}

interface InstanceStatus {
  store?: { ok?: boolean }
  users?: number
  messages?: number
  failed_jobs?: { kind: string; error: string; at: string }[]
}

const TRANSPORT: Record<string, string> = { webpush: tr('settings.diagnosticsAdmin.webPushBrowsers'), unifiedpush: tr('settings.diagnosticsAdmin.unifiedpushPhones'), ntfy: tr('settings.diagnosticsAdmin.ntfyTopics') }

/** The failing checks first, the rest in the server's order. */
export function orderChecks(checks: DiagCheck[]): DiagCheck[] {
  return [...checks.filter((c) => !c.ok && c.required), ...checks.filter((c) => !c.ok && !c.required), ...checks.filter((c) => c.ok)]
}

/** A check's row: passing, failing (a required check), or information (optional, or turned off). */
export function checkTone(c: DiagCheck): 'ok' | 'bad' | 'info' {
  if (c.off) return 'info'
  return c.ok ? 'ok' : c.required ? 'bad' : 'info'
}

/** "3 min" from seconds. */
export function waitText(seconds: number | null): string {
  if (seconds == null) return tr('settings.diagnosticsAdmin.nothingWaiting')
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))} s`
  if (seconds < 5400) return tr('settings.diagnosticsAdmin.min', { round: Math.round(seconds / 60) })
  return `${Math.round(seconds / 3600)} h`
}

/** The background work in one line. */
export function queueText(q: Diagnostics['queue']): string {
  const due = q.due ? tr('settings.diagnosticsAdmin.jobsDueNowTheOldest', { count: q.due, waitText: waitText(q.oldest_due_seconds) }) : tr('settings.diagnosticsAdmin.noJobsDueNow')
  const later = q.queued - q.due
  return tr('settings.diagnosticsAdmin.running', { due, running: q.running, value: later > 0 ? tr('settings.diagnosticsAdmin.scheduledForLater', { later }) : tr('settings.diagnosticsAdmin.noneScheduledForLater') })
}

/** One line for a last success / last error pair. */
export function outcomeText(h: LastOutcome | null | undefined): { tone: 'ok' | 'err' | 'none'; text: string } {
  if (!h) return { tone: 'none', text: tr('settings.diagnosticsAdmin.notUsedYet') }
  if (h.ok) return { tone: 'ok', text: tr('settings.diagnosticsAdmin.worked', { relative: formatRelative(h.ok_at!), value: h.error_at ? tr('settings.diagnosticsAdmin.lastError', { relative: formatRelative(h.error_at) }) : '' }) }
  return { tone: 'err', text: `${h.error ?? tr('common.failed')} (${formatRelative(h.error_at!)})` }
}

function Mark({ ok, info }: { ok: boolean; info?: boolean }) {
  if (ok) return <CheckCircle2 size={16} className="diag-ok" aria-label={tr('settings.diagnosticsAdmin.working')} />
  return info ? <Info size={16} className="diag-info" aria-label={tr('settings.diagnosticsAdmin.needsALook')} /> : <AlertTriangle size={16} className="diag-bad" aria-label={tr('settings.diagnosticsAdmin.failing')} />
}

/** Shows children after `ms`, so a fast answer never flashes a skeleton. */
function Late({ ms = 300, children }: { ms?: number; children: ReactNode }) {
  const [show, setShow] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setShow(true), ms)
    return () => clearTimeout(t)
  }, [ms])
  return show ? <>{children}</> : null
}

function Tile({ icon, label, value, tone }: { icon: ReactNode; label: string; value: ReactNode; tone?: 'ok' | 'bad' }) {
  return (
    <div className={`stat-tile${tone ? ` ${tone}` : ''}`}>
      <span className="icon" aria-hidden>
        {icon}
      </span>
      <span className="label">{label}</span>
      <span className="value">{value}</span>
    </div>
  )
}

// ACT settings.admin_diagnostics @web.settings
export function DiagnosticsSection() {
  const qc = useQueryClient()
  const q = useQuery<Diagnostics>({ queryKey: ['admin', 'diagnostics'], queryFn: () => api.get('/admin/diagnostics'), refetchInterval: 30_000 })
  const st = useApi<InstanceStatus>(['admin', 'status'], '/admin/status', undefined, { refetchInterval: 30_000 })
  const [running, setRunning] = useState(false)
  const runNow = async () => {
    setRunning(true)
    try {
      qc.setQueryData(['admin', 'diagnostics'], await api.get<Diagnostics>('/admin/diagnostics', { refresh: 'true' }))
    } catch (e) {
      toastError(e, tr('settings.diagnosticsAdmin.theChecksCouldNotRun'))
    } finally {
      setRunning(false)
    }
  }
  const d = q.data
  const failing = d ? d.checks.filter((c) => !c.ok && c.required).length : 0
  const notes = d ? d.checks.filter((c) => !c.ok && !c.required).length : 0
  return (
    <Section
      title={tr('settings.diagnosticsAdmin.diagnostics')}
      id="diagnostics"
      desc={tr('settings.diagnosticsAdmin.whatTheServerChecksAbout')}
      actions={
        <button type="button" className="btn outlined sm" disabled={running || !d} onClick={() => void runNow()} data-testid="diag-run">
          <RefreshCw /> {running ? tr('common.checking') : tr('settings.diagnosticsAdmin.runChecksNow')}
        </button>
      }
    >
      {q.isError ? (
        <div className="banner error">{(q.error as Error).message}</div>
      ) : !d ? (
        <div style={{ minHeight: 420 }}>
          <Late>
            <Skeleton height={64} />
            <div style={{ height: 12 }} />
            <Skeleton height={300} />
          </Late>
        </div>
      ) : (
        <div className="diag" data-testid="diagnostics">
          <div className="stat-tiles">
            <Tile icon={<HardDrive size={18} />} label={tr('settings.diagnosticsAdmin.mailStore')} value={st.data?.store?.ok === false ? 'Unreachable' : 'Reachable'} tone={st.data?.store?.ok === false ? 'bad' : 'ok'} />
            <Tile icon={<Globe size={18} />} label={tr('common.users')} value={(st.data?.users ?? 0).toLocaleString(appLocale())} />
            <Tile icon={<Globe size={18} />} label={tr('settings.diagnosticsAdmin.messages')} value={(st.data?.messages ?? 0).toLocaleString(appLocale())} />
            <Tile icon={<Cpu size={18} />} label={tr('settings.diagnosticsAdmin.jobsWaiting')} value={(d.queue.queued + d.queue.running).toLocaleString(appLocale())} />
            <Tile icon={<Cpu size={18} />} label={tr('settings.diagnosticsAdmin.failedJobsLastDay')} value={d.queue.failed_last_day.toLocaleString(appLocale())} tone={d.queue.failed_last_day > 0 ? 'bad' : undefined} />
          </div>

          <div className={`banner${failing ? ' error' : ''}`} data-testid="diag-summary">
            {failing ? <AlertTriangle /> : <CheckCircle2 />}
            <span>
              {failing ? tr('settings.diagnosticsAdmin.checksFailing', { count: failing }) : tr('settings.diagnosticsAdmin.everyRequiredCheckPasses')}
              {notes ? tr('settings.diagnosticsAdmin.moreToLookAtThese', { notes }) : ''}
              {d.checked_at && <span className="muted">{' '}{tr('settings.diagnosticsAdmin.checked', { relative: formatRelative(d.checked_at) })}</span>}
            </span>
          </div>

          <h3 className="sub-h">{tr('settings.diagnosticsAdmin.checks')}</h3>
          <ul className="diag-list" data-testid="diag-checks">
            {orderChecks(d.checks).map((c) => (
              <li key={c.name} className={checkTone(c)} data-check={c.name}>
                {c.off ? <Info size={16} className="diag-info" aria-label={tr('settings.diagnosticsAdmin.turnedOff')} /> : <Mark ok={c.ok} info={!c.required} />}
                <span className="diag-name">{c.label}</span>
                <span className="diag-detail">
                  {c.detail}
                  {!c.required && <span className="muted">{' '}{tr('settings.diagnosticsAdmin.informationOnly')}</span>}
                </span>
              </li>
            ))}
          </ul>

          <h3 className="sub-h">{tr('settings.diagnosticsAdmin.backup')}</h3>
          {d.backup.mail_export === false && <p className="muted" data-testid="diag-mail-export-off">{tr('settings.diagnosticsAdmin.mailExportOff')}</p>}
          {d.backup.store_backup_check === false && <p className="muted" data-testid="diag-backup-unchecked">{tr('settings.diagnosticsAdmin.backupUnchecked')}</p>}
          {(d.backup.purge_held ?? []).map((h) => (
            <div key={h.user} className="banner error" data-testid="diag-purge-held">
              <AlertTriangle />
              <span>
                {tx('settings.diagnosticsAdmin.purgeHeld', { user: h.user, rows: h.rows, files: h.files }, { code: (c) => <code>{c}</code> })}
              </span>
            </div>
          ))}
          {!d.backup.target_configured ? (
            <p className="muted">{tr('settings.diagnosticsAdmin.noSecondCopyIsSet')}</p>
          ) : d.backup.target_problem ? (
            <div className="banner error" data-testid="diag-backup-problem">
              <AlertTriangle />
              <span>
                {tx('settings.diagnosticsAdmin.theBackupCopyIsFailing', { targetProblem: d.backup.target_problem, marker: d.backup.marker }, { code: (c) => <code>{c}</code> })}
              </span>
            </div>
          ) : null}
          {d.backup.users.length > 0 && (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{tr('settings.diagnosticsAdmin.user')}</th>
                    <th>{tr('settings.diagnosticsAdmin.lastExport')}</th>
                    {d.backup.target_configured && <th>{tr('settings.diagnosticsAdmin.lastCopy')}</th>}
                  </tr>
                </thead>
                <tbody>
                  {d.backup.users.map((u) => (
                    <tr key={u.user}>
                      <td>{u.user}</td>
                      <td>{u.exported_at ? formatRelative(u.exported_at) : 'never'}</td>
                      {d.backup.target_configured && <td>{u.copied_at ? formatRelative(u.copied_at) : 'never'}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3 className="sub-h">{tr('settings.diagnosticsAdmin.push')}</h3>
          <ul className="diag-list">
            {Object.entries(TRANSPORT).map(([k, label]) => {
              const n = d.push.devices[k] ?? 0
              const o = outcomeText(d.push.transports[k])
              return (
                <li key={k} className={o.tone === 'err' ? 'bad' : o.tone === 'ok' ? 'ok' : 'info'}>
                  {o.tone === 'none' ? <Info size={16} className="diag-info" aria-hidden /> : <Mark ok={o.tone === 'ok'} />}
                  <span className="diag-name">{label}</span>
                  <span className="diag-detail">
                    {tr('settings.diagnosticsAdmin.devices', { count: n, value: o.tone === 'none' ? tr('settings.diagnosticsAdmin.nothingSentYet') : o.text })}
                  </span>
                </li>
              )
            })}
          </ul>

          <h3 className="sub-h">{tr('settings.diagnosticsAdmin.backgroundWork')}</h3>
          <p className="diag-line" data-testid="diag-queue">{queueText(d.queue)}</p>
          {(st.data?.failed_jobs ?? []).length > 0 && (
            <ul className="failed-jobs">
              {(st.data?.failed_jobs ?? []).map((f, i) => (
                <li key={i}>
                  <details>
                    <summary>
                      <b>{f.kind}</b> <span className="muted">· {formatRelative(f.at)}</span>
                    </summary>
                    <pre>{f.error}</pre>
                  </details>
                </li>
              ))}
            </ul>
          )}

          {d.connectors.length > 0 && (
            <>
              <h3 className="sub-h">{tr('common.mailAccounts')}</h3>
              <div className="table-wrap">
                <table className="table" data-testid="diag-accounts">
                  <thead>
                    <tr>
                      <th>{tr('common.account')}</th>
                      <th>{tr('settings.diagnosticsAdmin.user')}</th>
                      <th>{tr('settings.diagnosticsAdmin.state')}</th>
                      <th>{tr('settings.diagnosticsAdmin.lastSync')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.connectors.map((c) => (
                      <tr key={c.id}>
                        <td>{c.name}</td>
                        <td>{c.user}</td>
                        <td>
                          <span className={`pill ${healthTone(c.health.state)}`}>{c.health.text}</span>
                          {c.health.state === 'error' && c.detail && <div className="muted" style={{ fontSize: 12 }}>{c.detail}</div>}
                        </td>
                        <td>{c.last_sync_at ? formatRelative(c.last_sync_at) : 'never'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <h3 className="sub-h">{tr('common.models')}</h3>
          <ul className="diag-list" data-testid="diag-models">
            {d.models.tasks.map((t) => {
              const o = outcomeText(t.health)
              return (
                <li key={t.task} className={o.tone === 'err' ? 'bad' : o.tone === 'ok' ? 'ok' : 'info'}>
                  {o.tone === 'none' ? <Info size={16} className="diag-info" aria-hidden /> : <Mark ok={o.tone === 'ok'} />}
                  <span className="diag-name">{t.label}</span>
                  <span className="diag-detail">{o.text}</span>
                </li>
              )
            })}
          </ul>
          <p className="muted" style={{ fontSize: 12.5 }}>{tr('settings.diagnosticsAdmin.eachEndpointHasATest')}</p>

          <h3 className="sub-h">{tr('settings.diagnosticsAdmin.reverseProxy')}</h3>
          <p className="diag-line" data-testid="diag-proxy">
            {tr('settings.diagnosticsAdmin.forwardedAddressesAreBelievedFrom', { trusted: d.proxy.trusted.join(', '), value: d.proxy.source === 'set' ? ' (MAILR_TRUSTED_PROXIES)' : d.proxy.trusted.length > 2 ? tr('settings.diagnosticsAdmin.theDefaultThisMachineAnd') : tr('settings.diagnosticsAdmin.theDefaultThisMachineOnly') })}
            {d.proxy.untrusted_seen_at && (
              <span className="diag-warn">
                {' '}{tr('settings.diagnosticsAdmin.aRequestWithForwardedHeaders', { relative: formatRelative(d.proxy.untrusted_seen_at) })}
              </span>
            )}
          </p>
        </div>
      )}
    </Section>
  )
}

export function healthTone(state: AccountHealth['state']): string {
  return state === 'ok' ? 'ok' : state === 'syncing' ? 'info' : state === 'paused' || state === 'stale' ? 'warn' : 'err'
}
