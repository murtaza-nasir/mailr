// ACT-SURFACES: web.reader web.list
// Mid-width layouts: at these widths, in every theme, the
// open message's header draws nothing on top of anything else, no toolbar label is cut ("Com…"),
// no toolbar runs past its pane, and the Desk table's columns follow their content.
// - The sender's line (MessageCard .who-line): the address gives way first, then the date takes
//   its short form; the name keeps its room. An address has no place to break, so it used to run
//   under the date.
// - Toolbars fold to fit (hooks/useToolbarFit): groups drop their labels, then buttons move to More.
// - The chips beside the subject (the email's action, linked events and tasks): a chip that does
//   not fit moves to the next line whole, the action's fact wraps under the action, and an event's
//   status gives way (hooks/useChipFit). No chip label is cut ("Track pac…").
// - Desk: From and Labels as wide as their content (a dragged width still applies, From never
//   more than a third of the table), # as wide as its numbers or gone.
import type { Page } from '@playwright/test'
import { conv, expect, test } from './fixtures'
import { schemes, THEMES, widths } from './matrix'

/** A menu item by its name; the name may end in its shortcut ("Reply r"). */
const item = (page: Page, name: string) => page.getByRole('menuitem', { name: new RegExp(`^${name}( \\S+)?$`) })

/** A wide window with Ambrose open beside the mail, then common laptop widths and a narrow one. */
const SIZES = [
  { width: 1518, height: 582, ambrose: true },
  { width: 1280, height: 800, ambrose: false },
  { width: 1024, height: 768, ambrose: false },
  { width: 800, height: 700, ambrose: false },
  { width: 390, height: 844, ambrose: false },
] as const

async function setup(page: Page, theme: string, opts: { ambrose?: boolean; dark?: boolean } = {}) {
  await page.addInitScript(([t, a]) => {
    localStorage.setItem('mailr.uiTheme', t as string)
    if (a) localStorage.setItem('mailr.panel', 'agent')
    else localStorage.removeItem('mailr.panel')
    // From dragged wide.
    localStorage.setItem('mailr.desk.cols', JSON.stringify({ from: 302 }))
  }, [theme, !!opts.ambrose] as const)
  await page.emulateMedia({ colorScheme: opts.dark ? 'dark' : 'light', reducedMotion: 'reduce' })
}

/** The sender as in 310.png: a short name and an address with no place to break. */
async function longSender(page: Page, cid: number) {
  await page.route(`**/api/v1/conversations/${cid}`, async (route) => {
    const r = await route.fetch()
    const j = (await r.json()) as { messages: { from: { name: string; email: string } }[] }
    for (const m of j.messages) m.from = { name: 'Rowan', email: 'rowan.hale@example.com' }
    await route.fulfill({ response: r, json: j })
  })
}

/** A linked event (with its "Tomorrow 3 PM" status) and a linked task beside the subject. */
async function linkedChips(page: Page) {
  await page.route('**/api/v1/conversations/*/links', (route) => route.fulfill({ json: [
    { id: 1, kind: 'event', uid: 'e2e-narrow-ev', summary: 'Parcel delivery window' },
    { id: 2, kind: 'task', uid: 'e2e-narrow-task', summary: 'Check the parcel' },
  ] }))
  const start = new Date(Date.now() + 86_400_000)
  await page.route('**/api/v1/calendar/events/e2e-narrow-ev', (route) => route.fulfill({ json: {
    uid: 'e2e-narrow-ev', summary: 'Parcel delivery window', status: 'CONFIRMED', rrule: null,
    start: start.toISOString(), end: new Date(start.getTime() + 3_600_000).toISOString(),
  } }))
}

/** What is wrong with the layout on screen now: overlapping header parts, cut labels, overflowing bars. */
function problems(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = []
    const shown = (el: Element) => {
      const r = el.getBoundingClientRect()
      return r.width > 0.5 && r.height > 0.5 && getComputedStyle(el).visibility !== 'hidden'
    }
    const name = (el: Element) => (el.getAttribute('aria-label') ?? el.textContent ?? el.className).toString().trim().slice(0, 40)
    for (const bar of document.querySelectorAll('[role="toolbar"]')) {
      if (!shown(bar)) continue
      for (const l of bar.querySelectorAll('.lbl')) {
        if (shown(l) && getComputedStyle(l).display !== 'inline' && l.scrollWidth > l.clientWidth + 1) out.push(`cut label "${l.textContent}"`)
      }
    }
    for (const bar of document.querySelectorAll('.rb-body, .d-toolbar, .conv-view-head')) {
      if (shown(bar) && bar.scrollWidth > bar.clientWidth + 1) out.push(`toolbar runs past its edge: ${name(bar)} (${bar.scrollWidth} > ${bar.clientWidth})`)
    }
    // The subject line's chips: no label or fact is cut (a long one would be; these are short).
    for (const l of document.querySelectorAll('.conv-chips :is(.action-label, .action-fact, .cl-title, .cl-status, .nt-link-name), .conv-subject .title .label-chip')) {
      if (l.matches('[data-chip-folded] .cl-status')) continue // folded away on purpose (hooks/useChipFit)
      if (shown(l) && l.scrollWidth > l.clientWidth + 1) out.push(`cut chip "${l.textContent}" (${l.scrollWidth} > ${l.clientWidth})`)
    }
    // Desk: the seeded senders are short, so no From cell is ever cut.
    for (const cell of document.querySelectorAll('.d-row .d-from')) {
      if (shown(cell) && cell.scrollWidth > cell.clientWidth + 1) out.push(`From cut: "${cell.textContent}"`)
    }
    for (const head of document.querySelectorAll('.msg:not(.collapsed) .msg-head')) {
      if (!shown(head)) continue
      const parts = [...head.querySelectorAll('.who-line > *, .meta .date, .meta .icon-btn')].filter(shown)
      for (let i = 0; i < parts.length; i++) {
        for (let j = i + 1; j < parts.length; j++) {
          const a = parts[i].getBoundingClientRect()
          const b = parts[j].getBoundingClientRect()
          const x = Math.min(a.right, b.right) - Math.max(a.left, b.left)
          const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
          if (x > 1 && y > 1) out.push(`header overlap: "${name(parts[i])}" and "${name(parts[j])}"`)
        }
      }
      const who = head.querySelector('.who')!.getBoundingClientRect()
      for (const p of parts.filter((p) => p.closest('.who-line'))) {
        const r = p.getBoundingClientRect()
        if (r.right > who.right + 1 || r.left < who.left - 1) out.push(`"${name(p)}" runs out of the sender's box`)
      }
      // The name keeps priority: it is cut only when the address has given way completely.
      const nm = head.querySelector('.who-line .name')
      const em = head.querySelector('.who-line .email')
      if (nm && em && shown(em) && nm.scrollWidth > nm.clientWidth + 1 && em.getBoundingClientRect().top === nm.getBoundingClientRect().top) {
        out.push('the name is cut while the address still shows beside it')
      }
    }
    return out
  })
}

for (const theme of THEMES) {
  test(`${theme}: the reader header and toolbars fit at narrow desktop widths`, async ({ page }) => {
    test.setTimeout(240_000)
    const c = await conv(page.request, 'E2E Track me')
    await longSender(page, c.id)
    await linkedChips(page)
    // The matrix (e2e/matrix.ts): nightly every size; otherwise the narrowest and the widest.
    for (const size of widths(SIZES)) {
      // A fresh page per size, so the panel state and the layout start as they would on load.
      await setup(page, theme, { ambrose: size.ambrose })
      await page.setViewportSize({ width: size.width, height: size.height })
      await page.goto('/mail/inbox')
      await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
      expect(await problems(page), `${theme} list at ${size.width}`).toEqual([])
      await page.goto(`/mail/inbox/${c.id}`)
      await expect(page.locator('.msg:not(.collapsed) .who-line .name').first()).toHaveText('Rowan')
      await expect(page.locator('.conv-chips .cl-status').first()).toContainText(/\S/)
      expect(await problems(page), `${theme} reader at ${size.width}`).toEqual([])
    }
  })
}

test('dark: every theme fits at a 1280 px window with Ambrose open', async ({ page }) => {
  test.setTimeout(240_000)
  const c = await conv(page.request, 'E2E Track me')
  await longSender(page, c.id)
  await linkedChips(page)
  await page.setViewportSize({ width: 1518, height: 582 })
  // Nightly every theme; otherwise the themes whose extra look is dark (e2e/matrix.ts).
  for (const theme of THEMES.filter((t) => schemes(t).includes('dark'))) {
    await setup(page, theme, { ambrose: true, dark: true })
    await page.goto(`/mail/inbox/${c.id}`)
    await expect(page.locator('.msg:not(.collapsed) .who-line .name').first()).toHaveText('Rowan')
    await expect(page.locator('.conv-chips .cl-status').first()).toContainText(/\S/)
    expect(await problems(page), `${theme} dark`).toEqual([])
  }
})

test('the reader header gives way in order: the address, then the long date, then a line of its own', async ({ page }) => {
  const c = await conv(page.request, 'E2E Track me')
  await longSender(page, c.id)
  await setup(page, 'classic')
  await page.goto(`/mail/inbox/${c.id}`)
  const head = page.locator('.msg:not(.collapsed) .msg-head').first()
  await expect(head.locator('.who-line .name')).toHaveText('Rowan')
  // Set the header's own width (it is a container: the pane's width counts, not the window's).
  const at = async (w: number) => {
    // A re-render (the chips measuring themselves after the links load) can replace the header
    // element and drop the width set here: set it until it holds on the element on screen.
    await expect.poll(() => head.evaluate((el, px) => {
      (el as HTMLElement).style.width = `${px}px`
      return Math.round(el.getBoundingClientRect().width)
    }, w)).toBe(w)
    const r = await head.evaluate(async (el, px) => {
      (el as HTMLElement).style.width = `${px}px`
      // Reduced motion turns every change into a 0.01ms transition (base.css): read after it.
      for (let i = 0; i < 2; i++) await new Promise((done) => requestAnimationFrame(done))
      const email = el.querySelector('.who-line .email')!
      const name = el.querySelector('.who-line .name')!
      return {
        width: Math.round(el.getBoundingClientRect().width),
        long: getComputedStyle(el.querySelector('.date .long')!).display !== 'none',
        emailOwnLine: email.getBoundingClientRect().top > name.getBoundingClientRect().bottom - 2,
      }
    }, w)
    expect(await problems(page), `header at ${w}px`).toEqual([])
    return r
  }
  // Wide: everything on one line, the long date. At 480px and less: the short date. At 380px and
  // less: the address on its own line. At every width: nothing overlaps, the name is never cut
  // while the address still shows beside it (problems()).
  for (const w of [1400, 1100, 900, 700, 560, 500, 470, 420, 380, 340, 300]) {
    const r = await at(w)
    expect(r, `${w}px`).toEqual({ width: w, long: w > 480, emailOwnLine: w <= 380 })
  }
})

test('subject chips: a narrow pane moves them to their own lines and folds a status, never cutting a label', async ({ page }) => {
  const c = await conv(page.request, 'E2E Track me')
  await linkedChips(page)
  await setup(page, 'classic')
  await page.goto(`/mail/inbox/${c.id}`)
  const subject = page.locator('.conv-subject')
  const event = page.locator('.conv-chips .cl-chip').first()
  await expect(event.locator('.cl-status')).toContainText(/\S/)
  // The full text is in the tooltip, whatever the width.
  await expect(event).toHaveAttribute('title', /^Parcel delivery window · \S/)
  await expect(page.locator('.conv-chips .action-chip')).toHaveAttribute('aria-label', 'Track package')
  const read = () => subject.evaluate((el) => {
    const top = (sel: string) => el.querySelector(sel)!.getBoundingClientRect().top
    return {
      factUnder: top('.conv-chips .action-fact') > top('.conv-chips .action-chip') + 4,
      folded: el.querySelector('.conv-chips .cl-chip')!.hasAttribute('data-chip-folded'),
    }
  })
  const at = (w: number) => subject.evaluate((el, px) => { (el as HTMLElement).style.width = `${px}px` }, w)
  // Wide: the fact beside the action, the status in its chip. Narrower: the fact goes under the
  // action, and the status leaves the chip (screen readers keep it). problems() checks no label is cut.
  for (const [w, want] of [[1000, { factUnder: false, folded: false }], [380, { factUnder: true, folded: true }]] as const) {
    await at(w)
    await expect.poll(read, { message: `${w}px` }).toEqual(want)
    expect(await problems(page), `chips at ${w}px`).toEqual([])
  }
  await expect(event.locator('.cl-status')).toHaveCount(1)
  await at(1000)
  await expect(event).not.toHaveAttribute('data-chip-folded', /.*/)
})

test('office: a narrow ribbon drops labels by group, keeps Compose\'s longest, then moves groups to More', async ({ page }) => {
  await setup(page, 'office')
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
  const ribbon = page.locator('.ribbon')
  const body = ribbon.locator('.rb-body')
  const label = (name: string) => body.locator('.rb-btn', { hasText: name }).locator('.lbl')
  // Wide: every label shows, nothing in More.
  await expect(label('Compose')).toBeVisible()
  await expect(label('Delete')).toBeVisible()
  await expect(body.getByTestId('toolbar-more')).toHaveCount(0)
  // Narrower: groups drop their labels (the tooltip and the accessible name keep them); Compose keeps its own.
  await ribbon.evaluate((el) => { (el as HTMLElement).style.width = '760px' })
  await expect(body.getByRole('button', { name: 'Reply', exact: true }).locator('.lbl')).toBeHidden()
  await expect(label('Compose')).toBeVisible()
  expect(await problems(page)).toEqual([])
  // Narrower still: whole groups leave for More, in order; their commands work from there.
  await ribbon.evaluate((el) => { (el as HTMLElement).style.width = '360px' })
  const more = body.getByTestId('toolbar-more')
  await expect(more).toBeVisible()
  await expect(body.getByRole('button', { name: 'Reply', exact: true })).toBeHidden()
  await expect(body.getByRole('button', { name: 'Delete', exact: true })).toBeVisible()
  expect(await problems(page)).toEqual([])
  await more.click()
  for (const name of ['Reply', 'Forward', 'Flag']) await expect(item(page, name)).toBeVisible()
  await page.keyboard.press('Escape')
  // Wide again: everything comes back.
  await ribbon.evaluate((el) => { (el as HTMLElement).style.width = '' })
  await expect(more).toHaveCount(0)
  await expect(label('Delete')).toBeVisible()
})

test('desk: the toolbar folds instead of cutting labels, and More holds what left', async ({ page }) => {
  await setup(page, 'desk')
  await page.setViewportSize({ width: 1440, height: 800 })
  await page.goto('/mail/inbox')
  await expect(page.locator('[data-mail-list] [role="row"]').first()).toBeVisible()
  const bar = page.locator('.d-toolbar')
  await expect(bar.locator('.d-tool-compose .lbl')).toBeVisible()
  await bar.evaluate((el) => { (el as HTMLElement).style.width = '1000px' })
  await expect(bar.locator('.d-tool-reply .lbl')).toBeHidden()
  await expect(bar.locator('.d-tool-compose .lbl')).toBeVisible()
  expect(await problems(page)).toEqual([])
  await bar.evaluate((el) => { (el as HTMLElement).style.width = '760px' })
  const more = bar.getByTestId('toolbar-more')
  await expect(more).toBeVisible()
  expect(await problems(page)).toEqual([])
  // Every button that left the bar is in More.
  const gone = await bar.locator('.d-tools[data-fold-gone] .d-tool').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')!))
  expect(gone.length).toBeGreaterThan(0)
  await more.click()
  for (const name of gone) await expect(item(page, name)).toBeVisible()
})

test('reader toolbar: a narrow pane moves buttons into ⋮ one at a time, never past the edge', async ({ page }) => {
  await setup(page, 'cards')
  await page.setViewportSize({ width: 1920, height: 900 })
  // Opened from the list, so the pager knows its place ("9 of 21").
  await page.goto('/mail/inbox')
  await page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Track me' }).first().click()
  const head = page.locator('.conv-view .conv-view-head')
  await expect(head.getByTestId('reader-create')).toBeVisible()
  await expect(head.locator('.conv-pager')).toBeVisible()
  const width = (px: string) => page.locator('.conv-view').evaluate((el, w) => { (el as HTMLElement).style.width = w }, px)
  await width('330px')
  await expect(head.getByTestId('reader-create')).toBeHidden()
  await expect(head.locator('.conv-pager')).toBeHidden()
  expect(await problems(page)).toEqual([])
  await head.getByRole('button', { name: 'More' }).click()
  for (const name of ['Newer conversation', 'Create from email']) await expect(item(page, name)).toBeVisible()
  await page.keyboard.press('Escape')
  await width('')
  await expect(head.getByTestId('reader-create')).toBeVisible()
  await head.getByRole('button', { name: 'More' }).click()
  await expect(page.getByRole('menuitem', { name: 'Mute' })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: 'Create from email' })).toBeHidden()
})

test('desk table: From and Labels follow their content, # shows only with numbers', async ({ page }) => {
  await setup(page, 'desk')
  await page.setViewportSize({ width: 1518, height: 582 })
  await page.goto('/mail/inbox')
  await expect(page.locator('.d-row').first()).toBeVisible()
  const col = (cls: string) => page.locator(`.d-cols .${cls}`).evaluate((el) => el.getBoundingClientRect().width)
  const table = await page.locator('.d-cols').evaluate((el) => el.getBoundingClientRect().width)
  // A From of 302px dragged earlier; the senders need far less, so Subject gets the room.
  const from = await col('d-from')
  expect(from).toBeLessThan(200)
  expect(from).toBeLessThanOrEqual(table * 0.33)
  expect(await col('d-subject')).toBeGreaterThan(from * 2)
  // No labels on these rows: Labels shrinks to its heading.
  expect(await col('d-labels')).toBeLessThan(100)
  // The rows follow the header.
  const row = await page.locator('.d-row .d-from').first().evaluate((el) => el.getBoundingClientRect().width)
  expect(Math.abs(row - from)).toBeLessThan(1)

  // No row with more than one message: no # column.
  await page.route('**/api/v1/conversations?*', async (route) => {
    const r = await route.fetch()
    const j = (await r.json()) as { items?: { message_count: number }[] }
    for (const c of j.items ?? []) c.message_count = 1
    await route.fulfill({ response: r, json: j })
  })
  await page.reload()
  await expect(page.locator('.d-row').first()).toBeVisible()
  await expect(page.locator('.list-grid[data-no-count]')).toHaveCount(1)
  expect(await col('d-count')).toBe(0)
})

// Longer words (en-XA, the long pseudo-language) and right to left (ar-XB): the toolbars fold by
// measuring, so no label is cut in any language, and the header keeps its order mirrored.
for (const lang of ['en-XA', 'ar-XB'] as const) {
  test(`${lang}: the ribbon, the Desk toolbar and the reader header fit with Ambrose open`, async ({ page }) => {
    test.setTimeout(180_000)
    const c = await conv(page.request, 'E2E Track me')
    await longSender(page, c.id)
    await linkedChips(page)
    await page.setViewportSize({ width: 1518, height: 582 })
    for (const theme of ['office', 'desk', 'cards'] as const) {
      await setup(page, theme, { ambrose: true })
      await page.addInitScript((l) => {
        localStorage.setItem('mailr.i18n.pseudo', '1')
        localStorage.setItem('mailr.language', l)
      }, lang)
      await page.goto(`/mail/inbox/${c.id}`)
      await expect(page.locator('.msg:not(.collapsed) .who-line .name').first()).toHaveText('Rowan')
      if (lang === 'ar-XB') await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
      await expect(page.locator('.conv-chips .cl-status').first()).toContainText(/\S/)
      expect(await problems(page), `${theme} ${lang}`).toEqual([])
    }
  })
}
