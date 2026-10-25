// ACT-SURFACES: web.recordings
// Participants by person in the Recordings app (participant.merge, web.recordings). Speakr is never
// contacted: /api/v1/speakr is a Playwright route mock that resolves participant strings the way
// participants.py does (the user's full name and first name are You; an alias joins a name to
// another; Separate removes it), so the list, its counts and the filter are checked against it.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const CID = 3

const at = (daysAgo: number, h: number) => {
  const d = new Date()
  d.setDate(d.getDate() - daysAgo)
  d.setHours(h, 0, 0, 0)
  return d.toISOString()
}

const RAW: [number, string, string[]][] = [
  [31, 'Strategy review', ['Rowan hale', 'Ross Turner', 'Dana Smith']],
  [32, 'Strategy follow-up', ['Rowan', 'Ross']],
  [33, 'Advising', ['Rowan', 'Dana']],
  [34, 'Budget', ['Rowan hale', 'Rowan', 'Dana Smith']],
]

const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, ' ')

/** The mock server: aliases and separated names change what each string resolves to. */
async function mockSpeakr(page: Page) {
  const aliases = new Map<string, { target: string; label: string }>()
  const separated = new Set<string>()
  const dismissed = new Set<string>()
  const calls: { path: string; body: Record<string, unknown> }[] = []
  const resolve = (raw: string) => {
    const n = norm(raw)
    if (n === 'rowan hale') return { key: 'me', label: 'You', me: true, how: 'me', email: 'me@x.example' }
    if (n === 'rowan' && !separated.has(n)) return { key: 'me', label: 'You', me: true, how: 'me_first', email: 'me@x.example' }
    if (n === 'dana smith') return { key: 'email:dana@x.example', label: 'Dana Smith', me: false, how: 'people', email: 'dana@x.example' }
    const a = aliases.get(n)
    if (a) return { key: a.target, label: a.label, me: a.target === 'me', how: 'alias', email: null }
    return { key: `name:${n}`, label: raw, me: false, how: 'raw', email: null }
  }
  const items = () => RAW.map(([rid, title, participants], i) => ({
    connector_id: CID, recording_id: rid, title, meeting_date: at(i, 10), created_at: at(i, 10), duration_s: 1800,
    participants, tags: [], folder: 'Meetings', status: 'completed', has_transcript: true, has_summary: true,
    audio_available: true, is_archived: false, local_only: true, url: '', event_link: null,
    people: participants.map((name) => ({ name, ...resolve(name) })),
  }))
  await page.route('**/api/v1/speakr/**', async (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname.replace('/api/v1/speakr', '')
    const body = (req.postDataJSON() ?? {}) as Record<string, string>
    if (req.method() === 'POST') calls.push({ path, body })
    if (path === '/status') return route.fulfill({ json: { connected: true, connectors: [{ id: CID, name: 'Speakr', base_url: 'https://speakr.example' }] } })
    if (path === '/recordings') return route.fulfill({ json: { items: items(), more: false } })
    if (path === '/folders') return route.fulfill({ json: { connector_id: CID, folders: [] } })
    if (path === '/tags') return route.fulfill({ json: { connector_id: CID, tags: [] } })
    if (path === '/participants') {
      const suggestions = !aliases.has('ross') && !dismissed.has('ross')
        ? [{ connector_id: CID, name: 'ross', label: 'Ross', count: 1, into: 'name:ross turner', into_label: 'Ross Turner', into_key: 'name:ross turner' }]
        : []
      return route.fulfill({ json: { suggestions, aliases: [], write_back: [] } })
    }
    if (path === '/participants/merge') {
      const into = String(body.into)
      const label = into === 'me' ? 'You' : into === 'email:dana@x.example' ? 'Dana Smith' : into.startsWith('name:ross') ? 'Ross Turner' : into
      const names = body.name ? [String(body.name)] : RAW.flatMap(([, , ps]) => ps).filter((x) => resolve(x).key === body.key)
      for (const n of names) aliases.set(norm(n), { target: into, label })
      return route.fulfill({ json: { merged: [...new Set(names)], skipped: [], into, into_label: label, write_back: [] } })
    }
    if (path === '/participants/separate') {
      const n = norm(String(body.name))
      if (!aliases.delete(n)) separated.add(n)
      return route.fulfill({ json: { separated: 1, name: body.name } })
    }
    if (path === '/participants/dismiss') {
      dismissed.add(norm(String(body.name)))
      return route.fulfill({ json: { ok: true } })
    }
    return route.fulfill({ status: 404, json: { detail: `unmocked ${req.method()} ${path}` } })
  })
  return calls
}

test('participant.merge: Rowan and Rowan hale are You; a suggested merge for Ross; Separate undoes it', async ({ page }) => {
  const calls = await mockSpeakr(page)
  await page.goto('/recordings')
  const ppl = page.getByTestId('recordings-sidebar').getByRole('group', { name: 'Participants' })
  const person = (key: string) => ppl.locator(`[data-testid="participant-row"][data-key="${key}"]`)
  const rows = page.getByTestId('recording-row')
  await expect(rows).toHaveCount(4)

  // The user's full name and first name are one row, You, first, counted once per recording.
  await expect(ppl.getByTestId('participant-row').first()).toHaveAttribute('data-key', 'me')
  await expect(person('me')).toContainText('You')
  await expect(person('me')).toContainText('4')
  await expect(ppl.getByTestId('participant-row').filter({ hasText: /^Rowan/ })).toHaveCount(0)
  // A recording with both strings shows You once.
  const budget = rows.filter({ hasText: 'Budget' })
  await expect(budget.locator('.rx-name')).toHaveText(['You', 'Dana Smith'])
  // The filter by You keeps every recording with either string.
  await person('me').getByRole('button', { name: /You/ }).click()
  await expect(rows).toHaveCount(4)
  await person('me').getByRole('button', { name: /You/ }).click()

  // The suggestion: Merge Ross into Ross Turner.
  const sug = ppl.getByTestId('participant-suggestion')
  await expect(sug).toContainText('Merge Ross into Ross Turner?')
  await expect(person('name:ross')).toBeVisible()
  await sug.getByRole('button', { name: 'Merge' }).click()
  await expect(sug).toHaveCount(0)
  expect(calls.find((c) => c.path === '/participants/merge')?.body).toEqual({ connector_id: CID, name: 'ross', into: 'name:ross turner' })
  await expect(person('name:ross')).toHaveCount(0)
  await expect(person('name:ross turner')).toContainText('2')
  await person('name:ross turner').getByRole('button', { name: /Ross Turner/ }).click()
  await expect(rows).toHaveCount(2)
  await page.getByLabel('Remove Ross Turner').click()

  // Same person as…: Dana into Dana Smith, through the picker.
  await person('name:dana').hover()
  await person('name:dana').getByRole('button', { name: 'Merge or separate' }).click()
  await page.getByRole('menuitem', { name: 'Same person as…' }).click()
  const dlg = page.getByRole('dialog', { name: /Dana is the same person as/ })
  await dlg.getByLabel('Search participants').fill('dana')
  await dlg.getByRole('button', { name: /Dana Smith/ }).click()
  await expect(person('name:dana')).toHaveCount(0)
  await expect(person('email:dana@x.example')).toContainText('3')

  // Separate: Ross stands on its own again.
  await person('name:ross turner').hover()
  await person('name:ross turner').getByRole('button', { name: 'Merge or separate' }).click()
  await page.getByRole('menuitem', { name: 'Separate “Ross”' }).click()
  expect(calls.filter((c) => c.path === '/participants/separate').map((c) => c.body)).toEqual([{ name: 'Ross' }])
  await expect(person('name:ross')).toContainText('1')
  await expect(person('name:ross turner')).toContainText('1')
})
