// Office's calendar under its ribbon: "Today" showed three times
// (ribbon, header button, view picker) and the views twice. With the ribbon there, the header leaves
// out Today, the view switchers and Find a time.
// Covers: web.calendar (Office).
import { connectCalendar, expect, test } from './fixtures'
import { openApp } from './helpers'

test('Office calendar: one Today and one set of views, in the ribbon', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.setViewportSize({ width: 1440, height: 900 })
  await openApp(page, '/calendar/week', { theme: 'office', ready: page.getByTestId('cal-header') })
  const head = page.getByTestId('cal-header')
  await expect(head.getByRole('heading').first()).toBeVisible()
  await expect(head.locator('.cal-today')).toBeHidden()
  await expect(head.getByTestId('cal-views')).toBeHidden()
  await expect(head.locator('.cal-findtime')).toBeHidden()
  await expect(page.locator('.ribbon').getByRole('button', { name: /^Today/ }).first()).toBeVisible()
})
