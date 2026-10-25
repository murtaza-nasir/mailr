// ACT-SURFACES: web.list
// Desk's quick row actions (features/mail/RowParts.tsx RowQuickActions, themes/desk): the buttons
// of Classic's rows (Archive or Move to Inbox, Delete, Mark as read or unread, Snooze) sit over the
// Date cell of a table row on hover and on keyboard focus in the row. No column moves, the date shows
// otherwise, Tab reaches them, and the actions keep their undo. Phones get none (swipe, long press).
// Covers: mail.archive, mail.delete, mail.move, mail.read, mail.unread, mail.snooze (Desk rows).
// DESK_SHOTS=<dir> also takes the screenshot matrix (light and dark, three widths, en, en-XA, ar-XB).
import type { Locator, Page } from '@playwright/test'
import { conv, expect, roles, test } from './fixtures'

const row = (page: Page, subject: string) => page.locator('[data-mail-list] .d-row').filter({ hasText: subject }).first()
const actions = (r: Locator) => r.locator('.d-actions')

async function setup(page: Page, opts: { ambrose?: boolean; dark?: boolean; lang?: string } = {}) {
  await page.addInitScript(([a, l]) => {
    localStorage.setItem('mailr.uiTheme', 'desk')
    if (a) localStorage.setItem('mailr.panel', 'agent')
    else localStorage.removeItem('mailr.panel')
    if (l) {
      localStorage.setItem('mailr.i18n.pseudo', '1')
      localStorage.setItem('mailr.language', l)
    }
  }, [!!opts.ambrose, opts.lang ?? ''] as const)
  await page.emulateMedia({ colorScheme: opts.dark ? 'dark' : 'light', reducedMotion: 'reduce' })
}

/** Every cell's box in the row, to tell whether anything moved. */
const boxes = (r: Locator) =>
  r.evaluate((el) => [el, ...el.querySelectorAll('.d-cell')].map((c) => {
    const b = c.getBoundingClientRect()
    return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]
  }))

const opacity = (r: Locator) => actions(r).evaluate((el) => getComputedStyle(el).opacity)

test('desk: quick actions on hover, nothing moves, the date shows otherwise', async ({ page }) => {
  await setup(page)
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/mail/inbox')
  const r = row(page, 'E2E Read me')
  await expect(r).toBeVisible()
  await page.mouse.move(5, 5)
  const before = await boxes(r)
  await expect.poll(() => opacity(r)).toBe('0')
  await expect(r.locator('.d-date')).not.toBeEmpty()

  await r.hover()
  await expect.poll(() => opacity(r)).toBe('1')
  const btns = actions(r).getByRole('button')
  await expect(btns).toHaveCount(4)
  for (const name of ['Archive', 'Delete', 'Mark as read', 'Snooze']) await expect(actions(r).getByRole('button', { name, exact: true })).toBeVisible()
  expect(await boxes(r)).toEqual(before)
  // The buttons sit over the Date cell, inside the row.
  const date = (await r.locator('.d-date').boundingBox())!
  const bar = (await actions(r).boundingBox())!
  expect(bar.x).toBeGreaterThanOrEqual(date.x - 1)
  expect(bar.x + bar.width).toBeLessThanOrEqual(date.x + date.width + 1)
  const box = (await r.boundingBox())!
  expect(bar.y).toBeGreaterThanOrEqual(box.y - 0.5)
  expect(bar.y + bar.height).toBeLessThanOrEqual(box.y + box.height + 0.5)

  // Read and unread, from the same slot.
  await actions(r).getByRole('button', { name: 'Mark as read' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Read me')).unread_count).toBe(0)
  await r.hover()
  await actions(r).getByRole('button', { name: 'Mark as unread' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Read me')).unread_count).toBe(1)

  // Away from the row, the date is back (a clicked button keeps no keyboard focus ring).
  await page.mouse.move(5, 5)
  await expect.poll(() => opacity(r)).toBe('0')

  // Snooze opens the snooze menu at the button.
  await r.hover()
  await actions(r).getByRole('button', { name: 'Snooze' }).click()
  await expect(page.getByRole('menu').or(page.getByRole('dialog')).first()).toBeVisible()
  await page.keyboard.press('Escape')
})

test('desk: Tab reaches the quick actions, with a visible focus ring', async ({ page }) => {
  await setup(page)
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/mail/inbox')
  const r = row(page, 'E2E Archive me')
  await expect(r).toBeVisible()
  await page.mouse.move(5, 5)
  // The cursor row keeps its date (j and k move the focus row by row).
  await r.focus()
  await expect.poll(() => opacity(r)).toBe('0')
  let label = ''
  for (let i = 0; i < 8 && !label; i++) {
    await page.keyboard.press('Tab')
    label = await page.evaluate(() => (document.activeElement?.closest('.d-actions') ? document.activeElement.getAttribute('aria-label') ?? '' : ''))
  }
  expect(label).toBe('Archive')
  await page.mouse.move(5, 5)
  await expect.poll(() => opacity(r)).toBe('1')
  const ring = await page.evaluate(() => getComputedStyle(document.activeElement!).outlineStyle)
  expect(ring).not.toBe('none')
  await page.keyboard.press('Tab')
  expect(await page.evaluate(() => document.activeElement?.getAttribute('aria-label'))).toBe('Delete')
  // Enter on the focused button archives.
  await page.keyboard.press('Shift+Tab')
  await page.keyboard.press('Enter')
  await expect(r).toHaveCount(0)
  const inbox = (await roles(page.request)).inbox
  await expect.poll(async () => (await conv(page.request, 'E2E Archive me')).label_ids).not.toContain(inbox)
})

test('desk: Archive and Delete from a row, each with Undo', async ({ page }) => {
  await setup(page)
  await page.setViewportSize({ width: 1518, height: 582 })
  await page.goto('/mail/inbox')
  const r = await roles(page.request)
  for (const [subject, name, gone] of [['E2E Archive me', 'Archive', 'archive'], ['E2E Delete me', 'Delete', 'trash']] as const) {
    const it = row(page, subject)
    await it.hover()
    await actions(it).getByRole('button', { name, exact: true }).click()
    await expect(it).toHaveCount(0)
    await expect.poll(async () => {
      const ids = (await conv(page.request, subject)).label_ids
      return gone === 'trash' ? ids.includes(r.trash) : !ids.includes(r.inbox)
    }).toBe(true)
    await page.locator('.toast').getByRole('button', { name: 'Undo' }).first().click()
    await expect(row(page, subject)).toBeVisible()
    await expect.poll(async () => (await conv(page.request, subject)).label_ids).toContain(r.inbox)
    await expect.poll(async () => (await conv(page.request, subject)).label_ids).not.toContain(r.trash)
  }
})

test('desk: a row outside the Inbox offers Move to Inbox (mail.move)', async ({ page }) => {
  const c = await conv(page.request, 'E2E Delete me')
  const t = (await (await page.request.get('/api/v1/me')).json()).csrf
  expect((await page.request.post('/api/v1/conversations/actions', { headers: t ? { 'X-CSRF-Token': t } : {}, data: { ids: [c.id], action: 'archive' } })).ok()).toBeTruthy()
  await setup(page)
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/mail/all')
  const archived = row(page, 'E2E Delete me')
  const inInbox = row(page, 'E2E Archive me')
  await inInbox.hover()
  await expect(actions(inInbox).getByRole('button', { name: 'Archive', exact: true })).toBeVisible()
  await archived.hover()
  await expect(actions(archived).getByRole('button', { name: 'Archive', exact: true })).toHaveCount(0)
  await actions(archived).getByRole('button', { name: 'Move to Inbox' }).click()
  const inbox = (await roles(page.request)).inbox
  await expect.poll(async () => (await conv(page.request, 'E2E Delete me')).label_ids).toContain(inbox)
  await archived.hover()
  await expect(actions(archived).getByRole('button', { name: 'Archive', exact: true })).toBeVisible()
})

test('desk: no quick actions on the two-line rows of a phone or a narrow list', async ({ page }) => {
  await setup(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Read me')).toBeVisible()
  await expect(page.locator('.d-row.narrow').first()).toBeVisible()
  await expect(page.locator('.d-actions')).toHaveCount(0)
})

test('desk: screenshots of the quick actions', async ({ page }) => {
  const dir = process.env.DESK_SHOTS
  test.skip(!dir, 'DESK_SHOTS=<dir> takes the matrix')
  test.setTimeout(600_000)
  const c = await conv(page.request, 'E2E Delete me')
  const t = (await (await page.request.get('/api/v1/me')).json()).csrf
  await page.request.post('/api/v1/conversations/actions', { headers: t ? { 'X-CSRF-Token': t } : {}, data: { ids: [c.id], action: 'archive' } })
  const sizes = [
    { width: 1518, height: 582, ambrose: true },
    { width: 1280, height: 800, ambrose: false },
    { width: 1024, height: 768, ambrose: false },
  ]
  for (const lang of ['', 'en-XA', 'ar-XB']) {
    for (const dark of [false, true]) {
      for (const size of sizes) {
        if (lang && size.width !== 1518) continue
        // Init scripts add up in order: the languages come last, so a later one wins.
        const p = page
        await setup(p, { ambrose: size.ambrose, dark, lang: lang || undefined })
        await p.setViewportSize({ width: size.width, height: size.height })
        const name = `${lang || 'en'}-${dark ? 'dark' : 'light'}-${size.width}`
        for (const where of ['inbox', 'all']) {
          await p.goto(`/mail/${where}`)
          const rows = p.locator('[data-mail-list] .d-row')
          await expect(rows.nth(4)).toBeVisible()
          // Row 1 selected, row 2 open (focused), row 3 hovered (unread rows in the seed).
          await rows.nth(1).locator('.d-sel input').click()
          await rows.nth(2).locator('.d-subject').click()
          await rows.nth(0).hover()
          await p.screenshot({ path: `${dir}/${name}-${where}-hover.png` })
          await rows.nth(1).hover()
          await p.screenshot({ path: `${dir}/${name}-${where}-hover-selected.png` })
          await rows.nth(2).hover()
          await p.screenshot({ path: `${dir}/${name}-${where}-hover-focused.png` })
          // Keyboard: Tab from the open row into its buttons.
          await p.mouse.move(2, size.height - 2)
          await rows.nth(2).focus()
          for (let i = 0; i < 8; i++) {
            await p.keyboard.press('Tab')
            if (await p.evaluate(() => !!document.activeElement?.closest('.d-actions'))) break
          }
          await p.screenshot({ path: `${dir}/${name}-${where}-keyboard.png` })
          await p.keyboard.press('Escape')
          await rows.nth(3).focus()
          await p.screenshot({ path: `${dir}/${name}-${where}-rest.png` })
        }
      }
    }
  }
})
