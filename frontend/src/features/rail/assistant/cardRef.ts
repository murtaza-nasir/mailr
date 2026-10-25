// An approval card as the assistant and the approvals list show it. Apart from useAssistant.ts, so
// the bell's approvals count (approvals/approvals.ts) does not load the assistant's chat code.

import { displayTitle } from './present/serverText'
import type { ApprovalCardData, ApprovalRef } from './useAssistant'

export function cardToRef(a: ApprovalCardData): ApprovalRef {
  const display = a.display ?? a.preview?.display ?? null
  return {
    id: a.id, action: a.action, title: (display ? displayTitle(display) : null) ?? a.preview?.title ?? a.target ?? a.action, target: a.target ?? null,
    preview: a.preview ?? null, status: a.status, expires_at: a.expires_at ?? null, display, outcome: a.outcome ?? null,
    also_covers: a.also_covers ?? null, grants: a.grants ?? null,
  }
}
