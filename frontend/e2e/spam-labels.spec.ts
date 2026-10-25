// Labels never apply to spam (8 Oct 2026: a promotion in Spam carried the "GitHub" label and showed
// in the GitHub inbox tab). An inbox tab built on a label, its count and the label's own count leave
// out mail in Spam and Trash, as Gmail does; `in:spam label:…` still finds it.
// Covers: web.list.
import { conv, expect, test } from './fixtures'

const row = (page: import('@playwright/test').Page, subject: string) =>
  page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject })

test('a label tab and its count leave Spam and Trash out; in:spam finds them', async ({ page, seeded }) => {
  const label = seeded.labels['E2E Label']
  const act = async (subject: string, action: string, extra: Record<string, unknown> = {}) => {
    const c = await conv(page.request, subject)
    const r = await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action, ...extra } })
    expect(r.ok(), await r.text()).toBeTruthy()
  }
  await act('E2E Label me', 'add_labels', { label_ids: [label] })
  await act('E2E Not spam me', 'add_labels', { label_ids: [label] })   // in Spam
  await act('E2E Delete me', 'trash')
  await act('E2E Delete me', 'add_labels', { label_ids: [label] })     // in Trash

  const tabs = [
    { name: 'Inbox', icon: 'inbox', indicators: ['unread'], sections: [{ title: 'All', base: 'inbox' }] },
    { name: 'Labelled', icon: 'tag', indicators: ['all'], claims: true, sections: [{ title: 'All', base: `label:${label}` }] },
  ]
  const put = await page.request.put('/api/v1/layouts', { data: { active: 'spamtabs', layouts: [{ name: 'Spamtabs', tabs }] } })
  expect(put.ok(), await put.text()).toBeTruthy()

  await page.goto('/mail/inbox')
  const tab = page.getByRole('tab', { name: /^Labelled/ })
  await expect(tab.locator('[data-indicator="all"]')).toHaveText('1')
  await tab.click()
  await expect(row(page, 'E2E Label me')).toHaveCount(1)
  await expect(row(page, 'E2E Not spam me')).toHaveCount(0)
  await expect(row(page, 'E2E Delete me')).toHaveCount(0)

  const counts = await (await page.request.get('/api/v1/counts')).json()
  expect(counts.views[`label:${label}`].total).toBe(1)
  const search = async (q: string) => ((await (await page.request.get('/api/v1/conversations', { params: { view: 'all', q } })).json())
    .items as { subject: string }[]).map((c) => c.subject).sort()
  expect(await search('label:"E2E Label"')).toEqual(['E2E Label me'])
  expect(await search('in:spam label:"E2E Label"')).toEqual(['E2E Not spam me'])
  expect(await search('in:anywhere label:"E2E Label"')).toEqual(['E2E Delete me', 'E2E Label me', 'E2E Not spam me'])
})

// Report spam keeps your own labels aside (8 Oct 2026): a drop on Spam reports it, the label stops
// showing, and Not spam brings the conversation back with its label.
test('a drop on Spam keeps the label aside and Not spam brings it back', async ({ page, seeded }) => {
  const label = seeded.labels['E2E Label']
  const c = await conv(page.request, 'E2E Label me')
  const r = await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'add_labels', label_ids: [label] } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const inView = async (view: string) => ((await (await page.request.get('/api/v1/conversations', { params: { view } })).json())
    .items as { id: number }[]).some((x) => x.id === c.id)

  await page.goto('/mail/inbox')
  await expect(row(page, 'E2E Label me')).toBeVisible()
  // Spam sits under More in the Classic nav.
  const spam = page.locator('[data-drop-view="spam"]:not([data-favorite])').first()
  if (!(await spam.isVisible())) await page.locator('.sidebar .nav-item', { hasText: 'More' }).click()
  await expect(spam).toBeVisible()
  const done = page.waitForResponse((res) => res.url().includes('/conversations/actions') && res.request().method() === 'POST')
  await row(page, 'E2E Label me').dragTo(spam)
  expect((await done).ok()).toBeTruthy()
  await expect(row(page, 'E2E Label me')).toHaveCount(0)
  await expect.poll(() => inView('spam')).toBe(true)
  expect(await inView(`label:${label}`)).toBe(false)

  const back = await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'not_spam' } })
  expect(back.ok(), await back.text()).toBeTruthy()
  expect(await inView(`label:${label}`)).toBe(true)
  expect(await inView('inbox')).toBe(true)
})
