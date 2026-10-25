// "Bringing in your mail…": a slim, friendly strip while a new account's mail is still arriving
// (the first import, then the older mail in the background), until the backfill completes.

import { DownloadCloud, X } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { api, asList } from '../../api/client'
import { useMe } from '../../api/hooks'
import type { Connector } from '../../api/types'
import { importSummary, importText } from './importProgress'
import { t } from '../../i18n'

export function ImportProgress() {
  const me = useMe().data
  const [hidden, setHidden] = useState(false)
  // Without identities nothing is connected yet. The list polls only while an import runs (207
  // polls in under three hours with nothing importing, 13 Oct 2026); otherwise the live stream's
  // connector events refresh it when an account starts or stops syncing (api/events.ts).
  const conns = useQuery<Connector[]>({
    queryKey: ['connectors'],
    queryFn: async ({ signal }) => asList<Connector>(await api.get<unknown>('/connectors', undefined, signal)),
    enabled: !hidden && !!me && me.identities.length > 0,
    refetchInterval: (q) => (importSummary(q.state.data).active ? 15_000 : false),
  })
  const s = importSummary(conns.data)
  if (hidden || !s.active) return null
  return (
    <div className="ob-import no-print" role="status" data-testid="import-progress">
      <DownloadCloud aria-hidden />
      <span className="ob-import-text">
        <span>{importText(s)}</span>
        <span className="ob-import-sub">{t('onboarding.importProgress.youCanReadAndWrite')}</span>
      </span>
      <span className={`ob-import-bar${s.fraction == null ? ' busy' : ''}`} aria-hidden>
        <span style={s.fraction == null ? undefined : { width: `${Math.max(4, Math.round(s.fraction * 100))}%` }} />
      </span>
      <Link className="btn text sm" to="/settings/mail/accounts">
        {t('common.details')}
      </Link>
      <button type="button" className="icon-btn" aria-label={t('onboarding.importProgress.hideImportProgress')} onClick={() => setHidden(true)}>
        <X />
      </button>
    </div>
  )
}
