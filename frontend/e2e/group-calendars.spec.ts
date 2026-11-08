// ACT-SURFACES: web.settings web.calendar
// Group calendars (backend calendar/groups.py) in the web app: the admin's Group calendars panel in
// Settings → Admin → Calendars (empty states, a new read-only calendar with an editor, "who gets a
// mailr calendar"), and a member's Calendar, where group calendars sit under "Shared" with their
// group and cannot be renamed or deleted. Built-in calendars stay off in the e2e database because
// other specs share it, so the admin routes and the shared collections are mocked here. The real
// server is covered by backend/tests/test_group_calendars*.py.
// "Free/busy only" (Stalwart 1.x): the level picker, the switch with Can view, the server that does
// not offer it, and a member's busy blocks with the group note instead of details.
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

accessTest('an admin picks Free/busy only and switches a calendar between it and Can view (settings.group_calendars)', async ({ page, access }) => {
  await signIn(page, access.admin)
  const rota = { id: 81, group: 'family', name: 'Rota', color: '#1565C0', access: 'read', ready: true, items: 4,
    editors: [{ id: 501, name: 'Rosa', email: 'rosa@northwind.example' }] }
  const data = {
    enabled: true,
    access: ['write', 'read', 'freebusy'],
    groups: [{ name: 'family', members: GROUPS.groups[0].members, calendars: [rota] as Record<string, unknown>[] }],
  }
  const sent: { method: string; body: Record<string, unknown> }[] = []
  await page.route('**/api/v1/admin/calendars', (route) => route.fulfill({ json: { builtin: true, who: 'everyone', connectors: 2, users: 2 } }))
  await page.route(/\/api\/v1\/admin\/calendars\/groups(\/\d+)?$/, async (route) => {
    const req = route.request()
    if (req.method() === 'GET') return route.fulfill({ json: data })
    const body = req.postDataJSON() as Record<string, unknown>
    sent.push({ method: req.method(), body })
    if (req.method() === 'PATCH') {
      Object.assign(rota, body.access ? { access: body.access } : {})
      return route.fulfill({ json: rota })
    }
    const made = { id: 82, group: 'family', name: body.name, color: body.color, access: body.access, ready: true, items: 0,
      editors: data.groups[0].members.filter((m) => ((body.editors as number[]) ?? []).includes(m.id)) }
    data.groups[0].calendars.push(made)
    return route.fulfill({ json: made })
  })
  await page.goto('/settings/admin/calendars')
  const panel = page.getByTestId('group-calendars')
  const row = panel.getByTestId('group-calendar').filter({ hasText: 'Rota' })
  await expect(row).toContainText('Members can view · 1 editor')

  // Can view → Free/busy only, in place; Can edit cannot be picked for a made calendar.
  await row.getByRole('button', { name: 'edit' }).click()
  const dlg = page.getByRole('dialog', { name: 'Edit “Rota”' })
  await expect(dlg.getByRole('radio', { name: /Can edit/ })).toBeDisabled()
  await expect(dlg).toContainText('You can switch between Can view and Free/busy only.')
  await dlg.getByRole('radio', { name: /Free\/busy only/ }).check()
  await expect(dlg.getByRole('checkbox', { name: /Rosa/ })).toBeChecked()
  await dlg.getByTestId('group-calendar-save').click()
  await expect.poll(() => sent).toHaveLength(1)
  expect(sent[0]).toMatchObject({ method: 'PATCH', body: { access: 'freebusy', editors: [501] } })
  await expect(row).toContainText('Members see free/busy only · 1 editor')

  // A new calendar at Free/busy only, with an editor.
  await panel.getByRole('button', { name: 'Add calendar' }).click()
  const add = page.getByRole('dialog', { name: 'New calendar for family' })
  await add.getByLabel('Name').fill('On call')
  await add.getByRole('radio', { name: /Free\/busy only/ }).check()
  await expect(add).toContainText('Members see only when the calendar is busy')
  await add.getByRole('checkbox', { name: /Sam/ }).check()
  await add.getByTestId('group-calendar-save').click()
  await expect.poll(() => sent).toHaveLength(2)
  expect(sent[1]).toMatchObject({ method: 'POST', body: { group: 'family', name: 'On call', access: 'freebusy', editors: [502] } })
  await expect(panel.getByTestId('group-calendar').filter({ hasText: 'On call' })).toContainText('Members see free/busy only · 1 editor')
})

accessTest('Free/busy only is not offered when the mail server does not list it', async ({ page, access }) => {
  await signIn(page, access.admin)
  await page.route('**/api/v1/admin/calendars', (route) => route.fulfill({ json: { builtin: true, who: 'everyone', connectors: 2, users: 2 } }))
  await page.route('**/api/v1/admin/calendars/groups', (route) => route.fulfill({ json: {
    enabled: true, access: ['write', 'read'], groups: [{ name: 'staff', members: [], calendars: [] }] } }))
  await page.goto('/settings/admin/calendars')
  const panel = page.getByTestId('group-calendars')
  await panel.getByTestId('group-calendar-group').selectOption('staff')
  await panel.getByRole('button', { name: 'New group calendar' }).click()
  const dlg = page.getByRole('dialog', { name: 'New calendar for staff' })
  await expect(dlg.getByRole('radio', { name: /Can view/ })).toBeVisible()
  await expect(dlg.getByRole('radio', { name: /Free\/busy only/ })).toHaveCount(0)
})

test('a member sees a free/busy-only group calendar as busy blocks (calendar.group_shared)', async ({ page, seeded }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await connectCalendar(page.request, seeded)
  await page.request.get('/api/v1/calendar/calendars', { params: { refresh: '1' } })
  const url = 'http://stalwart:8080/dav/cal/grpcal-family%40example.test/rota/'
  await page.route('**/api/v1/calendar/calendars*', async (route) => {
    const r = await route.fetch().catch(() => null)
    if (!r) return
    const list = (await r.json().catch(() => null)) as { connector_id: number }[] | null
    if (!list) return
    const base = { connector_id: list[0]?.connector_id ?? 1, events: true, tasks: false, components: ['VEVENT'], hidden: false, busy: true, notify: true, sort_order: 0, local_only: false }
    const rota = { ...base, id: url, url, calendar_id: 990401, name: 'Rota', color: '#1565C0', read_only: true, shareable: false,
      shared: { kind: 'group', group: 'family', access: 'freebusy', editor: false, group_calendar_id: 81 } }
    await route.fulfill({ response: r, json: [...list, rota] }).catch(() => undefined)
  })
  const d = new Date()
  d.setUTCHours(0, 0, 0, 0)
  d.setUTCDate(d.getUTCDate() + 3)
  const day = d.toISOString().slice(0, 10)
  const block = {
    id: 990402, uid: 'mailr-busy-89abcdef0123456789abcdef@mailr', occurrence: `${day}T12:00:00Z`, recurrence_id: null,
    summary: 'Busy', location: '', description: '', start: `${day}T12:00:00Z`, end: `${day}T13:00:00Z`, all_day: false,
    status: '', transparent: false, class: '', color: null, calendar_color: '#1565C0', categories: [], url: null, sequence: 0,
    recurring: false, is_override: false, rrule: null, organizer: null, organizer_name: '', attendees: 0, attendee_list: [],
    reminders: [], no_reminders: true, calendar: url, calendar_id: 990401, href: `${url}mailr-busy-89ab.ics`,
    local_only: false, phone_busy: false, phone_details: false, shared_busy: true,
  }
  await page.route('**/api/v1/calendar/agenda*', async (route) => {
    const r = await route.fetch().catch(() => null)
    if (!r) return
    const body = (await r.json().catch(() => null)) as { events: unknown[] } | unknown[] | null
    if (!body) return
    const json = Array.isArray(body) ? [...body, block] : { ...body, events: [...body.events, block] }
    await route.fulfill({ response: r, json }).catch(() => undefined)
  })
  await page.goto(`/calendar/day/${day}`)
  await expect(page.getByTestId('cal-shared')).toContainText('Shared with the family group · free/busy only')
  const busy = page.locator('.cal-block', { hasText: 'Busy' })
  await expect(busy).toHaveCount(1)
  await expect(busy).toHaveClass(/\bro\b/)
  await busy.click()
  const detail = page.getByTestId('cal-detail')
  await expect(detail.getByRole('heading', { name: 'Busy' })).toBeVisible()
  await expect(detail.getByTestId('shared-busy-note')).toHaveText('The family group calendar shares only its busy times with you. Its editors see the titles and details.')
  await expect(detail.getByRole('button', { name: /^Edit/ })).toHaveCount(0)
  await expect(detail.getByRole('button', { name: /^Delete/ })).toHaveCount(0)
})
