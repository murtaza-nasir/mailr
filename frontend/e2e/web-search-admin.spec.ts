// ACT-SURFACES: web.settings
// settings.web_search: Settings → Admin → Web search. settings.page_readers: its Page readers section. Providers are instance-wide, so the admin routes are
// answered by an in-memory stand-in here (parallel specs share one server); tests/test_websearch.py covers
// the real routes, the key handling and the routing.
import type { Page, Route } from '@playwright/test'
import { accessTest as test, expect, signIn } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

interface Prov { id: number; name: string; purpose?: string; from_env?: boolean; kind: string; base_url: string; has_api_key: boolean; network: string; use_egress: boolean; priority: number; enabled: boolean; max_results: number; timeout_s: number; per_minute: number; per_day: number; used_today: number; last_ok_at: string | null; last_error: string | null }

const READER_KINDS = [
  { kind: 'browserless', label: 'browserless', needs_key: false, default_url: '', default_network: 'local', renders: true },
  { kind: 'firecrawl', label: 'Firecrawl', needs_key: false, default_url: 'https://api.firecrawl.dev', default_network: 'internet', renders: true },
  { kind: 'jina', label: 'Jina Reader', needs_key: false, default_url: 'https://r.jina.ai', default_network: 'internet', renders: true },
]

const KINDS = [
  { kind: 'exa', label: 'Exa', needs_key: true, default_url: 'https://api.exa.ai', default_network: 'internet', freshness: true },
  { kind: 'searxng', label: 'SearXNG', needs_key: false, default_url: '', default_network: 'local', freshness: true },
]

async function mockProviders(page: Page, seed: Prov[] = []) {
  const store: Prov[] = [...seed]
  const bodies: Record<string, unknown>[] = []
  let next = 1
  const results = [{ title: 'Self-hosting mail', url: 'https://docs.example.org/mail', site: 'docs.example.org', snippet: '' }]
  await page.route('**/api/v1/admin/websearch**', async (route: Route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace('/api/v1/admin/websearch', '')
    const method = req.method()
    if (method === 'GET' && path === '') {
      const sorted = [...store].sort((a, b) => a.priority - b.priority)
      return route.fulfill({ json: { providers: sorted.filter((x) => x.purpose !== 'reader'), readers: sorted.filter((x) => x.purpose === 'reader'), kinds: KINDS, reader_kinds: READER_KINDS, egress: { configured: true, required: false }, private_hosts: 'admins' } })
    }
    if (method === 'POST' && path === '/test') {
      const b = req.postDataJSON()
      bodies.push(b)
      if (b.purpose === 'reader' && b.use_egress === false) return route.fulfill({ json: { ok: true, via: 'lan', route: 'direct', status: 200, ms: 2100, page: { title: 'r/selfhosted', site: 'www.reddit.com', chars: 9120, text: 'A community for self-hosting.' } } })
      if (b.purpose === 'reader' && b.id) return route.fulfill({ json: { ok: false, error: 'the browser could not reach the egress proxy (egress:8888) (net::ERR_PROXY_CONNECTION_FAILED)', ms: 40 } })
      if (b.purpose === 'reader') return route.fulfill({ json: b.api_key ? { ok: true, via: 'lan', ms: 1800, page: { title: 'Email - Wikipedia', site: 'en.wikipedia.org', chars: 48211, text: 'Electronic mail is a method of transmitting messages…' } } : { ok: false, error: 'browserless refused the API key (HTTP 401): Bad or missing authentication.', ms: 30 } })
      return route.fulfill({ json: b.api_key === 'sk-wrong' ? { ok: false, error: 'Exa refused the API key (HTTP 401)', ms: 80 } : { ok: true, via: 'egress', ms: 120, results } })
    }
    if (method === 'POST' && path === '/providers') {
      const b = req.postDataJSON()
      bodies.push(b)
      const { api_key, ...rest } = b
      const p: Prov = { ...rest, id: next++, base_url: b.base_url || 'https://api.exa.ai', has_api_key: !!api_key, used_today: 0, last_ok_at: null, last_error: null }
      store.push(p)
      return route.fulfill({ json: p })
    }
    const m = /^\/providers\/(\d+)(\/test)?$/.exec(path)
    const p = m ? store.find((x) => x.id === Number(m[1])) : undefined
    if (!p) return route.fulfill({ status: 404, json: { detail: 'provider not found' } })
    if (m![2]) return route.fulfill({ json: { ok: true, via: 'egress', ms: 99, results } })
    if (method === 'PATCH') {
      const b = req.postDataJSON()
      bodies.push(b)
      Object.assign(p, b)
      return route.fulfill({ json: p })
    }
    if (method === 'DELETE') {
      store.splice(store.indexOf(p), 1)
      return route.fulfill({ json: { ok: true } })
    }
    return route.fallback()
  })
  return { store, bodies }
}

test('an admin adds, tests, switches off and deletes a web search provider', async ({ page, access }) => {
  await signIn(page, access.admin)
  const { store, bodies } = await mockProviders(page)
  await page.goto('/settings/admin/web-search')
  const section = page.locator('section.set-section').filter({ has: page.getByRole('heading', { name: 'Web search', exact: true }) })
  await expect(section).toContainText("No providers. Ambrose can't search the web until you add one.")

  await section.getByRole('button', { name: 'Add provider' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add web search provider' })
  await dialog.getByLabel('Service', { exact: true }).selectOption('exa')
  await expect(dialog.getByLabel('Name', { exact: true })).toHaveValue('Exa')
  await expect(dialog.getByRole('radio', { name: /On the internet/ })).toBeChecked()
  // A service that needs a key cannot be tested without one.
  await expect(dialog.getByRole('button', { name: 'Test' })).toBeDisabled()
  await dialog.getByLabel(/^API key/).fill('sk-wrong')
  await dialog.getByRole('button', { name: 'Test' }).click()
  await expect(dialog.getByTestId('web-test')).toContainText('Exa refused the API key (HTTP 401)')
  await dialog.getByLabel(/^API key/).fill('sk-right')
  await dialog.getByRole('button', { name: 'Test' }).click()
  await expect(dialog.getByTestId('web-test')).toContainText('1 result through the egress proxy')
  await expect(dialog.getByTestId('web-test')).toContainText('docs.example.org')
  await dialog.getByLabel('Searches a day (0: no limit)').fill('300')
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toBeHidden()
  expect(bodies.at(-1)).toMatchObject({ name: 'Exa', kind: 'exa', network: 'internet', use_egress: true, per_day: 300, api_key: 'sk-right' })

  const table = section.getByTestId('web-providers')
  await expect(table).toContainText('https://api.exa.ai · key saved')
  await expect(table).toContainText('Through the egress proxy')
  await expect(table).toContainText('0 of 300 today')
  await expect(section).not.toContainText('sk-right')

  await table.getByRole('button', { name: 'test' }).click()
  await expect(section.getByTestId('web-test')).toContainText('Test of Exa: 1 result through the egress proxy in 99 ms')

  // Edit: the saved key is kept unless a new one is typed.
  await table.getByRole('button', { name: 'edit' }).click()
  const edit = page.getByRole('dialog', { name: 'Edit web search provider' })
  await expect(edit.getByText('(empty keeps the saved key)')).toBeVisible()
  await edit.getByRole('button', { name: 'Save' }).click()
  await expect(edit).toBeHidden()
  expect(bodies.at(-1)).not.toHaveProperty('api_key')

  await table.getByRole('switch', { name: 'Exa enabled' }).click()
  await expect.poll(() => store[0]?.enabled).toBe(false)

  await table.getByRole('button', { name: 'delete' }).click()
  await page.getByRole('dialog', { name: 'Delete Exa?' }).getByRole('button', { name: 'Delete' }).click()
  await expect(section).toContainText('No providers.')
})

test('a self-hosted provider on the local network is reached directly', async ({ page, access }) => {
  await signIn(page, access.admin)
  const { bodies } = await mockProviders(page)
  await page.goto('/settings/admin/web-search')
  const section = page.locator('section.set-section').filter({ has: page.getByRole('heading', { name: 'Web search', exact: true }) })
  await section.getByRole('button', { name: 'Add provider' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add web search provider' })
  await dialog.getByLabel('Service', { exact: true }).selectOption('searxng')
  await expect(dialog.getByRole('radio', { name: /On my network/ })).toBeChecked()
  await expect(dialog.getByRole('checkbox', { name: /egress proxy/ })).toHaveCount(0)
  await dialog.getByLabel(/^Base URL/).fill('http://searxng.e2e.example:8080')
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toBeHidden()
  expect(bodies.at(-1)).toMatchObject({ kind: 'searxng', network: 'local', base_url: 'http://searxng.e2e.example:8080' })
  await expect(section.getByTestId('web-providers')).toContainText('On your network, reached directly')
})

test('an admin adds a browserless page reader after the built-in one', async ({ page, access }) => {
  await signIn(page, access.admin)
  const { bodies } = await mockProviders(page)
  await page.goto('/settings/admin/web-search')
  const section = page.locator('section.set-section').filter({ has: page.getByRole('heading', { name: 'Page readers', exact: true }) })
  const table = section.getByTestId('page-readers')
  await expect(table).toContainText('Built-in')
  await expect(table).toContainText('Always tried first')

  await section.getByRole('button', { name: 'Add reader' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add page reader' })
  await dialog.getByLabel('Service', { exact: true }).selectOption('jina')
  await expect(dialog.getByRole('radio', { name: /On the internet \(a third party\)/ })).toBeChecked()
  await expect(dialog).toContainText('It receives the address of every page it reads')
  await dialog.getByLabel('Service', { exact: true }).selectOption('browserless')
  await expect(dialog.getByRole('radio', { name: /On my network/ })).toBeChecked()
  await expect(dialog.getByRole('radio', { name: /^Through the egress proxy/ })).toBeChecked()
  await expect(dialog).toContainText('Every page is loaded through the egress proxy')
  await dialog.getByLabel('Name', { exact: true }).fill('Browser')
  await dialog.getByLabel(/^Base URL/).fill('http://browserless.e2e.example:3000')
  await dialog.getByRole('button', { name: 'Test' }).click()
  await expect(dialog.getByTestId('reader-test')).toContainText('Bad or missing authentication')
  await dialog.getByLabel(/^API key/).fill('bl-token')
  await dialog.getByRole('button', { name: 'Test' }).click()
  await expect(dialog.getByTestId('reader-test')).toContainText('“Email - Wikipedia”, 48211 characters, on your network')
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toBeHidden()
  expect(bodies.at(-1)).toMatchObject({ purpose: 'reader', kind: 'browserless', network: 'local', timeout_s: 30, api_key: 'bl-token' })
  await expect(table).toContainText('Browser')
  await expect(table).toContainText('self-hosted')
  await expect(section).not.toContainText('bl-token')
  // The web search table does not list readers.
  const search = page.locator('section.set-section').filter({ has: page.getByRole('heading', { name: 'Web search', exact: true }) })
  await expect(search).toContainText('No providers.')
})

test('a browserless reader can load pages directly from this network', async ({ page, access }) => {
  await signIn(page, access.admin)
  const { bodies } = await mockProviders(page)
  await page.goto('/settings/admin/web-search')
  const section = page.locator('section.set-section').filter({ has: page.getByRole('heading', { name: 'Page readers', exact: true }) })
  const table = section.getByTestId('page-readers')
  await section.getByRole('button', { name: 'Add reader' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add page reader' })
  await dialog.getByLabel('Service', { exact: true }).selectOption('browserless')
  await dialog.getByLabel(/^Base URL/).fill('http://browserless.e2e.example:3000')
  const route = dialog.getByTestId('reader-route')
  await route.getByRole('radio', { name: /^Directly from this network/ }).check()
  await expect(route).toContainText('Sites see your home address; nothing else changes.')
  await dialog.getByRole('button', { name: 'Test' }).click()
  await expect(dialog.getByTestId('reader-test')).toContainText('“r/selfhosted”, 9120 characters, directly from this network')
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toBeHidden()
  expect(bodies.at(-1)).toMatchObject({ purpose: 'reader', kind: 'browserless', network: 'local', use_egress: false })
  await expect(table.getByTestId('reader-direct')).toHaveText('direct')
  await expect(table).toContainText("By the browser, directly from your network's address")
  // Back to the egress proxy: the badge goes.
  await table.getByRole('button', { name: 'Edit' }).click()
  const edit = page.getByRole('dialog', { name: 'Edit page reader' })
  await expect(edit.getByRole('radio', { name: /^Directly from this network/ })).toBeChecked()
  await edit.getByRole('radio', { name: /^Through the egress proxy/ }).check()
  await edit.getByRole('button', { name: 'Save' }).click()
  await expect(edit).toBeHidden()
  expect(bodies.at(-1)).toMatchObject({ use_egress: true })
  await expect(table.getByTestId('reader-direct')).toHaveCount(0)
})

test('the server\'s own reader: route and order only, with the browser\'s own error', async ({ page, access }) => {
  await signIn(page, access.admin)
  const server: Prov = { id: 50, name: 'Browser (server)', purpose: 'reader', from_env: true, kind: 'browserless', base_url: 'http://browser:3000', has_api_key: true, network: 'local', use_egress: true, priority: 5, enabled: true, max_results: 10, timeout_s: 30, per_minute: 0, per_day: 0, used_today: 0, last_ok_at: null, last_error: null }
  const { bodies } = await mockProviders(page, [server])
  await page.goto('/settings/admin/web-search')
  const section = page.locator('section.set-section').filter({ has: page.getByRole('heading', { name: 'Page readers', exact: true }) })
  const table = section.getByTestId('page-readers')
  const row = table.getByRole('row').filter({ hasText: 'Browser (server)' })
  await expect(row).toContainText('Set up by the server (MAILR_BROWSER_URL)')
  await expect(row.getByRole('button', { name: 'Delete' })).toHaveCount(0)
  await row.getByRole('button', { name: 'Edit' }).click()
  const edit = page.getByRole('dialog', { name: 'Edit page reader' })
  await expect(edit.getByTestId('reader-from-env')).toContainText('a browser at http://browser:3000 (MAILR_BROWSER_URL)')
  await expect(edit.getByLabel('Service', { exact: true })).toHaveCount(0)
  await expect(edit.getByLabel(/^API key/)).toHaveCount(0)
  await edit.getByRole('button', { name: 'Test' }).click()
  await expect(edit.getByTestId('reader-test')).toContainText('the browser could not reach the egress proxy (egress:8888)')
  await edit.getByRole('radio', { name: /^Directly from this network/ }).check()
  await edit.getByRole('button', { name: 'Save' }).click()
  await expect(edit).toBeHidden()
  expect(bodies.at(-1)).toMatchObject({ use_egress: false, kind: 'browserless' })
  expect(bodies.at(-1)).not.toHaveProperty('api_key')
  await expect(row.getByTestId('reader-direct')).toHaveText('direct')
})
