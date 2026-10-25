import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import type { Label } from '../../api/types'
import { adjustCounts, applyServerCounts, labelView, sidebarCountsKey, type SidebarCounts } from './sidebarCounts'

const counts = (cursor: number, drafts: number, trash: number): SidebarCounts => ({
  cursor,
  views: { inbox: { unread: 2, total: 5 }, drafts: { unread: 0, total: drafts }, trash: { unread: 0, total: trash }, 'label:9': { unread: 1, total: 3 } },
  inbox_count: { count: 2, mode: 'first_tab', layout: 'default', tab: 'primary' },
  waiting: 0, approvals: 0, failed: 0,
})

const label = (id: number, role: Label['role']): Label => ({ id, name: String(id), role, color: null, show_in_list: 'show', show_in_message_list: true, sort: 0, home_account_id: null, unread: 0, total: 0 })

describe('sidebar counts', () => {
  it('a counts event replaces the numbers, the labels and the Inbox count, never with an older answer', () => {
    const qc = new QueryClient()
    qc.setQueryData(['labels'], [label(1, 'drafts'), label(9, null), label(2, 'trash')])
    qc.setQueryData(['labels', 'inbox-count', 'default', 'first_tab'], { count: 7, mode: 'first_tab', layout: 'default', tab: 'primary' })
    applyServerCounts(qc, counts(10, 1, 4))
    expect(qc.getQueryData<SidebarCounts>(sidebarCountsKey)?.views.drafts.total).toBe(1)
    const labels = qc.getQueryData<Label[]>(['labels'])!
    expect(labels.map((l) => [l.unread, l.total])).toEqual([[1, 1], [1, 3], [0, 4]])
    expect(qc.getQueryData<{ count: number }>(['labels', 'inbox-count', 'default', 'first_tab'])?.count).toBe(2)
    applyServerCounts(qc, counts(9, 4, 4))
    expect(qc.getQueryData<SidebarCounts>(sidebarCountsKey)?.views.drafts.total).toBe(1)
  })

  it('an own action moves the numbers at once', () => {
    const qc = new QueryClient()
    qc.setQueryData(sidebarCountsKey, counts(1, 1, 4))
    adjustCounts(qc, 'inbox', 'trash', 2, 1)
    const c = qc.getQueryData<SidebarCounts>(sidebarCountsKey)!
    expect(c.views.inbox).toEqual({ unread: 1, total: 3 })
    expect(c.views.trash).toEqual({ unread: 1, total: 6 })
    expect(c.inbox_count.count).toBe(1)
  })

  it('role labels open their system view', () => {
    expect(labelView({ id: 3, role: 'junk' })).toBe('spam')
    expect(labelView({ id: 3, role: null })).toBe('label:3')
  })
})
