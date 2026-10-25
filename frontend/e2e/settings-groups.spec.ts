// ACT-SURFACES: web.settings web.palette
// Settings grouped by task (features/settings/groups.ts): old addresses redirect, every
// change saves at once with Undo and no Save button, a text field saves when you leave it
// and an edit survives switching pages, one time zone under You → Region that Ambrose's profile
// shows, and the palette finds a setting ("Settings: …").
import { expect, test } from './fixtures'

const me = async (request: import('@playwright/test').APIRequestContext) => (await (await request.get('/api/v1/me')).json()) as { user: { name: string }; settings: Record<string, unknown> }

test('old settings addresses open their new place', async ({ page }) => {
  for (const [from, to] of [
    ['/settings/general', /\/settings\/mail\/reading$/],
    ['/settings/inbox', /\/settings\/mail\/inbox$/],
    ['/settings/templates', /\/settings\/mail\/writing#templates$/],
    ['/settings/assistant?section=models', /\/settings\/ambrose\/models$/],
    ['/settings/filters?new=from%3Ax%40example.org', /\/settings\/mail\/filters/],
    ['/settings/agents#approvals', /\/settings\/agents#approvals$/],
  ] as const) {
    await page.goto(from)
    await expect(page).toHaveURL(to)
  }
  await expect(page.getByRole('heading', { name: 'Approvals' })).toBeVisible()
  const groups = page.getByRole('navigation', { name: 'Settings groups' })
  for (const g of ['You', 'Look', 'Mail', 'Calendar & tasks', 'Notes & tables', 'Notifications', 'Ambrose']) await expect(groups.getByRole('link', { name: g, exact: true })).toBeVisible()
})

test('a choice saves at once and Undo puts it back; no page has a Save button (settings.undo_change)', async ({ page }) => {
  await page.goto('/settings/mail/writing')
  await page.getByRole('radio', { name: 'Reply all' }).click()
  await expect.poll(async () => (await me(page.request)).settings.default_reply).toBe('reply_all')
  const toast = page.locator('.toast', { hasText: 'Saved.' })
  await expect(toast).toBeVisible()
  await toast.getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await me(page.request)).settings.default_reply).toBe('reply')
  await expect(page.getByRole('radio', { name: 'Reply', exact: true })).toBeChecked()
  for (const p of ['/settings/mail/reading', '/settings/mail/writing', '/settings/mail/inbox', '/settings/mail/spam', '/settings/ambrose/about', '/settings/you/region']) {
    await page.goto(p)
    await expect(page.locator('.set-anchor').first()).toBeVisible()
    await expect(page.getByRole('button', { name: /^Save( changes)?$/i })).toHaveCount(0)
  }
})

test('a text field saves when you leave it, and switching pages keeps the edit', async ({ page }) => {
  await page.goto('/settings/mail/writing')
  await page.getByLabel('Away message subject').fill('Out this week')
  // Straight to another page, without leaving the field first.
  await page.getByRole('navigation', { name: 'Mail pages' }).getByRole('link', { name: 'Inbox', exact: true }).click()
  await expect.poll(async () => ((await me(page.request)).settings.vacation as { subject?: string } | undefined)?.subject).toBe('Out this week')
  await page.goto('/settings/you')
  const name = page.getByLabel('Your name')
  await name.fill('Dana Example')
  await name.blur()
  await expect.poll(async () => (await me(page.request)).user.name).toBe('Dana Example')
})

test('one time zone: Region sets it, and Ambrose → About you shows the same one (settings.region)', async ({ page }) => {
  await page.goto('/settings/you/region')
  await page.getByLabel('Time zone').selectOption('Europe/Lisbon')
  await expect.poll(async () => (await me(page.request)).settings.timezone).toBe('Europe/Lisbon')
  expect((await (await page.request.get('/api/v1/assistant/profile')).json()).timezone).toBe('Europe/Lisbon')
  await page.goto('/settings/ambrose/about')
  await expect(page.getByTestId('profile-zone')).toContainText('Europe/Lisbon')
  await page.getByTestId('profile-zone').getByRole('link', { name: 'You → Region' }).click()
  await expect(page).toHaveURL(/\/settings\/you\/region$/)
  // Currency and date order sit here too.
  await page.getByLabel('Currency').selectOption('EUR')
  await expect.poll(async () => (await me(page.request)).settings.currency).toBe('EUR')
})

test('spam: a plain level, with the numbers folded away', async ({ page }) => {
  await page.goto('/settings/mail/spam')
  await expect(page.getByRole('radio', { name: /^Normal/ })).toBeChecked()
  await page.getByRole('radio', { name: /^Strict/ }).click()
  await expect.poll(async () => (await me(page.request)).settings.spam_junk_score).toBe(10)
  await expect(page.getByLabel('Classifier score threshold')).toBeHidden()
  await page.getByText('Fine-tune the numbers').click()
  await expect(page.getByLabel('Classifier score threshold')).toHaveValue('10')
})

test('the palette finds a setting by its name (settings.find, web.palette)', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(page.getByTestId('waiting-bell')).toBeVisible()
  await page.keyboard.press('Control+k')
  const input = page.getByRole('combobox', { name: 'Command' })
  await input.fill('time zone')
  const item = page.getByRole('option', { name: /Settings: Region/ })
  await expect(item).toBeVisible()
  await item.click()
  await expect(page).toHaveURL(/\/settings\/you\/region#region$/)
  // Before you type, no settings entries crowd the list.
  await expect(page.locator('.set-anchor').first()).toBeVisible()
  await page.keyboard.press('Control+k')
  await expect(input).toBeVisible()
  await expect(page.getByRole('option', { name: /^Settings: / })).toHaveCount(0)
})

test('a renamed assistant names its settings group, its pages and its button (About you → name)', async ({ page }) => {
  const put = (name: string) => page.request.put('/api/v1/assistant/profile', { data: { options: { assistant_name: name } } })
  expect((await put('Jeeves')).ok()).toBe(true)
  try {
    await page.goto('/settings/ambrose/about')
    const groups = page.getByRole('navigation', { name: 'Settings groups' })
    await expect(groups.getByRole('link', { name: 'Jeeves', exact: true })).toBeVisible()
    await expect(groups.getByRole('link', { name: 'Ambrose', exact: true })).toHaveCount(0)
    await expect(page.getByRole('navigation', { name: 'Jeeves pages' })).toBeVisible()
    await expect(page.locator('button[data-ambrose]').first()).toHaveAccessibleName('Jeeves')
    await expect(page).toHaveURL(/\/settings\/ambrose\/about$/) // the address keeps its id
  } finally {
    await put('Ambrose')
  }
})
