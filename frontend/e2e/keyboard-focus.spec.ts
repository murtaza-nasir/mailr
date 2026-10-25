// Keyboard use with the focus on a control.
// - Enter on a focused link or button activates it, in Mail and in Settings; the shortcut
//     handler used to cancel the key first (Enter is "open" in mail).
// - the mail keys work only in Mail; # in Settings does nothing to the mail selected in the
//     hidden inbox.
// - in Tasks, Tab and Space on a control in the task pane move focus and press the control; they
//     no longer indent or complete the open task.
// Covers: web.shortcuts (scopes), web.focus.
import type { APIRequestContext, Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'

const rows = (page: Page) => page.locator('[data-mail-list] [role="row"]')

async function trashCount(request: APIRequestContext): Promise<number> {
  const r = await request.get('/api/v1/conversations', { params: { view: 'trash' } })
  expect(r.ok()).toBeTruthy()
  return ((await r.json()).items as unknown[]).length
}

test('Mail: Enter on a focused sidebar link follows it', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  const starred = page.getByRole('link', { name: /^Starred/ }).first()
  await starred.focus()
  await expect(starred).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/mail\/starred$/)
})

test('Mail: Enter on a focused button presses it', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  const compose = page.getByRole('button', { name: /^Compose/ }).first()
  await compose.focus()
  await page.keyboard.press('Enter')
  const editor = page.getByRole('dialog', { name: /New message/i })
  await expect(editor).toHaveCount(1)
})

test('Mail: Enter on a focused row still opens it, and j/k still move', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await page.keyboard.press('j')
  await expect(rows(page).first()).toHaveAttribute('tabindex', '0')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/mail\/inbox\/c[0-9A-Za-z]+/)
})

test('Settings: Enter on a focused tab link and on a focused button', async ({ page }) => {
  await page.goto('/settings/mail/reading')
  const labels = page.getByRole('navigation', { name: 'Mail pages' }).getByRole('link', { name: 'Labels', exact: true })
  await labels.focus()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/settings\/mail\/labels$/)

  await page.goto('/settings/mail/inbox')
  await page.getByLabel('Add VIP sender').fill('vip-e2e@example.org')
  const add = page.getByRole('button', { name: 'Add', exact: true })
  await add.focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('.vip-chip').filter({ hasText: 'vip-e2e@example.org' })).toBeVisible()
  // Space too.
  await page.getByLabel('Add VIP sender').fill('vip-two@example.org')
  await add.focus()
  await page.keyboard.press('Space')
  await expect(page.locator('.vip-chip').filter({ hasText: 'vip-two@example.org' })).toBeVisible()
})

test('Settings: # (and e, !) do nothing to the mail selected in the hidden inbox', async ({ page }) => {
  const sent: string[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().includes('/conversations/actions')) sent.push(r.postData() ?? '')
  })
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  const before = await trashCount(page.request)
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await page.keyboard.press('j')
  await page.keyboard.press('x')
  await expect(rows(page).first()).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('Control+,')
  await expect(page).toHaveURL(/\/settings$/)
  await page.locator('.settings-head h1').click()
  for (const key of ['#', 'e', '!', 'm', 'Shift+I']) await page.keyboard.press(key)
  // The help in Settings lists only the keys that work everywhere.
  await page.keyboard.press('?')
  const help = page.getByRole('dialog', { name: 'Keyboard shortcuts' })
  await expect(help).toBeVisible()
  await expect(help.getByText('Go to Inbox')).toBeVisible()
  await expect(help.getByText('Archive', { exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.getByText('Conversation moved to Trash')).toHaveCount(0)
  expect(sent).toEqual([])
  expect(await trashCount(page.request)).toBe(before)
  // Back in the inbox the mail keys work again.
  await page.keyboard.press('g')
  await page.keyboard.press('i')
  await expect(page).toHaveURL(/\/mail\/inbox$/)
})

test('Tasks: Tab and Space on controls in the task pane move focus and press them', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const today = iso(new Date())
  for (const summary of ['E2E Keys first', 'E2E Keys second']) {
    const r = await page.request.post('/api/v1/tasks', { data: { summary, due: today } })
    expect(r.ok(), await r.text()).toBeTruthy()
  }
  const task = async (summary: string) => {
    const j = await (await page.request.get('/api/v1/tasks', { params: { status: 'all' } })).json()
    return ((Array.isArray(j) ? j : j.items) as { summary: string; parent?: string | null; status: string; priority?: number }[])
      .find((t) => t.summary === summary)!
  }
  await page.goto('/tasks/today')
  const row = page.getByTestId('task-row').filter({ hasText: 'E2E Keys second' })
  await expect(row).toBeVisible()
  await row.locator('.tk-title').click()
  await page.keyboard.press('Enter')
  const pane = page.locator('.tk-pane')
  await expect(pane).toBeVisible()

  // Tab from the due-date button moves the focus; the task stays where it is.
  const due = pane.getByTestId('pane-due')
  await due.focus()
  await page.keyboard.press('Tab')
  await expect(due).not.toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(due).toBeFocused()
  // Space on a priority button sets the priority; the task is not completed.
  const high = pane.getByRole('radio').last()
  await high.focus()
  await page.keyboard.press('Space')
  await expect(high).toHaveAttribute('aria-checked', 'true')
  // The priority saves through the server's calendar, which can take seconds on a busy machine.
  await expect.poll(async () => (await task('E2E Keys second')).priority ?? 0).toBeGreaterThan(0)
  const t = await task('E2E Keys second')
  expect(t.parent ?? null).toBeNull()
  expect(t.status).not.toBe('completed')

  // From the list itself the keys still change the task: Space completes the focused one.
  await page.keyboard.press('Escape')
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await row.locator('.tk-title').click()
  await page.keyboard.press('Space')
  await expect.poll(async () => (await task('E2E Keys second')).status).toBe('completed')
})
