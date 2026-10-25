// ACT-SURFACES: web.reader
// mail.show_images: remote images load through mailr's image proxy, trackers are removed and
// counted, and Spam asks (with a warning) before loading. The seed (`--extras`) puts the hero
// image into the proxy cache, so nothing is fetched from its made-up host.
import { conv, expect, test } from './fixtures'

test.use({ extras: true })

test('the reader shows proxied images and the trackers it blocked', async ({ page }) => {
  const remote: string[] = []
  page.on('request', (r) => {
    const u = new URL(r.url())
    if (!['localhost', '127.0.0.1'].includes(u.hostname)) remote.push(r.url())
  })
  const c = await conv(page.request, 'E2E Images')
  await page.goto(`/mail/inbox/${c.id}`)
  const frame = page.frameLocator('iframe[title="Message body"]').last()
  const img = frame.locator('img[alt="Hero"]')
  await expect(img).toHaveAttribute('src', /^\/api\/v1\/messages\/\d+\/images\/[0-9a-f]{32}$/)
  await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth)).toBe(120)
  // The trackers never reach the page, and the wrapped link opens its real address.
  await expect(frame.locator('img')).toHaveCount(1)
  await expect(frame.getByRole('link', { name: 'See the sale' })).toHaveAttribute('href', 'https://shop.e2e.example.org/sale')
  await expect(page.getByTestId('images-banner')).toHaveCount(0)
  const chip = page.getByRole('button', { name: '2 trackers blocked' })
  await expect(chip).toBeVisible()
  await chip.click()
  const pop = page.getByRole('dialog', { name: 'Trackers blocked' })
  await expect(pop).toContainText('e2e.us1.list-manage.com')
  await expect(pop).toContainText('pixel.e2e.example.net')
  expect(remote).toEqual([])
})

test('spam asks before loading images, then loads them through Mailr', async ({ page }) => {
  const c = await conv(page.request, 'E2E Images in spam')
  await page.goto(`/mail/spam/${c.id}`)
  const banner = page.getByTestId('images-banner')
  await expect(banner).toContainText('Images are not displayed in spam.')
  const frame = page.frameLocator('iframe[title="Message body"]').last()
  await expect(frame.locator('img[alt="Hero"]')).not.toHaveAttribute('src', /images/)
  await banner.getByRole('button', { name: 'Display images below' }).click()
  await expect(banner).toContainText('the sender can see that this address is active')
  await banner.getByRole('button', { name: 'Load images' }).click()
  await expect(frame.locator('img[alt="Hero"]')).toHaveAttribute('src', /\/images\/[0-9a-f]{32}$/)
  await expect(page.getByRole('button', { name: '2 trackers blocked' })).toBeVisible()
})
