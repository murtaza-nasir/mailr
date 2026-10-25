// ACT-SURFACES: web.nav web.list
// Each account's own folders (acct:<id>:<view>) and the account: search operator on the web
// (lib/views.ts, lib/search.ts, features/layout/accounts.tsx). ACT mail.account_view, ACT mail.search_account
import { describe, expect, it } from 'vitest'
import type { MailAccount } from '../api/types'
import { mailboxHeading } from '../features/layout/accounts'
import { dropPlan } from './mailDrop'
import { accountTokens, scopeToAccount } from './search'
import { swipeEffect } from './swipe'
import { accountView, baseView, mailPath, viewAccount, viewIsTrashOrSpam, viewRemovesOnArchive, viewTitle } from './views'

const acct = (id: number, address: string): MailAccount => ({ id, address, name: address, color: 'blue', addresses: [address], identity_id: null, local_only: false })

describe('account folder views', () => {
  it('name an account and the folder they narrow', () => {
    const v = accountView(7, 'inbox')
    expect(v).toBe('acct:7:inbox')
    expect(viewAccount(v)).toBe(7)
    expect(baseView(v)).toBe('inbox')
    expect(viewAccount('inbox')).toBeNull()
    expect(baseView('label:3')).toBe('label:3')
    expect(viewAccount('acct:x:inbox')).toBeNull()
  })
  it('act as the folder they narrow', () => {
    expect(viewRemovesOnArchive('acct:7:inbox')).toBe(true)
    expect(viewIsTrashOrSpam('acct:7:spam')).toBe(true)
    expect(viewIsTrashOrSpam('acct:7:sent')).toBe(false)
    expect(swipeEffect('archive', { unread_count: 0, starred: false }, 'acct:7:trash').action).toBe('untrash')
    expect(dropPlan('inbox', { from: 'acct:7:spam', shift: false, labels: [] })?.req).toMatchObject({ action: 'not_spam' })
    expect(dropPlan('acct:7:inbox', { from: 'inbox', shift: false, labels: [] })).toBeNull()
  })
  it('keep their scope in the address bar and their title names the account', () => {
    expect(mailPath('acct:7:inbox', null, 'lunch')).toBe('/mail/acct%3A7%3Ainbox?q=lunch')
    expect(viewTitle('acct:7:sent', [], [acct(7, 'work@example.org')])).toBe('Sent · work@example.org')
    expect(viewTitle('acct:8:sent', [], [acct(7, 'work@example.org')])).toBe('Sent')
  })
})

describe('the mailbox heading', () => {
  it('says All accounts for several, the address for one', () => {
    expect(mailboxHeading([acct(1, 'a@example.org'), acct(2, 'b@example.net')], 'Mailbox')).toBe('All accounts')
    expect(mailboxHeading([acct(1, 'a@example.org')], 'Mailbox')).toBe('a@example.org')
    expect(mailboxHeading([], 'Mailbox')).toBe('Mailbox')
  })
})

describe('account: in a search', () => {
  it('reads the account tokens', () => {
    expect(accountTokens('account:work@example.org lunch')).toEqual(['work@example.org'])
    expect(accountTokens('from:bob lunch')).toEqual([])
  })
  it('scopes a search typed in an account folder, once', () => {
    expect(scopeToAccount('lunch', 'work@example.org')).toBe('account:work@example.org lunch')
    expect(scopeToAccount('account:home@example.net lunch', 'work@example.org')).toBe('account:home@example.net lunch')
    expect(scopeToAccount('lunch', null)).toBe('lunch')
    expect(scopeToAccount('  ', 'work@example.org')).toBe('  ')
  })
})
