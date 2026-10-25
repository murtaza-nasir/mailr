// ACT-SURFACES: web.settings
// "Copy into my Mailr calendar…" on a connected CalDAV account (Settings → Calendar & Tasks;
// backend calendar/migrate.py): the preview with counts and what stays behind, the copy with
// progress, the report, and the optional cut-over. The connectors and the migration job are mocked
// (e2e/calendar-migrate-data.ts): the e2e database keeps built-in calendars off and runs no Davis.
// Backend: tests/test_calendar_migrate*.py. Actions covered: calendar.migrate, calendar.migrate_cutover.
import { expect, test } from './fixtures'
import { mockMigration } from './calendar-migrate-data'

test('a connected account is copied into the Mailr calendar after a preview (calendar.migrate)', async ({ page }) => {
  const mock = await mockMigration(page)
  await page.goto('/settings/calendar')
  await page.locator('.card', { hasText: 'cal.example.com' }).getByTestId('migrate-open').click()
  const dlg = page.getByRole('dialog', { name: 'Copy Davis into your Mailr calendar' })
  await expect(dlg).toBeVisible()
  // The preview runs first: a dry run, with progress.
  await expect.poll(() => mock.starts).toEqual([{ dry_run: true, overwrite: false }])
  await expect(dlg.getByRole('table', { name: 'Copy preview' })).toBeVisible()
  const personal = dlg.getByRole('row', { name: /Personal/ })
  await expect(personal).toContainText('Into “Calendar”')
  await expect(personal).toContainText('409')
  await expect(dlg.getByRole('row', { name: /Chores/ })).toContainText('task list')
  await expect(dlg.getByRole('row', { name: /Work/ })).toContainText('New: Work')
  // What a copy cannot carry over, with what to do instead.
  const notCopied = dlg.getByTestId('migrate-not-copied')
  await expect(notCopied).toContainText('Kai Berg')
  await expect(notCopied).toContainText('open Share… on each calendar and add this person with Edit')
  await expect(notCopied).toContainText('Public holidays')
  // One item differs: kept unless the user asks for the other version.
  const replace = dlg.getByRole('checkbox', { name: /Replace the item that differs/ })
  await expect(replace).not.toBeChecked()
  await expect(dlg.getByRole('button', { name: 'Copy 672 items' })).toBeVisible()
  await replace.check()
  await expect(dlg.getByRole('button', { name: 'Copy 673 items' })).toBeEnabled()
  await dlg.getByRole('button', { name: 'Copy 673 items' }).click()
  await expect.poll(() => mock.starts).toEqual([{ dry_run: true, overwrite: false }, { dry_run: false, overwrite: true }])
  await expect(dlg.getByRole('status')).toContainText("Copied 671 items, replaced 1; 2 were already there; 1 couldn't be copied.")
  await expect(dlg.getByRole('table', { name: 'Copy results' })).toBeVisible()
  await expect(dlg.getByRole('list', { name: 'Not copied' })).toContainText('Too large for the Mailr calendar')
})

test('the cut-over is optional and changes only what the user ticks (calendar.migrate_cutover)', async ({ page }) => {
  const mock = await mockMigration(page)
  await page.goto('/settings/calendar')
  await page.locator('.card', { hasText: 'cal.example.com' }).getByTestId('migrate-open').click()
  const dlg = page.getByRole('dialog', { name: 'Copy Davis into your Mailr calendar' })
  await dlg.getByRole('button', { name: /^Copy \d+ items$/ }).click()
  const cut = dlg.getByTestId('migrate-cutover')
  await expect(cut).toContainText('Both options change only Mailr')
  const apply = dlg.getByRole('button', { name: 'Apply' })
  await expect(apply).toBeDisabled()
  await cut.getByRole('checkbox', { name: 'Use the Mailr calendar for new events and tasks' }).check()
  await cut.getByRole('checkbox', { name: /Hide the copied calendars of Davis/ }).check()
  await apply.click()
  await expect.poll(() => mock.cutovers).toEqual([{ make_default: true, hide_copied: true }])
  await expect(cut).toContainText('New events and tasks now go to the Mailr calendar. 3 calendars of Davis hidden in Calendar.')
  await expect(apply).toBeDisabled()
})

test('no copy is offered without a Mailr calendar', async ({ page }) => {
  await mockMigration(page, { builtin: false })
  await page.goto('/settings/calendar')
  // The account card names the server by its host, not the full CalDAV address.
  await expect(page.locator('.card .sub').filter({ hasText: 'cal.example.com' }).first()).toBeVisible()
  await expect(page.getByText('cal.example.com/dav/')).toHaveCount(0)
  await expect(page.getByTestId('migrate-open')).toHaveCount(0)
})
