import { describe, expect, it } from 'vitest'
import type { Label, LayoutTab } from '../../api/types'
import { arrangeByBundle } from '../../themes/shared/grouping'
import { isSectionedLayout, rowSender, rowSnippet, viewLabelId } from './rowText'

describe('rowSnippet', () => {
  it('drops a snippet that only repeats the subject', () => {
    expect(rowSnippet('Screenshot (Oct 1, 2026 15:49:33)', 'Screenshot (Oct 1, 2026 15:49:33)')).toBe('')
    expect(rowSnippet('mailr', '')).toBe('')
  })
  it('keeps what follows a repeated subject', () => {
    expect(rowSnippet('Read later: vLLM notes', 'Read later: vLLM notes. https://docs.vllm.ai')).toBe('https://docs.vllm.ai')
  })
  it('drops a subject that ends at a line or sentence boundary', () => {
    expect(rowSnippet('Weekly notes', 'Weekly notes\nBring the slides')).toBe('Bring the slides')
    expect(rowSnippet('Weekly notes', 'Weekly notes: bring the slides')).toBe('bring the slides')
    expect(rowSnippet('Weekly notes', 'Weekly notes — bring the slides')).toBe('bring the slides')
    expect(rowSnippet('Weekly notes', 'Weekly notes – bring the slides')).toBe('bring the slides')
    expect(rowSnippet('Weekly notes', 'Weekly notes - bring the slides')).toBe('bring the slides')
    expect(rowSnippet('Weekly notes', 'Weekly notes | bring the slides')).toBe('bring the slides')
    expect(rowSnippet('Weekly notes', 'Weekly notes. Bring the slides')).toBe('Bring the slides')
    expect(rowSnippet('Order shipped', 'Order shipped- it arrives Friday')).toBe('it arrives Friday')
  })
  it('drops one copy of a subject printed twice', () => {
    expect(rowSnippet('Title', 'Title Title body')).toBe('Title body')
    expect(rowSnippet('Title', 'Title Title - body')).toBe('body')
    expect(rowSnippet('Title', 'Title\nTitle')).toBe('')
    expect(rowSnippet('Title', 'Title Titleist rules')).toBe('Title Titleist rules')
  })
  it('never cuts a sentence that starts with the subject', () => {
    expect(rowSnippet('Your Tandem Bank sign-in code', 'Your Tandem Bank sign-in code is 715204. It expires in 10 minutes.'))
      .toBe('Your Tandem Bank sign-in code is 715204. It expires in 10 minutes.')
    expect(rowSnippet('Your code', 'Your code, as requested: 1234')).toBe('Your code, as requested: 1234')
    expect(rowSnippet('Version 2', 'Version 2.5 is out')).toBe('Version 2.5 is out')
    expect(rowSnippet('Sign-in', 'Sign-in-code: 1234')).toBe('Sign-in-code: 1234')
  })
  it('leaves other snippets alone', () => {
    expect(rowSnippet('Lunch', 'Lunchtime works for me')).toBe('Lunchtime works for me')
    expect(rowSnippet('Re: Quarterly plan', 'Added my comments')).toBe('Added my comments')
  })
})

describe('viewLabelId', () => {
  const labels = [
    { id: 1, name: 'Inbox', role: 'inbox' },
    { id: 20, name: 'Later' },
    { id: 21, name: 'Work/Projects' },
  ] as Label[]
  const later = { id: 'later', name: 'Later', sections: [] } as unknown as LayoutTab
  it('hides the context label first', () => {
    expect(viewLabelId('inbox', 21, later, 3, labels)).toBe(21)
  })
  it("hides a label's own view", () => {
    expect(viewLabelId('label:21', null, null, 0, labels)).toBe(21)
  })
  it('hides the label named like the open tab', () => {
    expect(viewLabelId('inbox', null, later, 3, labels)).toBe(20)
    expect(viewLabelId('inbox', null, { ...later, name: 'Projects' }, 3, labels)).toBe(21)
  })
  it('hides nothing on a one-tab inbox or a plain view', () => {
    expect(viewLabelId('inbox', null, later, 1, labels)).toBeUndefined()
    expect(viewLabelId('sent', null, null, 0, labels)).toBeUndefined()
  })
})

describe('isSectionedLayout (Cards bundles with card_bundles "flat")', () => {
  const later = { id: 'priority-later', preset: false }
  const items = [{ id: 1, subject: 'Hi', snippet: '', participants: [{ name: 'Ann', email: 'ann@example.org', me: false }],
    category: null } as never]
  const bundled = (sectioned: boolean, bundles: 'flat' | 'always' | 'never') =>
    arrangeByBundle(items, { view: 'inbox', prefs: { sort: null, quick: null, collapsed: {} } as never, labelsById: new Map(), sectioned, bundles })

  it('counts every tab of a user layout, also a one-section tab', () => {
    expect(isSectionedLayout(later)).toBe(true)
    expect(isSectionedLayout({ id: 'unread_first', preset: true })).toBe(true)
  })
  it('leaves plain views and the built-in Default inbox unsectioned', () => {
    expect(isSectionedLayout(null)).toBe(false)
    expect(isSectionedLayout({ id: 'default', preset: true })).toBe(false)
  })
  it('a one-section layout tab gets no bundles with "flat"; "always" still bundles', () => {
    expect(bundled(isSectionedLayout(later), 'flat')).toBeNull()
    expect(bundled(isSectionedLayout(later), 'always')).not.toBeNull()
    expect(bundled(isSectionedLayout({ id: 'default', preset: true }), 'flat')).not.toBeNull()
  })
})

describe('rowSender', () => {
  const me = { email: 'me@x.example', name: 'Me', me: true }
  const base = { participants: [me], message_count: 1, draft_count: 0 }
  it('names whom a sent email without a reply went to', () => {
    expect(rowSender({ ...base, recipients: [{ email: 'dana@out.example', name: 'Dana Whitfield' }] })).toBe('To: Dana Whitfield')
    expect(rowSender({ ...base, message_count: 2, recipients: [{ email: 'dana@out.example', name: 'Dana Whitfield' }, { email: 'lee@out.example', name: 'Lee Park' }] }))
      .toBe('To: Dana, Lee 2')
  })
  it('keeps "me" for a note to self and the senders once someone else writes', () => {
    expect(rowSender({ ...base, to_self: true, recipients: [] })).toBe('me')
    expect(rowSender({ ...base, participants: [me, { email: 'dana@out.example', name: 'Dana Whitfield' }], message_count: 2, recipients: [] }))
      .toBe('me, Dana 2')
  })
  it('works with a summary from a server without the fields', () => {
    expect(rowSender(base)).toBe('me')
  })
})
