// Deciding approvals: one card (POST /approvals/{id}/decide), with the toast and Undo that a grant
// gets when it also decided other waiting requests, and a group's Allow all / Deny all
// (POST /approvals/decide-many: each listed request once, as its own card would).

import type { QueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { toast, toastError } from '../../stores/ui'
import type { ApprovalCardData } from '../rail/assistant/useAssistant'
import { grantToast, undoToast, type GrantResult } from './grant'
import { t } from '../../i18n'

export type Scope = 'once' | 'session' | 'always'

/** Decide one card. A grant that also decided other waiting requests says so, with Undo
 * (ACT approval.undo_grant). `sessionLabel` names the time-limited grant for the toast. */
export async function postDecision(
  id: number, decision: 'approve' | 'deny', scope: Scope, sessionLabel: string, qc: QueryClient | undefined,
  extra: Record<string, unknown> = {},
): Promise<ApprovalCardData & GrantResult> {
  const r = await api.post<ApprovalCardData & GrantResult>(`/approvals/${id}/decide`, {
    decision, scope, constraint: scope === 'always' ? 'derived' : 'none', ...extra,
  })
  const also = r.also_decided?.count ?? 0
  if (also > 0 && scope !== 'once' && r.rule_id) {
    void qc?.invalidateQueries({ queryKey: ['approvals'] })
    const ruleId = r.rule_id
    toast({
      message: decision === 'approve' ? grantToast(scope, sessionLabel, also) : t('rail.assistant.approvalCard.deniedAlsoDeniedOtherRequests', { count: also }),
      actionLabel: t('common.undo'),
      onAction: () =>
        void api
          .post<{ reverted: number[]; kept: number[] }>(`/approvals/grants/${ruleId}/undo`)
          .then((u) => {
            toast({ message: undoToast(u.reverted.length, u.kept.length) })
            void qc?.invalidateQueries({ queryKey: ['approvals'] })
          })
          .catch((e) => toastError(e, t('common.undoFailed'))),
    })
  }
  return r
}

export type DecidedCard = ApprovalCardData & { id: number; status: string }

/** Allow all / Deny all of a group: exactly these requests, each decided once. The toast says how
 * many, and how many had been decided elsewhere meanwhile. */
export async function decideAll(ids: number[], decision: 'approve' | 'deny'): Promise<DecidedCard[] | null> {
  try {
    // ACT approval.approve_many @web.reader
    const r = await api.post<{ decided: DecidedCard[]; skipped: { id: number | string; status: string }[]; count: number }>(
      '/approvals/decide-many', { ids, decision })
    const skipped = r.skipped.length ? t('approvals.approvalsSection.hadAlreadyBeenDecided', { skippedLength: r.skipped.length }) : ''
    toast({
      message: decision === 'approve'
        ? t('approvals.approvalsSection.allowedRequests', { count: r.count, skipped })
        : t('approvals.cards.deniedRequests', { count: r.count, skipped }),
    })
    return r.decided
  } catch (e) {
    toastError(e, decision === 'approve' ? t('approvals.approvalsSection.couldNotAllowThem') : t('approvals.cards.couldNotDenyThem'))
    return null
  }
}
