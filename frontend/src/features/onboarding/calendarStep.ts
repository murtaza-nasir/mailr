// The calendar step's state (steps/Calendar.tsx), kept apart so it can be tested.
//
// The built-in mailr calendar (Connector.builtin) counts as connected but does not end the choice:
// "Use our calendar server" and "Connect a calendar I already use" stay on offer until she
// connects a calendar of her own. The prepared server stays on offer until a calendar on that
// server is connected.

import type { Connector } from '../../api/types'

export function hostOf(url: unknown): string {
  try {
    return new URL(String(url)).host
  } catch {
    return ''
  }
}

export interface CalendarStepState {
  /** Every connected calendar account, the built-in one first. */
  connected: Connector[]
  builtin: Connector | null
  /** Accounts she connected herself. */
  external: Connector[]
  /** The prepared calendar server still on offer, or null. */
  server: string | null
}

export function calendarStepState(conns: Connector[], offered: string | null | undefined): CalendarStepState {
  const cals = conns.filter((c) => c.kind === 'caldav')
  const builtin = cals.find((c) => c.builtin) ?? null
  const external = cals.filter((c) => !c.builtin)
  const server = offered && !external.some((c) => hostOf(c.config.url) === hostOf(offered)) ? offered : null
  return { connected: builtin ? [builtin, ...external] : external, builtin, external, server }
}

const GOOGLE = /@(gmail|googlemail)\.com$/
const MICROSOFT = /@(outlook|hotmail|live|msn)\.[a-z.]+$/

/** The big provider of her mail accounts whose calendar cannot be connected here (no CalDAV with an
 *  app password): Google or Microsoft, from the account's kind or its address. Null otherwise. */
export function closedCalendarProvider(conns: Connector[]): 'google' | 'microsoft' | null {
  for (const c of conns) {
    const cfg = c.config as { email?: unknown; imap?: { username?: unknown } }
    const addr = String(cfg.email ?? cfg.imap?.username ?? '').toLowerCase()
    if (c.kind === 'gmail' || GOOGLE.test(addr)) return 'google'
    if (c.kind === 'm365' || MICROSOFT.test(addr)) return 'microsoft'
  }
  return null
}
