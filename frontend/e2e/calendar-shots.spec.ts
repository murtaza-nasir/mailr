// Screenshots of every calendar view in every theme, light and dark, for visual review. Runs only
// with CAL_SHOTS=<dir> (it asserts nothing the other calendar specs do not):
//   CAL_SHOTS=/tmp/shots npx playwright test e2e/calendar-shots.spec.ts
import { test } from './fixtures'
import { dayOf, openIn, seedWeek, THEMES } from './calendar-data'

const dir = process.env.CAL_SHOTS
const only = process.env.CAL_SHOTS_THEMES?.split(',')
const views = (process.env.CAL_SHOTS_VIEWS ?? 'week,day,ndays,workweek,month,schedule,search,detail,quick,editor,phone').split(',')

test.describe('calendar screenshots', () => {
  test.skip(!dir, 'set CAL_SHOTS to a directory to take screenshots')
  test.setTimeout(240_000)

  for (const theme of THEMES.filter((t) => !only || only.includes(t))) {
    for (const dark of [false, true]) {
      test(`${theme} ${dark ? 'dark' : 'light'}`, async ({ page, seeded }) => {
        await seedWeek(page.request, seeded)
        const tag = `${theme}-${dark ? 'dark' : 'light'}`
        const shot = async (name: string) => {
          await page.waitForTimeout(350)
          await page.screenshot({ path: `${dir}/${name}-${tag}.png` })
        }
        const today = dayOf(3)
        for (const v of ['week', 'day', 'ndays', 'workweek', 'month', 'schedule'].filter((x) => views.includes(x))) {
          await openIn(page, theme, `/calendar/${v}/${today}`, dark)
          await page.getByTestId('calendar-app').waitFor()
          await page.waitForSelector('[data-testid="cal-event"]', { timeout: 15_000 }).catch(() => {})
          await shot(v)
        }
        if (views.includes('search')) {
          await openIn(page, theme, '/calendar/search?q=review', dark)
          await page.waitForSelector('[data-testid="cal-search"] [data-testid="cal-event"]', { timeout: 15_000 }).catch(() => {})
          await shot('search')
        }
        if (views.includes('detail')) {
          await openIn(page, theme, `/calendar/week/${today}`, dark)
          await page.getByText('Budget review').first().click()
          await page.getByTestId('cal-detail').waitFor()
          await shot('detail')
        }
        if (views.includes('quick')) {
          await openIn(page, theme, `/calendar/week/${today}`, dark)
          await page.getByTestId('calendar-app').waitFor()
          await page.keyboard.press('c')
          await page.getByRole('textbox', { name: 'Event, in one line' }).fill('Lunch with Dana tomorrow 12:30 at Café Luna #Personal')
          await shot('quick')
        }
        if (views.includes('editor')) {
          await page.keyboard.press('Control+Enter')
          await page.getByTestId('cal-editor').waitFor()
          await shot('editor')
        }
        if (views.includes('phone')) {
          await page.setViewportSize({ width: 390, height: 844 })
          for (const v of ['ndays', 'month', 'schedule']) {
            await openIn(page, theme, `/calendar/${v}/${today}`, dark)
            await page.getByTestId('calendar-app').waitFor()
            await page.waitForSelector('[data-testid="cal-event"]', { timeout: 15_000 }).catch(() => {})
            await shot(`phone-${v}`)
          }
        }
      })
    }
  }
})
