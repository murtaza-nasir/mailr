// ACT-SURFACES: web.list web.reader
// Cascading submenus (components/Popover.tsx SubMenu) in
// the row's right-click menu (web.list) and the reader's ⋮ menu (web.reader): hover with intent,
// the safe triangle, keys, flipping at the right edge, the phone's in-place panels, and every theme.
// Covers mail.snooze, mail.move ("Move to folder") and mail.label from the row menu's submenus with Undo,
// mail.category (the Category submenu under More, with Undo, in both menus), mail.to_task, mail.to_event and mail.follow_up ("Add to").
// Screenshots go to E2E_SHOTS (default: <tmp>/mailr-submenu-shots).
import os from 'node:os'
import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { apiSettled, conv, expect, roles, test } from './fixtures'
import { openApp } from './helpers'
import { schemes, THEMES } from './matrix'

type Theme = 'classic' | 'office' | 'focus' | 'desk' | 'cards'
const SHOTS = process.env.E2E_SHOTS ?? path.join(os.tmpdir(), 'mailr-submenu-shots')

const row = (page: Page, subject: string) => page.locator('[data-mail-list] [role="row"]').filter({ hasText: subject }).first()
const menu = (page: Page) => page.getByRole('menu', { name: 'Conversation actions' })
const sub = (page: Page, name: string) => page.getByRole('menu', { name, exact: true })
// An item's name is its text and its key hint ("Move to folder v").
const trigger = (page: Page, name: string) => menu(page).getByRole('menuitem', { name: new RegExp(`^${name}( \\S)?$`) })

const open = (page: Page, theme: Theme = 'classic') => openApp(page, '/mail/inbox', { theme: theme === 'classic' ? undefined : theme, ready: row(page, 'E2E Reply to me') })

async function rightClick(page: Page, subject: string, at?: { x: number; y: number }) {
  const r = row(page, subject)
  await r.scrollIntoViewIfNeeded()
  if (at) await page.mouse.click(at.x, at.y, { button: 'right' })
  else await r.click({ button: 'right', position: { x: 200, y: 10 } })
  await expect(menu(page)).toBeVisible()
}

async function hover(page: Page, l: Locator) {
  const b = (await l.boundingBox())!
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 3 })
}

async function undo(page: Page) {
  await page.getByRole('button', { name: 'Undo' }).last().click()
}

test('hover opens Snooze, Move to and Label as; each applies with Undo (mail.snooze, mail.move, mail.label)', async ({ page, seeded }) => {
  const r = await roles(page.request)
  await open(page)

  await rightClick(page, 'E2E Snooze me')
  await expect(trigger(page, 'Snooze').locator('.shortcut')).toHaveText('b') // a keyboard is likely: the hint shows
  await hover(page, trigger(page, 'Snooze'))
  await expect(sub(page, 'Snooze')).toBeVisible()
  await expect(trigger(page, 'Snooze')).toHaveAttribute('aria-expanded', 'true')
  await expect(trigger(page, 'Snooze')).toHaveAttribute('aria-haspopup', 'menu')
  await sub(page, 'Snooze').getByRole('menuitem', { name: /Tomorrow/ }).click()
  await expect(menu(page)).toHaveCount(0)
  await expect.poll(async () => (await conv(page.request, 'E2E Snooze me')).snoozed_until).not.toBeNull()
  await undo(page)
  await expect.poll(async () => (await conv(page.request, 'E2E Snooze me')).snoozed_until).toBeNull()

  await rightClick(page, 'E2E Move me')
  await hover(page, trigger(page, 'Move to folder'))
  const move = sub(page, 'Move to folder')
  await expect(move.getByRole('menuitem', { name: 'Archive' })).toBeVisible()
  await expect(move.getByRole('menuitem', { name: 'Inbox' })).toHaveCount(0) // already in the Inbox
  await move.getByRole('textbox', { name: 'Search labels' }).fill('E2E Oth')
  await move.getByRole('menuitem', { name: 'E2E Other' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Move me')).label_ids).toContain(seeded.labels['E2E Other'])
  expect((await conv(page.request, 'E2E Move me')).label_ids).not.toContain(r.inbox)
  await undo(page)
  await expect.poll(async () => (await conv(page.request, 'E2E Move me')).label_ids).toContain(r.inbox)

  // Label as: ticking keeps the submenu open, so several can be ticked; Apply puts them on.
  await rightClick(page, 'E2E Label me')
  await hover(page, trigger(page, 'Label as'))
  const lab = sub(page, 'Label as')
  await lab.getByRole('menuitemcheckbox', { name: 'E2E Label' }).click()
  await lab.getByRole('menuitemcheckbox', { name: 'E2E Other' }).click()
  await expect(lab.getByRole('menuitemcheckbox', { name: 'E2E Other' })).toHaveAttribute('aria-checked', 'true')
  await lab.getByRole('menuitem', { name: 'Apply' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Label me')).label_ids)
    .toEqual(expect.arrayContaining([seeded.labels['E2E Label'], seeded.labels['E2E Other']]))
  await undo(page)
  await expect.poll(async () => (await conv(page.request, 'E2E Label me')).label_ids).not.toContain(seeded.labels['E2E Other'])

  // Ticks also apply when the menu closes, as in the standalone menu. The menu ticks the labels
  // the page holds: let it reload the conversation after the Undo first.
  await apiSettled(page)
  await rightClick(page, 'E2E Label me')
  await hover(page, trigger(page, 'Label as'))
  await sub(page, 'Label as').getByRole('menuitemcheckbox', { name: 'E2E Other' }).click()
  await page.keyboard.press('Escape') // closes the submenu
  await expect(sub(page, 'Label as')).toHaveCount(0)
  await expect.poll(async () => (await conv(page.request, 'E2E Label me')).label_ids).toContain(seeded.labels['E2E Other'])
})

test('moving diagonally into a submenu keeps it open; resting on another item switches', async ({ page }) => {
  await open(page)
  await rightClick(page, 'E2E Reply to me')
  await hover(page, trigger(page, 'Snooze'))
  const snooze = sub(page, 'Snooze')
  await expect(snooze).toBeVisible()
  const from = (await trigger(page, 'Snooze').boundingBox())!
  const panel = (await snooze.boundingBox())!
  const target = (await snooze.getByRole('menuitem', { name: /Pick date/ }).boundingBox())!
  // From the item's right part, down and right to the panel's last item, slowly enough (≈300 ms)
  // that the items crossed on the way (Move to, Label as) would open without the safe triangle.
  const a = { x: from.x + from.width - 40, y: from.y + from.height / 2 }
  const b = { x: panel.x + 30, y: target.y + target.height / 2 }
  await page.mouse.move(a.x, a.y)
  for (let i = 1; i <= 10; i++) {
    await page.mouse.move(a.x + ((b.x - a.x) * i) / 10, a.y + ((b.y - a.y) * i) / 10)
    await page.waitForTimeout(25)
  }
  await page.waitForTimeout(500)
  await expect(snooze).toBeVisible()
  await expect(sub(page, 'Move to folder')).toHaveCount(0)
  await expect(sub(page, 'Label as')).toHaveCount(0)

  // Straight down onto Move to and resting there: it takes over.
  await hover(page, trigger(page, 'Move to folder'))
  await expect(sub(page, 'Move to folder')).toBeVisible()
  await expect(snooze).toHaveCount(0)
  // Resting on a plain item closes it.
  await hover(page, menu(page).getByRole('menuitem', { name: 'Mute' }))
  await expect(sub(page, 'Move to folder')).toHaveCount(0)
})

test('keys: → and Enter open a submenu with focus inside, ← and Escape go back to its item', async ({ page }) => {
  await open(page)
  await rightClick(page, 'E2E Reply to me')
  await trigger(page, 'Snooze').focus()
  await page.keyboard.press('ArrowRight')
  const snooze = sub(page, 'Snooze')
  await expect(snooze).toBeVisible()
  await expect(snooze.getByRole('menuitem').first()).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(snooze.getByRole('menuitem').nth(1)).toBeFocused()
  await page.keyboard.press('ArrowLeft')
  await expect(snooze).toHaveCount(0)
  await expect(trigger(page, 'Snooze')).toBeFocused()

  // ↓ in the main menu skips nothing and stays in it; Enter opens Move to with its filter focused.
  await page.keyboard.press('ArrowDown')
  await expect(trigger(page, 'Move to folder')).toBeFocused()
  await page.keyboard.press('Enter')
  const move = sub(page, 'Move to folder')
  await expect(move.getByRole('textbox', { name: 'Search labels' })).toBeFocused()
  // ← in the filter box moves the caret; Escape closes only the submenu.
  await page.keyboard.type('E2E')
  await page.keyboard.press('ArrowLeft')
  await expect(move).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(move).toHaveCount(0)
  await expect(menu(page)).toBeVisible()
  await expect(trigger(page, 'Move to folder')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(menu(page)).toHaveCount(0)
})

test('near the right edge a submenu opens on the left; near the bottom it shifts up', async ({ page }) => {
  await open(page)
  const vp = page.viewportSize()!
  const r = (await row(page, 'E2E Reply to me').boundingBox())!
  await rightClick(page, 'E2E Reply to me', { x: vp.width - 120, y: r.y + 10 })
  await hover(page, trigger(page, 'Label as'))
  const lab = sub(page, 'Label as')
  await expect(lab).toBeVisible()
  await expect(lab).toHaveAttribute('data-side', 'left')
  const m = (await menu(page).boundingBox())!
  const p = (await lab.boundingBox())!
  expect(p.x + p.width).toBeLessThanOrEqual(m.x + 6)
  await page.screenshot({ path: `${SHOTS}/flip-left.png` })
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')

  await rightClick(page, 'E2E Reply to me', { x: 400, y: vp.height - 20 })
  await hover(page, trigger(page, 'Label as'))
  const q = (await sub(page, 'Label as').boundingBox())!
  expect(q.y + q.height).toBeLessThanOrEqual(vp.height - 7)
  await expect(sub(page, 'Label as')).toHaveAttribute('data-side', 'right')
})

test('mail.category: the Category submenu under More, from the row and from the reader, with Undo', async ({ page }) => {
  await open(page)
  const before = (await conv(page.request, 'E2E Reply to me') as unknown as { category: string | null }).category
  await rightClick(page, 'E2E Reply to me')
  // The Default layout's tabs are the categories, so they are Show in tab's choices too.
  await hover(page, trigger(page, 'Show in tab'))
  await expect(sub(page, 'Show in tab').getByRole('menuitemradio')).toHaveText(['Primary', 'Promotions', 'Social', 'Updates', 'Forums'])
  await expect(sub(page, 'Show in tab').locator('[aria-checked="true"]')).toHaveText(['Primary'])
  await hover(page, trigger(page, 'More'))
  await hover(page, sub(page, 'More').getByRole('menuitem', { name: 'Category' }))
  const cats = sub(page, 'Category')
  await expect(cats.getByRole('menuitemradio', { name: 'Primary' })).toHaveAttribute('aria-checked', String(!before || before === 'primary'))
  await cats.getByRole('menuitemradio', { name: 'Promotions' }).click()
  const cat = async () => ((await conv(page.request, 'E2E Reply to me')) as unknown as { category: string | null }).category
  await expect.poll(cat).toBe('promotions')
  await expect(page.getByText('Category set to Promotions.')).toBeVisible()
  await undo(page)
  await expect.poll(cat).toBe(before)

  // The reader's ⋮ menu has the same submenus.
  await row(page, 'E2E Reply to me').click()
  await expect(page.getByRole('heading', { name: 'E2E Reply to me' })).toBeVisible()
  await page.getByRole('toolbar', { name: 'Conversation actions' }).getByRole('button', { name: 'More', exact: true }).click()
  await hover(page, page.getByRole('menuitem', { name: 'Category' }))
  await expect(sub(page, 'Category').getByRole('menuitemradio', { name: 'Primary' })).toHaveAttribute('aria-checked', 'true')
  await page.waitForTimeout(200)
  await page.screenshot({ path: `${SHOTS}/reader-more-category.png` })
  await sub(page, 'Category').getByRole('menuitemradio', { name: 'Updates' }).click()
  await expect.poll(cat).toBe('updates')
  await page.getByRole('toolbar', { name: 'Conversation actions' }).getByRole('button', { name: 'More', exact: true }).click()
  await hover(page, page.getByRole('menuitem', { name: 'Find mail' }))
  await expect(sub(page, 'Find mail').getByRole('menuitem', { name: 'With this subject' })).toBeVisible()
  await hover(page, page.getByRole('menuitem', { name: 'Filter mail like this' }))
  await sub(page, 'Filter mail like this').getByRole('menuitem', { name: 'With this subject…' }).click()
  await expect(page).toHaveURL(/\/settings\/mail\/filters\?new=subject/)
})

test('Find mail and Filter mail like this replace the single items', async ({ page }) => {
  await open(page)
  await rightClick(page, 'E2E Reply to me')
  await hover(page, trigger(page, 'Find mail'))
  const find = sub(page, 'Find mail')
  await expect(find.getByRole('menuitem', { name: /^From / }).first()).toBeVisible()
  await find.getByRole('menuitem', { name: 'With this subject' }).click()
  await expect(page).toHaveURL(/\/mail\/search/)
  await expect(page.getByTestId('search-input')).toHaveValue('subject:"E2E Reply to me"')
})

test('Create from email: Task and Event; Remind me if no reply beside it (mail.to_task, mail.to_event, mail.follow_up)', async ({ page }) => {
  await open(page)
  await rightClick(page, 'E2E Follow me')
  await hover(page, trigger(page, 'Create from email'))
  const add = sub(page, 'Create from email')
  await add.getByRole('menuitem', { name: 'Task', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Add to tasks' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Add to tasks' })).toHaveCount(0)

  await rightClick(page, 'E2E Follow me')
  await hover(page, trigger(page, 'Create from email'))
  await sub(page, 'Create from email').getByRole('menuitem', { name: 'Event', exact: true }).click()
  await expect(page.getByTestId('cal-editor')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('cal-editor')).toHaveCount(0)

  // Remind me if no reply: its own submenu, beside Create from email.
  await rightClick(page, 'E2E Follow me')
  await hover(page, trigger(page, 'Remind me if no reply'))
  const remind = sub(page, 'Remind me if no reply')
  await expect(remind).toBeVisible()
  await page.waitForTimeout(200)
  await page.screenshot({ path: `${SHOTS}/add-to-remind.png` })
  await remind.getByRole('menuitem', { name: /In 3 days/ }).click()
  const full = async () => ((await (await page.request.get('/api/v1/conversations', { params: { view: 'all', q: 'subject:"E2E Follow me"', limit: '5' } })).json())
    .items as { subject: string; follow_up_at: string | null }[]).find((x) => x.subject === 'E2E Follow me')!.follow_up_at
  await expect.poll(full).not.toBeNull()
})

test('phone width: a submenu replaces the menu in place, with Back', async ({ page, seeded }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await open(page)
  await rightClick(page, 'E2E Move me')
  // No key hints (b, v, l, e, #, s) at phone width: there is no keyboard to press them on.
  await expect(menu(page).locator('.shortcut').first()).toBeAttached()
  await expect(menu(page).locator('.shortcut:visible')).toHaveCount(0)
  await page.waitForTimeout(250)
  await page.screenshot({ path: `${SHOTS}/phone-menu.png` })
  await trigger(page, 'Move to folder').click()
  const back = menu(page).getByRole('menuitem', { name: 'Back' })
  await expect(back).toBeVisible()
  await expect(trigger(page, 'Move to folder')).toBeHidden()
  await expect(menu(page).getByRole('menuitem', { name: 'Archive', exact: true }).first()).toBeVisible() // Move to's Archive
  await page.waitForTimeout(250)
  await page.screenshot({ path: `${SHOTS}/phone-move.png` })
  await back.click()
  await expect(trigger(page, 'Move to folder')).toBeVisible()
  await expect(back).toHaveCount(0)
  await trigger(page, 'Move to folder').click()
  await menu(page).getByRole('menuitem', { name: 'E2E Other' }).click()
  await expect.poll(async () => (await conv(page.request, 'E2E Move me')).label_ids).toContain(seeded.labels['E2E Other'])
})

for (const theme of THEMES) {
  test(`${theme}: the row menu's submenus open beside it`, async ({ page }) => {
    await open(page, theme)
    for (const mode of schemes(theme)) {
      await page.emulateMedia({ colorScheme: mode })
      await rightClick(page, 'E2E Reply to me')
      await hover(page, trigger(page, 'Label as'))
      const lab = sub(page, 'Label as')
      await expect(lab).toBeVisible()
      const m = (await menu(page).boundingBox())!
      const p = (await lab.boundingBox())!
      expect(Math.abs(p.x - (m.x + m.width)) < 12 || Math.abs(p.x + p.width - m.x) < 12).toBe(true)
      await page.waitForTimeout(200)
      await page.screenshot({ path: `${SHOTS}/${theme}-${mode}-label.png` })
      await hover(page, trigger(page, 'Snooze'))
      await expect(sub(page, 'Snooze')).toBeVisible()
      await page.waitForTimeout(200)
      await page.screenshot({ path: `${SHOTS}/${theme}-${mode}-snooze.png` })
      await page.keyboard.press('Escape')
      await page.keyboard.press('Escape')
      await expect(menu(page)).toHaveCount(0)
    }
  })
}

test('web.nav: a label\'s menu has In label list and Label colour as submenus', async ({ page }) => {
  await open(page)
  for (const mode of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: mode })
    await page.locator('[data-drop-view^="label:"]').filter({ hasText: 'E2E Label' }).first().click({ button: 'right' })
    const opts = page.getByRole('menu').filter({ has: page.getByRole('menuitem', { name: 'Label colour' }) }).first()
    await hover(page, opts.getByRole('menuitem', { name: 'In label list' }))
    const inList = sub(page, 'In label list')
    await expect(inList.getByRole('menuitemradio', { name: 'Show', exact: true })).toHaveAttribute('aria-checked', 'true')
    await page.waitForTimeout(200)
    await page.screenshot({ path: `${SHOTS}/nav-label-list-${mode}.png` })
    await hover(page, opts.getByRole('menuitem', { name: 'Label colour' }))
    const dlg = page.getByRole('dialog', { name: 'Label colour' })
    await expect(dlg).toBeVisible()
    await expect(opts.getByRole('menuitem', { name: 'Label colour' })).toHaveAttribute('aria-haspopup', 'dialog')
    await page.waitForTimeout(200)
    await page.screenshot({ path: `${SHOTS}/nav-label-colour-${mode}.png` })
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')
  }
  await page.locator('[data-drop-view^="label:"]').filter({ hasText: 'E2E Label' }).first().click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'In label list' }).click()
  await sub(page, 'In label list').getByRole('menuitemradio', { name: 'Show if unread' }).click()
  const shown = async () => ((await (await page.request.get('/api/v1/labels')).json()) as { name: string; show_in_list: string }[] | { items: { name: string; show_in_list: string }[] })
  await expect.poll(async () => { const j = await shown(); return (Array.isArray(j) ? j : j.items).find((l) => l.name === 'E2E Label')?.show_in_list }).toBe('unread')
})

test('the b, v and l keys still open the standalone menus the row menu shows them for', async ({ page, seeded }) => {
  await open(page)
  const r = row(page, 'E2E Move me')
  await r.hover()
  await r.locator('[aria-label="Select"]').first().click()
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur()) // keys typed in a checkbox are not shortcuts
  await page.keyboard.press('l')
  await expect(page.getByRole('menu', { name: 'Label as' })).toBeVisible()
  await expect(page.getByRole('menu', { name: 'Label as' }).getByRole('textbox', { name: 'Search labels' })).toBeFocused()
  await page.keyboard.press('Escape')
  await page.keyboard.press('b')
  await expect(page.getByRole('menu', { name: 'Snooze until' })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.keyboard.press('v')
  const move = page.getByRole('menu', { name: 'Move to folder' })
  await expect(move.getByRole('textbox', { name: 'Search labels' })).toBeFocused()
  await page.waitForTimeout(200)
  await page.screenshot({ path: `${SHOTS}/standalone-move.png` })
  // Enter moves to the only label left by the filter.
  await page.keyboard.type('E2E Oth')
  await page.keyboard.press('Enter')
  await expect.poll(async () => (await conv(page.request, 'E2E Move me')).label_ids).toContain(seeded.labels['E2E Other'])
})
