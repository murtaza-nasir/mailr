// The week on a phone: seven columns of 45 px cut every title
// ("Studi", "Lu"). /calendar/week on a phone now shows three days, as the 3-day view.
// Covers: web.calendar (phone).
import { connectCalendar, expect, test } from './fixtures'

test('phone: /calendar/week shows three day columns', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/calendar/week')
  await expect(page.getByTestId('cal-header')).toBeVisible()
  await expect(page.locator('.cal-dayhead')).toHaveCount(3)
})
