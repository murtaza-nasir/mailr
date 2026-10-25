// ACT-SURFACES: web.editor web.settings
// Templates: Settings → Templates (web.settings) and the
// compose editor (web.editor). Each test checks the result through the API.
// Covers: template.create, template.edit, template.delete, template.insert.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

interface ApiTemplate { id: number; name: string; shortcut: string; subject: string; body_html: string }

async function templates(page: Page): Promise<ApiTemplate[]> {
  return (await (await page.request.get('/api/v1/templates')).json()).items as ApiTemplate[]
}

async function seedTemplate(page: Page) {
  const r = await page.request.post('/api/v1/templates', {
    data: { name: 'E2E Thanks', shortcut: 'thx', subject: 'Thanks, {first_name}', body_html: '<p>Hi {first_name},</p><p>Thanks for {unknown}.</p>' },
  })
  expect(r.ok(), await r.text()).toBeTruthy()
}

async function compose(page: Page) {
  await page.goto('/mail/inbox')
  await page.locator('.compose-btn').click()
  const win = page.locator('.compose-win')
  await expect(win).toBeVisible()
  return win
}

test('template.create, template.edit and template.delete in Settings → Templates', async ({ page }) => {
  await page.goto('/settings/templates')
  await page.getByRole('button', { name: 'New template' }).click()
  const dlg = page.getByRole('dialog', { name: 'New template' })
  await dlg.getByLabel('Name').fill('E2E Intro')
  await dlg.getByLabel('Shortcut (optional)').fill(';intro')
  await dlg.getByLabel(/^Subject/).fill('Hello {first_name}')
  await dlg.locator('[contenteditable="true"]').click()
  await page.keyboard.type('Nice to meet you, {first_name}.')
  await dlg.getByRole('button', { name: 'Save' }).click()
  await expect(dlg).toHaveCount(0)
  await expect.poll(async () => (await templates(page)).map((t) => [t.name, t.shortcut, t.subject])).toEqual([['E2E Intro', 'intro', 'Hello {first_name}']])
  expect((await templates(page))[0].body_html).toContain('Nice to meet you, {first_name}.')
  await expect(page.getByRole('table', { name: 'Templates' })).toContainText(';intro')

  await page.getByRole('button', { name: 'Edit E2E Intro' }).click()
  const edit = page.getByRole('dialog', { name: 'Edit template' })
  await edit.getByLabel('Name').fill('E2E Introduction')
  await edit.getByRole('button', { name: 'Save' }).click()
  await expect.poll(async () => (await templates(page)).map((t) => t.name)).toEqual(['E2E Introduction'])

  await page.getByRole('button', { name: 'Delete E2E Introduction' }).click()
  await page.getByRole('dialog', { name: 'Delete template' }).getByRole('button', { name: 'Delete' }).click()
  await expect.poll(async () => (await templates(page)).length).toBe(0)
  await expect(page.getByText('No templates yet.')).toBeVisible()
})

test('template.insert: ;shortcut and Space fills the template for the first recipient', async ({ page }) => {
  await seedTemplate(page)
  // The shortcut works once compose has its templates; under load the list could still be on its way.
  const loaded = page.waitForResponse((r) => /\/api\/v1\/templates(\?|$)/.test(r.url()) && r.request().method() === 'GET')
  const win = await compose(page)
  await loaded
  await win.getByLabel('To recipients').fill('pat@example.net')
  await page.keyboard.press('Enter')
  await win.locator('[contenteditable="true"]').first().click()
  await page.keyboard.type('Hello ;thx')
  await page.keyboard.press('Space')
  const body = win.locator('[contenteditable="true"]').first()
  // No name for the address: {first_name} stays as typed; so does the unknown variable.
  await expect(body).toContainText('Hi {first_name},')
  await expect(body).toContainText('Thanks for {unknown}.')
  await expect(body).not.toContainText(';thx')
  await expect(win.getByLabel('Subject')).toHaveValue('Thanks, {first_name}')

  // With a named recipient, Tab also triggers, and an existing subject is kept.
  await win.getByRole('button', { name: 'Remove pat@example.net' }).click()
  await win.getByLabel('To recipients').fill('Pat Doe <pat@example.net>')
  await page.keyboard.press('Enter')
  await body.click()
  await page.keyboard.press('Control+End')
  await page.keyboard.type(' ;thx')
  await page.keyboard.press('Tab')
  await expect(body).toContainText('Hi Pat,')
  await expect(win.getByLabel('Subject')).toHaveValue('Thanks, {first_name}')

  // A semicolon inside text is not a trigger.
  await page.keyboard.type('a;thx ')
  await expect(body).toContainText('a;thx')
})

test('template.insert from the Templates menu at the cursor', async ({ page }) => {
  await seedTemplate(page)
  const win = await compose(page)
  await win.getByLabel('Subject').fill('My own subject')
  await win.locator('[contenteditable="true"]').first().click()
  await page.keyboard.type('Start here.')
  await win.getByRole('button', { name: 'Templates' }).click()
  await page.getByRole('menuitem', { name: /E2E Thanks/ }).click()
  const body = win.locator('[contenteditable="true"]').first()
  await expect(body).toContainText('Start here.')
  await expect(body).toContainText('Hi {first_name},')
  await expect(win.getByLabel('Subject')).toHaveValue('My own subject')
  await expect(win.getByText('Draft saved')).toBeVisible()
})
