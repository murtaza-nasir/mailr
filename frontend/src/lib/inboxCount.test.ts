// ACT-SURFACES: web.nav
// The sidebar Inbox entry (settings.inbox_count): unread follows the setting, total stays the
// whole Inbox.
import { describe, expect, it } from 'vitest'
import { inboxCountMode, inboxNavCounts, type InboxCount } from './inboxCount'

const label = { unread: 12, total: 400 }
const served = (count: number, mode: InboxCount['mode']): InboxCount => ({ count, mode, layout: 'mine', tab: mode === 'all' ? null : 'inbox' })

describe('inbox count', () => {
  it('reads anything but "all" as the first tab', () => {
    expect(inboxCountMode(undefined)).toBe('first_tab')
    expect(inboxCountMode('first_tab')).toBe('first_tab')
    expect(inboxCountMode('nonsense')).toBe('first_tab')
    expect(inboxCountMode('all')).toBe('all')
  })

  it('shows the first tab count from the server, and the whole Inbox as total', () => {
    expect(inboxNavCounts('first_tab', served(0, 'first_tab'), label)).toEqual({ count: 0, bold: false, unread: 0, total: 400 })
    expect(inboxNavCounts('first_tab', served(3, 'first_tab'), label)).toEqual({ count: 3, bold: true, unread: 3, total: 400 })
  })

  it('shows every unread inbox conversation with "all"', () => {
    expect(inboxNavCounts('all', served(12, 'all'), label)).toEqual({ count: 12, bold: true, unread: 12, total: 400 })
  })

  it('does not show a count from the other mode while the new one loads', () => {
    // Switching to "Inbox tab only": the old "all" number is not shown, nothing is.
    expect(inboxNavCounts('first_tab', served(12, 'all'), label).count).toBeUndefined()
    expect(inboxNavCounts('first_tab', undefined, label)).toEqual({ count: undefined, bold: false, unread: undefined, total: 400 })
    // Switching to "All tabs": the Inbox label's unread count is the same number.
    expect(inboxNavCounts('all', served(2, 'first_tab'), label).count).toBe(12)
    expect(inboxNavCounts('all', undefined, undefined)).toEqual({ count: undefined, bold: false, unread: undefined, total: undefined })
  })
})
