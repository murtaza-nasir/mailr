// ACT-SURFACES: web.settings
// layout.inbox_only in the layout editor: a section that becomes a label section starts with
// "Only mail in the Inbox" on; an Inbox section has no use for it.
import { describe, expect, it } from 'vitest'
import { baseChange, newSection } from './LayoutEditor'

describe('layout editor: Conversations from', () => {
  it('turns Only mail in the Inbox on for a new label section', () => {
    expect(baseChange(newSection(), 'label:28')).toEqual({ base: 'label:28', category: null, inbox_only: true })
  })
  it('keeps the choice when one label becomes another', () => {
    const sec = newSection('Bills', { base: 'label:28', inbox_only: false })
    expect(baseChange(sec, 'label:29')).toEqual({ base: 'label:29', category: null })
  })
  it('clears it on an Inbox section and keeps the category there', () => {
    const sec = newSection('Bills', { base: 'label:28', inbox_only: true, category: null })
    expect(baseChange(sec, 'inbox')).toEqual({ base: 'inbox', category: null, inbox_only: false })
    expect(baseChange(newSection('S', { category: 'social' }), 'starred')).toEqual({ base: 'starred', category: null })
  })
})
