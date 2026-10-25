// ACT-SURFACES: web.list
// Inbox sections. With several sections on screen, the
// main section pages from its own head, and paging brings that head to the top; the toolbar has no
// pager then. Any section can be shown alone (ACT layout.section_focus), paged from the toolbar with
// its name ("Everything else · 1–n of n"); Back or the open tab returns. A capped section grows in
// place (ACT layout.section_more): "Show N more", then "Show fewer".
import type { APIRequestContext } from '@playwright/test'
import { expect, test } from './fixtures'

async function layout(request: APIRequestContext, cap: number) {
  const lay = { id: 'two', name: 'Two', tabs: [{ name: 'Inbox', icon: 'inbox', badge: 'unread', sections: [
    { title: 'First few', base: 'inbox', filter: 'unread', limit: cap },
    { title: 'Everything else', base: 'inbox', exclude_above: false }] }] }
  expect((await request.put('/api/v1/layouts', { data: { layouts: [lay], active: 'two' } })).ok()).toBeTruthy()
}

const head = (page: import('@playwright/test').Page, title: string) =>
  page.locator('[data-mail-list] .section-head').filter({ has: page.locator('.title, .section-back-title', { hasText: title }) })

test('the main section pages from its own head; the toolbar has no pager then', async ({ page }) => {
  const request = page.request
  await layout(request, 3)
  expect((await request.patch('/api/v1/me/settings', { data: { page_size: 15 } })).ok()).toBeTruthy()
  await page.setViewportSize({ width: 1280, height: 720 })
  try {
    await page.goto('/mail/inbox')
    const rest = head(page, 'Everything else')
    const scroller = page.locator('[data-mail-list] .list-scroll')
    await expect(rest.getByTestId('section-range')).toContainText(/1–15 of \d/)
    await expect(page.getByTestId('list-range')).toHaveCount(0)
    // Each count sits right after its section's name, whatever the name's length (a minimum width
    // on the name once pushed short names' counts away).
    const gaps = await page.locator('[data-mail-list] .section-head').evaluateAll((heads) => heads.map((h) => {
      const r = document.createRange()
      r.selectNodeContents(h.querySelector('.title')!)
      return Math.round(h.querySelector('.count')!.getBoundingClientRect().left - r.getBoundingClientRect().right)
    }))
    expect(new Set(gaps).size, `gaps ${gaps}`).toBe(1)
    expect(gaps[0]).toBeLessThan(20)
    await rest.getByRole('button', { name: 'Older' }).click()
    await expect(rest.getByTestId('section-range')).toContainText(/^16–/)
    // From the bottom of the list, Newer brings the head back to the top with the first page in view.
    await scroller.evaluate((el) => { el.scrollTop = el.scrollHeight })
    await rest.getByRole('button', { name: 'Newer' }).click()
    await expect(rest.getByTestId('section-range')).toContainText(/^1–15/)
    const top = (await scroller.boundingBox())!.y
    await expect.poll(async () => Math.abs((await rest.boundingBox())!.y - top)).toBeLessThan(4)
  } finally {
    await request.patch('/api/v1/me/settings', { data: { page_size: 50 } })
  }
})

test('any section can be shown alone, paged from the toolbar with its name', async ({ page }) => {
  const request = page.request
  await layout(request, 1)
  await page.goto('/mail/inbox')
  await head(page, 'Everything else').getByTestId('section-focus').click()
  await expect(head(page, 'First few')).toHaveCount(0)
  const range = page.getByTestId('list-range')
  await expect(range).toContainText(/Everything else\s*·\s*\d/)
  // The name and the numbers are visibly apart (an inline-block once swallowed the space).
  const name = (await range.locator('.range-of').boundingBox())!
  const sep = (await range.locator('.range-sep').boundingBox())!
  expect(sep.x - (name.x + name.width)).toBeGreaterThanOrEqual(4)
  // Opening a conversation and coming back keeps the section shown alone.
  await page.locator('[data-mail-list] [role="row"]').first().click()
  await page.goto('/mail/inbox')
  await expect(head(page, 'First few')).toHaveCount(0)
  await page.getByTestId('section-back').click()
  await expect(head(page, 'First few')).toHaveCount(1)
  await expect(page.getByTestId('list-range')).toHaveCount(0)
  // A capped section too; the name is part of the Back button.
  await head(page, 'First few').getByTestId('section-focus').click()
  await expect(page.getByTestId('list-range')).toContainText(/First few\s*·\s*1–/)
  await page.getByTestId('section-back').getByText('First few').click()
  await expect(head(page, 'Everything else')).toHaveCount(1)
  // The navigation's Inbox shows all sections again.
  await head(page, 'Everything else').getByTestId('section-focus').click()
  await expect(head(page, 'First few')).toHaveCount(0)
  await page.locator('.nav-item', { hasText: 'Inbox' }).first().click()
  await expect(head(page, 'First few')).toHaveCount(1)
  await page.goto('/mail/all')
  await expect(page.getByTestId('list-range')).not.toContainText('Everything else')
})

test('a capped section grows in place and folds back', async ({ page }) => {
  const request = page.request
  await layout(request, 1)
  await page.goto('/mail/inbox')
  const first = page.locator('[data-mail-list] section.section').first()
  const rows = first.locator('[role="row"]')
  await expect(rows).toHaveCount(1)
  await first.getByTestId('section-show-more').click()
  await expect.poll(() => rows.count()).toBeGreaterThan(1)
  await first.getByTestId('section-show-fewer').click()
  await expect(rows).toHaveCount(1)
})
