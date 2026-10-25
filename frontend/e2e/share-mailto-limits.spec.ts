// ACT-SURFACES: web.note web.editor
// Sharing and mailto: limits, against the real server: the share dialog finds people outside
// the viewer's groups only by a typed search (ACT note.share), and an email link fills at most
// 50 recipients whose chips show their address on hover (ACT compose.deeplink).
import { accessTest, expect, signIn, test } from './fixtures'

accessTest('note.share: the people dialog lists nobody outside your groups until you search', async ({ page, access }) => {
  await signIn(page, access.admin)
  const r = await page.request.post('/api/v1/notes', { data: { kind: 'checklist', title: 'E2E Directory', lines: 'a' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const id = ((await r.json()) as { note: { id: string } }).note.id
  await page.goto(`/notes/n/${id}`)
  await page.getByTestId('note-editor').getByRole('button', { name: 'Share', exact: true }).click()
  const dialog = page.getByTestId('note-share-dialog')
  await expect(dialog).toBeVisible()
  const cands = dialog.getByTestId('share-candidate')
  // The admin shares no group with the family member: they are not listed.
  await expect(dialog.getByTestId('share-search-hint')).toBeVisible()
  await expect(cands.filter({ hasText: access.family.email })).toHaveCount(0)
  await dialog.getByTestId('share-search').fill(access.family.email.slice(0, 2))
  await expect(cands.filter({ hasText: access.family.email })).toHaveCount(0)
  await dialog.getByTestId('share-search').fill(access.family.email)
  await expect(cands.filter({ hasText: access.family.email })).toHaveCount(1)
})

test('compose.deeplink: a link fills at most 50 recipients, and a chip shows its address on hover', async ({ page }) => {
  await page.goto(`/compose?to=${encodeURIComponent('"Jane Smith" <jane.s@example.org>')}`)
  const win = page.locator('.compose-win').last()
  const jane = win.locator('.recip-chip[data-email="jane.s@example.org"]')
  await expect(jane).toContainText('Jane Smith')
  await jane.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Jane Smith <jane.s@example.org>')
  const many = Array.from({ length: 70 }, (_, i) => `u${i}@example.com`).join(',')
  await page.goto(`/compose?to=${encodeURIComponent(many)}`)
  await expect(page.locator('.compose-win').last().locator('.recip-chip')).toHaveCount(50)
})
