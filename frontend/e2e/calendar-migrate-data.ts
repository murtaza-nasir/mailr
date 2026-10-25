// Mocked answers for "Copy into my Mailr calendar…" (backend calendar/migrate.py), shared by
// e2e/calendar-migrate.spec.ts and the docs screenshots. The e2e database
// keeps built-in calendars off and runs no Davis, so the connectors and the job are mocked.
import type { Page } from '@playwright/test'

export const BUILTIN_ID = 9100
export const SOURCE_ID = 9200

export const BUILTIN = {
  id: BUILTIN_ID, kind: 'caldav', name: 'Mailr calendar', builtin: true, preset: null, mode: 'mirror', data_class: 'normal',
  config: {}, has_password: false, retention_days: 14, delete_upstream: false, enabled: true, status: 'idle',
  status_detail: '2 calendars', last_sync_at: new Date().toISOString(),
}

export const SOURCE = {
  id: SOURCE_ID, kind: 'caldav', name: 'Davis', builtin: false, preset: null, mode: 'mirror', data_class: 'normal',
  config: { url: 'https://cal.example.com/dav/', username: 'rosa' }, has_password: true, retention_days: 14,
  delete_upstream: false, enabled: true, status: 'idle', status_detail: '5 calendars', last_sync_at: new Date().toISOString(),
}

const col = (name: string, kind: 'calendar' | 'addressbook', total: number, extra: Record<string, unknown> = {}) => ({
  source: `https://cal.example.com/dav/calendars/rosa/${name.toLowerCase()}/`, kind, name, color: null,
  components: ['VEVENT'], target: `http://stalwart:8080/dav/cal/rosa/${name.toLowerCase()}/`, target_name: name,
  action: 'create', total, copied: total, skipped: 0, conflicts: 0, replaced: 0, failed: 0, partial: false,
  conflict_uids: [], failures: [], ...extra,
})

const NOT_COPIED = [
  { kind: 'delegate', name: 'Kai Berg (kai)', detail: 'Delegate on the other server (can edit).',
    how: 'In Mailr, open Share… on each calendar and add this person with Edit. If they have no Mailr account, ask your administrator for one.' },
  { kind: 'share', name: 'Work', detail: 'Shared on the other server with kai@northwind.example (can edit).',
    how: 'Share the copy in Mailr with the same people.' },
  { kind: 'shared_with_you', name: 'Team', detail: 'Shared with you by kai on the other server.',
    how: 'Ask the owner to share it with you in Mailr.' },
  { kind: 'subscription', name: 'Public holidays', detail: 'A subscription to https://calendars.example.net/holidays.ics.',
    how: 'Subscriptions stay on the other server. Keep that account connected for it, or download the calendar from its link and import the file.' },
]

export function result(dry: boolean, overwrite = false) {
  const personal = col('Personal', 'calendar', 412, { color: '#3f51b5', action: 'existing', target_name: 'Calendar',
    copied: 409, skipped: 2, conflicts: dry || !overwrite ? 1 : 0, replaced: !dry && overwrite ? 1 : 0, conflict_uids: ['dentist@northwind.example'] })
  const cols = [
    personal,
    col('Work', 'calendar', 86, { color: '#0b8043' }),
    col('Chores', 'calendar', 23, { components: ['VTODO'], color: '#e67c73' }),
    col('Contacts', 'addressbook', 154, dry ? {} : { copied: 153, failed: 1,
      failures: [{ uid: 'card-17', item: '', reason: 'Too large for the Mailr calendar (512 KiB per item, attachments included).' }] }),
  ]
  const sum = (k: 'copied' | 'skipped' | 'conflicts' | 'replaced' | 'failed' | 'total') => cols.reduce((n, c) => n + (c[k] as number), 0)
  return {
    dry_run: dry, overwrite, collections: cols, not_migrated: NOT_COPIED, errors: [],
    totals: { collections: cols.length, created_collections: 3, total: sum('total'), copied: sum('copied'), skipped: sum('skipped'),
      conflicts: sum('conflicts'), replaced: sum('replaced'), failed: sum('failed') },
  }
}

export interface MigrationMock {
  starts: unknown[]
  cutovers: unknown[]
  polls: number
}

/** The connector list with the Mailr calendar and a Davis account; the migration routes. */
export async function mockMigration(page: Page, opts: { builtin?: boolean; finish?: boolean } = {}): Promise<MigrationMock> {
  const state: MigrationMock = { starts: [], cutovers: [], polls: 0 }
  let overwrite = false
  await page.route('**/api/v1/connectors', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const r = await route.fetch()
    const list = (await r.json()) as unknown[]
    await route.fulfill({ response: r, json: [...(opts.builtin === false ? [] : [BUILTIN]), SOURCE, ...list] })
  })
  await page.route(`**/api/v1/connectors/${SOURCE_ID}/migration**`, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    if (url.pathname.endsWith('/cutover')) {
      state.cutovers.push(req.postDataJSON())
      return route.fulfill({ json: { hidden: 3, defaults: { calendar: 'x', task_list: 'y' } } })
    }
    if (req.method() === 'POST') {
      const body = req.postDataJSON() as { dry_run: boolean; overwrite: boolean }
      state.starts.push(body)
      overwrite = body.overwrite
      return route.fulfill({ json: { job: body.dry_run ? 501 : 502, status: 'queued', dry_run: body.dry_run, done: 0, total: null, current: '', result: null, error: '' } })
    }
    const job = Number(url.pathname.split('/').pop())
    state.polls += 1
    const dry = job === 501
    if (state.polls === 1 || opts.finish === false) {
      return route.fulfill({ json: { job, status: 'running', dry_run: dry, done: 230, total: 675, current: 'Personal', result: null, error: '' } })
    }
    return route.fulfill({ json: { job, status: 'done', dry_run: dry, done: 675, total: 675, current: '', result: result(dry, overwrite), error: '' } })
  })
  return state
}
