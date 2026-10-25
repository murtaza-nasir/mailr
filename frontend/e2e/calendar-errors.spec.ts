// When the events request fails with a 5xx (or the server cannot be reached), Calendar says
// so with Retry instead of drawing an empty week.
// Covers: web.calendar.
import { apiSettled, connectCalendar, expect, test } from './fixtures'

test('a failing agenda request shows an error banner with Retry, not an empty week', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  let fail = true
  let failed = 0
  await page.route('**/api/v1/calendar/agenda**', (route) => {
    if (!fail) return route.fallback()
    failed += 1
    return route.fulfill({ status: 503, body: 'Service Unavailable' })
  })
  // Notes whether the banner ever leaves the page once it has been shown, however briefly.
  await page.addInitScript(() => {
    const w = window as unknown as { __bannerGone: boolean }
    w.__bannerGone = false
    let seen = false
    new MutationObserver(() => {
      const there = !!document.querySelector('[data-testid="cal-load-error"]')
      if (there) seen = true
      else if (seen) w.__bannerGone = true
    }).observe(document, { childList: true, subtree: true })
  })
  await page.goto('/calendar')
  const banner = page.getByTestId('cal-load-error')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText("Couldn't load events")

  // Retry while the server still fails: the banner stays through the new request and its retry.
  // React Query clears the error of a query without data when a refetch starts, and the banner
  // used to vanish until the refetch had failed again.
  const before = failed
  await banner.getByRole('button', { name: 'Retry' }).click()
  await expect.poll(() => failed).toBeGreaterThanOrEqual(before + 2)
  await apiSettled(page, /\/calendar\/agenda/)
  await expect(banner).toBeVisible()
  expect(await page.evaluate(() => (window as unknown as { __bannerGone: boolean }).__bannerGone)).toBe(false)

  // Once a request succeeds, the banner goes. The page also refetches by itself (live updates
  // after the calendar's first sync), so a refetch may succeed before the click lands; either
  // way the success clears it.
  fail = false
  await expect(async () => {
    if (await banner.count()) await banner.getByRole('button', { name: 'Retry' }).click({ timeout: 2_000 })
    await expect(banner).toHaveCount(0, { timeout: 2_000 })
  }).toPass()
})
