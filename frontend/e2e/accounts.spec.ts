// ACT-SURFACES: web.nav web.list web.settings web.palette
// Several mail accounts in one mailbox (backend core/mailaccounts.py). The
// mailbox is unified, so its heading says "All accounts" (one account: its address). With Settings →
// Mail → Inbox → "Show each account separately" on, each account has its own Inbox, Sent, Drafts,
// Scheduled, Spam and Trash in the navigation of every theme (ACT mail.account_folders,
// ACT mail.account_view), with the server's counts; Compose in an account's folders starts from its
// address; a search there stays in the account (ACT mail.search_account); the unified list shows
// each row's account as a coloured dot, only with two or more accounts (ACT mail.account_colour).
// Screenshots: Classic and Office, light and dark, the setting off and on.
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-accounts-shots')

const row = (page: Page, subject: string) => page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject })

async function theme(page: Page, name: string) {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), name)
}

async function folders(page: Page, on: boolean) {
  const r = await page.request.patch('/api/v1/me/settings', { data: { account_folders: on } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

async function shots(page: Page, name: string) {
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(200)
    await page.screenshot({ path: path.join(SHOTS, `${name}-${scheme}.png`) })
  }
  await page.emulateMedia({ colorScheme: 'light' })
}

test.describe('one account', () => {
  test('the mailbox is titled with its address, and rows carry no dots', async ({ page, seeded }) => {
    await theme(page, 'office')
    await page.goto('/mail/inbox')
    await expect(page.locator('.o-section[data-section="account"] .o-section-toggle').first()).toHaveText(seeded.email)
    await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
    await expect(page.getByTestId('row-accounts')).toHaveCount(0)
    // The setting changes nothing with one account.
    await folders(page, true)
    await page.reload()
    await expect(page.locator('.o-section[data-section^="acct."]')).toHaveCount(0)
  })
})

test.describe('two accounts', () => {
  test.use({ accounts: true })

  // Desk: long senders once became a bare "…" and the account dot
  // touched the name. The name keeps a visible start with its own ellipsis; the dot keeps a gap.
  test('Desk: a long sender keeps its start beside the account dot', async ({ page }) => {
    const long = 'Northfield Analytics Institute Annual Meeting Committee'
    await page.route(/\/api\/v1\/conversations(\?|$)/, async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      for (const c of body.items ?? []) if (c.subject === 'E2E Work report') for (const p of c.participants ?? []) if (!p.me) p.name = long
      await route.fulfill({ response: res, json: body })
    })
    await theme(page, 'desk')
    await page.setViewportSize({ width: 1300, height: 900 })
    await page.goto('/mail/inbox')
    const name = row(page, 'E2E Work report').locator('.d-from-name')
    await expect(name).toHaveText(long)
    const m = await name.evaluate((el) => {
      const r = el.getBoundingClientRect()
      const dot = el.parentElement!.querySelector('.row-accounts')!.getBoundingClientRect()
      return { shown: Math.round(r.width), cut: el.scrollWidth > el.clientWidth, gap: Math.round(dot.left - r.right) }
    })
    expect(m.shown, JSON.stringify(m)).toBeGreaterThan(60)
    expect(m.cut, JSON.stringify(m)).toBe(true)
    expect(m.gap, JSON.stringify(m)).toBeGreaterThanOrEqual(3)
  })

  test('the heading says All accounts in Office and Desk; rows show their account', async ({ page, seeded }) => {
    const a = seeded.accounts!
    await theme(page, 'office')
    await page.goto('/mail/inbox')
    await expect(page.locator('.o-section[data-section="account"] .o-section-toggle').first()).toHaveText('All accounts')
    await expect(page.locator('body')).not.toContainText(`${seeded.email}Inbox`)
    // The unified list: each account's mail with that account's dot, named for screen readers.
    await expect(row(page, 'E2E Work report').getByRole('img', { name: `Account: ${a.work_address}` })).toBeVisible()
    await expect(row(page, 'E2E Home letter').getByRole('img', { name: `Account: ${a.home_address}` })).toBeVisible()
    // Mail that came through no account has no dot.
    await expect(row(page, 'E2E Archive me').getByTestId('row-accounts')).toHaveCount(0)
    await shots(page, 'office-off')
    await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'desk'))
    await page.goto('/mail/inbox')
    await expect(page.locator('.d-account')).toHaveText('All accounts')
  })

  test('Classic: the setting adds a group per account with its own folders and counts', async ({ page, seeded }) => {
    const a = seeded.accounts!
    await page.goto('/mail/inbox')
    await expect(row(page, 'E2E Work report')).toBeVisible()
    await expect(page.getByTestId('nav-accounts')).toHaveCount(0)
    await shots(page, 'classic-off')
    // Settings → Mail → Inbox.
    await page.goto('/settings/mail/inbox')
    const box = page.getByRole('checkbox', { name: /Show each account separately/ })
    await expect(box).not.toBeChecked()
    await box.check()
    await expect(box).toBeChecked()
    await page.goto('/mail/inbox')
    const nav = page.getByTestId('nav-accounts')
    await expect(nav).toBeVisible()
    const work = nav.getByRole('group', { name: a.work_address })
    const home = nav.getByRole('group', { name: a.home_address })
    for (const name of ['Inbox', 'Sent', 'Drafts', 'Scheduled', 'Spam', 'Trash']) await expect(work.getByRole('link', { name })).toBeVisible()
    // The server's counts: Work has one unread in its Inbox and one in Spam, Home two in its Inbox.
    await expect(work.getByRole('link', { name: 'Inbox' })).toContainText('1')
    await expect(work.getByRole('link', { name: 'Spam' })).toContainText('1')
    // Home also holds the seeded threads you replied to from its address (sent mail is the account's).
    await expect(home.getByRole('link', { name: 'Inbox' })).toContainText(/[2-9]/)
    await shots(page, 'classic-on')

    // Work's Inbox lists only Work's mail, at an address of its own.
    await work.getByRole('link', { name: 'Inbox' }).click()
    await expect(page).toHaveURL(new RegExp(`/mail/acct%3A${a.work}%3Ainbox$`))
    await expect(row(page, 'E2E Work report')).toBeVisible()
    await expect(row(page, 'E2E Home letter')).toHaveCount(0)
    await expect(work.getByRole('link', { name: 'Inbox' })).toHaveAttribute('aria-current', 'page')
    await expect(page).toHaveTitle(new RegExp(`Inbox · ${a.work_address.replace(/\./g, '\\.')}`))
    // Back and forward keep the scope.
    await home.getByRole('link', { name: 'Inbox' }).click()
    await expect(row(page, 'E2E Home letter')).toBeVisible()
    await page.goBack()
    await expect(row(page, 'E2E Work report')).toBeVisible()
    await expect(row(page, 'E2E Home letter')).toHaveCount(0)
    await page.goForward()
    await expect(row(page, 'E2E Home bill')).toBeVisible()
    // A bookmark opens the same folder.
    await page.goto(`/mail/acct:${a.work}:spam`)
    await expect(row(page, 'E2E Work junk')).toBeVisible()
    // Folders close and stay closed.
    await nav.getByTestId(`acct-group-${a.work}`).click()
    await expect(work.getByRole('link', { name: 'Inbox' })).toHaveCount(0)
    await page.reload()
    await expect(page.getByTestId(`acct-group-${a.work}`)).toHaveAttribute('aria-expanded', 'false')
  })

  test('select all in an account folder acts on that account only', async ({ page, seeded }) => {
    const a = seeded.accounts!
    await folders(page, true)
    await page.goto(`/mail/acct:${a.home}:inbox`)
    await expect(row(page, 'E2E Home letter')).toBeVisible()
    await page.getByRole('checkbox', { name: 'Select all' }).click()
    await page.getByRole('toolbar').getByRole('button', { name: 'Archive' }).first().click()
    await expect(row(page, 'E2E Home letter')).toHaveCount(0)
    await page.goto('/mail/inbox')
    await expect(row(page, 'E2E Work report')).toBeVisible()
    await expect(row(page, 'E2E Home letter')).toHaveCount(0)
  })

  test('Compose in an account folder starts from its address; a search there stays in it', async ({ page, seeded }) => {
    const a = seeded.accounts!
    await folders(page, true)
    await page.goto(`/mail/acct:${a.work}:inbox`)
    await expect(row(page, 'E2E Work report')).toBeVisible()
    // The search carries the account and shows it as a chip; the chip takes it out again.
    const box = page.getByTestId('search-input')
    await box.fill('E2E')
    await box.press('Enter')
    await expect(page).toHaveURL(new RegExp(`q=account%3A${encodeURIComponent(a.work_address).replace(/\./g, '\\.')}`))
    const chip = page.getByTestId('search-account-chip')
    await expect(chip).toContainText(`In: ${a.work_address}`)
    await expect(row(page, 'E2E Work report')).toBeVisible()
    await expect(row(page, 'E2E Home letter')).toHaveCount(0)
    await chip.click()
    await expect(row(page, 'E2E Home letter')).toBeVisible()
    await expect(page.getByTestId('search-account-chip')).toHaveCount(0)
    // Compose inside the account's folder picks its address.
    await page.goto(`/mail/acct:${a.work}:inbox`)
    await expect(row(page, 'E2E Work report')).toBeVisible()
    await page.locator('.compose-btn').click()
    await expect(page.getByRole('combobox', { name: 'From' })).toHaveValue(String(a.work_identity))
  })

  test('Office: one folder node per account; Focus: the palette lists them', async ({ page, seeded }) => {
    const a = seeded.accounts!
    await folders(page, true)
    await theme(page, 'office')
    await page.goto('/mail/inbox')
    const node = page.locator(`.o-section[data-section="acct.${a.work}"]`)
    await expect(node.locator('.o-section-toggle')).toHaveText(a.work_address)
    await expect(node.getByRole('link', { name: 'Inbox' })).toContainText('1')
    await node.getByRole('link', { name: 'Spam' }).click()
    await expect(row(page, 'E2E Work junk')).toBeVisible()
    await expect(page.locator('.o-list-title h1')).toHaveText(`Spam · ${a.work_address}`)
    await page.goto('/mail/inbox')
    await expect(row(page, 'E2E Work report')).toBeVisible()
    await shots(page, 'office-on')
    // Focus has no navigation pane: the command palette lists each account's folders.
    await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'focus'))
    await page.goto('/mail/inbox')
    await expect(row(page, 'E2E Work report')).toBeVisible()
    await page.keyboard.press('Control+k')
    await page.keyboard.type(`Inbox · ${a.work_address}`)
    await page.getByRole('option', { name: new RegExp(`Inbox · ${a.work_address.replace(/\./g, '\\.')}`) }).first().click()
    await expect(page).toHaveURL(new RegExp(`/mail/acct%3A${a.work}%3Ainbox$`))
    await expect(page.locator('.f-view')).toHaveText(`Inbox · ${a.work_address}`)
  })

  test('an account colour is chosen on its card and shows on the rows', async ({ page, seeded }) => {
    const a = seeded.accounts!
    await page.goto('/settings/mail/accounts')
    const btn = page.getByTestId(`account-colour-${a.work}`)
    await expect(btn).toHaveAttribute('aria-label', /Orange/)
    await btn.click()
    await page.getByRole('menuitemradio', { name: 'Teal' }).or(page.getByRole('menuitem', { name: 'Teal' })).first().click()
    await expect(page.getByTestId(`account-colour-${a.work}`)).toHaveAttribute('aria-label', /Teal/)
    await page.goto('/mail/inbox')
    await expect(row(page, 'E2E Work report').locator('.acct-dot')).toHaveAttribute('data-hue', 'teal')
  })
})
