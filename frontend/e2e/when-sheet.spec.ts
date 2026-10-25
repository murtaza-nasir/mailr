// ACT-SURFACES: web.sheet
// The when sheet on the web:
// the reminder sheet's Snooze, Reschedule and Remind before expand into preset tiles in groups
// (Later, Same time, Next), a custom amount + unit with From now | From due time and a live
// result, and a footer with the verb and Pick date & time…. ACT task.snooze, ACT task.reschedule,
// ACT event.snooze, ACT event.remind_before. The tray's list and the actions are answered by the
// test; screenshots go to the job's shots/sheet folder (light and dark, desktop and phone).
import type { Page, Route } from '@playwright/test'
import { expect, shotsDir, test } from './fixtures'

const SHOTS = shotsDir('sheet')
const MIN = 60_000
const iso = (ms: number) => new Date(Date.now() + ms).toISOString()

interface Item {
  key: string; kind: 'event' | 'task'; uid: string; occurrence: string; minutes: number; title: string; detail: string
  start: string | null; fire_at: string; fired_at: string | null; missed: boolean; state: 'fired' | 'snoozed'; actions: string[]
}

function task(title: string, due: Date): Item {
  const d = due.toISOString()
  return {
    key: `t|tk-rec|${d}|0`, kind: 'task', uid: 'tk-rec', occurrence: d, minutes: 0, title, detail: '', start: d,
    fire_at: iso(-2 * MIN), fired_at: iso(-2 * MIN), missed: false, state: 'fired', actions: ['done', 'snooze', 'dismiss'],
  }
}

function event(title: string, inMin: number): Item {
  const start = iso(inMin * MIN)
  return {
    key: `e|ev-rev|${start}|10`, kind: 'event', uid: 'ev-rev', occurrence: start, minutes: 10, title, detail: 'Room 204', start,
    fire_at: iso(-MIN), fired_at: iso(-MIN), missed: false, state: 'fired', actions: ['snooze', 'dismiss'],
  }
}

async function serve(page: Page, items: Item[]) {
  const state = { items: [...items], calls: [] as { what: string; body: unknown }[] }
  const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  await page.route('**/api/v1/reminders/active*', (r) => json(r, { items: state.items }))
  await page.route('**/api/v1/reminders/snooze', (r) => {
    const b = r.request().postDataJSON() as { key: string; until: string }
    state.calls.push({ what: 'snooze', body: b })
    state.items = state.items.filter((i) => i.key !== b.key)
    return json(r, { key: b.key, state: 'snoozed', snoozed_until: b.until })
  })
  await page.route('**/api/v1/reminders/handle', (r) => {
    state.calls.push({ what: 'handle', body: r.request().postDataJSON() })
    return json(r, { ok: true })
  })
  await page.route('**/api/v1/reminders/add', (r) => {
    state.calls.push({ what: 'add', body: r.request().postDataJSON() })
    return json(r, { ok: true })
  })
  await page.route(/\/api\/v1\/tasks\/tk-[^/?]+$/, (r) => {
    state.calls.push({ what: `task ${r.request().method()}`, body: r.request().postDataJSON() })
    return json(r, { uid: 'tk-rec', summary: 'Record the class' })
  })
  return state
}

/** The overdue task of the user's report: due today four hours ago, at a whole hour when it can be. */
/** The page's clock in these tests: 2 PM today, so a task due four hours earlier is due today
 *  (between midnight and 4 AM the real clock put it on yesterday, and "in 8 days" read "in 7"). */
function pinnedNow(): Date {
  const d = new Date()
  d.setHours(14, 0, 0, 0)
  return d
}

function overdueDue(): Date {
  const d = new Date(pinnedNow().getTime() - 4 * 60 * MIN)
  d.setMinutes(0, 0, 0)
  return d
}

async function openSheet(page: Page, title: string) {
  await page.goto('/mail/inbox')
  await page.getByTestId('waiting-bell').click()
  const tray = page.getByRole('dialog', { name: 'Waiting for you' })
  await tray.getByTestId('reminder-row').filter({ hasText: title }).click()
  return tray.getByTestId('reminder-sheet')
}

test('task.snooze: tiles in groups, Same time from the original due, From due time and a live result (ACT task.snooze)', async ({ page }) => {
  await page.clock.setFixedTime(pinnedNow())
  const due = overdueDue()
  const state = await serve(page, [task('Record the class', due)])
  const sheet = await openSheet(page, 'Record the class')
  // The status is a small badge at the end of the when line.
  await expect(sheet.locator('.ws-badge.overdue')).toHaveText(/^Overdue \d+ h$/)
  await page.waitForTimeout(250)
  await sheet.screenshot({ path: `${SHOTS}/web-actions-light.png` })
  await sheet.getByRole('button', { name: 'Snooze' }).click()
  const ws = sheet.getByTestId('when-sheet')
  await expect(ws).toContainText('Only the reminder moves.')
  const groups = ws.getByRole('group', { name: 'Snooze until' })
  await expect(groups.getByRole('region', { name: 'Same time' }).locator('.ws-tile')).toHaveCount(3)
  // Same time tomorrow is the due's time of day on the next day.
  const clock = due.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  await expect(groups.locator('[data-tile="same-tomorrow"]')).toContainText(`Tomorrow, ${clock}`)
  // Equal tiles: every tile has the same height.
  const heights = await groups.locator('.ws-tile').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().height)))
  expect(new Set(heights).size).toBe(1)
  await page.waitForTimeout(250)
  await sheet.screenshot({ path: `${SHOTS}/web-snooze-light.png` })

  // The custom row: From now by default for a snooze; From due time counts from the due.
  const seg = ws.getByRole('radiogroup', { name: 'Counted from' })
  await expect(seg.getByRole('radio', { name: 'From now' })).toHaveAttribute('aria-checked', 'true')
  await ws.getByLabel('Unit').selectOption('days')
  await ws.getByLabel('Amount').selectOption('8')
  await seg.getByRole('radio', { name: 'From due time' }).click()
  await expect(ws.getByTestId('when-result')).toContainText('in 8 days')
  await expect(ws.getByTestId('when-result')).toContainText(clock)
  await ws.getByTestId('when-confirm').click()
  const until = new Date((state.calls.at(-1)!.body as { until: string }).until)
  const want = new Date(due)
  want.setDate(want.getDate() + 8)
  expect(until.getTime()).toBe(want.getTime())
  // The choice is remembered for the next snooze on this browser.
  expect(await page.evaluate(() => localStorage.getItem('mailr.whenSheet.base.snooze'))).toBe('due')
})

test('task.reschedule: From due time by default, Pick date & time… keeps the typed form (ACT task.reschedule)', async ({ page }) => {
  await page.clock.setFixedTime(pinnedNow())
  const due = overdueDue()
  const state = await serve(page, [task('Record the class', due)])
  const sheet = await openSheet(page, 'Record the class')
  await sheet.getByRole('button', { name: 'Reschedule' }).click()
  const ws = sheet.getByTestId('when-sheet')
  await expect(ws).toContainText('The due moves.')
  await expect(ws.getByRole('radio', { name: 'From due time' })).toHaveAttribute('aria-checked', 'true')
  await page.waitForTimeout(250)
  await sheet.screenshot({ path: `${SHOTS}/web-reschedule-light.png` })
  // Pick date & time… shows the typed form with its accelerators.
  await ws.getByRole('button', { name: /Pick date & time/ }).click()
  await expect(sheet.getByTestId('when-picker')).toBeVisible()
  await sheet.getByRole('button', { name: 'Back' }).click()
  await sheet.getByRole('button', { name: 'Reschedule' }).click()
  // A tile confirms at once: Same time tomorrow.
  await sheet.locator('[data-tile="same-tomorrow"]').click()
  const patch = state.calls.find((c) => c.what === 'task PATCH')!.body as { due: string }
  const want = new Date(due)
  want.setDate(want.getDate() + 1)
  expect(new Date(patch.due).getTime()).toBe(want.getTime())
})

test('event.snooze and event.remind_before: one group, no toggle; keys pick tiles (ACT event.snooze, ACT event.remind_before)', async ({ page }) => {
  const e = event('Budget review', 40)
  const state = await serve(page, [e])
  const sheet = await openSheet(page, 'Budget review')
  await sheet.getByRole('button', { name: 'Snooze' }).click()
  const ws = sheet.getByTestId('when-sheet')
  await expect(ws.getByRole('group', { name: 'Snooze until' }).locator('.ws-tile')).toHaveCount(3)
  await expect(ws.getByRole('radiogroup', { name: 'Counted from' })).toHaveCount(0)
  await sheet.getByRole('button', { name: 'Back' }).click()
  await sheet.getByRole('button', { name: 'Remind before' }).click()
  await expect(ws.getByRole('region', { name: 'Before it starts' }).locator('.ws-tile')).toHaveCount(5)
  await page.waitForTimeout(250)
  await sheet.screenshot({ path: `${SHOTS}/web-remind-light.png` })
  // 3 is the third tile: 10 min before.
  await ws.locator('.ws-tile').first().focus()
  await page.keyboard.press('3')
  expect(state.calls.at(-1)).toMatchObject({ what: 'add', body: { kind: 'e', minutes: 10 } })
})

test('the when sheet in dark mode and at phone width', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await serve(page, [task('Record the class', overdueDue())])
  let sheet = await openSheet(page, 'Record the class')
  await page.waitForTimeout(250)
  await sheet.screenshot({ path: `${SHOTS}/web-actions-dark.png` })
  await sheet.getByRole('button', { name: 'Snooze' }).click()
  await page.waitForTimeout(250)
  await sheet.screenshot({ path: `${SHOTS}/web-snooze-dark.png` })
  await page.setViewportSize({ width: 390, height: 844 })
  sheet = await openSheet(page, 'Record the class')
  await sheet.getByRole('button', { name: 'Snooze' }).click()
  await page.waitForTimeout(250)
  await page.screenshot({ path: `${SHOTS}/web-snooze-dark-phone.png` })
  // Two columns at phone width.
  const cols = await sheet.locator('.ws-grid').first().evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length)
  expect(cols).toBe(2)
})
