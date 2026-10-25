// ACT-SURFACES: web.calendar web.settings
// Sharing calendars between users (backend calendar/sharing.py) in the web app: the Share dialog
// from Calendar's calendar menu and from Settings → Calendar & Tasks (find people, add at a level,
// change it, stop), a calendar someone shares under "Shared" ("Shared by Rosa · view only", no
// rename or delete), the notice line above the calendar list, and the admin's sharing policy.
// Built-in calendars stay off in the e2e database because other specs share it, so the share
// routes and the shared collections are mocked here. The real server is covered by
// backend/tests/test_calendar_sharing*.py.
// Actions covered: calendar.share, calendar.user_shared, calendar.share_notices, settings.calendar_sharing.
import type { Page } from '@playwright/test'
import { accessTest, connectCalendar, expect, signIn, test } from './fixtures'
import { openIn } from './calendar-data'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

const ROSA = { id: 601, name: 'Rosa Diaz', email: 'rosa@northwind.example' }
const SAM = { id: 602, name: 'Sam Lee', email: 'sam@northwind.example' }
const TESS = { id: 603, name: 'Tess Ford', email: 'tess@northwind.example' }
const SHARED_URL = 'http://stalwart:8080/dav/cal/rosa%40example.test/work/'

type Level = 'view' | 'edit' | 'share'

/** The calendar list with the first calendar shareable and one calendar Rosa shares (view only). */
async function mockCalendars(page: Page, own: { sharing?: { people: number; others: number } } = {}) {
  await page.route('**/api/v1/calendar/calendars*', async (route) => {
    const r = await route.fetch().catch(() => null)
    if (!r) return
    // The page may have moved on (a refetch replaced this request): then there is nothing to answer.
    const list = (await r.json().catch(() => null)) as { connector_id: number; calendar_id: number; events?: boolean }[] | null
    if (!list) return
    const cid = list[0]?.connector_id ?? 1
    const mine = list.map((c, i) => (i === 0 ? { ...c, shareable: true, sharing: own.sharing ?? null } : c))
    const base = { connector_id: cid, events: true, tasks: false, components: ['VEVENT'], hidden: false, busy: true, notify: true, sort_order: 0, local_only: false }
    const shared = { ...base, id: SHARED_URL, url: SHARED_URL, calendar_id: 990201, name: 'Rosa work', color: '#8e24aa', read_only: true,
      shared: { kind: 'user', owner: 'Rosa Diaz', owner_id: ROSA.id, level: 'view', access: 'read', can_share: false }, shareable: false }
    await route.fulfill({ response: r, json: [...mine, shared] }).catch(() => undefined)
  })
}

/** GET/PUT/DELETE …/shares for any calendar, in memory. */
async function mockShares(page: Page, calls: string[]) {
  const people: { id: number; name: string; email: string; level: Level }[] = []
  const state = () => ({
    calendar_id: 1, name: 'Calendar', own: true, owner: { id: 1, name: 'Me', email: 'me@northwind.example' }, policy: 'anyone',
    levels: ['view', 'edit', 'share'], others: 0, max: 50,
    people: people.map((p) => ({ ...p, disabled: false, you: false, outside_policy: false })),
    candidates: [ROSA, SAM, TESS].filter((x) => !people.some((p) => p.id === x.id)),
  })
  await page.route(/\/api\/v1\/calendar\/collections\/\d+\/shares(\/\d+)?$/, async (route) => {
    const req = route.request()
    const m = /\/shares\/(\d+)$/.exec(req.url())
    if (m) {
      const uid = Number(m[1])
      const who = [ROSA, SAM, TESS].find((x) => x.id === uid)!
      if (req.method() === 'PUT') {
        const level = (req.postDataJSON() as { level: Level }).level
        calls.push(`PUT ${uid} ${level}`)
        const p = people.find((x) => x.id === uid)
        if (p) p.level = level
        else people.push({ ...who, level })
      } else if (req.method() === 'DELETE') {
        calls.push(`DELETE ${uid}`)
        people.splice(people.findIndex((x) => x.id === uid), 1)
      }
    }
    await route.fulfill({ json: state() })
  })
}

test('the owner shares a calendar from its menu, changes the level and stops (calendar.share)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  await mockCalendars(page, { sharing: { people: 2, others: 0 } })
  const calls: string[] = []
  await mockShares(page, calls)
  await openIn(page, 'classic', '/calendar/week')
  // The owner's line under a shared calendar.
  await expect(page.getByRole('region', { name: 'Calendars' })).toContainText('Shared with 2 people')
  const first = page.getByTestId('cal-calendar').first()
  await first.hover()
  await first.getByRole('button', { name: /^Options for / }).click()
  await page.getByRole('menuitem', { name: 'Share…' }).click()
  const dlg = page.getByTestId('share-dialog')
  await expect(dlg).toContainText('You can share with anyone on this server.')
  await expect(dlg.getByTestId('share-none')).toHaveText('Nobody yet. Only you see this calendar.')
  await expect(dlg.getByTestId('share-candidate')).toHaveCount(3)
  await dlg.getByTestId('share-search').fill('lee')
  await expect(dlg.getByTestId('share-candidate')).toHaveCount(1)
  await dlg.getByTestId('share-new-level').selectOption('edit')
  await expect(dlg).toContainText('Adds, changes and deletes events.')
  await dlg.getByRole('button', { name: 'Share with Sam Lee' }).click()
  await expect.poll(() => calls).toEqual([`PUT ${SAM.id} edit`])
  const row = dlg.getByTestId('share-person').filter({ hasText: 'Sam Lee' })
  await expect(row.getByLabel('Level for Sam Lee')).toHaveValue('edit')
  await row.getByLabel('Level for Sam Lee').selectOption('share')
  await expect.poll(() => calls).toEqual([`PUT ${SAM.id} edit`, `PUT ${SAM.id} share`])
  await row.getByRole('button', { name: 'Stop sharing with Sam Lee' }).click()
  await expect.poll(() => calls.at(-1)).toBe(`DELETE ${SAM.id}`)
  await expect(dlg.getByTestId('share-none')).toBeVisible()
})

test('a calendar someone shares sits under Shared, view only, with a notice (calendar.user_shared, calendar.share_notices)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  await mockCalendars(page)
  let notices = [{ path: '/dav/cal/rosa%40example.test/work/', name: 'Rosa work', by: 'Rosa Diaz', owner: 'Rosa Diaz', at: '2026-10-04T12:00:00Z' }]
  let dismissed = 0
  await page.route('**/api/v1/calendar/share-notices', async (route) => {
    if (route.request().method() === 'DELETE') {
      dismissed += 1
      notices = []
      return route.fulfill({ json: { ok: true } })
    }
    await route.fulfill({ json: { notices } })
  })
  await openIn(page, 'classic', '/calendar/week')
  const note = page.getByTestId('share-notices')
  await expect(note).toHaveText(/Rosa Diaz shared “Rosa work” with you\./)
  const shared = page.getByTestId('cal-shared')
  await expect(shared).toContainText('Rosa work')
  await expect(shared).toContainText('Shared by Rosa Diaz · view only')
  await expect(shared.getByLabel('Read-only')).toHaveCount(1)
  await page.getByRole('button', { name: 'Options for Rosa work' }).click()
  const menu = page.locator('.cal-cal-pop')
  await expect(menu).toContainText('Only Rosa Diaz can rename or delete it. You can hide it.')
  await expect(menu.getByRole('menuitem', { name: 'Rename…' })).toHaveCount(0)
  await expect(menu.getByRole('menuitem', { name: 'Delete…' })).toHaveCount(0)
  await expect(menu.getByRole('menuitem', { name: 'Share…' })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await note.getByRole('button', { name: 'Dismiss' }).click()
  await expect(note).toHaveCount(0)
  expect(dismissed).toBe(1)
})

test('Settings → Calendar & Tasks offers Share for own calendars (calendar.share)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  await mockCalendars(page)
  const calls: string[] = []
  await mockShares(page, calls)
  await page.goto('/settings/calendar')
  const rows = page.getByTestId('settings-collection')
  await expect(rows.filter({ hasText: 'Rosa work' })).toContainText('Shared by Rosa Diaz · view only')
  await expect(rows.filter({ hasText: 'Rosa work' }).getByRole('button', { name: /^Share / })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^Share / })).toHaveCount(1)
  await page.getByRole('button', { name: /^Share / }).click()
  const dlg = page.getByTestId('share-dialog')
  await dlg.getByRole('button', { name: 'Share with Rosa Diaz' }).click()
  await expect.poll(() => calls).toEqual([`PUT ${ROSA.id} view`])
  await expect(dlg.getByTestId('share-person')).toContainText('Rosa Diaz')
})

accessTest('an admin chooses who users may share calendars with (settings.calendar_sharing)', async ({ page, access }) => {
  await signIn(page, access.admin)
  const puts: unknown[] = []
  let sharing = 'anyone'
  await page.route('**/api/v1/admin/calendars', async (route) => {
    if (route.request().method() === 'PUT') {
      const b = route.request().postDataJSON() as { sharing?: string }
      puts.push(b)
      sharing = b.sharing ?? sharing
    }
    await route.fulfill({ json: { builtin: true, who: 'everyone', sharing, connectors: 2, users: 2 } })
  })
  await page.route('**/api/v1/admin/calendars/groups', (route) => route.fulfill({ json: { enabled: true, groups: [] } }))
  await page.goto('/settings/admin/calendars')
  const box = page.getByTestId('calendar-sharing')
  await expect(box.getByRole('radio', { name: 'With anyone on this server' })).toBeChecked()
  await box.getByRole('radio', { name: /Only with people in the same group/ }).click()
  await expect.poll(() => puts).toEqual([{ builtin: true, sharing: 'groups' }])
  await expect(box.getByRole('radio', { name: /Only with people in the same group/ })).toBeChecked()
})
