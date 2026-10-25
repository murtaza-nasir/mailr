// Small helpers that many specs had their own copy of: open a page in a theme, open a conversation,
// make a task or a note through the API. Fixtures (the seeded user, ingest, apiSettled) stay in
// fixtures.ts.
import type { APIRequestContext, Locator, Page } from '@playwright/test'
import { expect } from './fixtures'

/** Opens `path`, optionally in a theme (the page fixture starts in classic) and a colour scheme
 *  (reduced motion with it, as the theme specs use), and waits for `ready` when given. */
export async function openApp(page: Page, path: string, opts: { theme?: string; dark?: boolean; ready?: Locator } = {}) {
  if (opts.theme) await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), opts.theme)
  if (opts.dark !== undefined) await page.emulateMedia({ colorScheme: opts.dark ? 'dark' : 'light', reducedMotion: 'reduce' })
  await page.goto(path)
  if (opts.ready) await expect(opts.ready).toBeVisible()
}

/** Opens the list `view` and the conversation with this subject from it; waits for its heading
 *  unless `heading: false`. */
export async function openConversation(page: Page, subject: string, opts: { view?: string; heading?: boolean } = {}) {
  await page.goto(`/mail/${opts.view ?? 'inbox'}`)
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first().click()
  if (opts.heading !== false) await expect(page.getByRole('heading', { name: subject })).toBeVisible()
}

/** A task made through the API (POST /tasks); the answer is the task as the server holds it. */
export async function mkTask<T = { uid: string; summary: string }>(request: APIRequestContext, body: Record<string, unknown>): Promise<T> {
  const r = await request.post('/api/v1/tasks', { data: body })
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()) as T
}

/** A note made through the API (POST /notes). */
export async function mkNote<N = { id: string }>(request: APIRequestContext, body: Record<string, unknown>): Promise<N> {
  const r = await request.post('/api/v1/notes', { data: body })
  expect(r.ok(), await r.text()).toBeTruthy()
  return ((await r.json()) as { note: N }).note
}

/** A note as the API holds it now. */
export async function getNote<N = { id: string }>(request: APIRequestContext, id: string): Promise<N> {
  const r = await request.get(`/api/v1/notes/${id}`)
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()) as N
}
