// The recording sheet's People tab (web.recording, speaker.map). Mailr's
// /api/v1/speakr routes and the People search are Playwright route mocks; Speakr is never contacted.
// Covers: the user's own speaker shown as You with no buttons; the top three candidates as chips with
// addresses, organizations and hints; Confirm on a named speaker stored under the connector-wide key
// (all recordings); Someone else… searching People; Not them in this recording on a global mapping.
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const CID = 3
const RID = 12

interface Call { method: string; path: string; body: unknown }

const RECORDING = {
  connector_id: CID, recording_id: RID, title: 'Restructuring the Information Systems and AI Curriculum',
  meeting_date: new Date(Date.now() - 86_400_000).toISOString(), duration_s: 3600,
  participants: ['Mi', 'Aiden', 'Mateo', 'Ross', 'Rowan hale'], tags: [], folder: '', status: 'completed',
  has_transcript: true, has_summary: true, audio_available: true, is_archived: false, local_only: true,
  url: 'https://speakr.example/recordings/12', summary: '## Notes', notes: '', links: [], candidates: [],
}

const PEOPLE = {
  me: { email: 'me@example.com', name: 'Rowan hale' },
  guests: [],
  speakers: [
    { label: 'SPEAKER_00', name: 'Rowan hale', key: 'name:rowan hale', map_key: 'name:rowan hale', override_key: 'rec:12:name:rowan hale',
      me: true, applies: 'all', speaker_id: null, person: { email: 'me@example.com', name: 'Rowan hale', organization: '', me: true }, suggestions: [], more: 0 },
    { label: 'SPEAKER_01', name: 'Mateo', key: 'name:mateo', map_key: 'name:mateo', override_key: 'rec:12:name:mateo', me: false, applies: 'all',
      speaker_id: null, person: null, more: 2, suggestions: [
        { email: 'mateo@lakeside.example', name: 'Mateo Yilmaz', organization: 'Lakeside', emails: ['mateo@lakeside.example', 'mateo.y@gmail.com'], hints: ['in the linked meeting'], why: 'in the linked meeting' },
        { email: 'mateo.k@acme.example', name: 'Mateo Kaya', organization: 'Acme', emails: ['mateo.k@acme.example'], hints: ['emailed you about this'], why: 'emailed you about this' },
        { email: 'mateo.d@x.example', name: 'Mateo Demir', organization: 'x', emails: ['mateo.d@x.example'], hints: [], why: 'same first name' },
      ] },
    { label: 'SPEAKER_02', name: '', key: 'label:12:speaker_02', map_key: 'label:12:speaker_02', override_key: null, me: false, applies: 'recording',
      speaker_id: null, person: null, more: 0, suggestions: [] },
    { label: 'SPEAKER_03', name: 'Ross', key: 'name:ross', map_key: 'name:ross', override_key: 'rec:12:name:ross', me: false, applies: 'all',
      speaker_id: null, person: { email: 'ross@lakeside.example', name: 'Ross Geller', organization: 'Lakeside', me: false }, suggestions: [], more: 0 },
  ],
}

async function mock(page: Page): Promise<Call[]> {
  const calls: Call[] = []
  await page.route('**/api/v1/speakr/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace('/api/v1/speakr', '')
    const call = { method: req.method(), path: path + url.search, body: req.postData() ? req.postDataJSON() : null }
    calls.push(call)
    const key = `${req.method()} ${path}`
    if (path === '/status') return route.fulfill({ json: { connected: true, connectors: [{ id: CID, name: 'Speakr', base_url: 'https://speakr.example' }] } })
    if (key === `GET /recordings/${CID}/${RID}`) return route.fulfill({ json: RECORDING })
    if (key === `GET /recordings/${CID}/${RID}/summary`) return route.fulfill({ json: { summary: '## Notes', has_summary: true } })
    if (key === `GET /recordings/${CID}/${RID}/people`) return route.fulfill({ json: PEOPLE })
    if (key === `POST /speakers/${CID}/person`) return route.fulfill({ json: { state: 'confirmed', applies: 'all', ...(call.body as object) } })
    if (key === `DELETE /speakers/${CID}/person`) return route.fulfill({ json: { ok: true, removed: 0 } })
    return route.fulfill({ status: 404, json: { detail: `unmocked ${key}` } })
  })
  await page.route((u) => u.pathname === '/api/v1/people', (route) => route.fulfill({ json: [
    { email: 'aiden.k@x.example', name: 'Aiden Karimov', organization: 'Xco', two_way: true },
  ] }))
  return calls
}

test('People tab: You without buttons, top-3 chips, Someone else…, Not them in this recording (speaker.map)', async ({ page }) => {
  const calls = await mock(page)
  await page.goto(`/recordings/${CID}/${RID}`)
  const view = page.getByTestId('recording-sheet')
  await view.getByRole('tab', { name: 'People' }).click()

  // ACT speaker.map: the user's own speaker is You, with nothing to confirm.
  const me = view.getByTestId('rec-speaker').filter({ hasText: 'Rowan hale' })
  await expect(me.getByTestId('speaker-me')).toContainText('You')
  await expect(me.getByRole('button')).toHaveCount(0)

  // Three chips with addresses, organizations and why; one person with two addresses.
  const mateo = view.getByTestId('rec-speaker').filter({ hasText: 'Mateo' })
  const chips = mateo.getByTestId('speaker-suggestion')
  await expect(chips).toHaveCount(3)
  await expect(chips.nth(0)).toContainText('Mateo Yilmaz')
  await expect(chips.nth(0)).toContainText('mateo@lakeside.example · 2 addresses · Lakeside')
  await expect(chips.nth(0)).toContainText('in the linked meeting')
  await expect(chips.nth(1)).toContainText('mateo.k@acme.example · Acme')
  await expect(mateo).toContainText('Applies to all recordings with this speaker.')

  // Confirm stores the choice under the connector-wide key: every recording with Mateo.
  await chips.nth(0).getByRole('button', { name: 'This is Mateo Yilmaz (mateo@lakeside.example)' }).click()
  await expect.poll(() => calls.find((x) => x.method === 'POST')?.body).toEqual(
    // speaker_id and recording_id let the mapping follow a rename in Speakr; a name-only speaker has none.
    { key: 'name:mateo', speaker_name: 'Mateo', email: 'mateo@lakeside.example', name: 'Mateo Yilmaz', speaker_id: null, recording_id: RID })
  await expect(page.getByText('Mateo is Mateo Yilmaz. Applies to all recordings with this speaker.')).toBeVisible()

  // Someone else… searches People; an unnamed label applies to this recording only.
  const label = view.getByTestId('rec-speaker').filter({ hasText: 'SPEAKER_02' })
  await label.getByRole('button', { name: 'Choose a person…' }).click()
  const dlg = page.getByRole('dialog', { name: 'Who is SPEAKER_02?' })
  await dlg.getByLabel('Search people').fill('aiden')
  await dlg.getByRole('button', { name: /Aiden Karimov/ }).click()
  await expect.poll(() => calls.filter((x) => x.method === 'POST').map((x) => x.body)).toContainEqual(
    { key: 'label:12:speaker_02', speaker_name: 'SPEAKER_02', email: 'aiden.k@x.example', name: 'Aiden Karimov', speaker_id: null, recording_id: RID })

  // A global mapping: Not them in this recording uses the recording's override key.
  const ross = view.getByTestId('rec-speaker').filter({ hasText: 'Ross' })
  await expect(ross.getByTestId('speaker-person')).toContainText('ross@lakeside.example · Lakeside · every recording')
  await expect(ross.getByRole('link', { name: 'Open the card of Ross Geller' })).toHaveAttribute('href', '/settings/mail/people?person=ross%40lakeside.example')
  await ross.getByRole('button', { name: 'Not Ross Geller in this recording' }).click()
  await expect.poll(() => calls.find((x) => x.method === 'DELETE')?.path).toBe(
    `/speakers/${CID}/person?key=${encodeURIComponent('rec:12:name:ross')}&email=${encodeURIComponent('ross@lakeside.example')}`)
})
