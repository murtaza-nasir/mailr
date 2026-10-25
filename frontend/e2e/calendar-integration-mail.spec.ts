// ACT-SURFACES: web.editor web.reader
// Calendar and tasks in the reader and compose:
// InviteCard with its conflict line and mini timeline, an RSVP with a note, the ingest outcome
// ("Dana accepted — calendar updated"), an update's diff, a cancel with "Remove from calendar", a
// guest's proposal with Accept (invite.view, invite.apply_reply, invite.apply_update,
// invite.apply_cancel, invite.remove_cancelled, invite.link_existing, invite.counter, event.accept_proposal,
// event.decline_proposal, event.rsvp, event.change_suggestion); "Add to calendar" for a
// plain .ics file (event.import_ics); "Schedule a meeting" from a thread (mail.schedule_meeting,
// event.find_time on its Find-a-time tab);
// "Insert times" in a reply (event.insert_times); the conversation's links (event.open_email);
// and the message menu's mail.to_event / mail.to_task opening the shared editors. Messages with
// calendar parts are ingested for the seeded user; outcomes the ingest job would record are
// answered by the test.
import type { Page } from '@playwright/test'
import { connectCalendar, conv, E2E_AUTHSERV_ID, events, expect, ingest, type Seed, shotsDir, tasks, test } from './fixtures'

const SHOTS = shotsDir('integration')

/** Ingests one raw message into the seeded user's inbox; returns its ids. */

const pad = (n: number) => String(n).padStart(2, '0')
/** Tomorrow at a local hour, as an iCalendar UTC time and as an ISO instant. */
function tomorrowAt(h: number, m = 0) {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  d.setHours(h, m, 0, 0)
  const ical = `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}00Z`
  return { ical, iso: d.toISOString(), date: d }
}

/** The receiving server's verdict for Dana's domain, stamped with the e2e servers' trusted authserv-id. */
const DANA_VERIFIED = `Authentication-Results: ${E2E_AUTHSERV_ID}; dkim=pass header.d=example.org; dmarc=pass header.from=example.org`

function invitationRaw(seeded: Seed, o: { subject: string; uid: string; method?: string; start: string; end: string; location?: string; attendee?: boolean; verified?: boolean }) {
  const method = o.method ?? 'REQUEST'
  const ics = [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//E2E//EN', ...(method ? [`METHOD:${method}`] : []), 'BEGIN:VEVENT', `UID:${o.uid}`, 'SEQUENCE:0',
    `DTSTAMP:${o.start}`, `DTSTART:${o.start}`, `DTEND:${o.end}`, `SUMMARY:${o.subject}`, ...(o.location ? [`LOCATION:${o.location}`] : []),
    ...(o.attendee === false ? [] : ['ORGANIZER;CN=Dana Tester:mailto:dana@example.org', `ATTENDEE;CN=E2E Tester;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:${seeded.email}`,
      'ATTENDEE;CN=Bob;PARTSTAT=ACCEPTED:mailto:bob@example.org']),
    'END:VEVENT', 'END:VCALENDAR',
  ].join('\r\n')
  return [
    ...(o.verified ? [DANA_VERIFIED] : []),
    'From: Dana Tester <dana@example.org>', `To: ${seeded.email}`, `Subject: ${o.subject}`, `Date: ${new Date().toUTCString()}`,
    `Message-ID: <${o.uid.replace(/@.*/, '')}-${Date.now()}@example.org>`, 'MIME-Version: 1.0', 'Content-Type: multipart/mixed; boundary="b1"', '',
    '--b1', 'Content-Type: text/plain; charset=utf-8', '', `Details for ${o.subject}.`, '',
    '--b1', `Content-Type: text/calendar; charset=utf-8${method ? `; method=${method}` : ''}`, 'Content-Disposition: attachment; filename="invite.ics"', '', ics, '',
    '--b1--', '',
  ].join('\r\n')
}

async function openConv(page: Page, id: number, subject: string) {
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first().click()
  await expect(page.getByRole('heading', { name: subject })).toBeVisible()
  return id
}

test('web.reader: InviteCard shows the conflict and my day around the slot, and answers with a note (invite.view, event.rsvp)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const s = tomorrowAt(15)
  const e = tomorrowAt(16)
  await page.request.post('/api/v1/calendar/events', { data: { summary: 'E2E Team sync', start: tomorrowAt(15, 30).iso, end: tomorrowAt(16, 30).iso } })
  await page.request.post('/api/v1/calendar/events', { data: { summary: 'E2E Lunch', start: tomorrowAt(12).iso, end: tomorrowAt(13).iso } })
  const ids = await ingest(seeded.email, invitationRaw(seeded, { subject: 'E2E Budget review', uid: `budget-${Date.now()}@example.org`, start: s.ical, end: e.ical, location: 'Room 204' }))
  await openConv(page, ids.conversation_id, 'E2E Budget review')
  const card = page.getByTestId('invite-card')
  await expect(card).toContainText('Room 204')
  // The organizer's address beside the name.
  await expect(card).toContainText('Dana Tester <dana@example.org> · 2 guests')
  await expect(card.getByTestId('invite-conflict')).toHaveText(/You have E2E Team sync at that time/)
  await expect(card.getByRole('img', { name: 'Your day around this time' })).toBeVisible()
  await card.getByRole('button', { name: 'Add a note' }).click()
  await card.getByRole('textbox', { name: 'Note for the organiser' }).fill('Running 5 min late')
  const sent = page.waitForRequest((r) => r.url().includes('/calendar/invite-reply') && r.method() === 'POST')
  await page.screenshot({ path: `${SHOTS}/invite-conflict.png` })
  await card.getByRole('button', { name: 'Yes' }).click()
  const body = (await sent).postDataJSON()
  expect(body).toMatchObject({ response: 'accepted', comment: 'Running 5 min late' })
})

test('web.reader: InviteCard states from the ingest job: a reply, a proposal with Accept, a cancel, an update diff (invite.apply_reply, invite.counter, event.accept_proposal, invite.apply_cancel, invite.apply_update)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const s = tomorrowAt(10)
  const e = tomorrowAt(11)
  const uid = `review-${Date.now()}@example.org`
  // My calendar copy is at 10:00; the update moves it to 11:00.
  await page.request.post('/api/v1/calendar/events', { data: { uid, summary: 'E2E Design review', start: s.iso, end: e.iso, location: 'Room 4' } })
  const ids = await ingest(seeded.email, invitationRaw(seeded, { subject: 'E2E Design review', uid, start: s.ical, end: e.ical }))
  const base = {
    uid, summary: 'E2E Design review', start: s.iso, end: e.iso, location: 'Room 4', description: '', sequence: 1, recurrence_id: null,
    organizer: { email: seeded.email, name: 'E2E Tester' }, attendees: [{ email: 'dana@example.org', name: 'Dana', status: 'ACCEPTED' }],
    my_status: null, my_address: null, recurring: false, cancelled: false, in_calendar: true, conflicts: [],
  }
  let invite: Record<string, unknown> = { ...base, method: 'REPLY', itip: { applied: true, detail: 'Dana accepted — calendar updated', method: 'REPLY', attendee: 'dana@example.org', partstat: 'ACCEPTED' } }
  await page.route(/\/api\/v1\/messages\/[^/]+\/invite$/, (r) => r.fulfill({ json: invite }))
  await openConv(page, ids.conversation_id, 'E2E Design review')
  const card = page.getByTestId('invite-card')
  await expect(card.getByTestId('invite-outcome')).toHaveText('Dana accepted — calendar updated')
  await page.screenshot({ path: `${SHOTS}/invite-reply-outcome.png` })

  // A COUNTER from Dana on my event: Accept moves it (POST …/counter/accept).
  const proposed = tomorrowAt(14)
  invite = { ...base, method: 'COUNTER', itip: { applied: false, detail: 'Proposed a new time', method: 'COUNTER', counter: true, proposed: { start: proposed.iso, end: tomorrowAt(15).iso } } }
  await page.route(`**/api/v1/calendar/events/${encodeURIComponent(uid)}/counter/accept`, (r) => r.fulfill({ json: { ...base, start: proposed.iso } }))
  await page.reload()
  await expect(card.getByTestId('invite-proposal')).toContainText(/Dana proposes .* 2 PM|Dana proposes .* 14:00/)
  await page.screenshot({ path: `${SHOTS}/invite-proposal.png` })
  const accepted = page.waitForRequest((r) => r.url().includes('/counter/accept'))
  await card.getByRole('button', { name: 'Accept' }).click()
  expect((await accepted).postDataJSON()).toEqual({ message_id: expect.anything() })
  // ACT event.decline_proposal: Decline tells Dana (POST …/counter/decline) and the time stays.
  await page.route(`**/api/v1/calendar/events/${encodeURIComponent(uid)}/counter/decline`, (r) => r.fulfill({ json: { ok: true, uid, declined: 'dana@example.org' } }))
  await page.reload()
  const declined = page.waitForRequest((r) => r.url().includes('/counter/decline'))
  await card.getByTestId('invite-proposal').getByRole('button', { name: 'Decline' }).click()
  expect((await declined).postDataJSON()).toEqual({ message_id: expect.anything() })
  await expect(page.locator('.toast').filter({ hasText: 'The time stays as it is. Dana is told.' })).toBeVisible()
  await expect(card.getByTestId('invite-proposal')).toHaveCount(0)
  await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('mailr.counter.kept.')).forEach((k) => localStorage.removeItem(k)))

  // An update not applied yet: the diff against my copy.
  invite = { ...base, method: 'REQUEST', organizer: { email: 'dana@example.org', name: 'Dana' }, my_status: 'NEEDS-ACTION', start: tomorrowAt(11).iso, end: tomorrowAt(12).iso, location: 'Room 5', itip: null }
  await page.reload()
  await expect(card.getByRole('list', { name: 'Changes' })).toContainText('Time changed: was')
  await expect(card.getByRole('list', { name: 'Changes' })).toContainText('Place changed: was Room 4')
  await page.screenshot({ path: `${SHOTS}/invite-update-diff.png` })

  // A cancel nobody verified: the card never says the organizer cancelled it, and offers no Remove.
  invite = { ...base, method: 'CANCEL', cancelled: true, organizer: { email: 'dana@example.org', name: 'Dana' }, cancel_verified: false, itip: null }
  await page.reload()
  await expect(card.getByTestId('invite-cancel-unverified')).toContainText('Nobody verified that the organiser sent it')
  await expect(card.getByRole('button', { name: 'Remove from calendar' })).toHaveCount(0)
  // A verified cancel: the status and "Remove from calendar".
  invite = { ...base, method: 'CANCEL', cancelled: true, organizer: { email: 'dana@example.org', name: 'Dana' }, cancel_verified: true, itip: { applied: true, detail: 'Cancelled by the organiser — calendar updated', method: 'CANCEL' } }
  await page.reload()
  await expect(card).toContainText('Cancelled: E2E Design review')
  // The server checks the cancellation again (backend test_mailcal_access_regressions.py); here the
  // card's request and its Undo.
  await page.route(/\/api\/v1\/messages\/[^/]+\/itip\/remove$/, (r) => r.fulfill({ json: { ok: true, undo: { method: 'POST', path: r.request().url().replace(/^.*\/api\/v1/, '').replace(/remove$/, 'restore') } } }))
  await page.route(/\/api\/v1\/messages\/[^/]+\/itip\/restore$/, (r) => r.fulfill({ json: { uid } }))
  const removed = page.waitForRequest((r) => /\/itip\/remove$/.test(r.url()) && r.method() === 'POST')
  await card.getByRole('button', { name: 'Remove from calendar' }).click()
  await removed
  // invite.remove_cancelled: never silently; Undo puts the copy back.
  const restored = page.waitForRequest((r) => /\/itip\/restore$/.test(r.url()) && r.method() === 'POST')
  await page.locator('.toast').filter({ hasText: 'Removed from your calendar' }).getByRole('button', { name: 'Undo' }).click()
  await restored
})

test('web.reader: a copy under another UID is "In your calendar", never a conflict, and Link to this invitation has Undo (invite.link_existing)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const s = tomorrowAt(13)
  const e = tomorrowAt(14)
  // The case: an event "Fw: …" with its own UID at the invitation's times.
  const mine = await (await page.request.post('/api/v1/calendar/events', { data: { summary: 'Fw: E2E Ops Review', start: s.iso, end: e.iso } })).json()
  const uid = `ops-${Date.now()}@example.org`
  const ids = await ingest(seeded.email, invitationRaw(seeded, { subject: 'Fw: E2E Ops Review', uid, start: s.ical, end: e.ical, verified: true }))
  await openConv(page, ids.conversation_id, 'Fw: E2E Ops Review')
  const card = page.getByTestId('invite-card')
  await expect(card.getByTestId('invite-in-calendar')).toContainText('In your calendar')
  await expect(card.getByTestId('invite-conflict')).toHaveCount(0)
  await expect(card.getByRole('button', { name: 'Add to calendar' })).toHaveCount(0)
  // A match by time and title: the user confirms the organizer, shown with its address.
  await card.getByTestId('invite-link').click()
  const dialog = page.getByRole('dialog', { name: 'Link to this invitation?' })
  await expect(dialog).toContainText('Dana Tester <dana@example.org>')
  const linked = page.waitForRequest((r) => r.url().includes('/invite/link') && r.method() === 'POST')
  await dialog.getByRole('button', { name: 'Link to this invitation' }).click()
  expect((await linked).postDataJSON()).toMatchObject({ organizer: 'dana@example.org' })
  await expect.poll(async () => (await events(page.request)).map((x) => x.uid)).toContain(uid)
  await page.locator('.toast').filter({ hasText: 'Linked to the invitation' }).getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await events(page.request)).map((x) => x.uid)).toContain(mine.uid)
})

test('web.reader: a move announced in words is one suggestion card; Move changes the event (event.change_suggestion)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const s = tomorrowAt(13)
  const ev = await (await page.request.post('/api/v1/calendar/events', { data: { summary: 'E2E Ops Review', start: s.iso, end: tomorrowAt(14).iso } })).json()
  const sentence = 'Could we move our E2E Ops review to 3 PM tomorrow?'
  const raw = ['From: Chair <chair@example.org>', `To: ${seeded.email}`, 'Subject: E2E Ops time', `Date: ${new Date().toUTCString()}`,
    `Message-ID: <ops-move-${Date.now()}@example.org>`, 'Content-Type: text/plain; charset=utf-8', '', sentence, ''].join('\r\n')
  const ids = await ingest(seeded.email, raw)
  const change = { uid: ev.uid, summary: 'E2E Ops Review', old_start: s.iso, old_end: tomorrowAt(14).iso, new_start: tomorrowAt(15).iso, new_end: tomorrowAt(16).iso, sentence }
  // The suggestion as the local model would have left it (intel/event_changes.py).
  await page.route(/\/api\/v1\/conversations\/[^/?]+(\?.*)?$/, async (r) => {
    const res = await r.fetch()
    const j = await res.json()
    for (const m of j.messages ?? []) if (m.subject === 'E2E Ops time') m.event_change = change
    await r.fulfill({ response: res, json: j })
  })
  await page.route(/\/api\/v1\/messages\/[^/]+\/event-change$/, (r) => r.fulfill({ json: { ok: true } }))
  await openConv(page, ids.conversation_id, 'E2E Ops time')
  const card = page.getByTestId('event-change')
  await expect(card).toContainText('Move “E2E Ops Review” to')
  await expect(card).toContainText(sentence)
  await card.getByRole('button', { name: 'Move' }).click()
  await expect.poll(async () => new Date((await events(page.request)).find((x) => x.uid === ev.uid)?.start ?? 0).getTime()).toBe(tomorrowAt(15).date.getTime())
  await expect(card).toHaveCount(0)
})

test('web.reader: a plain .ics file gets "Add to calendar" (event.import_ics)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const s = tomorrowAt(18)
  const e = tomorrowAt(20)
  const ids = await ingest(seeded.email, invitationRaw(seeded, { subject: 'E2E Concert tickets', uid: `concert-${Date.now()}@example.org`, method: 'PUBLISH', start: s.ical, end: e.ical, location: 'Symphony Hall', attendee: false }))
  await openConv(page, ids.conversation_id, 'E2E Concert tickets')
  const card = page.getByTestId('import-card')
  await expect(card).toContainText('Symphony Hall')
  await card.getByRole('button', { name: 'Add to calendar' }).click()
  await expect.poll(async () => (await events(page.request)).find((x) => x.summary === 'E2E Concert tickets')?.location).toBe('Symphony Hall')
  await expect(card).toContainText('Added to your calendar')
})

test('web.reader: Meeting with these people (Create from email) opens the editor on Find a time, links the event, and offers a reply (mail.schedule_meeting)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const c = await conv(page.request, 'E2E Reply to me')
  await openConv(page, c.id, 'E2E Reply to me')
  await page.getByRole('button', { name: 'Create from email' }).click()
  await page.getByRole('menuitem', { name: 'Meeting with these people' }).click()
  const step = page.getByRole('dialog', { name: 'Meeting with these people' })
  await expect(step.getByLabel('Title')).toHaveValue('E2E Reply to me')
  const dana = step.getByRole('checkbox', { name: /Dana Tester/ })
  await expect(dana).toBeChecked()
  await page.screenshot({ path: `${SHOTS}/schedule-step.png` })
  await step.getByRole('button', { name: 'Find a time' }).click()
  const editor = page.getByTestId('cal-editor')
  await expect(editor.getByTestId('cal-findtime')).toBeVisible()
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/schedule-findtime.png` })
  await editor.getByRole('listbox', { name: 'Free times' }).getByRole('option').first().click()
  await editor.getByTestId('cal-editor-save').click()
  await page.getByRole('button', { name: "Don't send" }).click()
  await expect.poll(async () => (await events(page.request)).find((x) => x.summary === 'E2E Reply to me')).toBeTruthy()
  const links = await (await page.request.get(`/api/v1/conversations/${c.id}/links`)).json() as { uid: string; kind: string }[]
  expect(links.some((l) => l.kind === 'event')).toBe(true)
  await page.getByRole('button', { name: 'Reply on the thread' }).click()
  await expect(page.locator('[data-inline-reply]')).toContainText('I\'ve sent an invitation for E2E Reply to me')
  // The links panel shows the meeting with its time.
  const linked = page.getByRole('list', { name: 'Linked events and tasks' })
  await expect(linked).toContainText('E2E Reply to me')
  await expect(linked.locator('.cl-status')).toHaveText(/Today|Tomorrow|\w{3} \d+ \w{3}/)
  await page.screenshot({ path: `${SHOTS}/schedule-reply-links.png` })
})

test('web.editor (compose): Insert times puts three free slots at the cursor (event.insert_times)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const c = await conv(page.request, 'E2E Reply to me')
  await openConv(page, c.id, 'E2E Reply to me')
  await page.getByRole('article').last().getByRole('button', { name: 'Reply', exact: true }).first().click()
  const box = page.locator('[data-inline-reply]')
  await box.getByRole('button', { name: 'Insert times' }).click()
  const pop = page.getByRole('dialog', { name: 'Insert times' })
  await expect(pop.getByRole('group', { name: 'Times to insert' }).getByRole('checkbox')).toHaveCount(3)
  // Every option of the two menus fits its box ("Next 5 working c" was cut off).
  const cut = await pop.locator('select').evaluateAll((els) => els.flatMap((el) => {
    const sel = el as HTMLSelectElement
    const cs = getComputedStyle(sel)
    const ctx = document.createElement('canvas').getContext('2d')!
    ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`
    const room = sel.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
    return [...sel.options].filter((o) => ctx.measureText(o.text).width > room).map((o) => o.text)
  }))
  expect(cut).toEqual([])
  await page.screenshot({ path: `${SHOTS}/insert-times.png` })
  await pop.getByRole('button', { name: 'Insert' }).click()
  await expect(box.locator('.ProseMirror')).toContainText('Would any of these work?')
  await expect(box.locator('.ProseMirror li')).toHaveCount(3)
})

test('web.reader: Create event and Add to tasks open the shared editors and link the email (mail.to_event, mail.to_task)', async ({ page, seeded }) => {
  await connectCalendar(page.request, seeded)
  const c = await conv(page.request, 'E2E Task me')
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Task me' }).first().click()
  const message = page.getByRole('article')
  await message.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Add to tasks' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add to tasks' })
  await expect(dialog.getByLabel('Title')).not.toHaveValue('')
  await dialog.getByLabel('Title').fill('E2E Task from mail')
  await dialog.getByRole('button', { name: 'Add task' }).click()
  await expect.poll(async () => (await tasks(page.request)).some((t) => t.summary === 'E2E Task from mail')).toBe(true)

  await message.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Create event' }).click()
  const editor = page.getByTestId('cal-editor')
  await expect(editor.getByRole('textbox', { name: 'Title' })).not.toHaveValue('')
  await editor.getByRole('textbox', { name: 'Title' }).fill('E2E Event from mail')
  // The seeds of the editor (EditorInit.seed): the linked email (ACT event.open_email) and its link in the description.
  await expect(editor.getByRole('button', { name: 'From an email' })).toBeVisible()
  await expect(editor.getByRole('textbox', { name: 'Description' })).toHaveValue(/From the email: http/)
  await page.screenshot({ path: `${SHOTS}/to-event-editor.png` })
  await editor.getByTestId('cal-editor-save').click()
  // The suggestion may fall on the email's own day; the link is what matters here.
  await expect.poll(async () => ((await (await page.request.get(`/api/v1/conversations/${c.id}/links`)).json()) as { summary: string }[]).map((l) => l.summary))
    .toEqual(expect.arrayContaining(['E2E Task from mail', 'E2E Event from mail']))
  await expect(page.getByRole('list', { name: 'Linked events and tasks' })).toContainText('E2E Event from mail')
})

for (const t of ['office', 'cards'] as const) {
  test(`web.reader in ${t}: InviteCard`, async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    await page.request.post('/api/v1/calendar/events', { data: { summary: 'E2E Standup', start: tomorrowAt(9).iso, end: tomorrowAt(9, 30).iso } })
    const ids = await ingest(seeded.email, invitationRaw(seeded, { subject: 'E2E Planning poker', uid: `poker-${Date.now()}@example.org`, start: tomorrowAt(9, 15).ical, end: tomorrowAt(10).ical }))
    await page.addInitScript((x) => localStorage.setItem('mailr.uiTheme', x), t)
    await openConv(page, ids.conversation_id, 'E2E Planning poker')
    await expect(page.getByTestId('invite-card').getByTestId('invite-conflict')).toBeVisible()
    await page.screenshot({ path: `${SHOTS}/invite-${t}.png` })
  })
}

test('web.reader on a phone: InviteCard fits', async ({ page, seeded }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await connectCalendar(page.request, seeded)
  const ids = await ingest(seeded.email, invitationRaw(seeded, { subject: 'E2E Phone invite', uid: `phone-${Date.now()}@example.org`, start: tomorrowAt(13).ical, end: tomorrowAt(14).ical, location: 'Cafe Rio' }))
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Phone invite' }).first().click()
  const card = page.getByTestId('invite-card')
  await expect(card).toBeVisible()
  const box = await card.boundingBox()
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)
  await page.screenshot({ path: `${SHOTS}/invite-phone.png` })
  expect(ids.message_id).toBeGreaterThan(0)
})

test('web.reader: a change from an unverified sender waits, and "Apply anyway" applies it (invite.apply_unverified)', async ({ page, seeded }) => {
  const s = tomorrowAt(10)
  const e = tomorrowAt(11)
  const uid = `unverified-${Date.now()}@example.org`
  const ids = await ingest(seeded.email, invitationRaw(seeded, { subject: 'E2E Unverified update', uid, start: s.ical, end: e.ical }))
  const invite = {
    uid, summary: 'E2E Unverified update', start: s.iso, end: e.iso, location: '', description: '', sequence: 2, recurrence_id: null,
    method: 'REQUEST', organizer: { email: 'dana@example.org', name: 'Dana' }, attendees: [], my_status: null, my_address: null,
    recurring: false, cancelled: false, in_calendar: true, conflicts: [],
    itip: { applied: false, pending: true, detail: 'An update from an unverified sender; your calendar was not changed', method: 'REQUEST' },
  }
  await page.route(/\/api\/v1\/messages\/[^/]+\/invite$/, (r) => r.fulfill({ json: invite }))
  await page.route(/\/api\/v1\/messages\/[^/]+\/itip\/apply$/, (r) => r.fulfill({ json: { applied: true, detail: 'Updated by the organiser — calendar updated', method: 'REQUEST', confirmed: true } }))
  await openConv(page, ids.conversation_id, 'E2E Unverified update')
  const card = page.getByTestId('invite-card')
  await expect(card.getByTestId('invite-outcome')).toContainText('unverified sender')
  const applied = page.waitForRequest((r) => r.url().endsWith('/itip/apply') && r.method() === 'POST')
  await card.getByTestId('invite-apply-unverified').click()
  await applied
  await expect(page.locator('.toast').filter({ hasText: 'Calendar updated.' })).toBeVisible()
})
