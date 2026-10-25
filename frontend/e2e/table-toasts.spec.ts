// ACT-SURFACES: web.table
// One toast per row: edits to one row within a few seconds share one
// "Tue row changed · Undo", named after the row's first column, and its Undo puts back all of them
// (row.edit). Another row gets its own toast.
import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from './fixtures'

interface Table { id: string; columns: { id: string; name: string }[] }
interface Row { id: string; cells: Record<string, unknown> }

async function mkTable(request: APIRequestContext): Promise<Table> {
  const r = await request.post('/api/v1/tables', { data: { name: 'E2E Trip costs', columns: [
    { name: 'Item', type: 'text' }, { name: 'Cost', type: 'number' }, { name: 'Paid', type: 'checkbox' },
  ] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const t = ((await r.json()) as { table: Table }).table
  const c = (n: string) => t.columns.find((x) => x.name === n)!.id
  const a = await request.post(`/api/v1/tables/${t.id}/rows`, { data: { rows: [{ cells: { [c('Item')]: 'Hotel', [c('Cost')]: '200' } }, { cells: { [c('Item')]: 'Train', [c('Cost')]: '40' } }], parse: true } })
  expect(a.ok(), await a.text()).toBeTruthy()
  return t
}
const rows = async (request: APIRequestContext, id: string) => ((await (await request.get(`/api/v1/tables/${id}/rows`)).json()) as { rows: Row[] }).rows
const cell = (page: Page, r: number, c: number) => page.locator(`[data-testid="table-grid"] [data-r="${r}"][data-c="${c}"]`)

test('row.edit: a row edited twice in a row shows one toast, and its Undo puts back both', async ({ page }) => {
  const t = await mkTable(page.request)
  const cost = t.columns.find((x) => x.name === 'Cost')!.id
  const paid = t.columns.find((x) => x.name === 'Paid')!.id
  await page.goto(`/tables/t/${t.id}`)
  await expect(page.getByTestId('table-grid')).toBeVisible()
  await cell(page, 0, 1).click()
  await page.keyboard.type('240')
  await page.keyboard.press('Tab')
  await expect.poll(async () => (await rows(page.request, t.id))[0].cells[cost]).toBe('240')
  await page.keyboard.press('Space')
  await expect.poll(async () => (await rows(page.request, t.id))[0].cells[paid]).toBe(true)
  const toasts = page.locator('.toast').filter({ hasText: 'row changed' })
  await expect(toasts).toHaveCount(1)
  await expect(toasts).toContainText('Hotel row changed')
  await toasts.getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await rows(page.request, t.id))[0].cells[cost]).toBe('200')
  await expect.poll(async () => (await rows(page.request, t.id))[0].cells[paid] ?? false).toBe(false)
})
