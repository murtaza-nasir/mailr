// The row kit: in every theme the list rows honour Importance markers (Settings → Inbox),
// show the conversation's labels, and mark a muted conversation. Before, only Classic did.
import { conv, expect, test } from './fixtures'
import { THEMES } from './matrix'

for (const theme of THEMES) {
  test(`${theme}: importance marker setting, label chip and muted icon`, async ({ page, seeded }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    const labelId = Object.values(seeded.labels)[0]
    const labelName = Object.keys(seeded.labels)[0]
    const labelled = await conv(page.request, 'E2E Label me')
    const muted = await conv(page.request, 'E2E Mute me')
    expect((await page.request.post('/api/v1/conversations/actions', { data: { ids: [labelled.id], action: 'add_labels', label_ids: [labelId] } })).ok()).toBeTruthy()
    expect((await page.request.post('/api/v1/conversations/actions', { data: { ids: [muted.id], action: 'mute' } })).ok()).toBeTruthy()

    const row = (s: string) => page.locator(`[data-mail-list] [data-id]`).filter({ hasText: s }).first()

    // Markers off: no marker in any row.
    expect((await page.request.patch('/api/v1/me/settings', { data: { importance_markers: false } })).ok()).toBeTruthy()
    await page.goto('/mail/all')
    await expect(row('E2E Read me')).toBeVisible()
    await expect(page.locator('[data-mail-list] button.importance')).toHaveCount(0)
    await expect(row('E2E Label me').locator('.label-chip').filter({ hasText: labelName.split('/').pop()! })).toBeVisible()
    await expect(row('E2E Mute me').getByLabel('Muted')).toHaveCount(1)

    // Markers on: every row has one, and it marks the conversation important.
    expect((await page.request.patch('/api/v1/me/settings', { data: { importance_markers: true } })).ok()).toBeTruthy()
    await page.reload()
    const marker = row('E2E Read me').locator('button.importance')
    await expect(marker).toHaveCount(1)
    await expect(marker).toHaveAttribute('aria-pressed', 'false')
    await row('E2E Read me').hover()
    await marker.click()
    await expect(marker).toHaveAttribute('aria-pressed', 'true')
    await expect.poll(async () => (await conv(page.request, 'E2E Read me')).important).toBe(true)
  })
}

test('P7: list and reader show a nested label by its last part, with the path as the tooltip', async ({ page }) => {
  const r = await page.request.post('/api/v1/labels', { data: { name: 'Clients/Lumen Coffee' } })
  expect(r.ok()).toBeTruthy()
  const labelId = (await r.json()).id
  const c = await conv(page.request, 'E2E Label me')
  expect((await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'add_labels', label_ids: [labelId] } })).ok()).toBeTruthy()
  await page.goto('/mail/all')
  const row = page.locator('[data-mail-list] [data-id]').filter({ hasText: 'E2E Label me' }).first()
  await expect(row.locator('.label-chip', { hasText: 'Lumen Coffee' })).toHaveAttribute('title', 'Clients/Lumen Coffee')
  await row.click()
  const chip = page.locator('.conv-labels .label-chip, .conv-subject .label-chip').filter({ hasText: 'Lumen Coffee' }).first()
  await expect(chip).toHaveText(/^Lumen Coffee/)
  await expect(chip).toHaveAttribute('title', 'Clients/Lumen Coffee')
})
