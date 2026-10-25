// ACT-SURFACES: web.reader
// Opening an email: the body appears in one step at its real
// height, never as a small frame that then grows; a row hovered (or reached with j/k) is
// prefetched, so opening it waits for no request; a body opened again takes its remembered height
// at once. Every theme's reader (Classic's full page and reading pane, Office's pane, Focus, Desk,
// Cards), dark where the theme's extra look is dark, and a phone.
import type { Page } from '@playwright/test'
import { conv, expect, ingest, test, type Seed } from './fixtures'
import { openApp } from './helpers'
import { THEMES, schemes } from './matrix'

const PIXEL = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

/** A newsletter of about 30 KB of HTML (the size of production's bodies): tables, inline styles,
 *  an image with its size given, no remote content (nothing to fetch, so heights are stable). */
function newsletter(subject: string, marker: string): string {
  const rows = Array.from({ length: 48 }, (_, k) =>
    `<tr><td style="padding:12px 24px;font-family:Helvetica,Arial,sans-serif;font-size:15px;line-height:22px;color:#333">` +
    `<a href="https://shop.e2e.example.org/item/${k}" style="color:#0a66c2">Item ${k}</a> ` +
    `<b>${marker} ${k}</b>: a short description of the product, with some <i>emphasis</i>, and a price of $${k}.99 ` +
    `for the season's collection, available while stocks last.</td></tr>`).join('')
  const html = `<html><body><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4"><tr><td align="center">` +
    `<table width="600" style="background:#fff;max-width:100%"><tr><td><img src="data:image/png;base64,${PIXEL}" width="600" height="180" alt="Banner" style="display:block;width:100%;height:auto"></td></tr>` +
    `${rows}</table></td></tr></table></body></html>`
  const date = new Date().toUTCString().replace('GMT', '+0000')
  return [
    'From: Dana Whitfield <dana.whitfield@e2e.example.org>', 'To: me@example.com', `Subject: ${subject}`,
    `Message-ID: <${marker}-${Date.now()}-${Math.random().toString(36).slice(2)}@shop.e2e.example.org>`, `Date: ${date}`,
    'MIME-Version: 1.0', 'Content-Type: text/html; charset=utf-8', '', html, '',
  ].join('\r\n')
}

async function seedLetters(seeded: Seed, tag: string, n = 3) {
  const out: { subject: string; marker: string; cid: number; mid: number }[] = []
  for (let i = 0; i < n; i++) {
    const subject = `E2E Letter ${tag} ${i}`
    const marker = `Mk${tag}${i}`
    const ids = await ingest(seeded.email, newsletter(subject, marker))
    out.push({ subject, marker, cid: ids.conversation_id, mid: ids.message_id })
  }
  return out
}

interface Sample { t: number; shown: boolean; h: number; bottom: number }

/** Records, every frame from the next pointerdown on, whether the body with `marker` is visible,
 *  its frame's height and where its bottom edge is (what sits below moves with it), and the
 *  layout shifts the browser reports. */
async function startSampling(page: Page, marker: string, frameSelector?: string) {
  await page.evaluate(({ marker, frameSelector }) => {
    const w = window as unknown as Record<string, unknown>
    const samples: Sample[] = []
    const shifts: { t: number; v: number; nodes: string[] }[] = []
    w.__samples = samples
    w.__shifts = shifts
    w.__click = 0
    new PerformanceObserver((l) => {
      for (const e of l.getEntries() as (PerformanceEntry & { value: number; sources?: { node?: Node | null }[] })[])
        shifts.push({ t: e.startTime, v: e.value, nodes: (e.sources ?? []).map((x) => (x.node instanceof Element ? `${x.node.tagName.toLowerCase()}.${x.node.className}` : String(x.node?.nodeName))) })
    }).observe({ type: 'layout-shift' })
    document.addEventListener('pointerdown', () => {
      if (w.__click) return
      w.__click = performance.now()
      const tick = () => {
        const frame = frameSelector
          ? document.querySelector<HTMLIFrameElement>(frameSelector)
          : [...document.querySelectorAll<HTMLIFrameElement>('iframe[title="Message body"]')]
            .find((f) => f.contentDocument?.body?.textContent?.includes(marker))
        if (frame) {
          const r = frame.getBoundingClientRect()
          const shown = getComputedStyle(frame).visibility === 'visible' && r.height > 0
          samples.push({ t: performance.now(), shown, h: Math.round(r.height), bottom: Math.round(r.bottom) })
        } else samples.push({ t: performance.now(), shown: false, h: 0, bottom: 0 })
        if (performance.now() - (w.__click as number) < 2500) requestAnimationFrame(tick)
      }
      requestAnimationFrame(tick)
    }, { capture: true })
  }, { marker, frameSelector })
}

async function results(page: Page) {
  await page.waitForTimeout(2600)
  const { samples, shifts, click } = await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>
    return { samples: w.__samples as Sample[], shifts: w.__shifts as { t: number; v: number; nodes: string[] }[], click: w.__click as number }
  })
  const first = samples.findIndex((s) => s.shown)
  expect(first, 'the body never became visible').toBeGreaterThanOrEqual(0)
  const after = samples.slice(first)
  const visibleAt = samples[first].t
  const nextFrame = samples[first + 1]?.t ?? visibleAt
  const later = shifts.filter((s) => s.t >= nextFrame)
  // The first frame from which the body is visible and its height no longer changes.
  const last = samples[samples.length - 1]
  let final = samples.length - 1
  while (final > 0 && samples[final - 1].shown && samples[final - 1].h === last.h) final--
  return {
    clickToVisible: Math.round(visibleAt - click),
    clickToFinal: Math.round(samples[final].t - click),
    // How far the body's bottom edge (and so everything under it) moved after it first appeared.
    moved: Math.max(...after.map((s) => Math.abs(s.bottom - after[0].bottom))),
    grew: Math.max(...after.map((s) => Math.abs(s.h - after[0].h))),
    // Shifts after the frame that first painted the body. That frame's own shift (what sits below
    // moving from the placeholder's height to the body's) is the one step allowed; a body opened
    // before has its remembered height and makes none.
    shift: later.reduce((a, s) => a + s.v, 0),
    shifted: later.flatMap((s) => s.nodes),
    firstHeight: after[0].h,
  }
}

function report(name: string, r: Awaited<ReturnType<typeof results>>) {
  console.log(`READER-OPEN ${name}: click→visible ${r.clickToVisible} ms, click→final height ${r.clickToFinal} ms, moved ${r.moved}px, grew ${r.grew}px, CLS after visible ${r.shift.toFixed(4)}${r.shifted.length ? ` (${[...new Set(r.shifted)].join(', ')})` : ''}`)
}

const row = (page: Page, subject: string) => page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()

for (const theme of THEMES) {
  for (const scheme of schemes(theme)) {
    test(`${theme} ${scheme}: the body appears once, at its height`, async ({ page, seeded }) => {
      const [, letter] = await seedLetters(seeded, `${theme}${scheme}`)
      await openApp(page, '/mail/inbox', { theme, dark: scheme === 'dark', ready: row(page, letter.subject) })
      await startSampling(page, letter.marker)
      await row(page, letter.subject).click()
      const r = await results(page)
      report(`${theme} ${scheme}`, r)
      expect(r.moved).toBeLessThanOrEqual(2)
      expect(r.grew).toBeLessThanOrEqual(2)
      expect(r.shift).toBeLessThan(0.01)
      expect(r.firstHeight).toBeGreaterThan(400)
    })
  }
}

test('classic reading pane: the body appears once, at its height', async ({ page, seeded }) => {
  const r0 = await page.request.patch('/api/v1/me/settings', { data: { reading_pane: 'right' } })
  expect(r0.ok()).toBeTruthy()
  const [, letter] = await seedLetters(seeded, 'pane')
  await openApp(page, '/mail/inbox', { ready: row(page, letter.subject) })
  await startSampling(page, letter.marker)
  await row(page, letter.subject).click()
  const r = await results(page)
  report('classic pane', r)
  expect(r.moved).toBeLessThanOrEqual(2)
  expect(r.shift).toBeLessThan(0.01)
})

test('phone: the body appears once, at its height', async ({ page, seeded }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const [, letter] = await seedLetters(seeded, 'phone')
  await openApp(page, '/mail/inbox', { ready: row(page, letter.subject) })
  await startSampling(page, letter.marker)
  await row(page, letter.subject).click()
  const r = await results(page)
  report('phone', r)
  expect(r.moved).toBeLessThanOrEqual(2)
  expect(r.shift).toBeLessThan(0.01)
})

/** The public ids the web app uses for a conversation and its (only) message. */
async function ids(page: Page, subject: string) {
  const c = await conv(page.request, subject)
  const d = await (await page.request.get(`/api/v1/conversations/${c.id}`)).json() as { messages: { id: number }[] }
  return { cid: c.id, mid: d.messages[0].id, unread: c.unread_count }
}

function bodyRequests(page: Page) {
  const seen: string[] = []
  page.on('request', (r) => {
    const m = /\/api\/v1\/messages\/([^/]+)\/body/.exec(r.url())
    if (m) seen.push(m[1])
  })
  return seen
}

test('a hovered row opens with its body already fetched, and stays unread until opened', async ({ page, seeded }) => {
  const r0 = await page.request.patch('/api/v1/me/settings', { data: { reading_pane: 'right' } })
  expect(r0.ok()).toBeTruthy()
  const letters = await seedLetters(seeded, 'hover')
  const letter = letters[1]
  const { mid } = await ids(page, letter.subject)
  const bodies = bodyRequests(page)
  await openApp(page, '/mail/inbox', { ready: row(page, letter.subject) })
  const fetched = page.waitForResponse((r) => r.url().includes(`/messages/${mid}/body`) && r.ok())
  await row(page, letter.subject).hover()
  await fetched
  expect(bodies).toEqual([String(mid)])
  // Prefetching marked nothing read.
  expect((await conv(page.request, letter.subject)).unread_count).toBe(1)

  const before = bodies.length
  await startSampling(page, letter.marker)
  await row(page, letter.subject).click()
  const r = await results(page)
  report('hovered then opened', r)
  // The opened body came from the cache: after the click, only the neighbours' bodies are asked for.
  expect(bodies.slice(before)).not.toContain(String(mid))
  expect(r.moved).toBeLessThanOrEqual(2)
  // Opening it marks it read.
  await expect.poll(async () => (await conv(page.request, letter.subject)).unread_count).toBe(0)
})

test('the keyboard cursor and the open conversation\'s neighbours are prefetched', async ({ page, seeded }) => {
  const r0 = await page.request.patch('/api/v1/me/settings', { data: { reading_pane: 'right' } })
  expect(r0.ok()).toBeTruthy()
  const letters = await seedLetters(seeded, 'keys')
  const [m0, m1, m2] = await Promise.all(letters.map(async (l) => String((await ids(page, l.subject)).mid)))
  const bodies = bodyRequests(page)
  // The list is newest first: letter 2, letter 1, letter 0. j puts the cursor on the first row.
  await openApp(page, '/mail/inbox', { ready: row(page, letters[2].subject) })
  await page.keyboard.press('j')
  await expect.poll(() => bodies, { timeout: 10_000 }).toContain(m2)
  expect(bodies).not.toContain(m1)
  // Opening the middle one fetches its neighbours.
  await row(page, letters[1].subject).click()
  await expect(page.getByRole('heading', { name: letters[1].subject })).toBeVisible()
  await expect.poll(() => bodies, { timeout: 10_000 }).toEqual(expect.arrayContaining([m0, m1, m2]))
  // Nothing was marked read but the open one.
  await expect.poll(async () => (await conv(page.request, letters[1].subject)).unread_count).toBe(0)
  expect((await conv(page.request, letters[0].subject)).unread_count).toBe(1)
  expect((await conv(page.request, letters[2].subject)).unread_count).toBe(1)
})

test('a body opened again takes its remembered height before it appears', async ({ page, seeded }) => {
  const r0 = await page.request.patch('/api/v1/me/settings', { data: { reading_pane: 'right' } })
  expect(r0.ok()).toBeTruthy()
  const [a, b] = await seedLetters(seeded, 'again', 2)
  await openApp(page, '/mail/inbox', { ready: row(page, a.subject) })
  await row(page, a.subject).click()
  const frame = page.locator('iframe[title="Message body"][data-shown]')
  await expect(frame).toBeVisible()
  await row(page, b.subject).click()
  await expect(page.getByRole('heading', { name: b.subject })).toBeVisible()
  // The frame from the moment it is in the page, before its document has loaded.
  const { mid } = await ids(page, a.subject)
  await startSampling(page, a.marker, `[data-message-id="${mid}"] iframe[title="Message body"]`)
  await row(page, a.subject).click()
  const r = await results(page)
  report('reopened', r)
  const samples = await page.evaluate(() => (window as unknown as { __samples: Sample[] }).__samples)
  // From the first frame the body's frame exists, it has its final height, hidden or not.
  const present = samples.filter((s) => s.h > 0)
  const heightsSeen = present.map((s) => `${s.h}${s.shown ? '' : ' (hidden)'}`)
  expect(Math.max(...present.map((s) => Math.abs(s.h - present[present.length - 1].h))), [...new Set(heightsSeen)].join(', ')).toBeLessThanOrEqual(2)
})
