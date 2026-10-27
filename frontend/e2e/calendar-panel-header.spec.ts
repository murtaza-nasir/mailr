// ACT-SURFACES: web.panel
// The calendar panel's day header (rail/CalendarPanel.tsx): the day arrows stay in one place while
// the user clicks through days (the Today button keeps its room when hidden), and the date stays on
// one line in the panel's width.
import { connectCalendar, expect, test } from './fixtures'

test('the calendar panel day arrows stay in place and the date stays on one line', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/mail/inbox')
  await page.locator('.rail').getByRole('button', { name: 'Calendar beside' }).click()
  const panel = page.getByTestId('calendar-panel')
  const prev = panel.getByRole('button', { name: 'Previous day' })
  const next = panel.getByRole('button', { name: 'Next day' })
  await expect(next).toBeVisible()
  // On today the Today button is hidden but keeps its room.
  await expect(panel.getByRole('button', { name: 'Today', exact: true })).toHaveCount(0)
  const at = async () => [(await prev.boundingBox())!.x, (await next.boundingBox())!.x]
  const before = await at()

  for (let i = 0; i < 3; i++) {
    await next.click()
    expect(await at()).toEqual(before)
  }
  await expect(panel.getByRole('button', { name: 'Today', exact: true })).toBeVisible()
  await prev.click()
  expect(await at()).toEqual(before)

  // The short date ("28 Oct") is one line: its height is one line of its own text.
  const date = panel.locator('.cp-day-date')
  const { h, line } = await date.evaluate((el) => ({ h: el.getBoundingClientRect().height, line: parseFloat(getComputedStyle(el).lineHeight) || 18 }))
  expect(h).toBeLessThan(line * 1.5)

  await panel.getByRole('button', { name: 'Today', exact: true }).click()
  expect(await at()).toEqual(before)
})
