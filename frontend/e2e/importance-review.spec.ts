// ACT-SURFACES: web.settings web.palette
// mail.review_importance on web.settings and web.palette: the review queue in Settings →
// Assistant → Review importance. The list is stubbed with the seeded user's real messages (the
// model's judgments need the worker; backend/tests/test_importance_review.py covers the list);
// each choice goes to the real server, which records a "user" decision and sets the flag.
import type { Page } from '@playwright/test'
import { conv, expect, test } from './fixtures'

const SUBJECTS = ['E2E Important me', 'E2E Label me', 'E2E Move me']

interface Row { message_id: number; conversation_id: number; subject: string }

async function rows(page: Page): Promise<Row[]> {
  const out: Row[] = []
  for (const s of SUBJECTS) {
    const c = await conv(page.request, s)
    const d = await (await page.request.get(`/api/v1/conversations/${c.id}`)).json()
    out.push({ message_id: d.messages[0].id, conversation_id: c.id, subject: s })
  }
  return out
}

/** The stub answers with the rows not yet judged (as the server leaves them out). */
async function stubList(page: Page, list: Row[], seen: { since: string[]; judged: Set<number> }) {
  await page.route(/\/api\/v1\/intel\/importance\/review/, (r) => {
    seen.since.push(new URL(r.request().url()).searchParams.get('since') ?? '')
    return r.fulfill({ json: {
      since: new Date().toISOString(), threshold: 0.5, margin: 0.15,
      items: list.filter((x) => !seen.judged.has(x.message_id)).map((x, i) => ({
        ...x, from: { email: 'alice@example.org', name: 'Alice' }, snippet: `About ${x.subject}`, date: new Date().toISOString(),
        important: i === 0, judged_important: i === 0, score: i === 0 ? 0.71 : 0.42, borderline: i !== 0,
        reasons: i === 0 ? ['You often write to Alice', 'Sent directly to you'] : ['Sent to many people'], source: 'heuristic',
      })),
      status: { learned_from: 0, since_training: 0, retrain_after: 10, min_each: 20, model: null },
    } })
  })
  page.on('request', (req) => {
    const m = req.method() === 'POST' && req.url().match(/\/api\/v1\/messages\/(\d+)\/importance$/)
    if (m) seen.judged.add(Number(m[1]))
  })
}

async function lastImportance(page: Page, mid: number) {
  const d = await (await page.request.get(`/api/v1/messages/${mid}/decisions`)).json()
  return (d.decisions as { kind: string; source: string; value: boolean }[]).find((x) => x.kind === 'importance')
}

test('mail.review_importance: i, n, j/k and Enter work the queue; judged rows drop out', async ({ page }) => {
  const list = await rows(page)
  const seen = { since: [] as string[], judged: new Set<number>() }
  await stubList(page, list, seen)
  await page.goto('/settings/assistant?section=importance')
  const queue = page.getByRole('list', { name: 'Mail to review' })
  await expect(queue.locator('[data-message-id]')).toHaveCount(3)
  await expect(queue.locator('[data-message-id]').first()).toContainText('You often write to Alice')
  await expect(queue.locator('[data-message-id]').first()).toContainText('71%')
  await expect(page.getByLabel('Model status')).toContainText('No learned model yet')

  // j moves to the second row; i marks it important and it drops out.
  await page.keyboard.press('j')
  await expect(queue.locator('[data-message-id]').nth(1)).toHaveAttribute('aria-current', 'true')
  const posted = page.waitForResponse((r) => r.url().endsWith(`/messages/${list[1].message_id}/importance`))
  await page.keyboard.press('i')
  expect((await posted).ok()).toBeTruthy()
  await expect(queue.locator('[data-message-id]')).toHaveCount(2)
  await expect(queue).not.toContainText('E2E Label me')
  await expect(page.getByLabel('Model status')).toContainText('Learned from 1 choice')
  expect(await lastImportance(page, list[1].message_id)).toMatchObject({ source: 'user', value: true })
  expect((await conv(page.request, 'E2E Label me')).important).toBe(true)

  // The cursor is on the row after it (E2E Move me); k goes back to the first, n flips it.
  await expect(queue.locator('[data-message-id]').nth(1)).toHaveAttribute('aria-current', 'true')
  await page.keyboard.press('k')
  const posted2 = page.waitForResponse((r) => r.url().endsWith(`/messages/${list[0].message_id}/importance`))
  await page.keyboard.press('n')
  expect((await posted2).ok()).toBeTruthy()
  await expect(queue.locator('[data-message-id]')).toHaveCount(1)
  expect(await lastImportance(page, list[0].message_id)).toMatchObject({ source: 'user', value: false })
  await expect(page.getByLabel('Model status')).toContainText('Learned from 2 choices')

  // Enter opens the email.
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/mail\/all\//)
  await expect(page.getByRole('heading', { name: 'E2E Move me' })).toBeVisible()
})

test('mail.review_importance: the palette opens the queue; buttons judge and the range widens', async ({ page }) => {
  const list = await rows(page)
  const seen = { since: [] as string[], judged: new Set<number>() }
  await stubList(page, list, seen)
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list]')).toBeVisible()
  await page.keyboard.press('Control+k')
  await page.getByRole('combobox', { name: 'Command' }).fill('Review importance')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/settings\/ambrose\/importance/)
  const queue = page.getByRole('list', { name: 'Mail to review' })
  await expect(queue.locator('[data-message-id]')).toHaveCount(3)

  await page.getByRole('button', { name: 'Not important: E2E Move me' }).click()
  await expect(queue.locator('[data-message-id]')).toHaveCount(2)
  await page.getByRole('button', { name: 'Important: E2E Important me', exact: true }).click()
  await expect(queue.locator('[data-message-id]')).toHaveCount(1)
  // The queue drops a row at once; the decision is written by the request behind it.
  await expect.poll(() => lastImportance(page, list[2].message_id)).toMatchObject({ source: 'user', value: false })
  await expect.poll(() => lastImportance(page, list[0].message_id)).toMatchObject({ source: 'user', value: true })

  // 7 days asks from six days before today's midnight.
  await page.getByRole('group', { name: 'Range' }).getByRole('button', { name: '7 days' }).click()
  await expect.poll(() => seen.since.length).toBeGreaterThan(1)
  const start = new Date(seen.since[seen.since.length - 1])
  const want = new Date(); want.setHours(0, 0, 0, 0); want.setDate(want.getDate() - 6)
  expect(start.getTime()).toBe(want.getTime())
  await expect(queue.locator('[data-message-id]')).toHaveCount(1)
})
