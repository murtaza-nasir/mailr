// ACT-SURFACES: web.reader
// mail.copy_address: the open message's sender and the addresses in its details can be copied.
// - Desktop: a small copy button beside the address shows on hover or keyboard focus and keeps its
//   room while hidden (nothing moves). Click copies the address, Shift+click "Name <address>".
// - The message's ⋮ menu has Copy sender's address.
// - Touch (phone): no button; a long-press on the name or the address opens Copy address, Copy
//   name, Copy name and address. The press does not collapse the message.
import type { Locator, Page } from '@playwright/test'
import { conv, expect, test } from './fixtures'

const SENDER = { name: 'Bich Nguyen', email: 'bich.nguyen@example.com' }
const CC = { name: 'Lee, Sam', email: 'sam.lee@example.com' }

/** The example people on every message of the conversation. */
async function people(page: Page, cid: number) {
  await page.route(`**/api/v1/conversations/${cid}`, async (route) => {
    const r = await route.fetch()
    const j = (await r.json()) as { messages: Record<string, unknown>[] }
    for (const m of j.messages) {
      m.from = SENDER
      m.to = [{ name: 'Dana Reyes', email: 'dana.reyes@example.com' }]
      m.cc = [CC]
    }
    await route.fulfill({ response: r, json: j })
  })
}

const clipboard = (page: Page) => page.evaluate(() => navigator.clipboard.readText())
const head = (page: Page) => page.locator('.msg:not(.collapsed) .msg-head').first()

async function open(page: Page) {
  const c = await conv(page.request, 'E2E Track me')
  await people(page, c.id)
  await page.goto(`/mail/inbox/${c.id}`)
  await expect(head(page).locator('.who-line .name')).toHaveText(SENDER.name)
  return c
}

test.describe('desktop', () => {
  test.beforeEach(async ({ context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  })

  test('the copy button shows on hover without moving anything; click copies the address, Shift+click name and address', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await open(page)
    const h = head(page)
    const button = h.getByRole('button', { name: `Copy address: ${SENDER.name}` })
    await expect(button).toHaveCSS('opacity', '0')
    const before = await h.locator('.who-line').evaluate((el) => [...el.children].map((c) => JSON.stringify(c.getBoundingClientRect())))
    await h.locator('.who-line .name').hover()
    await expect(button).toHaveCSS('opacity', '1')
    const after = await h.locator('.who-line').evaluate((el) => [...el.children].map((c) => JSON.stringify(c.getBoundingClientRect())))
    expect(after).toEqual(before)

    await button.click()
    await expect(page.getByText('Copied', { exact: true })).toBeVisible()
    expect(await clipboard(page)).toBe(SENDER.email)
    // The header's click collapses the message; the button's does not.
    await expect(h.locator('.who-line .name')).toBeVisible()

    await button.click({ modifiers: ['Shift'] })
    await expect.poll(() => clipboard(page)).toBe(`${SENDER.name} <${SENDER.email}>`)
  })

  test('keyboard: the button shows on focus and Enter copies', async ({ page }) => {
    await open(page)
    const button = head(page).getByRole('button', { name: `Copy address: ${SENDER.name}` })
    await button.focus()
    await expect(button).toHaveCSS('opacity', '1')
    await page.keyboard.press('Enter')
    await expect.poll(() => clipboard(page)).toBe(SENDER.email)
  })

  test('the details list each address with its own copy button', async ({ page }) => {
    await open(page)
    await head(page).getByRole('button', { name: 'Show details' }).click()
    const details = page.locator('.details-pop')
    await expect(details).toBeVisible()
    const cc = details.locator('.addr-item').filter({ hasText: CC.email })
    await cc.hover()
    await cc.getByRole('button', { name: 'Copy address: Sam Lee' }).click()
    await expect.poll(() => clipboard(page)).toBe(CC.email)
    // A name with a comma is quoted, so a To field reads it back as one person.
    await cc.getByRole('button', { name: 'Copy address: Sam Lee' }).click({ modifiers: ['Shift'] })
    await expect.poll(() => clipboard(page)).toBe(`"${CC.name}" <${CC.email}>`)
  })

  test("the message menu copies the sender's address", async ({ page }) => {
    await open(page)
    await head(page).getByRole('button', { name: 'More' }).click()
    await page.getByRole('menuitem', { name: "Copy sender's address" }).click()
    await expect.poll(() => clipboard(page)).toBe(SENDER.email)
  })
})

/** A finger resting on the element (Chrome's own touch input, so pointer events say "touch"). */
async function longPress(page: Page, target: Locator) {
  // A sheet slides in on a phone: wait until the target stands still.
  let box = (await target.boundingBox())!
  await expect.poll(async () => {
    const prev = box
    await page.waitForTimeout(100)
    box = (await target.boundingBox())!
    return JSON.stringify(prev) === JSON.stringify(box)
  }).toBe(true)
  const point = { x: box.x + Math.min(12, box.width / 2), y: box.y + box.height / 2 }
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point] })
  await page.waitForTimeout(800)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await cdp.detach()
}

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
  test.beforeEach(async ({ context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  })

  test('no hover button; a long-press on the name opens the copy menu', async ({ page }) => {
    await open(page)
    const h = head(page)
    await expect(h.locator('.addr-copy')).toBeHidden()
    await longPress(page, h.locator('.who-line .name'))
    const menu = page.getByRole('menu')
    await expect(menu.getByRole('menuitem')).toHaveText(['Copy address', 'Copy name', 'Copy name and address'])
    await menu.getByRole('menuitem', { name: 'Copy name and address' }).click()
    await expect(page.getByText('Copied', { exact: true })).toBeVisible()
    expect(await clipboard(page)).toBe(`${SENDER.name} <${SENDER.email}>`)
    // The press did not collapse the message.
    await expect(h.locator('.who-line .name')).toBeVisible()

    await longPress(page, h.locator('.who-line .email-text'))
    await page.getByRole('menuitem', { name: 'Copy name', exact: true }).click()
    await expect.poll(() => clipboard(page)).toBe(SENDER.name)
  })

  test('a long-press on an address in the details copies it', async ({ page }) => {
    await open(page)
    await head(page).getByRole('button', { name: 'Show details' }).click()
    const to = page.locator('.details-pop .addr-item').filter({ hasText: 'dana.reyes@example.com' })
    await longPress(page, to.locator('.addr-text'))
    await page.getByRole('menuitem', { name: 'Copy address', exact: true }).click()
    await expect.poll(() => clipboard(page)).toBe('dana.reyes@example.com')
  })
})
