// Grants on an approval card: "Allow for 12 hours" and "Always
// allow" also decide the other waiting requests the new rule covers. The card says how many before
// the tap (the server's also_covers), and a toast says what happened after it, with Undo.

/** How many other waiting requests each grant on a card would approve too (permissions.would_cover). */
import { t } from '../../i18n'

export interface AlsoCovers {
  session: number
  always: number
  always_derived: number
}

/** The decide response's extra fields: the grant's rule and the other requests it decided. */
export interface GrantResult {
  rule_id?: number | null
  also_decided?: { ids: number[]; count: number } | null
}

/** " (also approves 2 other waiting)" on a grant button; empty when it covers no other request. */
export function alsoLabel(n: number | undefined): string {
  return n && n > 0 ? t('approvals.grant.alsoApprovesOtherWaiting', { n }) : ''
}

const requests = (n: number) => t('approvals.grant.otherRequests', { count: n })

/** The toast after a grant: "Allowed for 12 hours. Also approved 2 other requests." */
export function grantToast(scope: 'session' | 'always', sessionLabel: string, n: number): string {
  const first = scope === 'session' ? `${sessionLabel.replace(/^Allow\b/, 'Allowed')}.` : t('approvals.grant.ruleCreated')
  return t('approvals.grant.alsoApproved', { first, requests: requests(n) })
}

/** The toast after Undo: what went back to waiting, and what had already run. */
export function undoToast(reverted: number, kept: number): string {
  if (!kept) return t('approvals.grant.undoneWaitingAgain', { value: t('approvals.grant.requestsAre', { count: reverted }) })
  const ran = t('approvals.grant.alreadyRun', { value: t('approvals.grant.requestsHad', { count: kept }) })
  if (!reverted) return t('approvals.grant.theRuleIsRemovedBut', { ran })
  return t('approvals.grant.undoneFor', { value: t('approvals.grant.requests', { count: reverted }), ran: ran[0].toUpperCase(), ran2: ran.slice(1) })
}

/** What each choice on a card covers, for its buttons' tips. `verb` is "create or change labels". */
export function scopeHelp(verb: string, sessionLabel: string): { once: string; session: string; always: string; deny: string } {
  const span = sessionLabel.replace(/^Allow\s*/, '')
  return {
    once: t('approvals.grant.thisRequestOnly'),
    session: t('approvals.grant.thisAgentMayWithoutAsking', { verb, span }),
    always: t('approvals.grant.aStandingRuleForThis'),
    deny: t('approvals.grant.thisRequestOnly'),
  }
}
