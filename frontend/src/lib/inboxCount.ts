// The number by Inbox in the navigation (ACT settings.inbox_count @web.nav). The server counts it
// (GET /inbox/count): with the `inbox_count` setting "first_tab" (the default), the unread
// conversations the active layout's first tab lists; with "all", every unread conversation in
// the Inbox. The first tab's own unread indicator is the same number.

import type { InboxCountMode } from '../api/types'

export type { InboxCountMode }

/** GET /inbox/count. */
export interface InboxCount {
  count: number
  mode: InboxCountMode
  layout: string | null
  /** The counted tab (the layout's first); null with "all". */
  tab: string | null
}

/** The setting as the server reads it: anything but "all" counts the first tab. */
export function inboxCountMode(v: unknown): InboxCountMode {
  return v === 'all' ? 'all' : 'first_tab'
}

/** The Inbox entry's numbers. Unread is the server's Inbox count, once it answers for the mode
 *  in force; until then "all" shows the Inbox label's unread count (the same number) and
 *  "first_tab" shows nothing rather than a number that would then drop. Total is always the
 *  whole Inbox (folder panes show it beside unread). */
export function inboxNavCounts(
  mode: InboxCountMode,
  served: InboxCount | undefined,
  label: { unread: number; total: number } | undefined,
): { count?: number; bold: boolean; unread?: number; total?: number } {
  const unread = served && served.mode === mode ? served.count : mode === 'all' ? label?.unread : undefined
  return { count: unread, bold: (unread ?? 0) > 0, unread, total: label?.total }
}
