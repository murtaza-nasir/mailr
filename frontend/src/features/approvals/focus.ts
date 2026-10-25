// A request to open the approvals drawer, from an approval link (/approvals, /approvals/:id).
// Agents, push notifications and the MCP server hand out <base>/approvals/<id> (the id as a… with
// public ids, lib/pubid.ts); the route stores the request here and moves on to the inbox, and the "Waiting for you" bell (WaitingBell) opens on it.

import { useSyncExternalStore } from 'react'
import { approvalFromSlug } from '../../lib/pubid'

export interface ApprovalFocus {
  /** The approval to show, or null for the drawer as it is. */
  id: number | null
  /** A link whose id is not a number ("/approvals/abc"): shown as not found. */
  invalid?: string
  /** Bumped per request, so the same link opened twice opens the drawer twice. */
  seq: number
}

let current: ApprovalFocus | null = null
let seq = 0
const subs = new Set<() => void>()
const emit = () => subs.forEach((f) => f())

export function requestApprovalFocus(id: number | null, invalid?: string) {
  current = { id, seq: ++seq, ...(invalid !== undefined ? { invalid } : {}) }
  emit()
}

export function clearApprovalFocus() {
  if (current === null) return
  current = null
  emit()
}

export function useApprovalFocus(): ApprovalFocus | null {
  return useSyncExternalStore(
    (f) => {
      subs.add(f)
      return () => subs.delete(f)
    },
    () => current,
  )
}

/** "/approvals/aK7fQ2mXb9" (the public short form) or "/approvals/12" (a number: a public id, or an
 * old link's internal id) → the id; anything else → undefined. */
export function parseApprovalId(raw: string | undefined): number | null | undefined {
  if (raw === undefined || raw === '') return null
  const id = approvalFromSlug(raw)
  return id !== null && id > 0 ? id : undefined
}
