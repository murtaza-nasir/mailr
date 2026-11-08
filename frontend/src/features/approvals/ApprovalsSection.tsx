import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { formatRelative } from '../../lib/format'
import type { ApprovalCardData } from '../rail/assistant/useAssistant'
import '../rail/assistant/assistant.css'
import { ApprovalList } from './ApprovalList'
import { requesterKey, STATUS_TEXT, useApprovals, type PendingApproval } from './approvals'
import { kindOf } from './cardHead'
import type { ApprovalFocus } from './focus'
import { t } from '../../i18n'

/** The Approvals section of the "Waiting for you" bell: the waiting cards, decided in place.
 * An approval link (/approvals/:id, focus.ts) opens the bell on that approval: scrolled to and
 * outlined, with its outcome when it is already decided, or a notice when it cannot be found.
 * Consecutive requests of one kind from one chat or agent are grouped, with Allow all and Deny all
 * (ApprovalList).
 * ACT approval.approve @web.reader, ACT approval.deny @web.reader (through ApprovalCard) */
export function ApprovalsSection({ target }: { target: ApprovalFocus | null }) {
  const q = useApprovals('pending')
  const qc = useQueryClient()
  // Cards decided here stay visible with their live outcome (approved → running → done /
  // failed) until the bell closes, instead of vanishing from the pending list.
  const [decided, setDecided] = useState<Record<number, PendingApproval>>({})
  const pending = q.data ?? []
  const targetId = target?.id ?? null
  const one = useQuery<PendingApproval>({
    queryKey: ['approvals', 'one', targetId],
    queryFn: ({ signal }) => api.get<PendingApproval>(`/approvals/${targetId}`, undefined, signal),
    enabled: targetId !== null,
    retry: false,
    staleTime: 5_000,
  })
  // The card the server answers with. Its id can differ from the link's: an old link names the
  // internal id, and the card carries the public one.
  const linked = targetId !== null && one.data ? one.data : null
  const listed = [...Object.values(decided).filter((d) => !pending.some((p) => p.id === d.id)), ...pending]
  if (linked && !listed.some((p) => p.id === linked.id)) listed.push(linked)
  // Newest first. Public ids are not in creation order, so sort by the time asked.
  // Requests asked at the same moment keep one requester's requests of one kind together, so they group.
  const shown = listed.sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '')
    || `${requesterKey(a)} ${kindOf(a)}`.localeCompare(`${requesterKey(b)} ${kindOf(b)}`) || b.id - a.id)
  const notFound = target !== null && (target.invalid !== undefined || (targetId !== null && one.isError))
  const onStatus = useCallback(
    (id: number, status: string, card?: ApprovalCardData) => {
      const base = (q.data ?? []).find((p) => p.id === id) ?? (one.data?.id === id ? one.data : undefined)
      setDecided((d) => {
        const cur = d[id] ?? base
        // The card changes in place to its outcome ("Approved · moved 6 messages to Trash").
        return cur ? { ...d, [id]: { ...cur, status, ...(card ? { display: card.display ?? cur.display, outcome: card.outcome ?? null } : {}) } } : d
      })
    },
    [q.data, one.data],
  )
  const refresh = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['approvals'] })
    void qc.invalidateQueries({ queryKey: ['outbox'] })
  }, [qc])

  // Scroll the linked card into view once it is drawn.
  const linkedId = linked?.id ?? null
  useEffect(() => {
    if (linkedId === null) return
    document.querySelector<HTMLElement>(`.wb-approvals [data-item-id="${linkedId}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [linkedId])

  const loadingLinked = targetId !== null && one.isLoading
  if (!shown.length && !notFound && !loadingLinked) return null
  return (
    <section className="wb-section wb-approvals" role="region" aria-label={t('common.approvals')} data-testid="waiting-approvals">
      <h3 className="wb-section-head">{t('common.approvals')}</h3>
      {notFound && (
        <p className="approvals-notice" role="status" data-testid="approval-not-found">
          {t('approvals.approvalsSection.wasNotFoundTheLink', { value: target?.invalid !== undefined ? t('approvals.approvalsSection.approval', { invalid: target.invalid }) : t('approvals.approvalsSection.thisApproval') })}
        </p>
      )}
      {loadingLinked && <p className="muted wb-line">{t('approvals.approvalsSection.loadingTheApproval')}</p>}
      <div className="approvals-body">
        <ApprovalList
          items={shown}
          focusedId={linkedId}
          onStatus={onStatus}
          onDecided={refresh}
          focusedNote={(p) => p.status !== 'pending' && (
            <div className="approvals-meta">
              <b>{STATUS_TEXT[p.status] ?? p.status}</b>
              {p.decided_at ? ` ${formatRelative(p.decided_at)}` : ''}
            </div>
          )}
        />
      </div>
    </section>
  )
}
