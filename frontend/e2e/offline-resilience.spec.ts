// The web app when the network, the session check or the build changes under it,
// in the Classic and Cards themes:
// - offline with a conversation open, and while composing (context.setOffline);
// - a 503 from the session check keeps the app and its compose window, a 401 signs out and the
//   compose text comes back after signing in;
// - a deploy that removed a lazy chunk reloads once, and a second failure offers the reload;
// - compose text the server never saw comes back after a reload as "Restored unsent draft".
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { themes } from './matrix'

// The connection handling is shared: nightly Classic and Cards, otherwise Classic (e2e/matrix.ts).
const THEMES = themes({ all: ['classic', 'cards'], release: ['classic'] })

const rows = (page: Page) => page.locator('[data-mail-list] [role="row"]')
const banner = (page: Page, kind: 'offline' | 'unreachable' | 'new-version') => page.locator(`[data-connection="${kind}"]`)

async function openConversation(page: Page, subject: string) {
  await rows(page).filter({ hasText: subject }).first().click()
  await expect(page.locator('.conv-view').getByText(subject).first()).toBeVisible()
}

async function openCompose(page: Page, to: string, subject: string, body: string) {
  await page.goto('/mail/inbox?compose=1')
  const win = page.locator('.compose-win')
  await expect(win).toBeVisible()
  await win.getByLabel('To recipients').fill(to)
  await page.keyboard.press('Enter')
  await win.getByLabel('Subject').fill(subject)
  await win.locator('.ProseMirror').click()
  await page.keyboard.type(body)
  return win
}

/** Server drafts with this subject, as the API sees them now. */
async function serverDrafts(page: Page, subject: string): Promise<number> {
  const r = await page.request.get('/api/v1/conversations', { params: { view: 'all', q: `in:anywhere subject:"${subject}"`, limit: '5' } })
  const items = (await r.json()).items as { subject: string; draft_count: number }[]
  return items.filter((c) => c.subject === subject).reduce((n, c) => n + c.draft_count, 0)
}

/** Make the session check stale and ask for it again, the way a tab coming back does. */
async function recheckSession(page: Page) {
  await page.clock.fastForward('01:30')
  await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')))
}

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    })

    test('offline: a cached conversation stays readable, an uncached one says offline, not deleted', async ({ page, context }) => {
      await page.goto('/mail/inbox')
      await openConversation(page, 'E2E Read me')
      await page.goto('/mail/inbox')
      await openConversation(page, 'E2E Read me')
      await page.goBack()
      await expect(rows(page).filter({ hasText: 'E2E Star me' }).first()).toBeVisible()

      await context.setOffline(true)
      await expect(banner(page, 'offline')).toContainText("You're offline. Showing what's cached")
      // Never opened: the list row's subject stands in, with the offline note.
      await rows(page).filter({ hasText: 'E2E Star me' }).first().click()
      const reader = page.locator('.conv-offline')
      await expect(reader).toContainText("You're offline. Showing what's cached")
      await expect(reader.getByRole('article', { name: 'Cached conversation' })).toContainText('E2E Star me')
      await expect(page.getByText('The conversation may have been deleted')).toHaveCount(0)
      await expect(page.getByText("Couldn't load this conversation")).toHaveCount(0)
      // Opened before: still readable.
      await page.goBack()
      await openConversation(page, 'E2E Read me')
      await expect(page.locator('.conv-offline')).toHaveCount(0)

      await context.setOffline(false)
      await expect(banner(page, 'offline')).toHaveCount(0)
      await page.goBack()
      await rows(page).filter({ hasText: 'E2E Star me' }).first().click()
      await expect(page.locator('.conv-offline')).toHaveCount(0)
      await expect(page.locator('.conv-view').getByText('E2E Star me').first()).toBeVisible()
    })

    test('a real 404 still says the conversation may have been deleted', async ({ page }) => {
      await page.goto('/mail/inbox')
      await page.route('**/api/v1/conversations/*', (r) =>
        r.request().resourceType() === 'fetch' && /\/conversations\/[^/?]+$/.test(new URL(r.request().url()).pathname)
          ? r.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"Not Found"}' })
          : r.continue())
      await rows(page).filter({ hasText: 'E2E Star me' }).first().click()
      await expect(page.getByText("Couldn't find this conversation")).toBeVisible()
      await expect(page.getByText('The conversation may have been deleted')).toBeVisible()
    })

    test('offline while composing: the text is kept on this device, closing warns, and a reload restores it', async ({ page, context }) => {
      const subject = `E2E Offline draft ${theme}`
      const win = await openCompose(page, 'pat@example.net', subject, 'First line typed online.')
      await expect(win.getByText('Draft saved')).toBeVisible()

      await context.setOffline(true)
      await win.locator('.ProseMirror').click()
      await page.keyboard.press('End')
      await page.keyboard.type(' Second line typed offline.')
      await expect(win.getByText('Draft not saved')).toBeVisible()
      // The compose window survives the offline state.
      await expect(win).toBeVisible()
      await expect(win.locator('.ProseMirror')).toContainText('Second line typed offline.')

      await context.setOffline(false)
      // Leaving the page asks first.
      const dialogs: string[] = []
      page.on('dialog', (d) => {
        dialogs.push(d.type())
        void d.accept()
      })
      await page.reload()
      expect(dialogs).toContain('beforeunload')

      const back = page.locator('.compose-win')
      await expect(back).toBeVisible()
      await expect(back.getByText('Restored unsent draft')).toBeVisible()
      await expect(back.locator('.ProseMirror')).toContainText('First line typed online. Second line typed offline.')
      await expect(back.getByLabel('Subject')).toHaveValue(subject)
      // Restored text goes to the server, which is the source of truth from then on.
      await expect(back.getByText('Draft saved')).toBeVisible()
      await expect.poll(() => page.evaluate(() => localStorage.getItem('mailr.localDrafts.v1'))).toBeNull()
      await expect.poll(() => serverDrafts(page, subject)).toBe(1)
      // One window, not a copy per reload.
      await page.reload()
      await expect(page.locator('.compose-win')).toHaveCount(0)
    })

    test('a 503 from the session check keeps the app and the compose window', async ({ page }) => {
      await page.clock.install()
      const win = await openCompose(page, 'pat@example.net', `E2E 503 ${theme}`, 'Text that must survive.')
      await expect(win.getByText('Draft saved')).toBeVisible()

      let calls = 0
      await page.route('**/api/v1/auth/session', (r) => {
        calls++
        return r.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Service Unavailable"}' })
      })
      await recheckSession(page)
      await expect(banner(page, 'unreachable')).toContainText("Can't reach Mailr. Retrying")
      expect(calls).toBeGreaterThan(0)
      await expect(page).toHaveURL(/\/mail\/inbox/)
      await expect(win).toBeVisible()
      await expect(win.locator('.ProseMirror')).toContainText('Text that must survive.')
      await expect(page.getByRole('heading', { name: "Can't reach Mailr" })).toHaveCount(0)

      await page.unroute('**/api/v1/auth/session')
      await banner(page, 'unreachable').getByRole('button', { name: 'Retry now' }).click()
      await expect(banner(page, 'unreachable')).toHaveCount(0)
      await expect(win).toBeVisible()
    })

    test('a 401 from the session check signs out, and the compose text comes back after signing in', async ({ page, seeded }) => {
      await page.clock.install()
      const subject = `E2E 401 ${theme}`
      const win = await openCompose(page, 'pat@example.net', subject, 'Written before the session ended.')
      await expect(win).toBeVisible()

      await page.route('**/api/v1/auth/session', (r) => r.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"Not authenticated"}' }))
      await recheckSession(page)
      await expect(page).toHaveURL(/\/login/)
      await page.unroute('**/api/v1/auth/session')

      await page.getByLabel('Email').fill(seeded.email)
      await page.getByLabel('Password').fill(seeded.password)
      await page.locator('.login-submit').click()
      await expect(page).toHaveURL(/\/mail\/inbox/)
      const back = page.locator('.compose-win')
      await expect(back).toHaveCount(1)
      await expect(back.getByText('Restored unsent draft')).toBeVisible()
      await expect(back.getByLabel('Subject')).toHaveValue(subject)
      await expect(back.locator('.ProseMirror')).toContainText('Written before the session ended.')
    })

    test('a deploy removed a lazy chunk: the app reloads once and the new chunk loads', async ({ page }) => {
      let failures = 0
      await page.route('**/src/features/tasks/TasksApp.tsx*', (r) => {
        if (failures++ === 0) return r.fulfill({ status: 404, body: 'Not Found' })
        return r.continue()
      })
      let loads = 0
      page.on('load', () => loads++)
      await page.goto('/mail/inbox')
      await expect(rows(page).first()).toBeVisible()
      await page.evaluate(() => {
        history.pushState({}, '', '/tasks')
        window.dispatchEvent(new PopStateEvent('popstate'))
      })
      await expect.poll(() => loads).toBe(2)
      await expect(page).toHaveURL(/\/tasks/)
      await expect(page.getByTestId('task-row').or(page.getByText(/Nothing|No tasks|Connect/).first()).first()).toBeVisible()
      await expect(page.getByText('Something went wrong')).toHaveCount(0)
    })

    test('a chunk that stays missing offers "New version, reload" instead of looping', async ({ page }) => {
      await page.route('**/src/features/tasks/TasksApp.tsx*', (r) => r.fulfill({ status: 404, body: 'Not Found' }))
      let loads = 0
      page.on('load', () => loads++)
      await page.goto('/mail/inbox')
      await expect(rows(page).first()).toBeVisible()
      await page.evaluate(() => {
        history.pushState({}, '', '/tasks')
        window.dispatchEvent(new PopStateEvent('popstate'))
      })
      await expect(page.getByRole('heading', { name: 'A new version of Mailr is available' })).toBeVisible()
      await expect(banner(page, 'new-version')).toBeVisible()
      expect(loads).toBe(2)
      // The other apps still work, and the boundary does not follow there.
      await page.evaluate(() => {
        history.pushState({}, '', '/mail/inbox')
        window.dispatchEvent(new PopStateEvent('popstate'))
      })
      await expect(rows(page).first()).toBeVisible()
      await expect(page.getByRole('heading', { name: 'A new version of Mailr is available' })).toHaveCount(0)
    })

    test('a new index.html on the server offers the reload', async ({ page }) => {
      await page.route((url) => url.pathname === '/', async (r) => {
        if (r.request().resourceType() !== 'fetch') return r.continue()
        const res = await r.fetch()
        const html = (await res.text()).replace(/src="\/@vite\/client"/, 'src="/assets/index-NEWBUILD.js"')
        return r.fulfill({ response: res, body: html })
      })
      await page.goto('/mail/inbox')
      await expect(banner(page, 'new-version')).toContainText('New version of Mailr')
      await expect(banner(page, 'new-version').getByRole('button', { name: 'Reload' })).toBeVisible()
    })
  })
}
