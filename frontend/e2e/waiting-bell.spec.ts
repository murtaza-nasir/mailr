// ACT-SURFACES: web.reader web.nav
// "Waiting for you": one bell in every theme and on the phone, with the
// sections Approvals, Not sent and Reminders. The badge counts what needs you. Several waiting
// requests from one agent get "Allow all n", which lists each one first and then decides each one
// once (approval.approve_many). A failed send has Retry and a link into the Outbox
// (draft.retry_send, draft.outbox). Screenshots in five themes, light and dark, desktop and phone.
import os from 'node:os'
import path from 'node:path'
import { request as pwRequest, type APIRequestContext, type Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { schemes, THEMES } from './matrix'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-waiting-bell-shots')
const status = async (request: APIRequestContext, id: number) => (await (await request.get(`/api/v1/approvals/${id}`)).json()).status as string

async function labelRequests(request: APIRequestContext, names: string[]): Promise<number[]> {
  const tok = await (await request.post('/api/v1/tokens', { data: { name: 'claude-code', scopes: ['read', 'label'] } })).json()
  const agent = await pwRequest.newContext({
    baseURL: process.env.E2E_BASE ?? 'http://localhost:5173',
    extraHTTPHeaders: { Authorization: `Bearer ${tok.token as string}` },
  })
  const ids: number[] = []
  for (const name of names) {
    const r = await agent.post('/api/v1/labels', { data: { name } })
    expect(r.status()).toBe(202)
    ids.push((await r.json()).approval.id as number)
  }
  await agent.dispose()
  return ids
}

async function failedSend(page: Page) {
  let retried = 0
  const failed = {
    id: 999101, status: 'failed', send_at: new Date().toISOString(), recipients: ['marco@example.org'], subject: 'Quarterly numbers',
    identity_id: 1, token_name: null, approval_reason: '', error: 'SMTPRecipientsRefused: 550 5.1.1 <marco@example.org>: no such user',
    conversation_id: null, created_at: new Date().toISOString(), sent_at: null, draft_id: null, sent_message_id: null, payload: {},
  }
  await page.route('**/api/v1/outbox?active=true', (r) => r.fulfill({ json: retried ? [] : [failed] }))
  await page.route('**/api/v1/outbox/999101/retry', (r) => {
    retried++
    return r.fulfill({ json: { outbox: { ...failed, status: 'queued', error: '' } } })
  })
  return () => retried
}

test('approval.approve_many: Allow all lists each request from one agent, then approves each once', async ({ page, seeded }) => {
  expect(seeded.email).toBeTruthy()
  const ids = await labelRequests(page.request, ['E2E Many A', 'E2E Many B', 'E2E Many C'])
  await page.goto('/mail/inbox')
  const bell = page.getByTestId('waiting-bell')
  // The two seeded requests and these three.
  await expect(bell).toHaveAccessibleName(/Waiting for you: 5 approvals/)
  await bell.click()
  const approvals = page.getByRole('region', { name: 'Approvals' })
  const all = approvals.getByTestId('allow-all')
  // Only the agent with several waiting requests gets the line; the seeded ones are one per chat.
  await expect(all).toHaveCount(1)
  await expect(all).toContainText('3 requests from claude-code')
  await all.getByRole('button', { name: 'Allow all 3' }).click()
  const list = all.getByRole('group', { name: 'Allow all 3 from claude-code' })
  await expect(list.getByRole('listitem')).toHaveCount(3)
  await expect(list).toContainText('E2E Many A')
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.screenshot({ path: path.join(SHOTS, `allow-all-${scheme}.png`) })
  }
  await list.getByRole('button', { name: 'Allow all 3' }).click()
  await expect(page.locator('.toast').filter({ hasText: 'Allowed 3 requests.' })).toBeVisible()
  for (const id of ids) expect(await status(page.request, id)).not.toBe('pending')
  // No standing rule: a new request from the same agent still asks.
  const [later] = await labelRequests(page.request, ['E2E Many later'])
  expect(await status(page.request, later)).toBe('pending')
})

test('draft.retry_send from the bell: Not sent lists the failed send with Retry and opens the Outbox', async ({ page }) => {
  const retried = await failedSend(page)
  await page.goto('/mail/inbox')
  const bell = page.getByTestId('waiting-bell')
  await expect(bell).toHaveAccessibleName(/1 not sent/)
  await bell.click()
  const notSent = page.getByRole('region', { name: 'Not sent' })
  const row = notSent.getByTestId('waiting-not-sent')
  await expect(row).toContainText('Quarterly numbers')
  await expect(row).toContainText('An address was refused by the mail server')
  await notSent.getByRole('button', { name: 'Open Outbox' }).click()
  await expect(page).toHaveURL(/\/mail\/outbox/)
  await expect(page.getByRole('dialog', { name: 'Waiting for you' })).toHaveCount(0)
  await bell.click()
  await page.getByRole('region', { name: 'Not sent' }).getByRole('button', { name: 'Retry' }).click()
  await expect.poll(retried).toBe(1)
  await expect(page.locator('.toast').filter({ hasText: 'Sending again' })).toBeVisible()
})

for (const theme of THEMES) {
  test(`one bell in ${theme}, desktop and phone, light and dark`, async ({ page, seeded }) => {
    expect(seeded.email).toBeTruthy()
    await failedSend(page)
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    for (const [w, h, where] of [[1440, 900, 'desktop'], [390, 844, 'phone']] as const) {
      await page.setViewportSize({ width: w, height: h })
      await page.goto('/mail/inbox')
      const bell = page.getByTestId('waiting-bell')
      await expect(bell).toHaveCount(1)
      await expect(bell).toBeVisible()
      await expect(bell).toHaveAccessibleName('Waiting for you: 2 approvals, 1 not sent')
      await expect(bell.locator('.badge')).toHaveText('3')
      await bell.click()
      const pop = page.getByRole('dialog', { name: 'Waiting for you' })
      await expect(pop.getByRole('region', { name: 'Approvals' })).toBeVisible()
      await expect(pop.getByRole('region', { name: 'Not sent' })).toBeVisible()
      for (const scheme of schemes(theme)) {
        await page.emulateMedia({ colorScheme: scheme })
        await page.waitForTimeout(150)
        await page.screenshot({ path: path.join(SHOTS, `bell-${theme}-${where}-${scheme}.png`) })
      }
      await page.keyboard.press('Escape')
      await expect(pop).toHaveCount(0)
    }
  })
}
