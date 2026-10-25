import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { asQuestion, ApprovalCard } from '../rail/assistant/ApprovalCard'
import { displayTitle } from '../rail/assistant/present/serverText'
import type { ApprovalCardData } from '../rail/assistant/useAssistant'
import '../rail/assistant/assistant.css'
import { actorLabel, groupByRequester, groupSource, sessionLabelFor, STATUS_TEXT, toRef, useApprovals, type ApprovalGroup, type PendingApproval } from './approvals'
import type { ApprovalFocus } from './focus'
import { t, tx } from '../../i18n'

/** The Approvals section of the "Waiting for you" bell: the waiting cards, decided in place.
 * An approval link (/approvals/:id, focus.ts) opens the bell on that approval: scrolled to and
 * outlined, with its outcome when it is already decided, or a notice when it cannot be found.
 * Several waiting requests from one chat or agent get "Allow all n", which lists each
 * one before it decides them.
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
  const shown = listed.sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '') || b.id - a.id)
  const groups = groupByRequester(shown)
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
        {groups.map((g) => (
          <div key={g.key} className="wb-group">
            {g.pending.length > 1 && <AllowAll group={g} onDecided={(cards) => { for (const c of cards) onStatus(c.id, c.status, c); refresh() }} />}
            {g.items.map((p) => {
              const focused = linkedId === p.id
              return (
                <div key={p.id} className={`approvals-item${focused ? ' focused' : ''}`} data-item-id={p.id} data-focused={focused || undefined}>
                  <div className="approvals-meta">
                    {tx('approvals.approvalsSection.requestedBy', { actorLabel: actorLabel(p), value: p.created_at ? ` · ${formatRelative(p.created_at)}` : '' }, { b: (c) => <b>{c}</b> })}
                    {focused && p.status !== 'pending' && (
                      <>
                        {' · '}
                        <b>{STATUS_TEXT[p.status] ?? p.status}</b>
                        {p.decided_at ? ` ${formatRelative(p.decided_at)}` : ''}
                      </>
                    )}
                  </div>
                  <ApprovalCard a={toRef(p)} actorName={actorLabel(p)} sessionLabel={sessionLabelFor(p)} onStatus={onStatus} onDecided={refresh} />
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </section>
  )
}

/** One line per request "Allow all" would decide: the card's own question. */
function itemLine(p: PendingApproval): string {
  const d = p.display
  if (d?.title) return asQuestion(displayTitle(d))
  return p.action
}

/** "Allow all 3 from this chat": lists each request first, then decides each one as its own card
 * would once (POST /approvals/decide-many). A standing grant stays on each card's own buttons. */
function AllowAll({ group, onDecided }: { group: ApprovalGroup; onDecided: (cards: (ApprovalCardData & { id: number; status: string })[]) => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const n = group.pending.length
  const from = groupSource(group.pending[0])
  const allow = async () => {
    setBusy(true)
    try {
      // ACT approval.approve_many @web.reader
      const r = await api.post<{ decided: (ApprovalCardData & { id: number; status: string })[]; skipped: { id: number; status: string }[]; count: number }>(
        '/approvals/decide-many', { ids: group.pending.map((p) => p.id), decision: 'approve' })
      onDecided(r.decided)
      const skipped = r.skipped.length ? t('approvals.approvalsSection.hadAlreadyBeenDecided', { skippedLength: r.skipped.length }) : ''
      toast({ message: t('approvals.approvalsSection.allowedRequests', { count: r.count, skipped }) })
      setOpen(false)
    } catch (e) {
      toastError(e, t('approvals.approvalsSection.couldNotAllowThem'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className={`wb-allow${open ? ' open' : ''}`} data-testid="allow-all">
      <div className="wb-allow-row">
        <span>{t('approvals.approvalsSection.requestsFrom', { count: n, from })}</span>
        {!open && (
          <button type="button" className="btn tonal sm" aria-expanded={false} onClick={() => setOpen(true)}>
            {t('approvals.approvalsSection.allowAll', { n })}
          </button>
        )}
      </div>
      {open && (
        <div className="wb-allow-confirm" role="group" aria-label={t('approvals.approvalsSection.allowAllFrom', { n, from })}>
          <p>{t('approvals.approvalsSection.allowEachOfTheseOnce')}</p>
          <ol>
            {group.pending.map((p) => (
              <li key={p.id}>{itemLine(p)}</li>
            ))}
          </ol>
          <div className="wb-allow-actions">
            <button type="button" className="btn filled sm" disabled={busy} onClick={() => void allow()}>
              {t('approvals.approvalsSection.allowAll', { n })}
            </button>
            <button type="button" className="btn text sm" disabled={busy} onClick={() => setOpen(false)}>
              {t('common.cancel')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
