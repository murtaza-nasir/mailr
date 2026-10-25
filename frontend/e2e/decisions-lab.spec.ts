// Settings → Assistant → Decisions lab: the run shows each model's answers beside the saved
// decision and the rules; "Re-run on the inbox" starts the saving re-run. Model calls are stubbed
// (backend/tests/test_decisions_lab.py covers the server).
import { expect, test } from './fixtures'

test('the decisions lab shows answers side by side and starts a re-run', async ({ page }) => {
  await page.route('**/api/v1/intel/lab/endpoints', (r) => r.fulfill({ json: [{ name: 'jev', adapter: 'jev', locality: 'hosted' }, { name: 'decider-local', adapter: 'openai', locality: 'local' }] }))
  let body: Record<string, unknown> = {}
  await page.route('**/api/v1/intel/lab', (r) => {
    body = r.request().postDataJSON()
    return r.fulfill({ json: {
      endpoints: [{ name: 'jev', adapter: 'jev', locality: 'hosted', model: 'jev-latest' }, { name: 'decider-local', adapter: 'openai', locality: 'local', model: 'decider-4b' }],
      question: 'Is this a bill?',
      items: [{
        message: { id: 1, conversation_id: 1, subject: 'Slides for Friday?', from: 'Dana', date: null, local_only: false, bulk: false, to_me: true },
        stored: { category: { value: 'primary', source: 'classifier' }, signals: [] },
        rules: { category: { value: 'primary', confidence: 0.7 }, learned: null, signals: { needs_reply: false, waiting_on: false, deadline: null, reason: '' }, actions: [] },
        models: {
          jev: { category: { value: 'primary', confidence: 0.92, ms: 240 }, signals: { needs_reply: true, asks_question: true, ms: 230 }, question: { answer: false, confidence: 0.8, reason: 'not a bill', ms: 210 } },
          'decider-local': { category: { value: 'updates', confidence: 0.75, ms: 90 }, signals: { needs_reply: true, asks_question: false, request: 'send the slides', ms: 120 }, question: { answer: false, confidence: 0.7, ms: 80 } },
        },
      }],
    } })
  })
  let reran = false
  await page.route('**/api/v1/intel/retriage', (r) => { reran = true; return r.fulfill({ json: { queued: 12, conversations: 12 } }) })

  await page.goto('/settings/assistant?part=lab')
  await page.getByRole('link', { name: 'Decisions lab' }).or(page.getByRole('button', { name: 'Decisions lab' })).first().click()
  await page.getByLabel('Question (optional)').fill('Is this a bill?')
  await page.getByRole('button', { name: 'Run', exact: true }).click()
  await expect(page.locator('.lab-item')).toContainText('Slides for Friday?')
  await expect(page.locator('.lab-table')).toContainText('needs reply, asks a question')
  await expect(page.locator('.lab-table')).toContainText('“send the slides”')
  await expect(page.locator('.lab-table')).toContainText('updates 75%')
  expect(body).toMatchObject({ q: 'in:inbox', question: 'Is this a bill?', endpoints: ['jev', 'decider-local'] })

  await page.getByRole('button', { name: 'Re-run on the inbox' }).click()
  await expect(page.getByText(/Re-running category and signals on 12/)).toBeVisible()
  expect(reran).toBe(true)
})
