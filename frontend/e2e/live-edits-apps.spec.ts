// ACT-SURFACES: web.editor web.pane web.settings
// Live editing beyond drafts: the event editor, the task pane, and the
// memory note and filter dialogs follow a change saved elsewhere (here: another client through the
// API) in place, merge it with what is being typed, and show the conflict bar when both changed the
// same field. Covers: event.live_update, event.resolve_conflict, task.live_update, task.resolve_conflict,
// settings.memory_live_update, settings.filter_live_update.
import type { Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'
import { dayOf } from './calendar-data'

async function live(page: Page, kind: string, id: string | number) {
  return (await (await page.request.get(`/api/v1/live/${kind}/${encodeURIComponent(String(id))}`)).json()) as { version: number; content: Record<string, unknown> }
}

test('event.live_update: the open editor takes a change saved elsewhere; event.resolve_conflict on the same field', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  const r = await page.request.post('/api/v1/calendar/events', { data: { summary: 'Live review', start: `${dayOf(3)}T14:00:00`, end: `${dayOf(3)}T15:00:00`, tz, description: 'Agenda:\n- numbers\n' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const ev = await r.json()
  await page.goto(`/calendar/week/${dayOf(3)}`)
  await page.getByTestId('cal-event').filter({ hasText: 'Live review' }).first().click()
  await expect(page.getByTestId('cal-detail')).toBeFocused()
  const opened = page.waitForResponse((res) => res.url().includes('/api/v1/live/event/'))
  await page.keyboard.press('e')
  const ed = page.getByTestId('cal-editor')
  await expect(ed).toBeVisible()
  await opened
  // Another client sets the location: the clean editor shows it, with Undo.
  const v1 = (await live(page, 'event', ev.uid)).version
  expect((await page.request.patch(`/api/v1/calendar/events/${ev.uid}`, { data: { location: 'Room 4', base_version: v1 } })).ok()).toBeTruthy()
  await expect(ed.getByRole('textbox', { name: /location/i }).or(ed.locator('input[value="Room 4"]')).first()).toHaveValue('Room 4')
  await expect(page.getByText('Updated by another tab')).toBeVisible()
  // Typing in the title while the other client changes the description: both are kept.
  const title = ed.getByRole('textbox', { name: 'Title' })
  await title.fill('Live review (Q4)')
  const v2 = (await live(page, 'event', ev.uid)).version
  expect((await page.request.patch(`/api/v1/calendar/events/${ev.uid}`, { data: { description: 'Agenda:\n- numbers\n- plan\n', base_version: v2 } })).ok()).toBeTruthy()
  await expect(page.getByText('changed this event too; both changes are kept.')).toBeVisible()
  await expect(title).toHaveValue('Live review (Q4)')
  // The same title changed elsewhere: the conflict bar, and "Use mine" keeps this one.
  const v3 = (await live(page, 'event', ev.uid)).version
  expect((await page.request.patch(`/api/v1/calendar/events/${ev.uid}`, { data: { summary: 'Live review (moved)', base_version: v3 } })).ok()).toBeTruthy()
  const bar = page.getByTestId('live-conflict')
  await expect(bar).toBeVisible()
  await bar.getByRole('button', { name: 'Use mine' }).click()
  await expect(bar).toHaveCount(0)
  await ed.getByTestId('cal-editor-save').click()
  await expect.poll(async () => (await live(page, 'event', ev.uid)).content.summary).toBe('Live review (Q4)')
  const c = (await live(page, 'event', ev.uid)).content
  expect(c.location).toBe('Room 4')
  expect(c.description).toBe('Agenda:\n- numbers\n- plan\n')
})

test('task.live_update: the pane follows notes saved elsewhere; task.resolve_conflict on the same line', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const r = await page.request.post('/api/v1/tasks', { data: { summary: 'Live task', notes: 'one\ntwo\n' } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const t = await r.json()
  // The pane reads the task's version when it opens; the change below comes after that.
  const opened = page.waitForResponse((res) => res.url().includes('/api/v1/live/task/'))
  await page.goto(`/tasks/all/task/${encodeURIComponent(t.uid)}`)
  const pane = page.getByTestId('task-pane')
  await expect(pane).toBeVisible()
  await opened
  const v1 = (await live(page, 'task', t.uid)).version
  expect((await page.request.patch(`/api/v1/tasks/${t.uid}`, { data: { notes: 'one\ntwo\nthree\n', base_version: v1 } })).ok()).toBeTruthy()
  await expect(page.getByText('Updated by another tab')).toBeVisible()
  await expect(pane.getByText('three')).toBeVisible()
  // Typing in the first line while the same line changes elsewhere.
  const notes = pane.getByRole('textbox', { name: 'Notes' })
  if (!(await notes.count())) await pane.getByRole('button', { name: 'Edit notes' }).click()
  // The pane saves the typing after a 600 ms pause. The change elsewhere has to come first, while
  // the typing is unsaved: on a busy server reading the version and saving take longer than that, so
  // the pane's own save waits until the other change is in.
  let release!: () => void
  const held = new Promise<void>((r) => { release = r })
  await page.route('**/api/v1/tasks/**', async (route) => {
    if (route.request().method() === 'PATCH') await held
    await route.fallback()
  })
  await notes.fill('ONE\ntwo\nthree\n')
  const v2 = (await live(page, 'task', t.uid)).version
  expect((await page.request.patch(`/api/v1/tasks/${t.uid}`, { data: { notes: 'uno\ntwo\nthree\n', base_version: v2 } })).ok()).toBeTruthy()
  release()
  const bar = page.getByTestId('live-conflict')
  await expect(bar).toBeVisible()
  await bar.getByRole('button', { name: 'Use theirs' }).click()
  await expect(bar).toHaveCount(0)
  // The notes show theirs (read-only again: the bar took the focus), and theirs stays stored.
  await expect(pane.getByText('uno')).toBeVisible()
  await expect.poll(async () => (await live(page, 'task', t.uid)).content.notes).toMatch(/^uno\ntwo\nthree/)
})

test('settings.memory_live_update: the note dialog takes a change saved meanwhile', async ({ page }) => {
  const r = await page.request.post('/api/v1/assistant/memories', { data: { text: 'Dana prefers mornings.' } })
  expect(r.status(), await r.text()).toBe(201)
  const m = (await r.json()).memory
  await page.goto('/settings/assistant?section=memory')
  const opened = page.waitForResponse((res) => res.url().includes('/api/v1/live/memory/'))
  await page.getByRole('button', { name: 'Edit memory' }).first().click()
  const dlg = page.getByRole('dialog', { name: 'Edit memory' })
  await expect(dlg).toBeVisible()
  await opened
  expect((await page.request.patch(`/api/v1/assistant/memories/${m.id}`, { data: { text: 'Dana prefers early mornings.', base_version: m.version } })).ok()).toBeTruthy()
  await expect(dlg.getByLabel('What should Ambrose remember?')).toHaveValue('Dana prefers early mornings.')
})

test('settings.filter_live_update: saving over a change made meanwhile shows the conflict bar', async ({ page }) => {
  const r = await page.request.post('/api/v1/rules', { data: { name: 'News', query: 'from:news@x.example', actions: { archive: true } } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const rule = await r.json()
  await page.goto('/settings/filters')
  const opened = page.waitForResponse((res) => res.url().includes('/api/v1/live/rule/'))
  await page.getByRole('button', { name: 'edit' }).first().click()
  const dlg = page.getByRole('dialog', { name: 'Edit filter' })
  await expect(dlg).toBeVisible()
  await opened
  // The name changes elsewhere while the dialog is open (clean: it follows).
  expect((await page.request.patch(`/api/v1/rules/${rule.id}`, { data: { name: 'Newsletters', query: 'from:news@x.example', actions: { archive: true }, enabled: true, stop: false, base_version: rule.version } })).ok()).toBeTruthy()
  await expect(page.getByText('Updated by another tab')).toBeVisible()
})
