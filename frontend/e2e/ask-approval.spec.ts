// ACT-SURFACES: web.panel web.reader web.settings
// agent.ask_about on approval cards: the card's Ask opens the ask capture with the
// approval as its chip, in the top bar's approvals drawer (web.reader), in Settings → Agents
// (web.settings) and on the assistant panel's cards (web.panel, the same ApprovalCard). Send puts
// `focused_approval` {id} at the top of the chat's context. Asking decides nothing: the approval
// stays pending, and the card keeps its buttons. Screenshots light and dark.
import os from 'node:os'
import path from 'node:path'
import type { APIRequestContext, Page, Request } from '@playwright/test'
import { expect, test } from './fixtures'

const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-ask-approval-shots')
const status = async (request: APIRequestContext, id: number) => (await (await request.get(`/api/v1/approvals/${id}`)).json()).status as string

async function fakeChat(page: Page): Promise<Record<string, unknown>[]> {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/v1/agent/chat', async (route) => {
    bodies.push((route.request() as Request).postDataJSON() as Record<string, unknown>)
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: 'event: session\ndata: {"session_id":"e2e-ask-appr"}\n\nevent: final\ndata: {"content":"It asks because creating a standing order asks by default."}\n\nevent: done\ndata: {}\n\n',
    })
  })
  await page.route('**/api/v1/agent/sessions/e2e-ask-appr', (route) => route.fulfill({ json: { id: 'e2e-ask-appr', turns: [] } }))
  return bodies
}

test('Ask from an approval in the drawer sends focused_approval and decides nothing', async ({ page, seeded }) => {
  const id = seeded.approvals['E2E Approve me']
  const bodies = await fakeChat(page)
  await page.goto('/mail/inbox')
  await page.getByRole('button', { name: /Waiting for you: \d+ approvals?/ }).click()
  const drawer = page.getByRole('region', { name: 'Approvals' })
  const card = drawer.getByRole('group', { name: /E2E Approve me/ })
  await card.getByRole('button', { name: /Ask Ambrose about this approval/ }).click()
  const capture = page.getByTestId('ask-capture')
  await expect(capture).toBeVisible()
  await expect(capture).toContainText('E2E Approve me')
  await capture.getByRole('textbox').fill('Why does this need approval?')
  await capture.getByRole('button', { name: /^Send/ }).click()
  await expect.poll(() => bodies.length).toBe(1)
  const ctx = bodies[0].context as Record<string, unknown>
  expect(ctx.focused_approval).toEqual({ id })
  expect(bodies[0].message).toBe('Why does this need approval?')
  await expect(page.getByText('It asks because creating a standing order asks by default.')).toBeVisible()
  await expect(page.getByTestId('chat-about')).toContainText('E2E Approve me')
  expect(await status(page.request, id)).toBe('pending')
})

test('Ask on the cards in Settings → Agents, light and dark', async ({ page, seeded }) => {
  const id = seeded.approvals['E2E Deny me']
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('/settings/agents')
    const card = page.getByRole('group', { name: /E2E Deny me/ })
    await expect(card.getByRole('button', { name: /Ask Ambrose about this approval/ })).toBeVisible()
    await expect(card.getByRole('button', { name: 'Allow once' })).toBeVisible()
    await card.screenshot({ path: path.join(SHOTS, `approval-card-ask-${scheme}.png`) })
    await card.getByRole('button', { name: /Ask Ambrose about this approval/ }).click()
    const capture = page.getByTestId('ask-capture')
    await expect(capture).toContainText('E2E Deny me')
    await page.screenshot({ path: path.join(SHOTS, `capture-approval-${scheme}.png`) })
    await page.keyboard.press('Escape')
    await expect(capture).toBeHidden()
  }
  expect(await status(page.request, id)).toBe('pending')
})
