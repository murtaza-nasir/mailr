// Label colour schemes: the picker (scheme tabs, the hue × shade
// grid, keyboard, Custom… and Remove colour), chips that follow light and dark themes, the
// "Label colours" setting that moves scheme colours to another scheme, and the conversion of old
// colours after a preview. With LABEL_SHOTS=<dir> the picker is saved in light and dark for two
// themes.
import type { APIRequestContext, Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import data from '../src/lib/labelColours.json' with { type: 'json' }
import { conv, expect, test } from './fixtures'

const SHOTS = process.env.LABEL_SHOTS

function swatch(token: string, mode: 'light' | 'dark') {
  const [scheme, hue, shade] = token.slice(1).split('/')
  return data.schemes.find((s) => s.id === scheme)!.hues.find((h) => h.id === hue)![mode][Number(shade) - 1]
}
const rgb = (hex: string) => {
  const n = parseInt(hex.slice(1), 16)
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`
}
async function colorOf(request: APIRequestContext, id: number): Promise<string | null> {
  const j = await (await request.get('/api/v1/labels')).json()
  const list = (Array.isArray(j) ? j : j.items) as { id: number; color: string | null }[]
  return list.find((l) => l.id === id)!.color
}
async function setColor(request: APIRequestContext, id: number, color: string | null) {
  const r = await request.patch(`/api/v1/labels/${id}`, { data: { color } })
  expect(r.ok(), await r.text()).toBeTruthy()
}
async function openPicker(page: Page, name: string) {
  const row = page.locator('.sidebar .nav-item', { hasText: name }).first()
  await row.hover()
  await page.getByRole('button', { name: `Options for ${name}` }).click()
  await page.getByRole('menuitem', { name: 'Label colour' }).click()
  const dlg = page.getByRole('dialog', { name: 'Label colour' })
  await expect(dlg).toBeVisible()
  return dlg
}
const listChip = (page: Page, name: string) => page.locator('.conv-row .label-chip, [role="row"] .label-chip, .label-chip', { hasText: name }).first()

test('picker: a scheme tab, a colour, keyboard; the chip follows light and dark', async ({ page, seeded }) => {
  const id = seeded.labels['E2E Label']
  const c = await conv(page.request, 'E2E Reply to me')
  await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'add_labels', label_ids: [id] } })
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/')
  await expect(listChip(page, 'E2E Label')).toBeVisible()

  let dlg = await openPicker(page, 'E2E Label')
  await expect(dlg.getByRole('tab', { name: /Default/ })).toHaveAttribute('aria-selected', 'true')
  await dlg.getByRole('tab', { name: 'Earth' }).click()
  await expect(dlg.getByRole('grid', { name: 'Earth colours' })).toBeVisible()
  // Hovering a cell previews the label in that colour.
  await dlg.getByRole('button', { name: 'Sage, shade 3' }).hover()
  await expect(dlg.getByTestId('lcp-preview')).toHaveCSS('background-color', rgb(swatch('@earth/teal/3', 'light').bg))
  await expect(dlg.getByTestId('lcp-preview')).toHaveText('E2E Label')
  await dlg.getByRole('button', { name: 'Sage, shade 3' }).click()
  await expect.poll(() => colorOf(page.request, id)).toBe('@earth/teal/3')

  const chip = listChip(page, 'E2E Label')
  await expect(chip).toHaveCSS('background-color', rgb(swatch('@earth/teal/3', 'light').bg))
  await expect(chip).toHaveCSS('color', rgb(swatch('@earth/teal/3', 'light').fg))
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect(chip).toHaveCSS('background-color', rgb(swatch('@earth/teal/3', 'dark').bg))
  await expect(chip).toHaveCSS('color', rgb(swatch('@earth/teal/3', 'dark').fg))
  const dot = page.locator('.sidebar .nav-item', { hasText: 'E2E Label' }).locator('.label-dot span')
  await expect(dot).toHaveCSS('background-color', rgb(swatch('@earth/teal/3', 'dark').dot))

  // Keyboard: the chosen cell has focus; arrows move through hues and shades, Enter picks.
  dlg = await openPicker(page, 'E2E Label')
  await expect(dlg.getByRole('button', { name: 'Sage, shade 3' })).toBeFocused()
  await expect(dlg.getByRole('button', { name: 'Sage, shade 3' })).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowDown')
  await expect(dlg.getByRole('button', { name: 'Slate, shade 4' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect.poll(() => colorOf(page.request, id)).toBe('@earth/blue/4')
})

test('picker: Custom… takes any hex with readable text; Remove colour clears it', async ({ page, seeded }) => {
  const id = seeded.labels['E2E Other']
  await page.goto('/')
  let dlg = await openPicker(page, 'E2E Other')
  await dlg.getByRole('button', { name: 'Custom…' }).click()
  await dlg.getByRole('textbox', { name: 'Custom colour (hex)' }).fill('#123456')
  await expect(dlg.getByText(/White text, \d+\.\d:1/)).toBeVisible()
  await dlg.getByRole('button', { name: 'Use' }).click()
  await expect.poll(() => colorOf(page.request, id)).toBe('#123456')
  dlg = await openPicker(page, 'E2E Other')
  await expect(dlg.getByRole('textbox', { name: 'Custom colour (hex)' })).toHaveValue('#123456')
  await dlg.getByRole('button', { name: 'Remove colour' }).click()
  await expect.poll(() => colorOf(page.request, id)).toBeNull()
})

test('settings: a new scheme recolours scheme labels; old colours convert only after the preview', async ({ page, seeded }) => {
  const a = seeded.labels['E2E Label']
  const b = seeded.labels['E2E Other']
  await setColor(page.request, a, '@default/red/2')
  await setColor(page.request, b, '#16a766,#ffffff')
  await page.goto('/settings/mail/labels')
  const group = page.getByRole('radiogroup', { name: 'Label colour scheme' })
  await expect(group.getByRole('radio', { name: 'Default' })).toBeChecked()
  // Each card shows sample labels in light and dark, whichever theme is on.
  const ocean = page.locator('.lc-scheme', { has: page.getByRole('radio', { name: 'Ocean' }) })
  await expect(ocean.locator('.lc-panel.dark .label-chip', { hasText: 'Work' }).first()).toHaveCSS('background-color', rgb(swatch('@ocean/blue/2', 'dark').bg))
  await expect(ocean.locator('.lc-panel.light .label-chip', { hasText: 'Work' }).last()).toHaveCSS('background-color', rgb(swatch('@ocean/blue/4', 'light').bg))
  await group.getByText('Ocean', { exact: true }).click()
  await expect.poll(() => colorOf(page.request, a)).toBe('@ocean/red/2')
  expect(await colorOf(page.request, b)).toBe('#16a766,#ffffff')
  await expect(group.getByRole('radio', { name: 'Ocean' })).toBeChecked()

  await page.getByRole('button', { name: 'Convert my labels…' }).click()
  const dlg = page.getByRole('dialog', { name: 'Convert label colours' })
  const row = dlg.getByRole('listitem').filter({ hasText: 'E2E Other' })
  await expect(row).toContainText(/Kelp, shade [1-4] \(Ocean\)/)
  const shade = Number((await row.textContent())!.match(/Kelp, shade ([1-4])/)![1])
  await expect(row.locator('.label-chip').nth(1)).toHaveCSS('background-color', rgb(swatch(`@ocean/green/${shade}`, 'light').bg))
  expect(await colorOf(page.request, b)).toBe('#16a766,#ffffff') // nothing changes before the user converts
  await dlg.getByRole('button', { name: 'Convert 1 label' }).click()
  await expect.poll(() => colorOf(page.request, b)).toBe(`@ocean/green/${shade}`)
  await expect(page.getByRole('button', { name: 'Convert my labels…' })).toHaveCount(0)
})

test('screenshots of the picker in light and dark for two themes', async ({ page, seeded }) => {
  test.skip(!SHOTS, 'set LABEL_SHOTS=<dir> to save the screenshots')
  mkdirSync(SHOTS!, { recursive: true })
  await setColor(page.request, seeded.labels['E2E Label'], '@default/teal/3')
  await setColor(page.request, seeded.labels['E2E Other'], '@default/orange/2')
  const c = await conv(page.request, 'E2E Reply to me')
  await page.request.post('/api/v1/conversations/actions', { data: { ids: [c.id], action: 'add_labels', label_ids: [seeded.labels['E2E Label'], seeded.labels['E2E Other']] } })
  for (const theme of ['classic', 'cards'] as const) {
    await page.addInitScript((t) => localStorage.setItem('mailr.uiTheme', t), theme)
    for (const mode of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: mode })
      await page.goto('/settings/mail/labels')
      await page.getByRole('button', { name: 'Change colour of E2E Label' }).click()
      const dlg = page.getByRole('dialog', { name: 'Label colour' })
      await expect(dlg).toBeVisible()
      await dlg.getByRole('button', { name: 'Teal, shade 3' }).hover()
      await page.screenshot({ path: `${SHOTS}/picker-${theme}-${mode}.png` })
      await dlg.screenshot({ path: `${SHOTS}/picker-${theme}-${mode}-crop.png` })
      await page.keyboard.press('Escape')
      for (const s of ['earth', 'vivid'] as const) {
        await page.getByRole('button', { name: 'Change colour of E2E Label' }).click()
        await dlg.getByRole('tab', { name: s === 'earth' ? 'Earth' : 'Vivid' }).click()
        await dlg.screenshot({ path: `${SHOTS}/picker-${theme}-${mode}-${s}.png` })
        await page.keyboard.press('Escape')
      }
      await page.goto('/settings/mail/labels')
      await page.getByRole('radiogroup', { name: 'Label colour scheme' }).screenshot({ path: `${SHOTS}/schemes-${theme}-${mode}.png` })
      for (const s of ['default', 'muted', 'vivid', 'pastel', 'earth', 'ocean']) {
        await page.locator('.lc-scheme', { has: page.getByRole('radio', { name: data.schemes.find((x) => x.id === s)!.name }) })
          .screenshot({ path: `${SHOTS}/scheme-${s}-${theme}-${mode}.png` })
      }
    }
  }
})
