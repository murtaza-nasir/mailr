// ACT-SURFACES: web.editor
// Inline images in compose (mail.insert_image, surface web.editor): paste,
// drop and the Insert image button put an image in the body; it is resized and aligned from the
// image toolbar; it is still there after a draft reload; and it is shown in the reader in the
// sent message. The seeded identity's connector is disabled, so the queued message is stored as
// sent through the real pipeline (build_for_payload, then the outbox.store_sent job) from a
// small script.
// Covers: mail.insert_image.
import type { Locator, Page } from '@playwright/test'
import { backend, conv, expect, outbox, test } from './fixtures'


/** A PNG of the given size, drawn in the page. */
async function png(page: Page, w: number, h: number, colour: string): Promise<string> {
  return page.evaluate(({ w, h, colour }) => {
    const c = document.createElement('canvas')
    c.width = w
    c.height = h
    const ctx = c.getContext('2d')!
    ctx.fillStyle = colour
    ctx.fillRect(0, 0, w, h)
    ctx.fillStyle = '#fff'
    ctx.fillRect(w / 4, h / 4, w / 2, h / 2)
    return c.toDataURL('image/png').split(',')[1]
  }, { w, h, colour })
}

async function compose(page: Page) {
  await page.goto('/mail/inbox')
  await page.locator('.compose-btn').click()
  const win = page.locator('.compose-win')
  await expect(win).toBeVisible()
  await win.getByLabel('To recipients').fill('pat@example.net')
  await page.keyboard.press('Enter')
  return win
}

/** Pastes or drops a PNG onto the editor the way a browser delivers it. */
async function deliver(body: Locator, kind: 'paste' | 'drop', b64: string, name: string) {
  await body.evaluate((el, { kind, b64, name }) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
    const dt = new DataTransfer()
    dt.items.add(new File([bytes], name, { type: 'image/png' }))
    const r = el.getBoundingClientRect()
    if (kind === 'paste') el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
    else el.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true, clientX: r.left + 20, clientY: r.bottom - 10 }))
  }, { kind, b64, name })
}

/** The uploaded image of a given pixel width (the order in the body depends on the drop point). */
async function byNatural(win: Locator, width: number): Promise<Locator> {
  const i = await uploaded(win).evaluateAll((els, w) => els.findIndex((e) => (e as HTMLImageElement).naturalWidth === w), width)
  expect(i).toBeGreaterThanOrEqual(0)
  return uploaded(win).nth(i)
}

/** Uploaded (not placeholder) images in the editor. */
function uploaded(win: Locator) {
  return win.locator('.ProseMirror img[src^="/api/v1/uploads/"]')
}

async function loaded(img: Locator) {
  await expect.poll(() => img.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true)
}

test('mail.insert_image: paste, drop, resize and align, send, and the reader shows the image', async ({ page }) => {
  const win = await compose(page)
  await win.getByLabel('Subject').fill('E2E inline image')
  const body = win.locator('.ProseMirror')
  await body.click()
  await page.keyboard.type('Here is the chart: ')

  await deliver(body, 'paste', await png(page, 640, 480, '#1a73e8'), 'chart.png')
  await expect(uploaded(win)).toHaveCount(1)
  await loaded(uploaded(win).first())
  await deliver(body, 'drop', await png(page, 300, 200, '#188038'), 'photo.png')
  await expect(uploaded(win)).toHaveCount(2)
  for (const i of await uploaded(win).all()) await loaded(i)
  const chart = await byNatural(win, 640)
  const width = () => chart.evaluate((i) => Math.round(i.getBoundingClientRect().width))
  // A new image goes in at its own width, at most 600 px (and never wider than the window).
  expect(await chart.evaluate((i) => i.style.width)).toBe('600px')
  expect(await (await byNatural(win, 300)).evaluate((i) => i.style.width)).toBe('300px')

  // Select the chart: handles and the toolbar appear.
  await chart.click()
  const bar = page.getByRole('toolbar', { name: 'Image options' })
  await expect(bar).toBeVisible()
  await expect(win.locator('.mail-img.selected .mail-img-handle')).toHaveCount(4)
  await bar.getByRole('button', { name: 'Small' }).click()
  await expect(bar.getByRole('button', { name: 'Small' })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(width).toBe(200)
  await bar.getByRole('button', { name: 'Align centre' }).click()
  await expect(bar.getByRole('button', { name: 'Align centre' })).toHaveAttribute('aria-pressed', 'true')
  // Keyboard: + grows the selected image by a step.
  await page.keyboard.press('+')
  await expect.poll(width).toBe(220)
  await page.keyboard.press('-')
  await bar.getByRole('button', { name: 'Alt text' }).click()
  await bar.getByLabel('Alt text').fill('Q3 chart')
  await bar.getByRole('button', { name: 'Save' }).click()

  await win.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(win).toHaveCount(0)
  await expect.poll(async () => (await outbox(page.request)).filter((o) => o.subject === 'E2E inline image').length).toBe(1)
  const ob = (await outbox(page.request)).find((o) => o.subject === 'E2E inline image')! as unknown as { id: number; payload: { html: string } }
  const html = ob.payload.html
  expect(html).toMatch(/<img[^>]+src="\/api\/v1\/uploads\/[0-9a-f]{32}"[^>]*>/)
  expect(html).toContain('width="198"')
  expect(html).toContain('data-align="center"')
  expect(html).toContain('alt="Q3 chart"')

  // Store it as sent through the real build and the outbox.store_sent job.
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
`, String(ob.id)], { python: true, env: { MAILR_PUBLIC_IDS: 'true' } })

  const sent = await conv(page.request, 'E2E inline image')
  await page.goto(`/mail/sent/${sent.id}`)
  const frame = page.frameLocator('iframe').first()
  const imgs = frame.locator('img')
  await expect(imgs).toHaveCount(2)
  for (const i of await imgs.all()) {
    await expect.poll(() => i.evaluate((el: HTMLImageElement) => el.src.startsWith('data:image/png') && el.naturalWidth > 0)).toBe(true)
  }
  await expect(frame.locator('img[alt="Q3 chart"]')).toHaveAttribute('width', '198')
})

test('mail.insert_image: the Insert image button, make it an attachment, and a draft reload keeps the image', async ({ page }) => {
  const win = await compose(page)
  await win.getByLabel('Subject').fill('E2E inline draft')
  await win.locator('.ProseMirror').click()
  await page.keyboard.type('Draft with a picture ')
  const b64 = await png(page, 400, 300, '#d93025')
  await win.locator('input[aria-label="Image to insert"]').setInputFiles({ name: 'red.png', mimeType: 'image/png', buffer: Buffer.from(b64, 'base64') })
  await expect(uploaded(win)).toHaveCount(1)
  await win.locator('input[aria-label="Image to insert"]').setInputFiles({ name: 'blue.png', mimeType: 'image/png', buffer: Buffer.from(await png(page, 120, 90, '#1a73e8'), 'base64') })
  await expect(uploaded(win)).toHaveCount(2)

  // The second image becomes an attachment.
  await uploaded(win).nth(1).click()
  await page.getByRole('toolbar', { name: 'Image options' }).getByRole('button', { name: 'Convert to attachment' }).click()
  await expect(uploaded(win)).toHaveCount(1)
  await expect(win.locator('.compose-att')).toHaveCount(1)

  // SVG is refused in the body.
  await win.locator('input[aria-label="Image to insert"]').setInputFiles({ name: 'logo.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>') })
  await expect(page.getByText(/is an SVG image\. Attach it instead/)).toBeVisible()

  await expect(win.getByText('Draft saved')).toBeVisible()
  const c = await conv(page.request, 'E2E inline draft')
  const j = await (await page.request.get(`/api/v1/conversations/${c.id}`)).json()
  const draftId = (j.messages as { id: number; draft: boolean }[]).find((m) => m.draft)!.id
  await expect.poll(async () => {
    const d = await (await page.request.get(`/api/v1/drafts/${draftId}`)).json()
    return /src="\/api\/v1\/uploads\/[0-9a-f]{32}"/.test(d.payload.html) && d.payload.attachments.length === 1
  }).toBe(true)

  // Reload and reopen the draft: the image is back in the body.
  await page.reload()
  await page.goto('/mail/drafts')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E inline draft' }).first().click()
  await page.getByRole('button', { name: 'Edit draft' }).click()
  const again = page.locator('.compose-win, [data-inline-reply]').first()
  await expect(again).toBeVisible()
  await expect(uploaded(again)).toHaveCount(1)
  await loaded(uploaded(again).first())
  await expect(again.locator('.compose-att')).toHaveCount(1)
})
