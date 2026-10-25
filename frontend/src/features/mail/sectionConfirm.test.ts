import { describe, expect, it } from 'vitest'
import { sectionConfirmText } from './sectionConfirm'

describe('sectionConfirmText', () => {
  it('names the action, the count and the section; the button carries the count', () => {
    const t = sectionConfirmText({ action: 'archive', count: 2, where: 'Promotions', loaded: 2 })
    expect(t.title).toBe('Archive 2 conversations in Promotions?')
    expect(t.confirmLabel).toBe('Archive 2')
    expect(t.danger).toBe(false)
    expect(t.body).toBe('They leave Promotions and stay in All mail. All of Promotions is included and on screen. You can undo this afterwards.')
  })

  it('Delete is destructive and says where the mail goes', () => {
    const t = sectionConfirmText({ action: 'trash', count: 1, where: 'Purchases', loaded: 1 })
    expect(t.title).toBe('Delete 1 conversation in Purchases?')
    expect(t.confirmLabel).toBe('Delete 1')
    expect(t.danger).toBe(true)
    expect(t.body).toMatch(/^It moves to Trash\. All of Purchases is included and on screen\./)
  })

  it('says how many are not loaded on screen, from the server total', () => {
    const t = sectionConfirmText({ action: 'read', count: 120, where: 'Updates', loaded: 50 })
    expect(t.title).toBe('Mark 120 conversations in Updates as read?')
    expect(t.confirmLabel).toBe('Mark 120 as read')
    expect(t.body).toContain('included, with 70 conversations not on screen')
  })

  it('a capped total says "at least" and shows the plus', () => {
    const t = sectionConfirmText({ action: 'mute', count: 200_000, capped: true, where: 'Everything else', loaded: 50 })
    expect(t.title).toBe(`Mute ${(200_000).toLocaleString()}+ conversations in Everything else?`)
    expect(t.body).toContain('Everything else holds at least')
    expect(t.body).toContain("This can't be undone. Undo covers up to")
  })

  it('over the Undo limit it says so', () => {
    const t = sectionConfirmText({ action: 'archive', count: 12_000, where: 'Inbox', loaded: 50, undoMax: 10_000 })
    expect(t.body).toContain(`Undo covers up to ${(10_000).toLocaleString()} conversations`)
  })

  it('the ⋯ submenus name their target', () => {
    expect(sectionConfirmText({ action: 'move', count: 3, where: 'Promotions', loaded: 3, detail: 'Receipts' }).title)
      .toBe('Move 3 conversations in Promotions to Receipts?')
    expect(sectionConfirmText({ action: 'add_labels', count: 3, where: 'Promotions', loaded: 3, detail: 'Travel' }).body)
      .toMatch(/^They get the label Travel\./)
    expect(sectionConfirmText({ action: 'snooze', count: 3, where: 'Promotions', loaded: 3, detail: 'on Mon 8:00 AM' }).body)
      .toMatch(/^They leave Promotions and come back on Mon 8:00 AM\./)
  })

  it('page scope (the toolbar) says only the loaded rows change', () => {
    const t = sectionConfirmText({ action: 'read', count: 4, where: 'Inbox', loaded: 4, scope: 'page' })
    expect(t.body).toContain('Only conversations on screen are included. The rest of Inbox is unchanged.')
  })

  it('nothing to change: an OK that changes nothing', () => {
    const t = sectionConfirmText({ action: 'read', count: 0, where: 'Inbox', loaded: 0 })
    expect(t.title).toBe('Nothing to change in Inbox')
    expect(t.confirmLabel).toBe('OK')
  })
})
