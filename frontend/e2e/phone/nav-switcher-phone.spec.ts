// The phone bar in every theme: Mail, Calendar, Tasks,
// Notes and Ambrose, Ambrose far right, nothing past five, no More. The mail folders are in the
// theme's drawer, Settings in the account menu, Ambrose's top-bar button gives way to the bar, and
// the Compose button shrinks to its icon while the list scrolls.
import { expect, test } from '../fixtures'
import { THEMES } from '../matrix'
import { rows } from './phone'

for (const theme of THEMES) {
  test(`${theme}: the five-app bar, Ambrose at the right, Settings in the account menu`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await page.goto('/mail/inbox')
    await expect(rows(page).first()).toBeVisible()
    const tabs = page.getByTestId('phone-tabs')
    expect(await tabs.locator('.tab').evaluateAll((els) => els.map((e) => e.getAttribute('data-tab')))).toEqual(['mail', 'calendar', 'tasks', 'notes', 'assistant'])
    expect(await tabs.locator('.tab-label').allTextContents()).toEqual(['Mail', 'Calendar', 'Tasks', 'Notes', 'Ambrose'])
    // Ambrose is the right-most tab, and the bar holds no More.
    const last = (await tabs.locator('.tab').last().boundingBox())!
    expect(Math.round(last.x + last.width)).toBe(page.viewportSize()!.width)
    await expect(page.getByTestId('more-sheet')).toHaveCount(0)
    await expect(tabs.getByText('More')).toHaveCount(0)
    // The bar is Ambrose's place on a phone: the top bar's button hides.
    await expect(page.locator('[data-ambrose]:visible')).toHaveCount(0)

    // Settings is in the account menu.
    await page.getByRole('button', { name: /^Account:/ }).click()
    await page.getByRole('menuitem', { name: 'Settings' }).click()
    await expect(page).toHaveURL(/\/settings(\/you)?$/)
  })
}

test('over a list with rows the Compose button is its round icon, and the last row clears it', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  const fab = page.locator('.compose-fab')
  // No label over rows, at rest too: it covered row text and chips until the list scrolled.
  await expect(fab).not.toHaveClass(/roomy/)
  await expect(fab.locator('.fab-label')).toBeHidden()
  expect((await fab.boundingBox())!.width).toBeLessThanOrEqual(60)
  await expect(fab).toHaveAccessibleName('Compose')
  // At the end of the list, the last row ends above the button.
  const list = page.locator('.list-scroll').first()
  await list.evaluate((el) => el.scrollTo({ top: el.scrollHeight }))
  const lastRow = rows(page).last()
  await expect(lastRow).toBeVisible()
  const r = (await lastRow.boundingBox())!
  const f = (await fab.boundingBox())!
  expect(r.y + r.height).toBeLessThanOrEqual(f.y + 1)
  // An empty list has room for the label.
  await page.goto('/mail/scheduled')
  await expect(fab).toHaveClass(/roomy/)
  await expect(fab).toContainText('Compose')
})
