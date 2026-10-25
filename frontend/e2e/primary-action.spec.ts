// ACT-SURFACES: web.list web.reader
// mail.primary_action on web.list and web.reader: the email's main action as a chip that opens
// the link in a new tab (the server never follows it).
import { expect, test } from './fixtures'

test('the chip shows on the row and in the reader, and opens the link', async ({ page, context }) => {
  await page.goto('/mail/inbox')
  const row = page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Track me' }).first()
  const chip = row.getByRole('link', { name: 'Track package' })
  await expect(chip).toHaveAttribute('href', 'https://example.org/track/1Z999')
  await expect(chip).toHaveAttribute('target', '_blank')

  // Opening the link must not open the conversation (the row click stays put).
  await context.route('https://example.org/**', (r) => r.fulfill({ body: 'tracking' }))
  const [tab] = await Promise.all([context.waitForEvent('page'), chip.click()])
  expect(tab.url()).toBe('https://example.org/track/1Z999')
  await tab.close()
  await expect(page).toHaveURL(/\/mail\/inbox$/)

  await row.click()
  await expect(page.getByRole('heading', { name: 'E2E Track me' })).toBeVisible()
  await expect(page.locator('.conv-primary-action').getByRole('link', { name: 'Track package' }))
    .toHaveAttribute('href', 'https://example.org/track/1Z999')
  await expect(page.locator('.conv-primary-action')).toContainText('Arriving today 1:30 PM - 4:30 PM')
})

test('in compact rows the chip stays clickable while the hover buttons show', async ({ page, context }) => {
  expect((await page.request.patch('/api/v1/me/settings', { data: { density: 'compact' } })).ok()).toBeTruthy()
  await page.goto('/mail/inbox')
  const row = page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Track me' }).first()
  await row.hover()
  await context.route('https://example.org/**', (r) => r.fulfill({ body: 'tracking' }))
  const [tab] = await Promise.all([context.waitForEvent('page'), row.getByRole('link', { name: 'Track package' }).click()])
  expect(tab.url()).toBe('https://example.org/track/1Z999')
})

// Every theme shows hover buttons on the row; the chip must stay on top of them.
for (const theme of ['cards', 'focus', 'office', 'desk']) {
  test(`${theme} theme: the chip is on top while the row is hovered`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await page.goto('/mail/inbox')
    const chip = page.getByRole('link', { name: 'Track package' }).first()
    await expect(chip).toBeVisible()
    await chip.scrollIntoViewIfNeeded()
    let box = (await chip.boundingBox())!
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.waitForTimeout(300)
    box = (await chip.boundingBox())!
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.waitForTimeout(200)
    const top = await page.evaluate(([x, y]) => { const el = document.elementFromPoint(x, y); return el?.closest('a.action-chip') ? 'chip' : el?.outerHTML.slice(0, 120) }, [box.x + box.width / 2, box.y + box.height / 2])
    expect(top).toBe('chip')
  })
}


test('a verification email: the chip copies its code', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto('/mail/inbox')
  const row = page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Code me' }).first()
  await row.getByRole('button', { name: /Copy 480204/ }).click()
  await expect(page.getByText('Code 480204 copied')).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('480204')
  await expect(page).toHaveURL(/\/mail\/inbox$/) // copying does not open the email
})

// Desk: the chip was once squeezed to an icon in a wide empty pill
// or cut to "View P…". Wide: the whole label, the subject gives way. Narrow: a round icon button.
test('desk theme: the chip keeps its label on a wide subject column and is a round icon on a narrow one', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'desk'))
  await page.setViewportSize({ width: 1700, height: 900 })
  await page.goto('/mail/inbox')
  const chip = page.getByRole('link', { name: 'Track package' }).first()
  await expect(chip).toBeVisible()
  const look = () => chip.evaluate((el) => {
    const label = el.querySelector('.action-label') as HTMLElement
    const r = el.getBoundingClientRect()
    return { labelShown: label.offsetWidth > 0, labelWhole: label.scrollWidth <= label.clientWidth + 1, w: Math.round(r.width), h: Math.round(r.height), cell: Math.round(el.closest('.d-subject')!.getBoundingClientRect().width) }
  })
  const wide = await look()
  expect(wide.labelShown && wide.labelWhole, JSON.stringify(wide)).toBe(true)
  await page.setViewportSize({ width: 1000, height: 900 })
  await expect.poll(async () => (await look()).labelShown).toBe(false)
  const narrow = await look()
  expect(narrow.w, JSON.stringify(narrow)).toBe(24)
  expect(narrow.h, JSON.stringify(narrow)).toBe(24)
})
