// ACT-SURFACES: web.panel
// Chats on the phone layout (features/rail/assistant/ChatList.tsx): Ambrose fills the screen, and its
// list of earlier chats does too, with Back to the chat (ACT agent.chat_list, ACT agent.chat_open).
// The server's chat routes are faked. The wide panel: e2e/assistant-chats.spec.ts.
import fs from 'node:fs'
import { expect, test } from '../fixtures'

const SHOTS = process.env.CHATS_SHOTS
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true })

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString()

test('phone: Chats fills the Ambrose screen; Back returns, a row opens its chat', async ({ page }) => {
  const chats = [
    { id: 'e2e-p1', title: 'What did Dana say about the Lumen kickoff?', updated_at: ago(12), preview: 'Dana wants the kickoff on Monday at 10, with the design review moved to Thursday.' },
    { id: 'e2e-p2', title: 'Which invoices are still unpaid?', updated_at: ago(60 * 30), preview: 'Two: Northwind Hosting for September and the Acme print run.' },
  ]
  await page.route(/\/api\/v1\/agent\/sessions(\?.*)?$/, (route) => route.fulfill({ json: chats }))
  await page.route(/\/api\/v1\/agent\/sessions\/e2e-p2$/, (route) => route.fulfill({ json: { id: 'e2e-p2', turns: [
    { role: 'user', id: 1, content: 'Which invoices are still unpaid?', citations: [], steps: [] },
    { role: 'assistant', content: 'Two: Northwind Hosting for September and the Acme print run.', status: 'done', citations: [], steps: [] },
  ] } }))
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
  await page.goto('/mail/inbox')
  await page.getByTestId('phone-tabs').locator('[data-tab="assistant"]').click()
  await expect(page.locator('.side-panel .chat')).toBeVisible()

  await page.locator('.side-panel-head').getByRole('button', { name: 'Chats' }).click()
  const list = page.getByTestId('chats')
  await expect(list.locator('[data-chat-row]')).toHaveCount(2)
  // The list takes the whole panel, and the panel the whole width.
  const vw = page.viewportSize()!.width
  const box = (await list.boundingBox())!
  expect(box.width).toBeGreaterThan(vw - 40)
  await expect(page.locator('.side-panel .chat')).toBeHidden()
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/chats-phone.png` })

  await page.getByTestId('chats-back').click()
  await expect(list).toHaveCount(0)
  await expect(page.locator('.side-panel .chat')).toBeVisible()

  await page.locator('.side-panel-head').getByRole('button', { name: 'Chats' }).click()
  await list.locator('[data-chat-row]').nth(1).click()
  await expect(page.locator('.side-panel .chat .chat-turn').last()).toContainText('Northwind Hosting for September')
})

test.describe('phone: search and rename', () => {
  test.use({ extras: true })

  test('the search box and the row menu (a bottom sheet) work on the phone', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
    await page.goto('/mail/inbox')
    await page.getByTestId('phone-tabs').locator('[data-tab="assistant"]').click()
    await page.locator('.side-panel-head').getByRole('button', { name: 'Chats' }).click()
    const list = page.getByTestId('chats')
    await expect(list.locator('[data-chat-row]')).toHaveCount(3)
    const box = page.getByRole('searchbox', { name: 'Search chats' })
    await box.fill('harbour')
    await expect(list.locator('[data-chat-row]')).toHaveCount(1)
    await expect(list.getByTestId('chat-snippet').locator('.chats-hit')).toHaveText(['harbour'])
    // Nothing wider than the screen.
    const vw = page.viewportSize()!.width
    expect((await box.boundingBox())!.width).toBeLessThan(vw)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/chats-search-phone.png` })

    // The row's menu button shows on touch; its menu opens as a sheet.
    await list.getByTestId('chat-more').first().click()
    await page.getByRole('menuitem', { name: 'Rename' }).click()
    const field = page.getByRole('textbox', { name: 'Chat title' })
    await field.fill('Offsite plans')
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/chats-rename-phone.png` })
    await field.press('Enter')
    await box.fill('')
    await expect(list.locator('[data-chat-row]').filter({ hasText: 'Offsite plans' })).toHaveCount(1)
    await expect(page.locator('.toast', { hasText: 'Chat renamed.' })).toBeVisible()
    // The open chat's title fits in the header beside the buttons.
    await list.locator('[data-chat-row]').filter({ hasText: 'Offsite plans' }).click()
    const title = page.locator('.side-panel-head').getByTestId('chat-title')
    await expect(title).toHaveText('Offsite plans')
    expect((await title.boundingBox())!.x + (await title.boundingBox())!.width).toBeLessThanOrEqual(vw)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/chats-header-phone.png` })
  })
})
