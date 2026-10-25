// ACT-SURFACES: web.reader web.settings
// The assistant's records on the web: briefs and approvals in the reader
// (web.reader), standing orders and the brief setting in Settings (web.settings). The seed gives
// each user a brief on "E2E Brief me", the standing order "E2E Order" (stars "E2E Order me"), and
// two orders the assistant asked to create, waiting for approval. Results are checked through the API.
// Covers: brief.read, brief.dock, approval.approve, approval.deny (web.reader); order.create,
// order.edit, order.pause, order.test, order.run, order.delete, brief.dock (web.settings).
import type { APIRequestContext, Page } from '@playwright/test'
import { conv, expect, test } from './fixtures'
import { openConversation } from './helpers'

const open = (page: Page, subject: string) => openConversation(page, subject)

const briefPosition = async (request: APIRequestContext) => (await (await request.get('/api/v1/me')).json()).settings.brief_position ?? 'top'
const approval = async (request: APIRequestContext, id: number) => (await (await request.get(`/api/v1/approvals/${id}`)).json()).status as string
interface ApiOrder { id: number; name: string; enabled: boolean; query: string }
const orders = async (request: APIRequestContext) => (await (await request.get('/api/v1/assistant/orders')).json()).items as ApiOrder[]

test('brief.read and brief.dock in the reader', async ({ page }) => {
  await open(page, 'E2E Brief me')
  const card = page.locator('.brief-card')
  await expect(card).toContainText('E2E brief: the invoice is due on Friday.')
  await card.getByRole('button', { name: 'Show beside the messages', exact: true }).click()
  await expect.poll(() => briefPosition(page.request)).toBe('side')
  await expect(page.locator('.conv-side .brief-card')).toBeVisible()
  await page.locator('.brief-card').getByRole('button', { name: 'Show above the messages', exact: true }).click()
  await expect.poll(() => briefPosition(page.request)).toBe('top')
})

test('approval.approve and approval.deny from the approvals drawer while reading', async ({ page, seeded }) => {
  await open(page, 'E2E Reply to me')
  await page.getByRole('button', { name: /Waiting for you: \d+ approvals?/ }).click()
  const drawer = page.getByRole('region', { name: 'Approvals' })
  await drawer.getByRole('group', { name: /E2E Approve me/ }).getByRole('button', { name: 'Allow once' }).click()
  await expect.poll(() => approval(page.request, seeded.approvals['E2E Approve me'])).toMatch(/^(approved|executing|executed)$/)
  await drawer.getByRole('group', { name: /E2E Deny me/ }).getByRole('button', { name: 'Deny', exact: true }).click()
  await expect.poll(() => approval(page.request, seeded.approvals['E2E Deny me'])).toBe('denied')
  // The heading stays: deciding never leaves the conversation.
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toBeVisible()
})

test('brief.dock in Settings → Ambrose → Briefs & replies', async ({ page }) => {
  await page.goto('/settings/ambrose/briefs')
  await page.getByRole('radio', { name: /Beside the messages/ }).check()
  await expect.poll(() => briefPosition(page.request)).toBe('side')
})

test('order.create, order.edit, order.pause and order.delete in Settings', async ({ page }) => {
  await page.goto('/settings/assistant?section=orders')
  await page.getByRole('button', { name: 'New order' }).click()
  let editor = page.getByRole('dialog', { name: 'New standing order' })
  await editor.getByLabel('Name').fill('E2E Created')
  await editor.getByLabel('Mail it applies to (search)').fill('from:created@example.org')
  await editor.getByLabel('Star').check()
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (await orders(page.request)).find((o) => o.name === 'E2E Created')?.query).toBe('from:created@example.org')

  const row = (name: string) => page.locator('li.as-order').filter({ hasText: name })
  await row('E2E Order').getByRole('button', { name: 'Edit' }).click()
  editor = page.getByRole('dialog', { name: 'Edit standing order' })
  await editor.getByLabel('Name').fill('E2E Order renamed')
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (await orders(page.request)).some((o) => o.name === 'E2E Order renamed')).toBe(true)

  await row('E2E Order renamed').getByRole('switch', { name: 'Pause' }).click()
  await expect.poll(async () => (await orders(page.request)).find((o) => o.name === 'E2E Order renamed')?.enabled).toBe(false)

  await row('E2E Created').getByRole('button', { name: 'Delete' }).click()
  await page.getByRole('dialog', { name: 'Delete this standing order?' }).getByRole('button', { name: 'Delete' }).click()
  await expect.poll(async () => (await orders(page.request)).some((o) => o.name === 'E2E Created')).toBe(false)
})

test('order.test and order.run on recent mail from Settings', async ({ page }) => {
  await page.goto('/settings/assistant?section=orders')
  await page.locator('li.as-order').filter({ hasText: 'E2E Order' }).getByRole('button', { name: 'Test and run on recent mail' }).click()
  const dialog = page.getByRole('dialog', { name: /Test “E2E Order”/ })
  // The dry run lists the matching email and changes nothing.
  const item = dialog.locator('li').filter({ hasText: 'E2E Order me' })
  await expect(item.getByLabel('Would run')).toBeVisible()
  expect((await conv(page.request, 'E2E Order me')).starred).toBe(false)
  await dialog.getByRole('button', { name: 'Run on 1 selected' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Order me')).starred).toBe(true)
})
