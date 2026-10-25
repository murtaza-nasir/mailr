// ACT-SURFACES: web.settings web.calendar
// Group calendars (backend calendar/groups.py) in the web app: the admin's Group calendars panel in
// Settings → Admin → Calendars (empty states, a new read-only calendar with an editor, "who gets a
// mailr calendar"), and a member's Calendar, where group calendars sit under "Shared" with their
// group and cannot be renamed or deleted. Built-in calendars stay off in the e2e database because
// other specs share it, so the admin routes and the shared collections are mocked here. The real
// server is covered by backend/tests/test_group_calendars*.py.
// Actions covered: settings.group_calendars, calendar.group_shared.
import type { Page } from '@playwright/test'
import { accessTest, connectCalendar, expect, signIn, test } from './fixtures'
import { openIn } from './calendar-data'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

const GROUPS = {
  enabled: true,
  groups: [
    { name: 'family', members: [{ id: 501, name: 'Rosa', email: 'rosa@northwind.example' }, { id: 502, name: 'Sam', email: 'sam@northwind.example' }], calendars: [] as unknown[] },
    { name: 'staff', members: [], calendars: [] },
  ],
}

async function mockAdmin(page: Page, posts: unknown[], puts: unknown[]) {
  let who = 'everyone'
  await page.route('**/api/v1/admin/calendars', async (route) => {
    if (route.request().method() === 'PUT') {
      const b = route.request().postDataJSON() as { who?: string }
      puts.push(b)
      who = b.who ?? who
    }
    await route.fulfill({ json: { builtin: true, who, connectors: 2, users: 2 } })
  })
  await page.route('**/api/v1/admin/calendars/groups', async (route) => {
    if (route.request().method() === 'POST') {
      const b = route.request().postDataJSON() as { group: string; name: string; color: string; access: string; editors?: number[] }
      posts.push(b)
      const g = GROUPS.groups.find((x) => x.name === b.group)!
      g.calendars.push({ id: 77, group: b.group, name: b.name, color: b.color, access: b.access, ready: true, items: 0,
        editors: g.members.filter((m) => (b.editors ?? []).includes(m.id)) })
      return route.fulfill({ json: g.calendars.at(-1) })
    }
    await route.fulfill({ json: GROUPS })
  })
}

accessTest('an admin adds a read-only group calendar with an editor (settings.group_calendars)', async ({ page, access }) => {
  await signIn(page, access.admin)
  const posts: unknown[] = []
  const puts: unknown[] = []
  await mockAdmin(page, posts, puts)
  await page.goto('/settings/admin/calendars')
  const panel = page.getByTestId('group-calendars')
  await expect(panel.getByRole('heading', { name: 'Group calendars' })).toBeVisible()
  await expect(panel.getByTestId('group-calendars-none')).toHaveText('No group calendars yet.')

  // Who gets a mailr calendar.
  await page.getByRole('radio', { name: 'Members of chosen groups' }).click()
  await expect.poll(() => puts).toEqual([{ builtin: true, who: 'groups' }])
  await expect(page.getByRole('radio', { name: 'Members of chosen groups' })).toBeChecked()

  await panel.getByTestId('group-calendar-group').selectOption('family')
  await panel.getByRole('button', { name: 'New group calendar' }).click()
  const dlg = page.getByRole('dialog', { name: 'New calendar for family' })
  await dlg.getByLabel('Name').fill('School')
  await dlg.getByRole('radio', { name: /Can view/ }).check()
  await dlg.getByRole('checkbox', { name: /Rosa/ }).check()
  await dlg.getByTestId('group-calendar-save').click()
  await expect.poll(() => posts).toHaveLength(1)
  expect(posts[0]).toMatchObject({ group: 'family', name: 'School', access: 'read', editors: [501] })
  const row = panel.getByTestId('group-calendar').filter({ hasText: 'School' })
  await expect(row).toContainText('Members can view · 1 editor')
  await expect(row).toContainText('Editors: Rosa')
  await expect(row).toContainText('0 items')

  // Delete offers the .ics copy first.
  await row.getByRole('button', { name: 'delete' }).click()
  const confirm = page.getByRole('dialog', { name: 'Delete “School”?' })
  await expect(confirm.getByRole('button', { name: 'Download .ics first' })).toBeVisible()
  await expect(confirm).toContainText('for every member of family')
})

accessTest('the panel says what to do while built-in calendars are off', async ({ page, access }) => {
  await signIn(page, access.admin)
  await page.route('**/api/v1/admin/calendars', (route) => route.fulfill({ json: { builtin: false, who: 'everyone', connectors: 0, users: 2 } }))
  await page.route('**/api/v1/admin/calendars/groups', (route) => route.fulfill({ json: { ...GROUPS, enabled: false } }))
  await page.goto('/settings/admin/calendars')
  await expect(page.getByTestId('group-calendars-off')).toHaveText('Turn on built-in calendars above to add group calendars.')
  await expect(page.getByRole('radio', { name: 'Everyone', exact: true })).toHaveCount(0)
})

test('group calendars sit under Shared with their group, and only hiding is offered', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  await page.route('**/api/v1/calendar/calendars*', async (route) => {
    // A refetch can still be in flight when the test ends and the page closes.
    const r = await route.fetch().catch(() => null)
    if (!r) return
    const list = (await r.json()) as { connector_id: number }[]
    const cid = list[0]?.connector_id ?? 1
    const base = { connector_id: cid, events: true, tasks: false, components: ['VEVENT'], hidden: false, busy: true, notify: true, sort_order: 0, local_only: false }
    const shared = [
      { ...base, id: 'http://stalwart:8080/dav/cal/grp-family%40example.test/family/', url: 'http://stalwart:8080/dav/cal/grp-family%40example.test/family/', calendar_id: 990101, name: 'Family', color: '#0b8043', read_only: false, shared: { kind: 'group', group: 'family', access: 'write', editor: false } },
      { ...base, id: 'http://stalwart:8080/dav/cal/grpcal-family%40example.test/school/', url: 'http://stalwart:8080/dav/cal/grpcal-family%40example.test/school/', calendar_id: 990102, name: 'School', color: '#3f51b5', read_only: true, shared: { kind: 'group', group: 'family', access: 'read', editor: false } },
    ]
    await route.fulfill({ response: r, json: [...list, ...shared] }).catch(() => undefined)
  })
  await openIn(page, 'classic', '/calendar/week')
  const shared = page.getByTestId('cal-shared')
  await expect(shared).toContainText('Shared')
  await expect(shared).toContainText('Family')
  await expect(shared).toContainText('Shared with the family group')
  await expect(shared).toContainText('Shared with the family group · view only')
  await expect(shared.getByLabel('Read-only')).toHaveCount(1)
  // Hide it like any calendar; no rename, no delete.
  await expect(page.getByRole('checkbox', { name: 'Show School' })).toBeChecked()
  await page.getByRole('button', { name: 'Options for School' }).click()
  const menu = page.locator('.cal-cal-pop')
  await expect(menu).toContainText('Only an administrator can rename or delete it')
  await expect(menu.getByRole('menuitem', { name: 'Delete…' })).toHaveCount(0)
  await expect(menu.getByRole('menuitem', { name: 'Rename…' })).toHaveCount(0)
})
