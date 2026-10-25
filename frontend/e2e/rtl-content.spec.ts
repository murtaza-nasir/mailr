// Right-to-left text in an English page: what people and their
// correspondents write follows its own direction. Arabic and Hebrew subjects, snippets, sender
// names, mail bodies (plain text and HTML), the compose fields, notes and tasks read right to left,
// and their punctuation stays at their end: "שלום, עולם!" shows its "!" on the left. English text
// beside it stays left to right. The mail responses are edited in flight.
import type { Locator, Page } from '@playwright/test'
import { connectCalendar, expect, test } from './fixtures'

const AR_SUBJECT = 'اجتماع الفريق غداً؟'
const HE_SNIPPET = 'שלום, עולם!'
const AR_NAME = 'محمد علي'
const AR_LINE = 'مرحبا يا فريق!'
const HE_ITEM = 'חלב וביצים!'
const EN_LINE = 'The report is ready.'

/** The computed direction of an element. */
const direction = (l: Locator) => l.evaluate((el) => getComputedStyle(el).direction)

/** Where a character of an element's text is drawn: its left edge on the screen. */
async function charX(l: Locator, ch: string, nth = 0): Promise<number> {
  return l.evaluate((el, [c, n]) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
    let seen = 0
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent ?? ''
      for (let i = text.indexOf(c as string); i >= 0; i = text.indexOf(c as string, i + 1)) {
        if (seen++ < (n as number)) continue
        const r = document.createRange()
        r.setStart(node, i)
        r.setEnd(node, i + (c as string).length)
        return r.getBoundingClientRect().left
      }
    }
    throw new Error(`no ${c} in ${el.textContent}`)
  }, [ch, nth] as const)
}

/** The mail list and the conversation say what this test needs: Arabic and Hebrew on two rows. */
async function rewriteMail(page: Page) {
  await page.route(/\/api\/v1\/conversations\?/, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    for (const c of body.items ?? []) {
      if (c.subject === 'E2E Read me') {
        c.subject = AR_SUBJECT
        c.snippet = HE_SNIPPET
        if (c.participants?.[0]) c.participants[0].name = AR_NAME
      }
    }
    await route.fulfill({ response: res, json: body })
  })
  await page.route(/\/api\/v1\/conversations\/\d+$/, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    const conv = body.conversation
    if (conv && (conv.subject === 'E2E Read me' || conv.subject === 'E2E Archive me')) {
      const arabic = conv.subject === 'E2E Read me'
      if (arabic) conv.subject = AR_SUBJECT
      for (const m of body.messages ?? []) {
        m.subject = conv.subject
        if (arabic) m.from = { ...m.from, name: AR_NAME }
        m.to = [{ email: 'dana@example.org', name: 'דנה' }, { email: 'lee@example.org', name: 'Lee Park' }]
        m.snippet = HE_SNIPPET
      }
    }
    await route.fulfill({ response: res, json: body })
  })
}

test.describe('right-to-left text in an English page', () => {
  test('mail: rows, the reader and a plain-text body take their own direction', async ({ page, seeded }) => {
    await rewriteMail(page)
    const id = seeded.conversations['E2E Read me']
    await page.route(/\/api\/v1\/messages\/\d+\/body/, async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      body.html = null
      body.text = `${AR_LINE}\n${EN_LINE}\n${HE_SNIPPET}`
      await route.fulfill({ response: res, json: body })
    })
    await page.goto('/mail/inbox')
    await expect(page.locator('html')).toHaveAttribute('dir', 'ltr')
    const row = page.locator('[data-mail-list] [role="row"]').filter({ hasText: AR_SUBJECT }).first()
    await expect(row).toBeVisible()

    // The row: subject, snippet and sender read right to left; an English row beside it does not.
    const subject = row.locator('.subject')
    expect(await direction(subject)).toBe('rtl')
    expect(await direction(row.locator('.snippet'))).toBe('rtl')
    expect(await direction(row.locator('.sender'))).toBe('rtl')
    const english = page.locator('[data-mail-list] [role="row"]').filter({ hasText: 'E2E Archive me' }).first().locator('.subject')
    expect(await direction(english)).toBe('ltr')
    // Punctuation stays at the end of the Hebrew and the Arabic: on the left.
    const snippet = row.locator('.snippet')
    expect(await charX(snippet, '!')).toBeLessThan(await charX(snippet, 'ש'))
    expect(await charX(subject, '؟')).toBeLessThan(await charX(subject, 'ا'))

    // The reader: the subject, the sender, and the names in "to …" each in their own direction.
    await page.goto(`/mail/inbox/${id}`)
    const title = page.locator('.subject-text').first()
    await expect(title).toHaveText(AR_SUBJECT)
    expect(await direction(title)).toBe('rtl')
    expect(await charX(title, '؟')).toBeLessThan(await charX(title, 'ا'))
    const card = page.locator('article.msg').last()
    await expect(card.locator('.name').first()).toHaveText(AR_NAME)
    expect(await direction(card.locator('bdi.name').first())).toBe('rtl')
    // "to דנה, Lee Park": the Hebrew name is isolated, so the comma stays after it.
    const toLine = card.locator('.to-line .ellipsis')
    await expect(toLine).toContainText('Lee Park')
    expect(await toLine.textContent()).toContain('⁨דנה⁩')
    expect(await charX(toLine, ',')).toBeGreaterThan(await charX(toLine, 'ד'))

    // The plain-text body: the Arabic line aligns right with its "!" on the left, the English line
    // aligns left, the Hebrew line right.
    const frame = card.frameLocator('iframe')
    const lines = frame.locator('body > div').first()
    await expect(lines).toContainText(EN_LINE)
    const box = await lines.evaluate((el) => el.getBoundingClientRect().toJSON())
    const arBang = await charX(lines, '!', 0)
    const arStart = await charX(lines, 'م')
    expect(arBang).toBeLessThan(arStart)
    const enStart = await charX(lines, 'T')
    expect(enStart - box.left).toBeLessThan(4)
    const heBang = await charX(lines, '!', 1)
    expect(heBang).toBeLessThan(await charX(lines, 'ש'))
    // Right-aligned: the Arabic line's first letter sits near the frame's right edge.
    expect(box.right - arStart).toBeLessThan(40)
  })

  test('mail: an HTML body keeps each paragraph in its own direction', async ({ page, seeded }) => {
    await rewriteMail(page)
    await page.route(/\/api\/v1\/messages\/\d+\/body/, async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      body.html = `<p>${AR_LINE}</p><p>${EN_LINE}</p><div dir="ltr"><p>${HE_SNIPPET} (written left to right on purpose)</p></div>`
      body.text = null
      await route.fulfill({ response: res, json: body })
    })
    await page.goto(`/mail/inbox/${seeded.conversations['E2E Archive me']}`)
    const frame = page.locator('article.msg').last().frameLocator('iframe')
    const ar = frame.locator('p').nth(0)
    const en = frame.locator('p').nth(1)
    const kept = frame.locator('p').nth(2)
    await expect(en).toHaveText(EN_LINE)
    expect(await charX(ar, '!')).toBeLessThan(await charX(ar, 'م'))
    const arBox = await ar.evaluate((el) => el.getBoundingClientRect().toJSON())
    expect(arBox.right - (await charX(ar, 'م'))).toBeLessThan(20)
    const enBox = await en.evaluate((el) => el.getBoundingClientRect().toJSON())
    expect((await charX(en, 'T')) - enBox.left).toBeLessThan(4)
    // A direction the message sets itself is kept: this paragraph stays left to right.
    expect(await kept.evaluate((el) => getComputedStyle(el).unicodeBidi)).not.toBe('plaintext')
  })

  test('mail: a card centred by align="center" stays centred, with its centred text', async ({ page, seeded }) => {
    await rewriteMail(page)
    await page.route(/\/api\/v1\/messages\/\d+\/body/, async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      body.html = `<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="background:#dedede">`
        + `<table id="card" style="width:300px;background:#fff"><tr><td align="center"><div id="mid">Centred line</div></td></tr></table>`
        + `</td></tr></table>`
      body.text = null
      await route.fulfill({ response: res, json: body })
    })
    await page.goto(`/mail/inbox/${seeded.conversations['E2E Archive me']}`)
    const frame = page.locator('article.msg').last().frameLocator('iframe')
    await expect(frame.locator('#mid')).toHaveText('Centred line')
    const [card, view, mid] = await frame.locator('#card').evaluate((el) => {
      const d = el.ownerDocument
      const m = d.getElementById('mid')!
      const range = d.createRange()
      range.selectNodeContents(m)
      return [el.getBoundingClientRect().toJSON(), d.documentElement.clientWidth, range.getBoundingClientRect().toJSON()]
    })
    // The card sits in the middle of the frame, not against the start edge.
    expect(Math.abs(card.left - (view - card.right))).toBeLessThan(4)
    // The text in its centred cell is centred inside the card.
    expect(Math.abs((mid.left - card.left) - (card.right - mid.right))).toBeLessThan(4)
  })

  test('compose: the subject and the body follow what is typed', async ({ page }) => {
    await page.goto('/mail/inbox')
    await page.getByRole('button', { name: 'Compose', exact: true }).first().click()
    const subject = page.getByRole('textbox', { name: 'Subject' })
    await subject.fill(AR_SUBJECT)
    expect(await subject.getAttribute('dir')).toBe('auto')
    expect(await direction(subject)).toBe('rtl')
    const body = page.getByRole('textbox', { name: 'Message body' })
    await body.click()
    await page.keyboard.type(AR_LINE)
    await page.keyboard.press('Enter')
    await page.keyboard.type(EN_LINE)
    const paras = body.locator('p')
    await expect(paras).toHaveCount(2)
    await expect(paras.first()).toHaveAttribute('dir', 'auto')
    expect(await direction(paras.first())).toBe('rtl')
    expect(await direction(paras.nth(1))).toBe('ltr')
    expect(await charX(paras.first(), '!')).toBeLessThan(await charX(paras.first(), 'م'))
  })

  test('notes: titles, text and items take their own direction', async ({ page }) => {
    const note = await (await page.request.post('/api/v1/notes', { data: { kind: 'text', title: AR_SUBJECT, body: `${AR_LINE}\n${EN_LINE}` } })).json()
    expect(note.note.id).toBeTruthy()
    const list = await (await page.request.post('/api/v1/notes', { data: { kind: 'checklist', title: 'Shopping', lines: `${HE_ITEM}\nmilk` } })).json()
    expect(list.note.id).toBeTruthy()

    await page.goto('/notes')
    const title = page.locator('.nt-card-title').filter({ hasText: AR_SUBJECT }).first()
    await expect(title).toBeVisible()
    expect(await direction(title)).toBe('rtl')
    const cardBody = page.locator('.nt-card-body').filter({ hasText: EN_LINE }).first()
    const arPara = cardBody.locator('p').first()
    expect(await charX(arPara, '!')).toBeLessThan(await charX(arPara, 'م'))
    const item = page.locator('.nt-item-text').filter({ hasText: HE_ITEM }).first()
    expect(await direction(item)).toBe('rtl')
    expect(await charX(item, '!')).toBeLessThan(await charX(item, 'ח'))
    expect(await direction(page.locator('.nt-item-text').filter({ hasText: 'milk' }).first())).toBe('ltr')
    // The English paragraph under the Arabic one stays left to right.
    const enPara = cardBody.locator('p').nth(1)
    expect(await charX(enPara, 'T')).toBeLessThan(await charX(enPara, '.'))
  })
})

test.describe('right-to-left text in an English page, with a calendar', () => {
  test('tasks: a title reads right to left with its punctuation at its end', async ({ page, seeded }) => {
    await connectCalendar(page.request, seeded)
    const r = await page.request.post('/api/v1/tasks', { data: { summary: HE_SNIPPET } })
    expect(r.ok(), await r.text()).toBeTruthy()
    await page.goto('/tasks/all')
    const task = page.locator('.tk-title').filter({ hasText: HE_SNIPPET }).first()
    await expect(task).toBeVisible()
    expect(await direction(task)).toBe('rtl')
    expect(await charX(task, '!')).toBeLessThan(await charX(task, 'ש'))
  })
})
