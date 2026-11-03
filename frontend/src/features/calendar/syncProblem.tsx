// A calendar sync failure in plain words (backend calendar/sync_errors.py): the server sends the
// kind and the host, the words are the client's, so they are translated and the same everywhere
// (Settings → Calendar & tasks, the Calendar app's banners and left column). The technical text
// stays behind "Details".

import { t } from '../../i18n'

export const SYNC_PROBLEM_KINDS = ['auth', 'not_found', 'tls', 'unreachable', 'server', 'blocked', 'partial', 'unknown'] as const
export type SyncProblemKind = (typeof SYNC_PROBLEM_KINDS)[number]

/** The kind as the client knows it: an unknown or missing kind (an older server) is "unknown". */
export function problemKind(kind: string | null | undefined): SyncProblemKind {
  return (SYNC_PROBLEM_KINDS as readonly string[]).includes(kind ?? '') ? (kind as SyncProblemKind) : 'unknown'
}

/** What went wrong and what to do, for an account whose calendars live at `host`. Without a host
 *  (an older server), `fallback` names the account instead. */
export function syncProblemText(kind: string | null | undefined, host: string | null | undefined, fallback = ''): string {
  const where = host || fallback || t('calendar.syncProblem.theServer')
  switch (problemKind(kind)) {
    case 'auth': return t('calendar.syncProblem.auth', { host: where })
    case 'not_found': return t('calendar.syncProblem.notFound', { host: where })
    case 'tls': return t('calendar.syncProblem.tls', { host: where })
    case 'unreachable': return t('calendar.syncProblem.unreachable', { host: where })
    case 'server': return t('calendar.syncProblem.server', { host: where })
    case 'blocked': return t('calendar.syncProblem.blocked', { host: where })
    case 'partial': return t('calendar.syncProblem.partial', { host: where })
    default: return t('calendar.syncProblem.unknown', { host: where })
  }
}

/** The plain message, then the technical text folded under "Details". */
export function SyncProblem({ kind, host, detail, fallback, testId = 'sync-problem' }: {
  kind: string | null | undefined
  host: string | null | undefined
  detail?: string | null
  fallback?: string
  testId?: string
}) {
  return (
    <div className="sync-problem" data-testid={testId} data-kind={problemKind(kind)}>
      <div className="sub" style={{ color: 'var(--danger)' }}>{syncProblemText(kind, host, fallback)}</div>
      {detail && (
        <details className="sync-problem-details" style={{ fontSize: 12, marginTop: 2 }}>
          <summary className="muted" style={{ cursor: 'pointer' }}>{t('common.details')}</summary>
          <code style={{ display: 'block', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', marginTop: 2 }}>{detail}</code>
        </details>
      )}
    </div>
  )
}
