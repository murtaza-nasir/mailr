import { describe, expect, it } from 'vitest'
import type { ConversationSummary, Label } from '../../api/types'
import type { ArrangeContext, ListPrefs } from '../types'
import { arrangeByBundle, arrangeByDate, arrangeSorted, bundleOf, dateBucket } from './grouping'

function conv(id: number, over: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    id,
    subject: `Subject ${id}`,
    snippet: '',
    last_date: new Date().toISOString(),
    message_count: 1,
    unread_count: 0,
    draft_count: 0,
    has_attachments: false,
    starred: false,
    important: false,
    importance_score: null,
    category: null,
    label_ids: [],
    participants: [{ email: `p${id}@example.com`, name: `Person ${id}`, me: false }],
    snoozed_until: null,
    muted: false,
    ...over,
  } as ConversationSummary
}

const prefs = (p: Partial<ListPrefs> = {}): ListPrefs => ({
  sort: null,
  quick: { unread: false, starred: false, attachments: false },
  collapsed: {},
  ...p,
})
const ctx = (p: Partial<ListPrefs> = {}, view = 'inbox'): ArrangeContext => ({ view, prefs: prefs(p), labelsById: new Map<number, Label>() })

describe('dateBucket', () => {
  const now = new Date(2026, 8, 30, 15, 0) // Wednesday 30 Sep 2026
  it('buckets by calendar day and week', () => {
    expect(dateBucket(new Date(2026, 8, 30, 8).toISOString(), now).id).toBe('today')
    expect(dateBucket(new Date(2026, 8, 29, 23).toISOString(), now).id).toBe('yesterday')
    expect(dateBucket(new Date(2026, 8, 28, 9).toISOString(), now).id).toBe('week')
    expect(dateBucket(new Date(2026, 8, 22).toISOString(), now).id).toBe('lastweek')
    expect(dateBucket(new Date(2026, 8, 2).toISOString(), now).id).toBe('month')
    expect(dateBucket(new Date(2026, 7, 10).toISOString(), now).id).toBe('lastmonth')
    expect(dateBucket(new Date(2025, 1, 1).toISOString(), now).id).toBe('older')
    expect(dateBucket(null, now).id).toBe('none')
  })
})

describe('arrangeByDate', () => {
  it('puts a heading before each bucket and hides collapsed rows', () => {
    const items = [conv(1), conv(2, { last_date: new Date(2020, 0, 1).toISOString() })]
    const out = arrangeByDate(items, ctx())
    expect(out.map((e) => (e.kind === 'group' ? `g:${e.group.id}` : e.c.id))).toEqual(['g:date:inbox:today', 1, 'g:date:inbox:older', 2])
    const collapsed = arrangeByDate(items, ctx({ collapsed: { 'date:inbox:older': true } }))
    expect(collapsed.filter((e) => e.kind === 'row').map((e) => e.kind === 'row' && e.c.id)).toEqual([1])
  })
})

describe('bundles', () => {
  it('classifies senders', () => {
    expect(bundleOf(conv(1))).toBe('people')
    expect(bundleOf(conv(5, { participants: [{ email: 'me@example.org', name: 'me', me: true }] }))).toBe('notes')
    expect(bundleOf(conv(2, { participants: [{ email: 'no-reply@shop.example', name: 'Shop', me: false }] }))).toBe('notifications')
    expect(bundleOf(conv(3, { category: 'promotions' }))).toBe('newsletters')
    expect(bundleOf(conv(4, { participants: [{ email: 'digest@news.example', name: 'News', me: false }] }))).toBe('newsletters')
  })
  it('orders people first and folds the bundles by default', () => {
    const items = [conv(1, { category: 'promotions' }), conv(2), conv(3, { category: 'updates' })]
    const out = arrangeByBundle(items, ctx())!
    const rows = out.filter((e) => e.kind === 'row').map((e) => e.kind === 'row' && e.c.id)
    expect(rows).toEqual([2])
    const heads = out.filter((e) => e.kind === 'group').map((e) => e.kind === 'group' && [e.group.title, e.group.collapsed])
    expect(heads).toEqual([['People', false], ['Notifications', true], ['Newsletters', true]])
  })
  it('leaves inbox sections alone unless bundles are always on', () => {
    const items = [conv(1, { category: 'promotions' }), conv(2)]
    expect(arrangeByBundle(items, { ...ctx(), sectioned: true })).toBeNull()
    expect(arrangeByBundle(items, { ...ctx(), sectioned: true, bundles: 'always' })).not.toBeNull()
    expect(arrangeByBundle(items, { ...ctx(), bundles: 'never' })).toBeNull()
  })
})

describe('arrangeSorted', () => {
  const items = [conv(1, { subject: 'banana', starred: true }), conv(2, { subject: 'Apple', unread_count: 1 }), conv(3, { subject: 'cherry' })]
  it('keeps the server order by default and sorts by a column', () => {
    expect(arrangeSorted(items, ctx()).map((e) => e.kind === 'row' && e.c.id)).toEqual([1, 2, 3])
    expect(arrangeSorted(items, ctx({ sort: { key: 'subject', dir: 'asc' } })).map((e) => e.kind === 'row' && e.c.id)).toEqual([2, 1, 3])
    expect(arrangeSorted(items, ctx({ sort: { key: 'star', dir: 'desc' } })).map((e) => e.kind === 'row' && e.c.id)[0]).toBe(1)
  })
  it('applies quick filters', () => {
    const q = { unread: true, starred: false, attachments: false }
    expect(arrangeSorted(items, ctx({ quick: q })).map((e) => e.kind === 'row' && e.c.id)).toEqual([2])
  })
})
