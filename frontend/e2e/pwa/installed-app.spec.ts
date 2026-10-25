// The installed app against the production build (vite preview; playwright.config.ts projects
// pixel-pwa and iphone-pwa): a valid manifest with its icons, Apple touch icons and start-up
// images; the service worker precaching the build and controlling the page; a cold start and a
// cached conversation offline; the offline action queue surviving a restart and replaying when the
// connection is back; sign-out clearing this device's mail.
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from '../fixtures'
import { drag, isIPhone, row, rows } from '../phone/phone'

/** Width and height from a PNG's IHDR chunk. */
function pngSize(b: Buffer): [number, number] {
  expect(b.subarray(1, 4).toString()).toBe('PNG')
  return [b.readUInt32BE(16), b.readUInt32BE(20)]
}

async function png(request: APIRequestContext, url: string): Promise<[number, number]> {
  const r = await request.get(url)
  expect(r.status(), url).toBe(200)
  return pngSize(await r.body())
}

async function controlled(page: Page) {
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 20_000 })
}

test('the manifest, icons, Apple metas and start-up images are valid', async ({ page }) => {
  const r = await page.request.get('/manifest.webmanifest')
  expect(r.ok()).toBeTruthy()
  const m = await r.json()
  expect(m).toMatchObject({ name: 'Mailr', short_name: 'Mailr', start_url: '/mail/inbox', scope: '/', display: 'standalone', id: '/mail/inbox' })
  expect(m.theme_color).toMatch(/^#[0-9a-f]{6}$/i)
  expect(m.background_color).toMatch(/^#[0-9a-f]{6}$/i)
  const icons = m.icons as { src: string; sizes: string; purpose?: string }[]
  for (const want of ['192x192', '512x512']) {
    expect(icons.some((i) => i.sizes === want && (i.purpose ?? 'any') === 'any')).toBe(true)
    expect(icons.some((i) => i.sizes === want && i.purpose === 'maskable')).toBe(true)
  }
  for (const i of icons) expect((await png(page.request, i.src)).join('x')).toBe(i.sizes)
  expect((m.shortcuts as { name: string }[]).map((s) => s.name)).toEqual(['Compose', 'Calendar', 'Tasks'])
  for (const s of m.shortcuts as { url: string; icons: { src: string; sizes: string }[] }[]) {
    expect(s.url.startsWith('/')).toBe(true)
    expect((await png(page.request, s.icons[0].src)).join('x')).toBe(s.icons[0].sizes)
  }

  const html = await (await page.request.get('/mail/inbox')).text()
  expect(html).toContain('<meta name="apple-mobile-web-app-capable" content="yes"')
  expect(html).toContain('<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"')
  expect(html).toContain('<meta name="apple-mobile-web-app-title" content="Mailr"')
  expect(html).toMatch(/<meta name="theme-color" content="#[0-9a-f]{6}" media="\(prefers-color-scheme: dark\)"/)
  expect(html).toContain('viewport-fit=cover')
  for (const [, size, href] of html.matchAll(/<link rel="apple-touch-icon"(?: sizes="(\d+)x\d+")? href="([^"]+)"/g)) {
    const [w] = await png(page.request, href)
    if (size) expect(w).toBe(Number(size))
  }
  // Every start-up image exists and has the size its media query names.
  const splash = [...html.matchAll(/<link rel="apple-touch-startup-image" media="([^"]+)" href="([^"]+)"/g)]
  expect(splash.length).toBeGreaterThanOrEqual(40)
  for (const [, media, href] of splash) {
    const dw = Number(/device-width: (\d+)px/.exec(media)![1])
    const dh = Number(/device-height: (\d+)px/.exec(media)![1])
    const ratio = Number(/pixel-ratio: (\d)/.exec(media)![1])
    const landscape = media.includes('landscape')
    const [w, h] = await png(page.request, href)
    expect([w, h], href).toEqual(landscape ? [dh * ratio, dw * ratio] : [dw * ratio, dh * ratio])
  }
})

test('the service worker precaches the build and controls the app', async ({ page }) => {
  await page.goto('/mail/inbox')
  await expect(rows(page).first()).toBeVisible()
  await controlled(page)
  const state = await page.evaluate(async () => {
    const keys = await caches.keys()
    const shell = keys.find((k) => k.startsWith('mailr-shell-') && k !== 'mailr-shell-dev')
    const cached = shell ? (await (await caches.open(shell)).keys()).map((r) => new URL(r.url).pathname) : []
    const assets = [...document.querySelectorAll('script[src], link[rel="stylesheet"], link[rel="modulepreload"]')]
      .map((e) => new URL((e as HTMLScriptElement).src || (e as HTMLLinkElement).href).pathname)
      .filter((p) => p.startsWith('/assets/'))
    return { keys, shell, cached, assets, waiting: !!(await navigator.serviceWorker.getRegistration())?.waiting }
  })
  expect(state.shell).toBeTruthy()
  expect(state.cached).toContain('/index.html')
  for (const a of state.assets) expect(state.cached).toContain(a)
  // Every lazy app is there too (Calendar, Tasks, Settings open offline).
  expect(state.cached.some((p) => /\/assets\/CalendarApp-/.test(p))).toBe(true)
  expect(state.cached.some((p) => /\/assets\/SettingsPage-/.test(p))).toBe(true)
  // The signed-in user's mail cache.
  expect(state.keys.some((k) => k.startsWith('mailr-data-'))).toBe(true)
  // A first install never waits for a reload.
  expect(state.waiting).toBe(false)
})

test('the installed app leaves /help to the server: the user guide, not the app', async ({ page }) => {
  const root = path.resolve(import.meta.dirname, '../../..')
  if (!fs.existsSync(path.join(root, 'backend/mailr/help/index.html'))) {
    execFileSync(path.join(root, 'tools/build-help'), { cwd: root, stdio: 'inherit', timeout: 120_000 })
  }
  await page.goto('/mail/inbox')
  await controlled(page)
  await page.goto('/help/user-guide/inbox/')
  await expect(page.getByRole('heading', { level: 1, name: 'The inbox' })).toBeVisible()
  await expect(page.locator('#root')).toHaveCount(0)
})

test('offline: the app starts and a conversation read before opens; one never opened says so', async ({ page, context }, info) => {
  await page.goto('/mail/inbox')
  await controlled(page)
  await row(page, 'E2E Read me').click()
  await expect(page.frameLocator('iframe[title="Message body"]').last().getByText('Body of E2E Read me.')).toBeVisible()
  await page.goBack()
  await expect(row(page, 'E2E Star me')).toBeVisible()
  // Let the worker finish writing what it fetched.
  await page.waitForTimeout(500)

  // The conversation, its body and the list are in this user's cache.
  const kept = await page.evaluate(async () => {
    const out: string[] = []
    for (const k of await caches.keys()) if (k.startsWith('mailr-data-')) for (const r of await (await caches.open(k)).keys()) out.push(new URL(r.url).pathname)
    return out
  })
  expect(kept).toContain('/api/v1/auth/session')
  expect(kept.some((p) => /^\/api\/v1\/conversations\/\d+$/.test(p))).toBe(true)
  expect(kept.some((p) => /^\/api\/v1\/messages\/\d+\/body$/.test(p))).toBe(true)
  expect(kept).toContain('/api/v1/conversations')
  // Playwright's WebKit cannot load a page offline, even from a service worker (an internal
  // error); Safari can. The cold start is checked in Chromium.
  test.skip(isIPhone(info.project.name), 'offline page loads are Chromium-only in Playwright')

  await context.setOffline(true)
  await page.reload()
  // The app starts from this device: the shell from the worker, the list from its cache.
  await expect(row(page, 'E2E Read me')).toBeVisible()
  await row(page, 'E2E Read me').click()
  await expect(page.frameLocator('iframe[title="Message body"]').last().getByText('Body of E2E Read me.')).toBeVisible()
  await page.goBack()
  await row(page, 'E2E Important me').click()
  await expect(page.locator('.conv-offline')).toContainText(/You're offline|Can't reach Mailr/)
  await context.setOffline(false)
})

// The first visit's lists load before the worker takes over; the page asks for them again through
// it (lib/serviceWorker.ts warmCache), also the ones still loading then, so the restart offline
// shows the kept rows (the tabbed inbox's Primary list was missed, 8 Oct 2026).
test('offline changes wait on the device, survive a restart and are sent when back online', async ({ page, context }, info) => {
  await page.goto('/mail/inbox')
  await controlled(page)
  await expect(row(page, 'E2E Move me')).toBeVisible()
  await page.waitForTimeout(500)
  await context.setOffline(true)
  const width = page.viewportSize()!.width
  await drag(row(page, 'E2E Move me'), width * 0.6, 0)
  await expect(page.getByText("Queued until you're back online.")).toBeVisible()
  await expect(rows(page).filter({ hasText: 'E2E Move me' })).toHaveCount(0)
  await expect(page.getByTestId('pending-chip')).toContainText('1 pending change')

  // Closed and opened again, still offline: the change is still waiting and still applied
  // (Chromium: Playwright's WebKit cannot load a page offline).
  if (!isIPhone(info.project.name)) await page.reload()
  await expect(row(page, 'E2E Read me')).toBeVisible()
  await expect(rows(page).filter({ hasText: 'E2E Move me' })).toHaveCount(0)
  await page.getByTestId('pending-chip').click()
  await expect(page.getByTestId('pending-list')).toContainText('Archive · 1 conversation')
  await page.getByTestId('pending-sheet').getByRole('button', { name: 'Close' }).click()
  await context.setOffline(false)
  await page.evaluate(() => window.dispatchEvent(new Event('online')))
  await expect(page.getByTestId('pending-chip')).toHaveCount(0, { timeout: 20_000 })
  await expect.poll(async () => {
    const r = await page.request.get('/api/v1/conversations', { params: { view: 'inbox', limit: '100' } })
    return ((await r.json()).items as { subject: string }[]).some((c) => c.subject === 'E2E Move me')
  }).toBe(false)
})

test('signing out deletes the mail kept on this device', async ({ page }) => {
  await page.goto('/mail/inbox')
  await controlled(page)
  await expect(row(page, 'E2E Read me')).toBeVisible()
  await expect.poll(() => page.evaluate(async () => (await caches.keys()).some((k) => k.startsWith('mailr-data-')))).toBe(true)
  await page.getByRole('button', { name: /^Account:/ }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page).toHaveURL(/\/login/)
  await expect.poll(() => page.evaluate(async () => (await caches.keys()).some((k) => k.startsWith('mailr-data-')))).toBe(false)
})
