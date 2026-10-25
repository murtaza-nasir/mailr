// Calendar test data for the calendar-*.spec.ts files: a week of events around today, written
// through the API into the seeded user's dev-Radicale calendar, and theme helpers.
import type { APIRequestContext, Page } from '@playwright/test'
import { connectCalendar, expect, type Seed } from './fixtures'

import type { Theme } from './matrix'

export { THEMES, type Theme } from './matrix'

const pad = (n: number) => String(n).padStart(2, '0')

/** A local date (the browser and the test share a zone) as YYYY-MM-DD. */
export function isoDay(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** This week's Monday at local midnight. */
export function monday(now = new Date()): Date {
  const d = new Date(now)
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7))
  return d
}

/** Local wall time on a day of this week (0 = Monday) as an instant string. */
export function at(day: number, hour: number, minute = 0): string {
  const d = monday()
  d.setDate(d.getDate() + day)
  d.setHours(hour, minute, 0, 0)
  return d.toISOString()
}

export function dayOf(day: number): string {
  const d = monday()
  d.setDate(d.getDate() + day)
  return isoDay(d)
}

export interface Created { uid: string; summary: string; start: string; etag?: string | null }

export async function createEvent(request: APIRequestContext, body: Record<string, unknown>): Promise<Created> {
  const r = await request.post('/api/v1/calendar/events', { data: body })
  expect(r.ok(), await r.text()).toBeTruthy()
  return (await r.json()) as Created
}

/** Connects the calendar and writes a week of events (overlaps, all-day, multi-day, a series). */
export async function seedWeek(request: APIRequestContext, seeded: Seed): Promise<Record<string, Created>> {
  await connectCalendar(request, seeded)
  const out: Record<string, Created> = {}
  // Timed events are written in the browser's zone, so a weekly series keeps its wall time.
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  const add = async (key: string, body: Record<string, unknown>) => {
    out[key] = await createEvent(request, body.all_day ? body : { tz, ...body })
  }
  await add('sync', { summary: 'Team sync', start: at(0, 9), end: at(0, 10), recurrence: 'FREQ=WEEKLY;COUNT=8' })
  await add('review', { summary: 'Design review', start: at(1, 10), end: at(1, 11, 30), location: 'Room 204' })
  await add('overlapA', { summary: 'Hiring panel', start: at(1, 10, 30), end: at(1, 11) })
  await add('overlapB', { summary: 'Vendor call', start: at(1, 10, 45), end: at(1, 12) })
  await add('conf', { summary: 'Analytics conference', start: dayOf(2), end: dayOf(5), all_day: true, location: 'Chicago' })
  await add('budget', { summary: 'Budget review', start: at(3, 14), end: at(3, 15), url: 'https://meet.jit.si/mailr-budget', description: 'Q4 numbers. Agenda: https://example.org/agenda' })
  await add('quick', { summary: 'Quick call', start: at(3, 16), end: at(3, 16, 20) })
  await add('lunch', { summary: 'Lunch with Dana', start: at(4, 12, 30), end: at(4, 13, 30), status: 'TENTATIVE', location: 'Café Luna' })
  await add('hike', { summary: 'Hiking', start: dayOf(5), end: dayOf(6), all_day: true })
  await add('focus', { summary: 'Writing block', start: at(2, 13), end: at(2, 16), color: '#0b8043' })
  return out
}

/** Starts the browser's clock at a wall time of this week (0 = Monday), so a spec that reads
 *  "tomorrow" or splits past from upcoming gives the same answer on any day. The clock runs on. */
export async function pinNow(page: Page, day: number, hour: number) {
  await page.clock.install({ time: new Date(at(day, hour)) })
}

/** Opens a page in a theme, light or dark. */
export async function openIn(page: Page, theme: Theme, path: string, dark = false) {
  await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
  await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light', reducedMotion: 'reduce' })
  await page.goto(path)
}

/** Every event in the agenda between two days, from the API. */
export async function agenda(request: APIRequestContext, fromDay = dayOf(-7), toDay = dayOf(21)) {
  const j = await (await request.get('/api/v1/calendar/agenda', { params: { from: fromDay, to: toDay } })).json()
  return (j.events ?? []) as { uid: string; summary: string; start: string; end: string; all_day: boolean; location: string; recurring: boolean; rrule: string | null; reminders: number[]; no_reminders?: boolean; attendee_list: { email: string }[] }[]
}

/** Scrolls the time grid so `hour` is in view and returns the page point of a slot. */
export async function slotPoint(page: Page, day: string, hour: number, minute = 0): Promise<{ x: number; y: number }> {
  const col = page.locator(`.cal-col[data-day="${day}"]`)
  await col.waitFor()
  await page.evaluate((h) => {
    const sc = document.querySelector('.cal-tg-scroll') as HTMLElement
    const hh = parseFloat(getComputedStyle(document.querySelector('.cal-tg')!).getPropertyValue('--hour-h'))
    sc.scrollTop = Math.max(0, h * hh - 120)
  }, hour)
  await page.waitForTimeout(80)
  const box = (await col.boundingBox())!
  const hourH = await page.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.cal-tg')!).getPropertyValue('--hour-h')))
  return { x: box.x + box.width / 2, y: box.y + (hour + minute / 60) * hourH }
}

/** Drags with the mouse from one point to another in small steps. */
export async function drag(page: Page, from: { x: number; y: number }, to: { x: number; y: number }, opts: { alt?: boolean } = {}) {
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  if (opts.alt) await page.keyboard.down('Alt')
  await page.mouse.move(from.x + (to.x - from.x) / 3, from.y + (to.y - from.y) / 3, { steps: 4 })
  await page.mouse.move(to.x, to.y, { steps: 8 })
  await page.mouse.up()
  if (opts.alt) await page.keyboard.up('Alt')
}

/** The local wall time of an API instant, "HH:MM". */
export function hhmm(iso: string): string {
  const d = new Date(iso)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}
