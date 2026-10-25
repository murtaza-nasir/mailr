// Inbox tab indicators: "Show on the tab" in the layout editor turns on unread
// ("N new"), all ("N", a neutral pill) and due ("Due Dec 3 · 2"); the pills sit right after the
// tab's name; Desk shows one compact indicator; a strip of tabs wider than the pane scrolls with
// ‹ › buttons.
import { apiSettled, expect, type Page, test } from './fixtures'
import { THEMES } from './matrix'

async function putLayout(page: Page, tabs: unknown[]) {
  const csrf = (await (await page.request.get('/api/v1/me')).json()).csrf
  const r = await page.request.put('/api/v1/layouts', {
    headers: csrf ? { 'X-CSRF-Token': csrf } : {},
    data: { active: 'counted', layouts: [{ name: 'Counted', tabs }] },
  })
  expect(r.ok(), await r.text()).toBeTruthy()
  return r.json()
}

const inboxSec = (q = '') => [{ title: 'All', base: 'inbox', q }]

test('the editor turns indicators on per tab, and an old boolean reads as unread', async ({ page }) => {
  // An old client's boolean is stored as the choice it meant.
  const saved = await putLayout(page, [
    { name: 'Inbox', icon: 'inbox', badge: true, sections: inboxSec() },
    { name: 'Other', icon: 'tag', badge: false, sections: inboxSec('subject:"E2E Mute me"') },
  ])
  const tabsOf = (r: { layouts: { id: string; tabs: { indicators: string[] }[] }[] }) =>
    r.layouts.find((l) => l.id === 'counted')!.tabs.map((t) => t.indicators)
  expect(tabsOf(saved)).toEqual([['unread'], []])

  await page.goto('/settings/inbox')
  await page.getByRole('button', { name: 'Edit Counted' }).click()
  const group = page.getByRole('group', { name: 'Show on the tab' })
  await expect(group.getByRole('checkbox')).toHaveCount(3)
  await expect(group.getByRole('checkbox', { name: 'Unread' })).toBeChecked()
  await expect(group.getByRole('checkbox', { name: 'All' })).not.toBeChecked()
  await group.getByRole('checkbox', { name: 'Unread' }).uncheck()
  await group.getByRole('checkbox', { name: 'All' }).check()
  await group.getByRole('checkbox', { name: 'Due' }).check()
  await page.getByRole('dialog').getByRole('tab', { name: 'Other' }).click()
  await expect(group.getByRole('checkbox', { name: 'Unread' })).not.toBeChecked()
  await page.getByRole('button', { name: 'Save layout' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  const lays = await (await page.request.get('/api/v1/layouts')).json()
  expect(tabsOf(lays)).toEqual([['all', 'due'], []])
  const total = (await (await page.request.get('/api/v1/conversations', { params: { layout: 'counted', tab: 'inbox', sec: 'all' } })).json()).total
  const counts = await (await page.request.get('/api/v1/layouts/counted/counts', { params: { detail: 'true' } })).json()
  expect(counts.tabs.inbox.all).toBe(total)

  await page.goto('/mail/inbox')
  const pill = page.getByRole('tab', { name: /^Inbox/ }).locator('.count')
  await expect(pill).toHaveText(total.toLocaleString('en-US'))
})

test.describe('deadlines on a tab', () => {
  test.use({ extras: true })
  const financial = [
    { name: 'Inbox', icon: 'inbox', indicators: ['unread'], sections: inboxSec() },
    { name: 'Financial', icon: 'briefcase', indicators: ['due', 'all'], claims: true, sections: inboxSec('subject:"E2E Due"') },
  ]

  test('classic: "Due Dec 3 · 2" in the warning colour, with the full text on hover', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
    await putLayout(page, financial)
    await page.goto('/mail/inbox')
    const tab = page.getByRole('tab', { name: /^Financial/ })
    const due = tab.locator('[data-indicator="due"]')
    await expect(due).toHaveText('Due Dec 3 · 2')
    await expect(due).toHaveClass(/\bdue\b/)
    await expect(due).not.toHaveClass(/urgent/)
    await expect(due).toHaveAttribute('data-tip', '2 due · earliest Fri 3 Dec (Rita Forms)')
    // Two pills fit: the deadline, then all.
    await expect(tab.locator('[data-indicator]')).toHaveText(['Due Dec 3 · 2', '2'])
    const bg = await due.evaluate((el) => getComputedStyle(el).backgroundColor)
    const allBg = await tab.locator('[data-indicator="all"]').evaluate((el) => getComputedStyle(el).backgroundColor)
    expect(bg).not.toBe(allBg)
    await due.hover()
    await expect(page.getByText('2 due · earliest Fri 3 Dec (Rita Forms)')).toBeVisible()

    // "Not a deadline" on one of them: the tab follows.
    await tab.click()
    const row = page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Due form' }).first()
    await row.getByRole('button', { name: 'Due Dec 3' }).click()
    await page.getByRole('menuitem', { name: 'Not a deadline', exact: true }).click()
    await expect(due).toHaveText('Due Dec 10')
  })

  test('desk: one compact indicator, a dot with the full text as its tooltip', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'desk'))
    await putLayout(page, financial)
    await page.goto('/mail/inbox')
    const tab = page.getByRole('tab', { name: /^Financial/ })
    await expect(tab.locator('[data-indicator]')).toHaveCount(1)
    const dot = tab.locator('.ti-dot')
    await expect(dot).toHaveAttribute('data-tip', '2 due · earliest Fri 3 Dec (Rita Forms)')
    const box = (await dot.boundingBox())!
    expect(box.width).toBeLessThan(12)
    await expect(tab).toHaveAccessibleName('Financial, 2 due · earliest Fri 3 Dec (Rita Forms)')
  })
})

for (const theme of ['classic', 'cards'] as const) {
  test(`${theme}: the "all" pill is neutral, the "new" pill is the accent, both right after the name`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    await putLayout(page, [
      { name: 'Inbox', icon: 'inbox', badge: 'all', sections: inboxSec() },
      { name: 'Later', icon: 'bookmark', badge: 'unread', sections: inboxSec('subject:"E2E Reply to me"') },
      { name: 'Github', icon: 'info', badge: 'none', sections: inboxSec('subject:"E2E Mute me"') },
    ])
    await page.goto('/mail/inbox')
    const all = page.getByRole('tab', { name: /^Inbox/ }).locator('.count')
    const fresh = page.getByRole('tab', { name: /^Later/ }).locator('.new')
    await expect(all).toHaveText(/^\d[\d,]*$/)
    await expect(fresh).toHaveText(/^\d+ new$/)
    await expect(page.getByRole('tab', { name: /^Github/ }).locator('.count, .new')).toHaveCount(0)

    const bg = (sel: typeof all) => sel.evaluate((el) => getComputedStyle(el).backgroundColor)
    expect(await bg(all)).not.toBe(await bg(fresh))
    for (const tab of ['Inbox', 'Later']) {
      const t = page.getByRole('tab', { name: new RegExp(`^${tab}`) })
      const name = await t.locator('.cat-tab-name').boundingBox()
      const p = await t.locator('.count, .new').boundingBox()
      expect(p!.x - (name!.x + name!.width)).toBeGreaterThanOrEqual(0)
      expect(p!.x - (name!.x + name!.width)).toBeLessThan(16)
    }
  })
}

for (const theme of THEMES) {
  test(`${theme}: a strip wider than the pane scrolls with buttons and keeps the selected tab in view`, async ({ page }) => {
    await page.setViewportSize({ width: 560, height: 900 })
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    const names = ['Inbox', 'Shopping', 'Receipts', 'Newsletters', 'Travel', 'Family', 'School', 'Github']
    await putLayout(page, names.map((name, i) => ({ name, icon: 'folder', badge: i % 2 ? 'all' : 'unread', sections: inboxSec() })))
    await page.goto('/mail/inbox')
    const strip = page.getByRole('tablist', { name: 'Counted tabs' })
    await expect(strip.getByRole('tab')).toHaveCount(8)
    const left = page.getByRole('button', { name: 'Scroll Counted tabs left' })
    const right = page.getByRole('button', { name: 'Scroll Counted tabs right' })
    await expect(right).toBeVisible()
    await expect(left).toHaveCount(0)

    // No pill wraps onto a second line or covers its name.
    for (const t of await strip.getByRole('tab').all()) {
      const pill = t.locator('.count, .new')
      if (!(await pill.count())) continue
      const pb = (await pill.boundingBox())!
      const nb = (await t.locator('.cat-tab-name').boundingBox())!
      expect(pb.height).toBeLessThan(24)
      expect(pb.x).toBeGreaterThanOrEqual(nb.x + nb.width - 0.5)
    }

    // The strip brings the selected tab back into view when the selection settles: let the page
    // finish loading (the list, the counts) before scrolling away from it.
    await apiSettled(page)
    await right.click()
    await expect(left).toBeVisible()
    await expect.poll(() => strip.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0)

    // The mouse wheel scrolls it sideways too.
    await left.click()
    await expect(left).toHaveCount(0)
    await strip.hover()
    await page.mouse.wheel(0, 400)
    await expect.poll(() => strip.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0)

    // Keyboard: End moves to the last tab, Enter opens it, and it is in view.
    await strip.getByRole('tab', { selected: true }).focus()
    await page.keyboard.press('End')
    await expect(strip.getByRole('tab', { name: /^Github/ })).toBeFocused()
    await page.keyboard.press('Enter')
    const last = strip.getByRole('tab', { name: /^Github/ })
    await expect(last).toHaveAttribute('aria-selected', 'true')
    const inView = () => last.evaluate((el) => {
      const s = el.parentElement!.getBoundingClientRect()
      const b = el.getBoundingClientRect()
      return b.left >= s.left - 1 && b.right <= s.right + 1
    })
    await expect.poll(inView).toBe(true)
    await expect(right).toHaveCount(0)

    // On load, the selected tab is scrolled into view.
    await page.reload()
    await expect(last).toHaveAttribute('aria-selected', 'true')
    await expect.poll(inView).toBe(true)
  })
}

test('the Settings groups and the Ambrose pages scroll at phone width, with the current one in view', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/settings/notes')
  const strip = page.getByRole('navigation', { name: 'Settings groups' })
  const current = strip.getByRole('link', { name: 'Notes & tables' })
  const inView = (loc: typeof current) => loc.evaluate((el) => {
    const s = el.parentElement!.getBoundingClientRect()
    const b = el.getBoundingClientRect()
    return b.left >= s.left - 1 && b.right <= s.right + 1
  })
  await expect.poll(() => inView(current)).toBe(true)
  const left = page.getByRole('button', { name: 'Scroll Settings groups left' })
  const right = page.getByRole('button', { name: 'Scroll Settings groups right' })
  await expect(left).toBeVisible()
  await expect(right).toBeVisible()
  // Nothing wraps: one row of links, each on one line.
  const tops = await strip.getByRole('link').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))
  expect(new Set(tops).size).toBe(1)
  for (let i = 0; i < 8 && (await left.count()); i++) {
    await left.click()
    await page.waitForTimeout(400)
  }
  await expect(left).toHaveCount(0)
  await expect.poll(() => inView(strip.getByRole('link', { name: 'You' }))).toBe(true)

  await strip.getByRole('link', { name: 'You' }).focus()
  await page.keyboard.press('End')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/settings\/(agents|admin)$/)

  await page.goto('/settings/ambrose/importance')
  const chips = page.getByRole('navigation', { name: 'Ambrose pages' })
  await expect.poll(() => inView(chips.getByRole('link', { name: 'Review importance' }))).toBe(true)
  const chipTops = await chips.getByRole('link').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))
  expect(new Set(chipTops).size).toBe(1)
  await expect(page.getByRole('button', { name: 'Scroll Ambrose pages left' })).toBeVisible()
})
