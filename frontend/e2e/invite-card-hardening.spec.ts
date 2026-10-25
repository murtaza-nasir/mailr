// ACT-SURFACES: web.reader
// Calendar hardening in the reader's InviteCard:
// - a guest's proposal (COUNTER) shows who gets the updated invitation; one from an
//   unverified sender says so with its address, and Accept sends nothing until the user picks
//   "I trust this proposal" (event.accept_proposal, invite.counter);
// - an invitation whose own time zone was too costly to use says so (invite.view).
// The invitation summary is answered by the test; the message itself is ingested for real.
import type { Page } from '@playwright/test'
import { connectCalendar, expect, ingest, type Seed, shotsDir, test } from './fixtures'

const SHOTS = shotsDir('sec2-calendar')

const pad = (n: number) => String(n).padStart(2, '0')
function tomorrowAt(h: number) {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  d.setHours(h, 0, 0, 0)
  const ical = `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}00Z`
  return { ical, iso: d.toISOString() }
}

function proposalRaw(seeded: Seed, subject: string, uid: string, start: string, end: string) {
  const ics = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//E2E//EN', 'METHOD:COUNTER', 'BEGIN:VEVENT', `UID:${uid}`, 'SEQUENCE:0',
    `DTSTAMP:${start}`, `DTSTART:${start}`, `DTEND:${end}`, `SUMMARY:${subject}`, `ORGANIZER:mailto:${seeded.email}`,
    'ATTENDEE;PARTSTAT=TENTATIVE:mailto:dana@example.org', 'END:VEVENT', 'END:VCALENDAR'].join('\r\n')
  return ['From: Dana <dana@example.org>', `To: ${seeded.email}`, `Subject: ${subject}`, `Date: ${new Date().toUTCString()}`,
    `Message-ID: <${uid.replace(/@.*/, '')}-${Date.now()}@example.org>`, 'MIME-Version: 1.0', 'Content-Type: multipart/mixed; boundary="b1"', '',
    '--b1', 'Content-Type: text/plain; charset=utf-8', '', 'New time?', '',
    '--b1', 'Content-Type: text/calendar; charset=utf-8; method=COUNTER', '', ics, '', '--b1--', ''].join('\r\n')
}

async function openConv(page: Page, subject: string) {
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first().click()
  await expect(page.getByRole('heading', { name: subject })).toBeVisible()
}

test('web.reader: an unverified proposal shows its address and who gets mail, and moves nothing until trusted (event.accept_proposal, invite.counter)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const p = tomorrowAt(14)
  const uid = `counter-${Date.now()}@example.org`
  const subject = 'E2E Proposal check'
  await ingest(seeded.email, proposalRaw(seeded, subject, uid, p.ical, tomorrowAt(15).ical))
  const base = {
    uid, summary: subject, start: p.iso, end: tomorrowAt(15).iso, location: '', description: '', sequence: 0, recurrence_id: null,
    organizer: { email: seeded.email, name: 'E2E Tester' }, attendees: [{ email: 'dana@example.org', name: 'Dana', status: 'TENTATIVE' }],
    my_status: null, my_address: null, recurring: false, cancelled: false, in_calendar: true, conflicts: [], method: 'COUNTER',
  }
  const invite = {
    ...base,
    itip: { applied: false, detail: 'A proposal from an unverified sender (dana@example.org)', method: 'COUNTER', counter: true, verified: false,
      sender: 'dana@example.org', proposed: { start: p.iso, end: tomorrowAt(15).iso } },
    counter_check: { verified: false, sender: 'dana@example.org', notify: ['dana@example.org', 'evan@example.org'] },
  }
  await page.route(/\/api\/v1\/messages\/[^/]+\/invite$/, (r) => r.fulfill({ json: invite }))
  const posts: unknown[] = []
  await page.route(`**/api/v1/calendar/events/${encodeURIComponent(uid)}/counter/accept`, (r) => {
    posts.push(r.request().postDataJSON())
    return r.fulfill({ json: { ...base, start: p.iso } })
  })
  await openConv(page, subject)
  const card = page.getByTestId('invite-card')
  await expect(card.getByTestId('invite-proposal-unverified')).toContainText('dana@example.org')
  await expect(card.getByTestId('invite-proposal-notify')).toHaveText('If you accept, the new time is mailed to 2 guests: dana@example.org, evan@example.org.')
  await card.getByTestId('invite-proposal').getByRole('button', { name: 'Accept' }).click()
  await expect(card.getByTestId('invite-proposal-trust')).toBeVisible()
  expect(posts).toEqual([])  // the first click sends nothing
  await page.screenshot({ path: `${SHOTS}/proposal-unverified.png` })
  const accepted = page.waitForRequest((r) => r.url().includes('/counter/accept'))
  await card.getByRole('button', { name: 'I trust this proposal: move the meeting' }).click()
  expect((await accepted).postDataJSON()).toEqual({ message_id: expect.anything(), notify: ['dana@example.org', 'evan@example.org'], trust: true })
})

test('web.reader: a verified proposal shows who gets mail and accepts in one click; a replaced zone is named (event.accept_proposal, invite.view)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const p = tomorrowAt(14)
  const uid = `counter-ok-${Date.now()}@example.org`
  const subject = 'E2E Proposal verified'
  await ingest(seeded.email, proposalRaw(seeded, subject, uid, p.ical, tomorrowAt(15).ical))
  const invite = {
    uid, summary: subject, start: p.iso, end: tomorrowAt(15).iso, location: '', description: '', sequence: 0, recurrence_id: null,
    organizer: { email: seeded.email, name: 'E2E Tester' }, attendees: [{ email: 'dana@example.org', name: 'Dana', status: 'TENTATIVE' }],
    my_status: null, my_address: null, recurring: false, cancelled: false, in_calendar: true, conflicts: [], method: 'COUNTER',
    zones_replaced: ['Mallory Standard Zone'],
    itip: { applied: false, detail: 'Proposed a new time', method: 'COUNTER', counter: true, verified: true, sender: 'dana@example.org',
      proposed: { start: p.iso, end: tomorrowAt(15).iso } },
    counter_check: { verified: true, sender: 'dana@example.org', notify: ['dana@example.org'] },
  }
  await page.route(/\/api\/v1\/messages\/[^/]+\/invite$/, (r) => r.fulfill({ json: invite }))
  await page.route(`**/api/v1/calendar/events/${encodeURIComponent(uid)}/counter/accept`, (r) => r.fulfill({ json: { ...invite, start: p.iso } }))
  await openConv(page, subject)
  const card = page.getByTestId('invite-card')
  await expect(card.getByTestId('invite-zone-note')).toContainText('Mallory Standard Zone')
  await expect(card.getByTestId('invite-proposal-unverified')).toHaveCount(0)
  await expect(card.getByTestId('invite-proposal-notify')).toHaveText('If you accept, the new time is mailed to dana@example.org.')
  await page.screenshot({ path: `${SHOTS}/proposal-verified.png` })
  const accepted = page.waitForRequest((r) => r.url().includes('/counter/accept'))
  await card.getByTestId('invite-proposal').getByRole('button', { name: 'Accept' }).click()
  expect((await accepted).postDataJSON()).toEqual({ message_id: expect.anything(), notify: ['dana@example.org'] })
})
