// File attachments in compose (ComposeFooter's Attach files, the form's drop target, AttachmentList,
// useAttachments): attach through the file picker and by dropping files on the window, name and
// size in the list, Remove, and send: the message goes out with the files (the outbox payload,
// then the stored sent message and its attachment's bytes through the API). Before sending, the
// "Send without an attachment?" check (the text mentions one, nothing is attached), the refusal of
// a message with nothing in it, and the question for a message without a subject. The seeded identity's connector is disabled, so the queued message is stored as
// sent through the real pipeline from a small script, as in inline-images.spec.ts.
// The form is the same component in every theme; Office opens it in the reading pane instead of a
// window, so release runs take Classic and Office.
import type { Locator, Page } from '@playwright/test'
import { backend, conv, expect, outbox, test } from './fixtures'
import { themes } from './matrix'
import { openApp } from './helpers'

const CSV = Buffer.from(`${'month,amount\n'}${Array.from({ length: 200 }, (_, i) => `2026-${String(1 + (i % 12)).padStart(2, '0')},${1000 + i}`).join('\n')}\n`)
const PDF = Buffer.from(`%PDF-1.4\n% e2e\n${'0'.repeat(880)}\n%%EOF\n`)
const NOTE = Buffer.from('Packing list: tent, stove, maps.\n')

async function compose(page: Page, theme: string, subject: string): Promise<Locator> {
  await openApp(page, '/mail/inbox', { theme, ready: page.locator('[data-mail-list] [role="row"]').first() })
  await page.getByRole('button', { name: 'Compose', exact: true }).first().click()
  // A compose window, or (Office) a tab of the reading pane.
  const win = page.locator('.compose-form')
  await expect(win).toBeVisible()
  await win.getByLabel('To recipients').fill('pat@example.net')
  await page.keyboard.press('Enter')
  if (subject) await win.getByLabel('Subject').fill(subject)
  return win
}

async function typeBody(page: Page, win: Locator, text: string) {
  await win.locator('.ProseMirror').click()
  await page.keyboard.type(text)
}

/** Files as the browser delivers a drop of them from the desktop onto the window. */
async function drop(target: Locator, files: { name: string; type: string; text: string }[]) {
  await target.evaluate((el, files) => {
    const dt = new DataTransfer()
    for (const f of files) dt.items.add(new File([f.text], f.name, { type: f.type }))
    const r = el.getBoundingClientRect()
    const at = { clientX: r.left + 10, clientY: r.top + 10 }
    el.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true, ...at }))
    el.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true, ...at }))
  }, files)
}

const att = (win: Locator, name: string) => win.locator('.compose-att').filter({ hasText: name })

/** The queued message stored as sent through the real build (outbox.store_sent). */
function storeSent(outboxId: number) {
  backend(['-c', `
import asyncio, sys
from mailr.core.drafts import build_for_payload
from mailr.core.sendgate import _SystemPrincipal, _outbox_dir, store_sent_job
from mailr.db import session_scope
from mailr.models import Outbox, User

async def go(oid):
    async with session_scope() as db:
        ob = await db.get(Outbox, oid)
        raw, _, _ = await build_for_payload(db, _SystemPrincipal(await db.get(User, ob.user_id)), ob.payload)
        (_outbox_dir() / f"{oid}.eml").write_bytes(raw)
        ob.status = "sent"
    await store_sent_job({"outbox_id": oid, "parent_id": None}, None)

asyncio.run(go(int(sys.argv[1])))
`, String(outboxId)], { python: true, env: { MAILR_PUBLIC_IDS: 'true' } })
}

interface Payload { attachments?: { blob: string; filename: string; type: string; size: number }[] }
interface ApiAttachment { part: number; filename: string; type: string; size: number; inline?: boolean }

for (const theme of themes({ release: ['classic', 'office'] })) {
  test(`${theme}: attach by picker and by drop, sizes, Remove, and the sent message carries the files`, async ({ page }) => {
    const subject = `E2E attachments ${theme}`
    const win = await compose(page, theme, subject)
    await typeBody(page, win, 'The figures are in the spreadsheet.')

    // The file picker of Attach files (several at once).
    const chooser = page.waitForEvent('filechooser')
    await win.getByRole('button', { name: 'Attach files' }).click()
    await (await chooser).setFiles([
      { name: 'figures.csv', mimeType: 'text/csv', buffer: CSV },
      { name: 'contract.pdf', mimeType: 'application/pdf', buffer: PDF },
    ])
    await expect(att(win, 'figures.csv')).toContainText(`(${Math.round(CSV.length / 1024)} KB)`)
    await expect(att(win, 'contract.pdf')).toContainText(`(${PDF.length} B)`)

    // A drop of a file anywhere on the window attaches it too.
    await drop(win.getByLabel('Subject'), [{ name: 'packing.txt', type: 'text/plain', text: NOTE.toString() }])
    await expect(att(win, 'packing.txt')).toContainText(`(${NOTE.length} B)`)
    await expect(win.locator('.compose-att')).toHaveCount(3)

    // Remove takes one off the list.
    await win.getByRole('button', { name: 'Remove contract.pdf' }).click()
    await expect(att(win, 'contract.pdf')).toHaveCount(0)
    await expect(win.locator('.compose-att')).toHaveCount(2)

    // The draft keeps the list (the server copy names the two files).
    await expect(win.getByText('Draft saved')).toBeVisible()

    await win.getByRole('button', { name: 'Send', exact: true }).click()
    await expect(win).toHaveCount(0)
    await expect.poll(async () => (await outbox(page.request)).filter((o) => o.subject === subject).length).toBe(1)
    const ob = (await outbox(page.request)).find((o) => o.subject === subject)! as unknown as { id: number; payload: Payload }
    const sentFiles = (ob.payload.attachments ?? []).map((a) => [a.filename, a.type, a.size])
    expect(sentFiles.sort()).toEqual([['figures.csv', 'text/csv', CSV.length], ['packing.txt', 'text/plain', NOTE.length]])

    // Stored as sent: the message has the two files, byte for byte.
    storeSent(ob.id)
    const sent = await conv(page.request, subject)
    const full = await (await page.request.get(`/api/v1/conversations/${sent.id}`)).json()
    const msg = (full.messages as { id: number; attachments: ApiAttachment[] }[]).at(-1)!
    const files = msg.attachments.filter((a) => !a.inline)
    expect(files.map((a) => a.filename).sort()).toEqual(['figures.csv', 'packing.txt'])
    const csv = files.find((a) => a.filename === 'figures.csv')!
    const body = await (await page.request.get(`/api/v1/messages/${msg.id}/attachments/${csv.part}?download=true`)).body()
    expect(body.equals(CSV)).toBe(true)

    // The reader lists them.
    await page.goto(`/mail/sent/${sent.id}`)
    await expect(page.getByRole('heading', { name: subject })).toBeVisible()
    await expect(page.getByTestId('att-name')).toHaveText(['figures.csv', 'packing.txt'])
  })
}

test('a file over 25 MB is refused with a message and never listed', async ({ page }) => {
  const win = await compose(page, 'classic', 'E2E too big')
  const chooser = page.waitForEvent('filechooser')
  await win.getByRole('button', { name: 'Attach files' }).click()
  await (await chooser).setFiles({ name: 'huge.bin', mimeType: 'application/octet-stream', buffer: Buffer.alloc(25 * 1024 * 1024 + 1) })
  await expect(page.locator('.toast').filter({ hasText: '"huge.bin" is larger than 25 MB and can\'t be attached.' })).toBeVisible()
  await expect(win.locator('.compose-att')).toHaveCount(0)
})

test('the text mentions an attachment but none is attached: Send asks first', async ({ page }) => {
  const subject = 'E2E forgot the file'
  const win = await compose(page, 'classic', subject)
  await typeBody(page, win, 'Please find attached the minutes.')
  await win.getByRole('button', { name: 'Send', exact: true }).click()
  const ask = page.getByRole('dialog', { name: 'Send without an attachment?' })
  await expect(ask).toBeVisible()
  await expect(ask).toContainText('You mention an attachment, but nothing is attached.')

  // Cancel keeps the window, nothing is queued; attaching a file then sends without asking.
  await ask.getByRole('button', { name: 'Cancel' }).click()
  await expect(ask).toHaveCount(0)
  await expect(win).toBeVisible()
  expect((await outbox(page.request)).filter((o) => o.subject === subject)).toHaveLength(0)
  const chooser = page.waitForEvent('filechooser')
  await win.getByRole('button', { name: 'Attach files' }).click()
  await (await chooser).setFiles({ name: 'minutes.txt', mimeType: 'text/plain', buffer: NOTE })
  await expect(att(win, 'minutes.txt')).toContainText('B)')
  await win.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(win).toHaveCount(0)
  await expect(ask).toHaveCount(0)
  await expect.poll(async () => (await outbox(page.request)).filter((o) => o.subject === subject).length).toBe(1)
})

test('Send anyway sends the message that mentions an attachment without one', async ({ page }) => {
  const subject = 'E2E no file after all'
  const win = await compose(page, 'classic', subject)
  await typeBody(page, win, 'I have attached nothing, on purpose.')
  await win.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByRole('dialog', { name: 'Send without an attachment?' }).getByRole('button', { name: 'Send anyway' }).click()
  await expect(win).toHaveCount(0)
  await expect.poll(async () => (await outbox(page.request)).filter((o) => o.subject === subject).length).toBe(1)
})

test('a message with nothing in it is refused at once; one with text and no subject asks first', async ({ page }) => {
  const win = await compose(page, 'classic', '')
  const ask = page.getByRole('dialog', { name: 'Send without a subject?' })

  // No subject, no text, no file: the server would refuse it (core/sendgate.py), so no Send is offered.
  await win.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.getByText('The message is empty. Add a subject, some text or a file.')).toBeVisible()
  await expect(ask).toHaveCount(0)
  await expect(win).toBeVisible()

  // Text but no subject: asked once; Cancel keeps the draft, Send sends it.
  const before = (await outbox(page.request)).length
  await typeBody(page, win, 'E2E text without a subject')
  await win.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(ask).toBeVisible()
  await expect(ask).toContainText('The message has no subject line.')
  await ask.getByRole('button', { name: 'Cancel' }).click()
  await expect(win).toBeVisible()
  await win.getByRole('button', { name: 'Send', exact: true }).click()
  await ask.getByRole('button', { name: 'Send' }).click()
  await expect(win).toHaveCount(0)
  await expect.poll(async () => (await outbox(page.request)).length).toBe(before + 1)

  // With a subject it goes without asking.
  const win2 = await compose(page, 'classic', 'E2E only a subject')
  await win2.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(ask).toHaveCount(0)
  await expect(win2).toHaveCount(0)
  await expect.poll(async () => (await outbox(page.request)).filter((o) => o.subject === 'E2E only a subject').length).toBe(1)
})
