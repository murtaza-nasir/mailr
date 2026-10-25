// The right-to-left sweep: every theme's main screens in ar-XB,
// the right-to-left pseudo-language, on a desktop (e2e/rtl.spec.ts) and a phone
// (e2e/phone/rtl-phone.spec.ts). Each screen is checked for a page that scrolls sideways and its
// screenshot is kept (SWEEP_SHOTS=<dir> writes them all to one folder, to look through).
//
// The data: a week of events, a note and a list with Arabic and English text, a task, a table with
// rows, and an answer from Ambrose with a citation (its chat stream is answered in the page).
import type { Page, TestInfo } from '@playwright/test'
import { expect, type Seed } from '../fixtures'
import { seedWeek } from '../calendar-data'
import { pseudoPage, sweepScreen } from './sweep'

const AR = 'مرحبا يا فريق! الاجتماع غداً.'

/** Notes, a list, a task and a table, some of them in Arabic: written text keeps its own direction. */
export async function seedRtlData(page: Page, seeded: Seed) {
  await seedWeek(page.request, seeded)
  const post = async (url: string, data: unknown) => {
    const r = await page.request.post(url, { data })
    expect(r.ok(), await r.text()).toBeTruthy()
    return r.json()
  }
  await post('/api/v1/notes', { kind: 'text', title: 'ملاحظات الاجتماع', body: `${AR}\nThe report is ready.` })
  await post('/api/v1/notes', { kind: 'checklist', title: 'Groceries', lines: 'milk\nחלב וביצים!\nbread' })
  await post('/api/v1/tasks', { summary: 'File the report' })
  await post('/api/v1/tasks', { summary: 'להתקשר לדנה!' })
  const t = (await post('/api/v1/tables', { name: 'Trip costs', columns: [
    { name: 'Item', type: 'text' }, { name: 'Cost', type: 'number' }, { name: 'Paid', type: 'checkbox' },
  ] })) as { table: { id: string; columns: { id: string; name: string }[] } }
  const c = (n: string) => t.table.columns.find((x) => x.name === n)!.id
  await post(`/api/v1/tables/${t.table.id}/rows`, { rows: [
    { cells: { [c('Item')]: 'Hotel', [c('Cost')]: '200', [c('Paid')]: true } },
    { cells: { [c('Item')]: 'فندق', [c('Cost')]: '40' } },
  ], parse: true })
  return { table: t.table.id }
}

/** Ambrose's chat stream, answered in the page: one answer with a citation to a seeded email. */
export async function cannedAmbrose(page: Page) {
  await page.addInitScript(() => {
    const real = window.fetch.bind(window)
    const enc = new TextEncoder()
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (url.includes('/api/v1/agent/chat') && init?.method === 'POST' && !url.includes('/stop')) {
        const ev = (e: string, d: unknown) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`
        const body = ev('session', { session_id: 'e2e-rtl', user_message_id: 1, seq: 1 })
          + ev('final', { seq: 2, segment: 0, content: 'Your invoice was paid on 1 August [1](mailr-cite:1). مرحبا!\n\n- First point\n- نقطة ثانية', citations: [
            { n: 1, kind: 'mail', conversation_id: 1, message_id: 1, subject: 'Your invoice is available', from: 'Billing Team',
              date: '2026-08-01T10:00:00Z', quote: 'Your balance was charged to your card', verified: true },
          ] })
          + ev('done', { seq: 3 })
        return Promise.resolve(new Response(new Blob([enc.encode(body)]).stream(), { status: 200, headers: { 'Content-Type': 'text/event-stream' } }))
      }
      return real(input, init)
    }
  })
}

const settle = (page: Page) => page.waitForLoadState('networkidle').catch(() => undefined)

/** Every screen of the sweep, in one theme. */
export async function rtlSweep(page: Page, info: TestInfo, theme: string, phone: boolean, data: { table: string }) {
  const shot = async (name: string) => {
    await settle(page)
    await sweepScreen(page, info, `${theme}-${name}`, { lang: 'ar-XB' })
  }
  await pseudoPage(page, theme, 'ar-XB')
  await cannedAmbrose(page)

  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [data-id]').first()).toBeVisible()
  await shot('inbox')

  await page.locator('[data-mail-list] [data-id]').first().click()
  await expect(page.locator('article.msg').first()).toBeVisible()
  await shot('reader')

  await page.goto('/calendar/week')
  await page.getByTestId('calendar-app').waitFor()
  await page.waitForSelector('[data-testid="cal-event"]', { timeout: 15_000 }).catch(() => undefined)
  await shot('calendar-week')

  await page.goto('/calendar/today')
  await page.getByTestId('calendar-app').waitFor()
  await shot('calendar-today')

  await page.goto('/tasks/all')
  await expect(page.locator('.tk-title').first()).toBeVisible()
  await shot('tasks')

  await page.goto('/notes')
  await expect(page.locator('.nt-card-title').first()).toBeVisible()
  await shot('notes')

  await page.goto(`/tables/t/${data.table}`)
  await expect(page.getByTestId('table-app')).toBeVisible()
  await shot('table')

  await page.goto('/settings/you/region')
  await shot('settings')

  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [data-id]').first()).toBeVisible()
  // The top bar's Ambrose button, or on a phone the last tab of the bottom bar.
  if (phone) await page.locator('[data-tab="assistant"]:visible').first().click()
  else await page.locator('[data-ambrose]:visible').first().click()
  await expect(page.locator('.chat')).toBeVisible()
  const input = page.locator('.chat-input textarea')
  await input.fill('When was my invoice paid?')
  await input.press('Enter')
  const chip = page.locator('.cite-chip').first()
  await expect(chip).toBeVisible()
  if (!phone) await chip.hover()
  else await chip.click()
  await page.waitForTimeout(300)
  await shot('ambrose')
  await page.keyboard.press('Escape')

  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [data-id]').first()).toBeVisible()
  const bell = page.getByTestId('waiting-bell').first()
  if (await bell.isVisible()) {
    await bell.click()
    await page.waitForTimeout(300)
    await shot('bell')
    await page.keyboard.press('Escape')
  }

  if (!phone) {
    await page.keyboard.press('Control+k')
    await expect(page.locator('.palette-item').first()).toBeVisible()
    await shot('palette')
  }

  // Compose last: its window stays open across pages.
  await page.keyboard.press('Escape')
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [data-id]').first()).toBeVisible()
  await page.locator('body').press('c')
  await expect(page.locator('.ProseMirror:visible').first()).toBeVisible()
  await page.locator('.ProseMirror:visible').first().click()
  await page.keyboard.type('مرحبا! The report is ready.')
  await shot('compose')

}
