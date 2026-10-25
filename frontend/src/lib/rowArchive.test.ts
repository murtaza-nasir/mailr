// ACT-SURFACES: web.list
// Row actions follow the row (mail.archive, mail.move):
// a conversation that is not in the Inbox offers Move to Inbox where Archive sits, in any list,
// an Inbox layout tab included. Trash and Spam keep their own actions.
import { describe, expect, it } from 'vitest'
import { archiveKeyAction, archiveSlot } from './rowArchive'
import { swipeEffect } from './swipe'

const INBOX = 1
const TRASH = 2
const SPAM = 3
const LATER = 19
const FINANCIAL = 28
const roles = new Map([
  [INBOX, { role: 'inbox' as const }],
  [TRASH, { role: 'trash' as const }],
  [SPAM, { role: 'junk' as const }],
  [LATER, { role: null }],
  [FINANCIAL, { role: null }],
])
const none = { contextLabelId: null, removesCurrent: true }
const inInbox = { label_ids: [INBOX, FINANCIAL] }
const archived = { label_ids: [FINANCIAL] }

describe('archive slot', () => {
  it('offers Archive for a row in the Inbox and Move to Inbox for an archived one, in any list', () => {
    for (const view of ['inbox', 'all', `label:${FINANCIAL}`, 'starred', 'search', 'acct:3:inbox']) {
      expect(archiveSlot([inInbox], view, roles, none)).toBe('archive')
      expect(archiveSlot([archived], view, roles, none)).toBe('inbox')
    }
  })
  it('a Financial section in an Inbox tab: the archived row offers Move to Inbox', () => {
    expect(archiveSlot([archived], 'inbox', roles, none)).toBe('inbox')
  })
  it('a label list whose Archive removes the label (the Later tab) keeps Archive', () => {
    const later = { label_ids: [LATER] }
    expect(archiveSlot([later], 'inbox', roles, { contextLabelId: LATER, removesCurrent: true })).toBe('archive')
    expect(archiveSlot([later], 'inbox', roles, { contextLabelId: LATER, removesCurrent: false })).toBe('inbox')
    expect(archiveSlot([archived], 'inbox', roles, { contextLabelId: LATER, removesCurrent: true })).toBe('inbox')
  })
  it('several rows: Archive while any of them would change', () => {
    expect(archiveSlot([archived, inInbox], 'inbox', roles, none)).toBe('archive')
    expect(archiveSlot([archived, archived], 'inbox', roles, none)).toBe('inbox')
    expect(archiveSlot([], 'inbox', roles, none)).toBe('archive')
    expect(archiveSlot([archived], 'inbox', new Map(), none)).toBe('archive') // labels not loaded yet
  })
  it('Trash and Spam keep their own actions', () => {
    expect(archiveSlot([archived], 'trash', roles, none)).toBe('keep')
    expect(archiveSlot([archived], 'spam', roles, none)).toBe('keep')
    expect(archiveSlot([{ label_ids: [TRASH] }], 'search', roles, none)).toBe('keep')
    expect(archiveSlot([{ label_ids: [SPAM] }], 'all', roles, none)).toBe('keep')
  })
  it('the archive swipe follows the row', () => {
    const c = { unread_count: 0, starred: false }
    expect(swipeEffect('archive', c, 'inbox', 'inbox')).toMatchObject({ action: 'inbox', removes: false })
    expect(swipeEffect('archive', c, 'inbox', 'archive')).toMatchObject({ action: 'archive', removes: true })
    expect(swipeEffect('archive', c, 'trash', 'keep').action).toBe('untrash')
    expect(swipeEffect('archive', c, 'spam', 'keep').action).toBe('not_spam')
  })
  it('the e key runs what the row shows', () => {
    expect(archiveKeyAction([archived], 'inbox', roles, none)).toBe('inbox')
    expect(archiveKeyAction([inInbox], 'inbox', roles, none)).toBe('archive')
    expect(archiveKeyAction([{ label_ids: [LATER] }], 'inbox', roles, { contextLabelId: LATER, removesCurrent: true })).toBe('archive')
    expect(archiveKeyAction([archived], 'trash', roles, none)).toBe('archive')
  })
})
