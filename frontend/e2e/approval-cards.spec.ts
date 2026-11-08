// ACT-SURFACES: web.reader
// The approval card: a type label and who asked inside the card, the object's own title as the
// heading (two lines, "more" for the rest), the key facts on a line, a short reason with its whole
// text behind ⓘ, and one row of actions: Allow once, Deny, the grants menu (+N) and ⋮ (Ask).
// Consecutive requests of one kind from one agent are grouped, with Allow all and Deny all
// (approval.approve_many, approval.approve, approval.deny). Review shots with APPROVAL_SHOTS.
import path from 'node:path'
import { request as pwRequest, type APIRequestContext, type Page } from '@playwright/test'
import { expect, test } from './fixtures'

const SHOTS = process.env.APPROVAL_SHOTS
const status = async (request: APIRequestContext, id: number) => (await (await request.get(`/api/v1/approvals/${id}`)).json()).status as string

const LONG = 'Research workshop: dry run in the seminar room with the team (check projector, outlets and the HDMI cable before Monday)'

/** Task requests from one agent token ("claude-code"): a token's task text asks first. */
async function taskRequests(request: APIRequestContext, tasks: { summary: string; due?: string }[], name = 'claude-code'): Promise<number[]> {
  const tok = await (await request.post('/api/v1/tokens', { data: { name, scopes: ['read', 'calendar'] } })).json()
  const agent = await pwRequest.newContext({
    baseURL: process.env.E2E_BASE ?? 'http://localhost:5173',
    extraHTTPHeaders: { Authorization: `Bearer ${tok.token as string}` },
  })
  const ids: number[] = []
  for (const t of tasks) {
    const r = await agent.post('/api/v1/tasks', { data: t })
    expect(r.status(), await r.text()).toBe(202)
    const j = await r.json()
    ids.push((j.approval?.id ?? j.id) as number)
  }
  await agent.dispose()
  return ids
}

const inDays = (n: number) => {
  const d = new Date()
  d.setDate(d.getDate() + n)
  return `${d.toISOString().slice(0, 10)}T09:00:00`
}

async function openBell(page: Page) {
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  const approvals = page.getByRole('region', { name: 'Approvals' })
  await expect(approvals.locator('.approval').first()).toBeVisible()
  return approvals
}

async function shots(page: Page, name: string) {
  if (!SHOTS) return
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.waitForTimeout(200)
    await page.screenshot({ path: path.join(SHOTS, `${name}-${scheme}.png`) })
  }
  await page.emulateMedia({ colorScheme: 'light' })
}

test('review shots of the bell with two task requests from one agent', async ({ page, seeded }) => {
  test.skip(!SHOTS, 'APPROVAL_SHOTS not set')
  expect(seeded.email).toBeTruthy()
  await taskRequests(page.request, [{ summary: 'Update the workshop RSVP form', due: inDays(1) }, { summary: LONG, due: inDays(25) }])
  for (const [w, h, where] of [[1280, 900, 'panel'], [376, 800, 'narrow']] as const) {
    await page.setViewportSize({ width: w, height: h })
    await openBell(page)
    await shots(page, `bell-${where}`)
    await page.keyboard.press('Escape')
  }
})

test('approval.approve_many: Allow all on a group of two tasks from one token allows exactly those two', async ({ page, seeded }) => {
  expect(seeded.email).toBeTruthy()
  const ids = await taskRequests(page.request, [{ summary: 'E2E group A', due: inDays(2) }, { summary: 'E2E group B', due: inDays(3) }])
  const approvals = await openBell(page)
  const group = approvals.getByTestId('approval-group')
  await expect(group).toHaveCount(1)
  await expect(group.getByRole('heading', { name: 'claude-code wants to add 2 tasks' })).toBeVisible()
  // Up to three: open, each card listed under the header.
  await expect(group.getByRole('button', { name: 'Hide the requests' })).toHaveAttribute('aria-expanded', 'true')
  await expect(group.locator('.approval')).toHaveCount(2)
  await group.getByRole('button', { name: 'Allow all', exact: true }).click()
  await expect(page.locator('.toast').filter({ hasText: 'Allowed 2 requests.' })).toBeVisible()
  for (const id of ids) expect(await status(page.request, id)).not.toBe('pending')
  // The seeded requests (one per chat) still wait.
  await expect(page.getByTestId('waiting-bell')).toHaveAccessibleName(/2 approvals/)
})

test('approval.deny: Deny all asks in place, then denies exactly the group', async ({ page, seeded }) => {
  expect(seeded.email).toBeTruthy()
  const ids = await taskRequests(page.request, [{ summary: 'E2E deny A' }, { summary: 'E2E deny B' }])
  const approvals = await openBell(page)
  const group = approvals.getByTestId('approval-group')
  await group.getByRole('button', { name: 'Deny all', exact: true }).click()
  const confirm = group.getByRole('group', { name: 'Deny all 2 requests? The agent is told they were denied.' })
  await expect(confirm).toBeVisible()
  // Cancel keeps both waiting.
  await confirm.getByRole('button', { name: 'Cancel' }).click()
  for (const id of ids) expect(await status(page.request, id)).toBe('pending')
  await group.getByRole('button', { name: 'Deny all', exact: true }).click()
  await group.getByRole('group', { name: /Deny all 2 requests/ }).getByRole('button', { name: 'Deny all', exact: true }).click()
  await expect(page.locator('.toast').filter({ hasText: 'Denied 2 requests.' })).toBeVisible()
  for (const id of ids) expect(await status(page.request, id)).toBe('denied')
})

test('a card: the grants menu and ⋮ are menus, the heading has more, ⓘ gives the whole reason', async ({ page, seeded }) => {
  expect(seeded.email).toBeTruthy()
  const [, long] = await taskRequests(page.request, [{ summary: 'E2E short one', due: inDays(1) }, { summary: LONG, due: inDays(25) }])
  await page.setViewportSize({ width: 376, height: 800 })
  const approvals = await openBell(page)
  const card = approvals.locator(`[data-approval-id="${long}"]`)
  // The top line: the kind and who asked, inside the card.
  await expect(card.locator('.ap-kind')).toHaveText('New task')
  await expect(card.getByTestId('approval-who')).toContainText('claude-code')
  // The heading is the task's own title, two lines at most until "more".
  const heading = card.locator('.ap-heading')
  await expect(heading).toHaveText(LONG)
  const lines = async () => heading.evaluate((el) => Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight)))
  expect(await lines()).toBe(2)
  const more = card.getByRole('button', { name: 'more', exact: true })
  await expect(more).toHaveAttribute('aria-expanded', 'false')
  await more.click()
  expect(await lines()).toBeGreaterThan(2)
  await card.getByRole('button', { name: 'Show less' }).click()
  expect(await lines()).toBe(2)
  // The facts on a line.
  await expect(card.getByTestId('approval-facts')).toContainText('Due')
  // Why it asks: one line, the whole text behind ⓘ.
  await expect(card.locator('.ap-reason-short')).toHaveText('Asks first: written through an API token')
  const why = card.getByRole('button', { name: 'Why it asks' })
  await why.click()
  await expect(why).toHaveAttribute('aria-expanded', 'true')
  await expect(card.locator('.ap-reason-full')).toHaveText('Task text written through an API token asks first')
  // One row of actions.
  const row = card.locator('.ap-buttons')
  const tops = await row.locator(':scope > button').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))
  expect(new Set(tops).size).toBe(1)
  // The grants menu, by keyboard: its count, then both grants with what they also approve.
  const grants = row.getByRole('button', { name: 'More ways to allow (also approves 1 other waiting)' })
  await expect(grants).toHaveText('+1')
  await grants.focus()
  await page.keyboard.press('Enter')
  const menu = page.getByRole('menu', { name: 'More ways to allow' })
  await expect(menu.getByRole('menuitem', { name: 'Allow for 12 hours (also approves 1 other waiting)' })).toBeFocused()
  await expect(menu.getByRole('menuitem', { name: 'Always allow… (also approves 1 other waiting)' })).toBeVisible()
  // Escape closes the menu, not the bell, and gives the focus back.
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: 'Waiting for you' })).toBeVisible()
  await expect(grants).toBeFocused()
  // Always allow… asks in place, as before.
  await grants.click()
  await page.getByRole('menuitem', { name: /Always allow…/ }).click()
  await expect(card.getByRole('button', { name: /Create rule and allow/ })).toBeVisible()
  await card.getByRole('button', { name: 'Back' }).click()
  // ⋮ holds Ask Ambrose; asking decides nothing.
  await row.getByRole('button', { name: 'More choices' }).click()
  await page.getByRole('menu', { name: 'More choices' }).getByRole('menuitem', { name: 'Ask Ambrose' }).click()
  await expect(page.getByTestId('ask-capture')).toBeVisible()
  expect(await status(page.request, long)).toBe('pending')
  if (SHOTS) {
    await page.keyboard.press('Escape')
    const approvals2 = await openBell(page)
    await approvals2.locator(`[data-approval-id="${long}"] .ap-grants`).click()
    await shots(page, 'grants-menu-narrow')
  }
})

test('a long requester name and a right-to-left title keep the card in its width, actions on one row', async ({ page, seeded }) => {
  expect(seeded.email).toBeTruthy()
  const rtl = 'تحضير ورشة العمل: تجربة في قاعة الندوات مع الفريق (التحقق من جهاز العرض والمقابس وكابل HDMI)'
  const [id] = await taskRequests(page.request, [{ summary: rtl, due: inDays(4) }], 'research-assistant-agent-for-the-workshop-team-and-more')
  await page.setViewportSize({ width: 376, height: 800 })
  const approvals = await openBell(page)
  const card = approvals.locator(`[data-approval-id="${id}"]`)
  await expect(card.locator('.ap-heading')).toHaveText(rtl)
  // Nothing sticks out of the card, and the type label keeps its place beside the long name.
  const box = await card.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }))
  expect(box.sw).toBeLessThanOrEqual(box.cw)
  await expect(card.locator('.ap-kind')).toBeVisible()
  const kindW = await card.locator('.ap-kind').evaluate((el) => el.getBoundingClientRect().width)
  expect(kindW).toBeGreaterThan(30)
  const tops = await card.locator('.ap-buttons > button').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))
  expect(new Set(tops).size).toBe(1)
  await shots(page, 'long-names-narrow')
})
