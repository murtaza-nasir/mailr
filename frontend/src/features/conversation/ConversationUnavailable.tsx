// The reader when a conversation's detail is not on screen: offline or unreachable shows
// what the list already holds for it; only a real 404 says it may have been deleted.
import { useQueryClient } from '@tanstack/react-query'
import { CloudOff, WifiOff } from 'lucide-react'
import { useMemo } from 'react'
import type { ConversationSummary, Page } from '../../api/types'
import { EmptyState } from '../../components/bits'
import { useOnline } from '../../hooks/useOnline'
import { formatDateTime } from '../../lib/format'
import { classifyError, isTransient } from '../../lib/netErrors'
import { t } from '../../i18n'

/** The conversation's row from any list already loaded (subject, people, snippet). */
function useCachedRow(id: number): ConversationSummary | null {
  const qc = useQueryClient()
  return useMemo(() => {
    for (const [, page] of qc.getQueriesData<Page<ConversationSummary>>({ queryKey: ['conversations'] })) {
      const hit = page?.items?.find((c) => c.id === id)
      if (hit) return hit
    }
    return null
  }, [qc, id])
}

export function ConversationUnavailable({ id, error, paused, onRetry }: { id: number; error: unknown; paused: boolean; onRetry: () => void }) {
  const online = useOnline()
  const row = useCachedRow(id)
  const kind = classifyError(error)
  const waiting = !online || paused || (!!error && isTransient(error))

  if (waiting) {
    const people = row?.participants?.map((p) => p.name || p.email).filter(Boolean).join(', ')
    return (
      <div className="conv-offline" role="status" style={{ padding: '24px 32px', maxWidth: 760 }}>
        <div className="banner" style={{ borderRadius: 12, marginBottom: 20 }}>
          {online ? <CloudOff /> : <WifiOff />}
          <span style={{ flex: 1 }}>
            {t('conversation.conversationUnavailable.thisConversationOpensWhenThe', { value: online ? t('conversation.conversationUnavailable.cantReachMailrShowingWhats') : t('conversation.conversationUnavailable.youreOfflineShowingWhatsCached') })}
          </span>
          <button type="button" className="btn tonal sm" onClick={onRetry}>
            {t('common.retry')}
          </button>
        </div>
        {row ? (
          <article aria-label={t('conversation.conversationUnavailable.cachedConversation')}>
            <h2 dir="auto" style={{ margin: '0 0 8px', fontSize: 22, fontWeight: 500 }}>{row.subject || t('common.noSubject')}</h2>
            <p className="muted" style={{ margin: '0 0 12px' }}>
              {people}
              {row.last_date ? ` · ${formatDateTime(row.last_date)}` : ''}
              {row.message_count > 1 ? t('conversation.conversationUnavailable.messages', { count: row.message_count }) : ''}
            </p>
            {row.snippet && <p dir="auto" style={{ margin: 0 }}>{row.snippet}</p>}
          </article>
        ) : (
          <p className="muted">{t('conversation.conversationUnavailable.nothingFromThisConversationIs')}</p>
        )}
      </div>
    )
  }
  if (kind === 'not_found')
    return (
      <EmptyState title={t('conversation.conversationUnavailable.thisConversationCouldNotBe')}>
        <p>{t('conversation.conversationUnavailable.itMayHaveBeenDeleted')}</p>
      </EmptyState>
    )
  return (
    <EmptyState title={t('conversation.conversationUnavailable.thisConversationCouldNotBe2')}>
      <p>{(error as Error | null)?.message || t('conversation.conversationUnavailable.mailrDidNotAnswer')}</p>
      <button type="button" className="btn tonal" onClick={onRetry}>
        {t('common.retry')}
      </button>
    </EmptyState>
  )
}
