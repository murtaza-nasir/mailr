// The ⋮ menus on a phone, so a long menu never runs off the
// screen: bottom sheets in groups, the common actions first, then Create from
// email, Organise and More, the same grouping as the Android app. The sheet stays on screen and
// scrolls, so its last row is reachable. The screenshots are attached.
import { expect, REVIEW_SHOTS, test, type Page } from '../fixtures'
import { row, settled } from './phone'

async function sheetOnScreen(page: Page) {
  const sheet = page.locator('.popover.as-sheet')
  await expect(sheet).toBeVisible()
  await settled(sheet)
  const b = (await sheet.boundingBox())!
  const vh = page.viewportSize()!.height
  expect(b.y).toBeGreaterThanOrEqual(0)
  expect(b.y + b.height).toBeLessThanOrEqual(vh + 1)
  return sheet
}

const headers = (sheet: ReturnType<Page['locator']>) =>
  sheet.locator('.menu-header').evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => e.textContent?.trim()))

test("the reader's ⋮: a grouped sheet that fits the screen", async ({ page }, info) => {
  await page.goto('/mail/inbox')
  await row(page, 'E2E Read me').click()
  await expect(page.locator('.conv-view').getByText('E2E Read me').first()).toBeVisible()
  await page.locator('.conv-view-head').getByRole('button', { name: 'More', exact: true }).click()
  const sheet = await sheetOnScreen(page)
  expect(await headers(sheet)).toEqual(['Organise', 'More'])
  const items = await sheet.getByRole('menuitem').evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => e.textContent?.trim() ?? ''))
  // The common actions first; Create from email before Organize; Mute and Report spam under More.
  expect(items[0]).toMatch(/^Ask /)
  const at = (re: RegExp) => items.findIndex((t) => re.test(t))
  expect(at(/Create from email/)).toBeLessThan(at(/Category/))
  expect(at(/Category/)).toBeLessThan(at(/^Mute/))
  expect(at(/^Mute/)).toBeLessThan(at(/Report spam/))
  const shot = info.outputPath('reader-menu.png')
  await page.screenshot({ path: shot })
  if (REVIEW_SHOTS) await info.attach('reader ⋮', { path: shot, contentType: 'image/png' })
  // The last row is reachable and works.
  const last = sheet.getByRole('menuitem', { name: /Expand all|Collapse all/ })
  await last.scrollIntoViewIfNeeded()
  await expect(last).toBeInViewport()
})

test("the selection's ⋮: a grouped sheet", async ({ page }, info) => {
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Read me')).toBeVisible()
  await page.getByRole('checkbox', { name: /Select all/ }).click()
  await page.getByRole('button', { name: 'More', exact: true }).last().click()
  const sheet = await sheetOnScreen(page)
  expect(await headers(sheet)).toEqual(['Organise', 'More'])
  const shot = info.outputPath('selection-menu.png')
  await page.screenshot({ path: shot })
  if (REVIEW_SHOTS) await info.attach('selection ⋮', { path: shot, contentType: 'image/png' })
  await sheet.getByRole('menuitem', { name: 'Mute' }).click()
  await expect(sheet).toHaveCount(0)
})
