// Choice orders in Settings → Assistant → Standing orders: one question, several named choices,
// each with its own actions; the decision model picks one (or none). The pick itself is covered
// by backend/tests/test_assistant_orders.py.
import type { APIRequestContext } from '@playwright/test'
import { expect, test } from './fixtures'

interface ApiOrder { id: number; name: string; question: string; choices: { key: string; name: string; description: string; actions: Record<string, unknown> }[] }
const orders = async (request: APIRequestContext) => (await (await request.get('/api/v1/assistant/orders')).json()).items as ApiOrder[]

test('create a choice order with labelled choices, then see it listed', async ({ page, seeded }) => {
  await page.goto('/settings/assistant?section=orders')
  await page.getByRole('button', { name: 'New order' }).click()
  const editor = page.getByRole('dialog', { name: 'New standing order' })
  await editor.getByLabel('Name').fill('E2E Projects')
  await editor.getByRole('radio', { name: /Pick one of several/ }).check()
  await editor.getByLabel(/picks one, for/).fill('Which project is this about?')
  await editor.getByLabel('Choice 1 name').fill('Grant renewal')
  await editor.getByLabel('Choice 1 description').fill('the renewal application and budget')
  await editor.getByLabel('Choice 1: add a label').selectOption({ label: 'E2E Label' })
  await editor.getByLabel('Choice 2 name').fill('Teaching')
  await editor.getByLabel('Choice 2 description').fill('courses and students')
  await editor.getByLabel('Choice 2: add a label').selectOption({ label: 'E2E Other' })
  await editor.getByRole('button', { name: 'Add choice' }).click()
  await editor.getByLabel('Choice 3 name').fill('Speakr')
  await editor.getByRole('button', { name: 'Remove choice 3' }).click()
  await editor.getByRole('button', { name: 'Save' }).click()

  await expect.poll(async () => (await orders(page.request)).find((o) => o.name === 'E2E Projects')?.choices.length).toBe(2)
  const o = (await orders(page.request)).find((x) => x.name === 'E2E Projects')!
  expect(o.question).toBe('Which project is this about?')
  expect(o.choices.map((c) => [c.key, c.description, c.actions])).toEqual([
    ['grant-renewal', 'the renewal application and budget', { add_labels: [seeded.labels['E2E Label']] }],
    ['teaching', 'courses and students', { add_labels: [seeded.labels['E2E Other']] }],
  ])
  const row = page.locator('li.as-order').filter({ hasText: 'E2E Projects' })
  await expect(row).toContainText('Which project is this about?')
  await expect(row.locator('.as-choice-list')).toContainText('Grant renewal → label E2E Label')
})
