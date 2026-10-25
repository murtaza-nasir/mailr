// ACT-SURFACES: web.nav web.list
// Folders inside each account (lib/accountFolders.ts, lib/views.ts, features/layout/accounts.tsx):
// ACT mail.account_label_view, ACT mail.account_folder_create.
import { describe, expect, it } from 'vitest'
import type { Label, MailAccount } from '../api/types'
import { accountLabels } from '../features/layout/accounts'
import { shouldRemove } from '../api/hooks'
import { filedFromOtherAccount, movePin, pinFolder, pinsOf, splitAccountFolders, unpinFolder } from './accountFolders'
import { dropPlan } from './mailDrop'
import { accountLabelView, baseView, viewAccount, viewLabelOf, viewTitle } from './views'

const acct = (id: number, address: string): MailAccount => ({ id, address, name: address, color: 'blue', addresses: [address], identity_id: null, local_only: false })
const label = (id: number, name: string, home: number | null = null): Label =>
  ({ id, name, role: null, color: null, show_in_list: 'show', show_in_message_list: true, sort: 0, home_account_id: home, unread: 0, total: 0 })

describe('a label seen through an account', () => {
  it('is acct:<id>:label:<label id> and acts as the label', () => {
    const v = accountLabelView(3, 7)
    expect(v).toBe('acct:3:label:7')
    expect(viewAccount(v)).toBe(3)
    expect(baseView(v)).toBe('label:7')
    expect(viewLabelOf(v)).toBe(7)
    expect(viewLabelOf('label:9')).toBe(9)
    expect(viewLabelOf('acct:3:inbox')).toBeNull()
    expect(viewAccount('acct:3:label:x')).toBeNull()
    expect(viewTitle(v, [label(7, 'Receipts')], [acct(3, 'work@example.org')])).toBe('Receipts · work@example.org')
  })
  it('takes a drop as the label it shows, and Remove label takes the row out', () => {
    const labels = [label(7, 'Receipts')]
    expect(dropPlan('acct:3:label:7', { from: 'inbox', shift: false, labels })?.req).toEqual({ action: 'add_labels', label_ids: [7] })
    expect(dropPlan('acct:3:label:7', { from: 'acct:3:label:7', shift: false, labels })).toBeNull()
    expect(dropPlan('acct:3:label:7', { from: 'label:7', shift: false, labels })).toBeNull()
    expect(shouldRemove({ view: 'acct:3:label:7' } as never, { ids: [1], action: 'remove_labels', label_ids: [7] })).toBe(true)
  })
})

describe("an account's folders", () => {
  it('are the labels with its mail, those made in it, and the labels above them', () => {
    const labels = [label(1, 'Receipts'), label(2, 'Receipts/2026'), label(3, 'Travel', 5), label(4, 'Other'), label(6, 'Projects/Garden', 5), label(7, 'Projects')]
    const views = { 'acct:5:label:2': { unread: 1, total: 1 }, 'acct:9:label:4': { unread: 0, total: 2 } }
    expect(accountLabels(labels, 5, views).map((l) => l.name).sort()).toEqual(['Projects', 'Projects/Garden', 'Receipts', 'Receipts/2026', 'Travel'])
    expect(accountLabels(labels, 9, views).map((l) => l.name)).toEqual(['Other'])
    expect(accountLabels(labels, 8, views)).toEqual([])
  })
})

describe('filing mail of another account', () => {
  const accounts = [acct(1, 'home@example.net'), acct(2, 'work@example.org')]
  const labels = [label(7, 'Receipts', 2), label(8, 'Plain')]
  it("names the folder's account when the mail belongs only to others", () => {
    expect(filedFromOtherAccount({ action: 'add_labels', label_ids: [7] }, labels, [{ accounts: [1] }], accounts)?.address).toBe('work@example.org')
    expect(filedFromOtherAccount({ action: 'move', label_id: 7 }, labels, [{ accounts: [2] }, { accounts: [1] }], accounts)?.id).toBe(2)
  })
  it('says nothing for its own mail, plain labels, unknown rows or one account', () => {
    expect(filedFromOtherAccount({ action: 'add_labels', label_ids: [7] }, labels, [{ accounts: [2, 1] }], accounts)).toBeNull()
    expect(filedFromOtherAccount({ action: 'add_labels', label_ids: [8] }, labels, [{ accounts: [1] }], accounts)).toBeNull()
    expect(filedFromOtherAccount({ action: 'add_labels', label_ids: [7] }, labels, [undefined, {}], accounts)).toBeNull()
    expect(filedFromOtherAccount({ action: 'add_labels', label_ids: [7] }, labels, [{ accounts: [1] }], [accounts[0]])).toBeNull()
    expect(filedFromOtherAccount({ action: 'archive' }, labels, [{ accounts: [1] }], accounts)).toBeNull()
  })
})

// ACT mail.account_folder_pin, ACT mail.account_folder_unpin, ACT mail.account_folder_reorder,
// ACT mail.account_folders_more, ACT mail.account_folders_show_unread: the grouping and the pins.
describe("an account group's pinned folders and More folders", () => {
  const labels = [label(1, 'Receipts'), label(2, 'Receipts/2026'), label(3, 'Travel', 5), label(4, 'Shopping'), label(6, 'github'), label(7, 'Old')]
  const unread: Record<number, number> = { 2: 3, 4: 2, 6: 0, 1: 1 }
  const u = (l: Label) => unread[l.id] ?? 0
  const folders = labels.filter((l) => l.id !== 7) // Old holds none of the account's mail

  it('shows the pins in order, the rest under More with its unread sum', () => {
    const s = splitAccountFolders(folders, labels, [4, 3, 99], u, { showUnread: false, moreOpen: false })
    expect(s.pinned.map((l) => l.name)).toEqual(['Shopping', 'Travel'])
    expect(s.surfaced).toEqual([])
    expect(s.rest.map((l) => l.name)).toEqual(['github', 'Receipts', 'Receipts/2026'])
    expect(s.restUnread).toBe(4)
  })
  it('keeps a pinned label that holds none of the account mail', () => {
    expect(splitAccountFolders(folders, labels, [7], u, { showUnread: false, moreOpen: false }).pinned.map((l) => l.id)).toEqual([7])
  })
  it('surfaces folders with unread mail when the option is on, and not the pinned ones twice', () => {
    const s = splitAccountFolders(folders, labels, [4], u, { showUnread: true, moreOpen: false })
    expect(s.pinned.map((l) => l.id)).toEqual([4])
    expect(s.surfaced.map((l) => l.name)).toEqual(['Receipts', 'Receipts/2026'])
    expect(s.rest.map((l) => l.name)).toEqual(['github', 'Travel'])
    expect(s.restUnread).toBe(0)
  })
  it('keeps hidden labels out of the sum and of the surfaced ones', () => {
    const hidden = labels.map((l) => (l.id === 2 ? { ...l, show_in_list: 'hide' as const } : l))
    const s = splitAccountFolders(hidden.filter((l) => l.id !== 7), hidden, [], u, { showUnread: true, moreOpen: false })
    expect(s.surfaced.map((l) => l.id)).toEqual([1, 4])
    expect(s.restUnread).toBe(0)
  })
  it('shows the open folder above More while More is closed', () => {
    expect(splitAccountFolders(folders, labels, [], u, { showUnread: false, moreOpen: false, current: 6 }).surfaced.map((l) => l.id)).toEqual([6])
    expect(splitAccountFolders(folders, labels, [], u, { showUnread: false, moreOpen: true, current: 6 }).surfaced).toEqual([])
  })
  it('pins, unpins and reorders per account', () => {
    let p = pinFolder({}, 5, 1)
    p = pinFolder(p, 5, 2)
    p = pinFolder(p, 5, 3, 1)
    p = pinFolder(p, 9, 1)
    expect(p).toEqual({ 5: [3, 1, 2], 9: [1] })
    expect(pinsOf(p, 5)).toEqual([3, 1, 2])
    expect(pinFolder(p, 5, 2, 3)).toEqual({ 5: [2, 3, 1], 9: [1] })
    expect(movePin(p, 5, 1, -1)).toEqual({ 5: [1, 3, 2], 9: [1] })
    expect(movePin(p, 5, 3, -1)).toEqual(p)
    expect(movePin(p, 5, 2, 1)).toEqual(p)
    // A step over a deleted label: only the shown ones count.
    expect(movePin({ 5: [3, 8, 1] }, 5, 1, -1, [3, 1])).toEqual({ 5: [1, 3, 8] })
    expect(unpinFolder(p, 9, 1)).toEqual({ 5: [3, 1, 2] })
    expect(unpinFolder(p, 5, 3)).toEqual({ 5: [1, 2], 9: [1] })
  })
})
