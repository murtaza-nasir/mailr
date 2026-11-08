// ACT-SURFACES: web.panel web.settings
// Images and files attached to a message to Ambrose (ChatAttachments.tsx, backend agent/chat_files.py,
// ACT agent.chat_attach): the paperclip and a paste upload at once and show chips; Send carries the
// uploads' ids; the sent message shows a thumbnail and a file chip, also when the chat is opened again.
// Admin → Models → Can view images (ACT settings.model_capabilities) and the person's switch
// (ACT settings.assistant_images). The uploads are real; the chat stream is faked.
import type { Page } from '@playwright/test'
import { accessTest, expect, signIn, test } from './fixtures'

const sse = (events: [string, unknown][]) => events.map(([name, data], i) => `event: ${name}\ndata: ${JSON.stringify({ ...(data as object), seq: i + 1 })}\n\n`).join('')

// A 2×2 PNG, and a small text file.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8z8DAwMDAxMDAwMDAAAANHQEDasKb6QAAAABJRU5ErkJggg==',
  'base64',
)

async function openAmbrose(page: Page) {
  await page.goto('/mail/inbox')
  await page.locator('[data-ambrose]:visible').click()
  await expect(page.locator('.side-panel .chat')).toBeVisible()
}

async function fakeChat(page: Page) {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/v1/agent/chat', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const body = route.request().postDataJSON() as Record<string, unknown>
    bodies.push(body)
    await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse([
      ['session', { session_id: 'e2e-files', user_message_id: 7, title: 'Files' }],
      ['final', { content: 'A tiny red square and your notes.', citations: [] }],
      ['done', {}],
    ]) })
  })
  return bodies
}

test('attach with the paperclip and a paste, send, and see them in the chat', async ({ page }) => {
  const bodies = await fakeChat(page)
  await openAmbrose(page)
  const panel = page.locator('.side-panel')
  await panel.getByTestId('chat-attach-input').setInputFiles([
    { name: 'square.png', mimeType: 'image/png', buffer: PNG },
    { name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('Buy milk on Friday.') },
  ])
  const chips = panel.getByTestId('chat-pending-file')
  await expect(chips).toHaveCount(2)
  await expect(panel.getByRole('status', { name: 'Uploading' })).toHaveCount(0)
  // A file the server cannot read is refused before upload.
  await panel.getByTestId('chat-attach-input').setInputFiles([{ name: 'tool.exe', mimeType: 'application/x-msdownload', buffer: Buffer.from('MZ') }])
  await expect(page.getByText('tool.exe cannot be read')).toBeVisible()
  await expect(chips).toHaveCount(2)
  // A pasted screenshot is attached too.
  const box = panel.locator('.chat-input textarea')
  await box.focus()
  await box.evaluate((el, b64) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
    const dt = new DataTransfer()
    dt.items.add(new File([bytes], 'shot.png', { type: 'image/png' }))
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, PNG.toString('base64'))
  await expect(chips).toHaveCount(3)
  await expect(panel.getByRole('status', { name: 'Uploading' })).toHaveCount(0)
  // Removing one deletes its upload.
  const del = page.waitForResponse((r) => r.request().method() === 'DELETE' && r.url().includes('/agent/attachments/'))
  await panel.getByRole('button', { name: 'Remove shot.png' }).click()
  expect((await del).ok()).toBeTruthy()
  await expect(chips).toHaveCount(2)

  await box.fill('What are these?')
  await box.press('Enter')
  await expect(panel.locator('.chat-log')).toContainText('A tiny red square and your notes.')
  expect(bodies).toHaveLength(1)
  const ids = bodies[0].attachments as string[]
  expect(ids).toHaveLength(2)
  await expect(chips).toHaveCount(0)
  const sent = panel.getByTestId('chat-sent-files')
  await expect(sent.getByRole('img', { name: 'Attached image square.png' })).toBeVisible()
  await expect(sent.getByRole('link', { name: 'Download notes.txt' })).toBeVisible()

  // Opening the chat again shows them from the server's copy (thumbnail and download).
  await page.route('**/api/v1/agent/sessions/e2e-files', (route) => route.fulfill({ json: {
    id: 'e2e-files', title: 'Files', turns: [
      { role: 'user', id: 7, content: 'What are these?', citations: [], steps: [], status: 'done',
        attachments: [{ id: ids[0], name: 'square.png', type: 'image/png', size: PNG.length, kind: 'image', thumb: true, width: 2, height: 2 },
                      { id: ids[1], name: 'notes.txt', type: 'text/plain', size: 19, kind: 'file', thumb: false }] },
      { role: 'assistant', content: 'A tiny red square and your notes.', citations: [], steps: [], timeline: [], status: 'done', user_message_id: 7 },
    ], running: false } }))
  await page.reload()
  // The panel stays open across the reload; open it when it did not.
  const chat = page.locator('.side-panel .chat')
  try {
    await expect(chat).toBeVisible({ timeout: 5000 })
  } catch {
    await page.locator('[data-ambrose]:visible').click()
    await expect(chat).toBeVisible()
  }
  const img = page.locator('.side-panel').getByRole('img', { name: 'Attached image square.png' })
  await expect(img).toBeVisible()
  await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0)
  const dl = await page.request.get(`/api/v1/agent/attachments/${ids[1]}`)
  expect(await dl.text()).toBe('Buy milk on Friday.')
})

test('Settings → Ambrose → Models: the images switch', async ({ page }) => {
  await page.goto('/settings/ambrose/models')
  const row = page.getByTestId('assistant-images')
  await expect(row).toBeVisible()
  const sw = row.getByRole('checkbox', { name: /see images/ })
  await expect(sw).toBeChecked()
  const saved = page.waitForResponse((r) => r.url().endsWith('/api/v1/me/settings') && r.request().method() === 'PATCH')
  await sw.click()
  expect((await saved).request().postDataJSON()).toEqual({ assistant_images: false })
  await expect(sw).not.toBeChecked()
})

accessTest('Admin → Models: can a model view images, with an override', async ({ page, access }) => {
  await signIn(page, access.admin)
  await page.goto('/settings/admin/models')
  const eps = (await (await page.request.get('/api/v1/admin/models')).json()) as { id: number; name: string }[]
  const ep = eps.find((e) => e.name === access.endpoint)!
  const cell = page.getByTestId(`model-vision-${ep.id}`)
  const select = cell.getByRole('combobox', { name: 'Can e2e-small view images' })
  await expect(select).toHaveValue('auto')
  const put = page.waitForResponse((r) => r.url().endsWith(`/admin/models/${ep.id}/capabilities`))
  await select.selectOption('yes')
  expect((await put).request().postDataJSON()).toEqual({ model: 'e2e-small', vision: true })
  await page.reload()
  await expect(page.getByTestId(`model-vision-${ep.id}`).getByRole('combobox')).toHaveValue('yes')
  const listed = (await (await page.request.get('/api/v1/admin/models')).json()) as { id: number; capabilities: Record<string, { vision: boolean; source: string }> }[]
  expect(listed.find((e) => e.id === ep.id)!.capabilities['e2e-small']).toMatchObject({ vision: true, source: 'manual' })
})
