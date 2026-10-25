// The app frame (plan §3, product spec §3.0): Mail, Calendar and Tasks in every theme. Each theme
// reaches the apps through its own nav entries (Classic sidebar, Office app bar, Focus palette,
// Desk spaces bar, Cards rail) and through the global keys g c / g k / g i; deep links load the
// right app; ? shows the current app's keys. The switcher itself (same apps, order and names in
// every theme) is nav-switcher.spec.ts.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { THEMES, themes } from './matrix'
import { openApp } from './helpers'

type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'
type App = 'mail' | 'calendar' | 'tasks'
// The keys and deep links are the frame's, the same in every theme: nightly every theme, otherwise
// Classic and Cards (e2e/matrix.ts). The nav entries are each theme's own and run in every theme.
const SHARED = new Set<string>(themes({ release: ['classic', 'cards'] }))

const mailList = (page: Page) => page.locator('[data-mail-list]').first()
const appRoot = (page: Page, app: Exclude<App, 'mail'>) => page.getByTestId(`${app}-app`)

const open = (page: Page, theme: Theme, path = '/mail/inbox') => openApp(page, path, { theme })

/** Go to an app through the theme's own nav entry. */
async function navTo(page: Page, theme: Theme, app: App) {
  switch (theme) {
    case 'classic':
      // In Calendar and Tasks the sidebar is a rail of the apps: Mail is its entry.
      await page.locator(`.sidebar [data-app-nav="${app}"]`).click()
      break
    case 'office':
      await page.locator(`.o-appbar [data-app-nav="${app}"]`).click()
      break
    case 'focus': {
      await page.locator('.f-where').click()
      const input = page.getByRole('combobox', { name: 'Command' })
      await input.fill(`Go to ${app === 'mail' ? 'Mail' : app === 'calendar' ? 'Calendar' : 'Tasks'}`)
      await input.press('Enter')
      break
    }
    case 'desk':
      await page.locator(`.d-spaces [data-space="${app}"]`).click()
      break
    case 'cards':
      await page.locator(`.c-rail [data-app-nav="${app}"]`).click()
      break
  }
}

async function expectApp(page: Page, app: App) {
  if (app === 'mail') {
    await expect(page).toHaveURL(/\/mail\//)
    await expect(mailList(page)).toBeVisible()
  } else {
    await expect(page).toHaveURL(new RegExp(`/${app}(/|$)`))
    await expect(appRoot(page, app)).toBeVisible()
    await expect(mailList(page)).toHaveCount(0)
  }
}

async function keys(page: Page, ...ks: string[]) {
  for (const k of ks) await page.keyboard.press(k)
}

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    test('nav entries reach Calendar and Tasks and come back to mail', async ({ page }) => {
      await open(page, theme)
      await expectApp(page, 'mail')
      await navTo(page, theme, 'calendar')
      await expectApp(page, 'calendar')
      await navTo(page, theme, 'tasks')
      await expectApp(page, 'tasks')
      await navTo(page, theme, 'mail')
      await expectApp(page, 'mail')
    })

    if (SHARED.has(theme)) {
      test('g c, g k and g i switch apps; ? shows the app\'s own keys', async ({ page }) => {
        await open(page, theme)
        await expectApp(page, 'mail')
        await keys(page, 'g', 'c')
        await expectApp(page, 'calendar')
        await keys(page, '?')
        await expect(page.getByRole('dialog', { name: 'Calendar shortcuts' })).toBeVisible()
        await expect(page.getByRole('dialog').getByText('Find a time')).toBeVisible()
        await keys(page, 'Escape')
        await expect(page.getByRole('dialog', { name: 'Calendar shortcuts' })).toHaveCount(0)
        await keys(page, 'g', 'k')
        await expectApp(page, 'tasks')
        await keys(page, '?')
        await expect(page.getByRole('dialog', { name: 'Tasks shortcuts' })).toBeVisible()
        await keys(page, 'Escape')
        // c means "add a task" here, not "compose": no compose window opens.
        await keys(page, 'c')
        await expect(page.locator('.compose-win')).toHaveCount(0)
        // c focused the quick-add line; Esc leaves it (keeping the text) so g i is a shortcut again.
        await keys(page, 'Escape')
        await keys(page, 'g', 'i')
        await expectApp(page, 'mail')
        await expect(page).toHaveURL(/\/mail\/inbox/)
      })
    }

    if (SHARED.has(theme)) {
      test('deep links load the app', async ({ page }) => {
        await open(page, theme, '/calendar/week/2026-10-01')
        await expectApp(page, 'calendar')
        await page.goto(`/calendar/event/${encodeURIComponent('e2e-uid@mailr.test')}/${encodeURIComponent('2026-10-01T15:00:00+00:00')}`)
        await expectApp(page, 'calendar')
        await page.goto('/tasks/today')
        await expectApp(page, 'tasks')
        await page.goto('/tasks/task/e2e-task-uid')
        await expectApp(page, 'tasks')
        // Settings still loads from inside the frame.
        await page.goto('/settings/general')
        await expect(page).toHaveURL(/\/settings\/general/)
        await expect(appRoot(page, 'calendar')).toHaveCount(0)
      })
    }
  })
}

test('the panels beside the page open the full apps (Classic rail)', async ({ page }) => {
  await open(page, 'classic')
  await expectApp(page, 'mail')
  const rail = page.getByRole('complementary', { name: 'Open beside' })
  await rail.getByRole('button', { name: 'Calendar beside' }).click()
  await page.getByRole('link', { name: 'Open Calendar' }).click()
  await expectApp(page, 'calendar')
  await expect(page.getByRole('complementary', { name: 'Calendar', exact: true })).toHaveCount(0)
  await rail.getByRole('button', { name: 'Tasks beside' }).click()
  await page.getByRole('link', { name: 'Open Tasks' }).click()
  await expectApp(page, 'tasks')
})

test('a nav entry returns to the last place in that app', async ({ page }) => {
  await open(page, 'classic', '/mail/starred')
  await expect(mailList(page)).toBeVisible()
  await keys(page, 'g', 'c')
  await expectApp(page, 'calendar')
  // Move within the calendar the way the app does (a client-side navigation).
  await page.evaluate(() => {
    history.pushState({}, '', '/calendar/month/2026-10-01')
    dispatchEvent(new PopStateEvent('popstate'))
  })
  await expect(page).toHaveURL(/\/calendar\/month\/2026-10-01/)
  // The address changes at once; the month view is drawn later (the router renders a navigation as
  // a transition). The last place is noted only once the view is drawn, so wait for it: a click
  // before then ends the transition unrendered, and the Calendar entry still points at /calendar
  // (the flake under --repeat-each, 7 Oct 2026).
  await expect(appRoot(page, 'calendar')).toHaveAttribute('data-view', 'month')
  // The sidebar in Calendar is the apps' rail: Mail returns to the last mail view.
  await page.locator('.sidebar [data-app-nav="mail"]').click()
  await expect(page).toHaveURL(/\/mail\/starred/)
  await expect(mailList(page)).toBeVisible()
  await page.locator('.sidebar [data-app-nav="calendar"]').click()
  await expect(page).toHaveURL(/\/calendar\/month\/2026-10-01/)
  // g k goes to the start of Tasks; the Mail entries return to the last mail view.
  await keys(page, 'g', 'k')
  await expectApp(page, 'tasks')
})

// The icons once moved down when the Classic sidebar collapsed
// (rows 8 px taller, a smaller Compose button), and 20 px icons looked heavy beside 14 px names.
test('the Classic sidebar keeps each icon on its row when it collapses; icons are 18 px', async ({ page }) => {
  await page.goto('/mail/inbox')
  const icons = page.locator('.sidebar .nav > * .nav-item .lucide')
  await expect(icons.first()).toBeVisible()
  const tops = async () => (await icons.evaluateAll((els) => els.slice(0, 5).map((e) => {
    const r = e.getBoundingClientRect()
    return { top: Math.round(r.top + r.height / 2), w: Math.round(r.width) }
  })))
  const open = await tops()
  expect(open.every((x) => x.w === 18), JSON.stringify(open)).toBe(true)
  await page.getByRole('button', { name: 'Main menu' }).click()
  await expect(page.locator('.sidebar.collapsed')).toBeVisible()
  await page.mouse.move(900, 600)
  await expect.poll(async () => (await page.locator('.sidebar').evaluate((e) => e.getBoundingClientRect().width))).toBeLessThan(80)
  const closed = await tops()
  for (let i = 0; i < open.length; i++) expect(Math.abs(closed[i].top - open[i].top), JSON.stringify({ open, closed })).toBeLessThanOrEqual(1)
})
