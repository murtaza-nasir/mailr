// ACT-SURFACES: web.reader
// A grant on one card covers the other waiting requests of the same kind. An agent token asks twice to create a label. In the approvals drawer the first card's
// "Allow for 12 hours" says it also approves the other one, before the tap. After the tap a toast
// says so, with Undo (approval.approve, approval.undo_grant). Screenshots light and dark.
import os from 'node:os'
import path from 'node:path'
import { request as pwRequest, type APIRequestContext } from '@playwright/test'
import { expect, test } from './fixtures'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-approval-grant-shots')
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

test('Allow for 12 hours says it also approves the other waiting request, then offers Undo', async ({ page, seeded }) => {
  expect(seeded.email).toBeTruthy()
  const [first, second] = await labelRequests(page.request, ['E2E Grant A', 'E2E Grant B'])
  await page.goto('/mail/inbox')
  await page.getByRole('button', { name: /Waiting for you: \d+ approvals?/ }).click()
  const drawer = page.getByRole('region', { name: 'Approvals' })
  const card = drawer.locator(`[data-approval-id="${first}"]`)
  const grant = card.getByRole('button', { name: 'Allow for 12 hours (also approves 1 other waiting)' })
  await expect(grant).toBeVisible()
  // "Always allow…" sits behind the card's ⋮.
  await card.getByRole('button', { name: 'More choices' }).click()
  await expect(card.getByRole('button', { name: 'Always allow… (also approves 1 other waiting)' })).toBeVisible()
  // The help on the button says what the grant covers.
  await expect(grant).toHaveAttribute('data-tip', /This agent may create or change labels for 12 hours without asking, with any input/)
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await card.screenshot({ path: path.join(SHOTS, `approval-grant-card-${scheme}.png`) })
  }
  await grant.click()
  const t = page.getByRole('status').filter({ hasText: 'Allowed for 12 hours. Also approved 1 other request.' })
  await expect(t).toBeVisible()
  await page.screenshot({ path: path.join(SHOTS, 'approval-grant-toast.png') })
  expect(await status(page.request, second)).not.toBe('pending')
  await t.getByRole('button', { name: 'Undo' }).click()
  // Undo puts the other request back unless the worker has already run it; the toast says which.
  const after = page.getByText(/Undone\. 1 request is waiting again\.|The rule is removed, but 1 request had already run\./)
  await expect(after).toBeVisible()
  const text = await after.textContent()
  expect(await status(page.request, second)).toBe(text?.startsWith('Undone') ? 'pending' : 'executed')
})
