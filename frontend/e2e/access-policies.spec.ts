// Settings → Access (admins): group policies and the groups seen; Admin → Users shows each user's
// groups and effective access. Backend: tests/test_access*.py cover the rules.
import { accessTest as test, expect, shot, signIn } from './fixtures'

// One worker for the file, in order: its tests change settings the whole instance shares (Admin,
// the household), which the other tests of the file read.
test.describe.configure({ mode: 'default' })

test('an admin creates and edits a group policy and sees it', async ({ page, access }) => {
  await signIn(page, access.admin)
  await page.goto('/settings/access')
  await expect(page.getByRole('heading', { name: 'Group policies' })).toBeVisible()

  // The seeded family policy, and its group among the groups seen.
  const family = page.getByTestId(`policy-${access.group}`)
  await expect(family).toContainText(`${access.endpoint} · e2e-small`)
  await expect(family).toContainText('1 user')
  const seenRow = page.getByTestId('groups-seen').getByRole('row').filter({ hasText: access.group })
  await expect(seenRow).toContainText('Has a policy')

  // The built-in Everyone policy: shown by name, edited but never deleted.
  const everyone = page.getByTestId('policy-*')
  await expect(everyone).toContainText('Everyone')
  await expect(everyone).toContainText('Built in')
  await expect(everyone.getByRole('button', { name: 'edit' })).toBeVisible()
  await expect(everyone.getByRole('button', { name: 'delete' })).toHaveCount(0)

  // A new policy: any endpoint for both tasks, the decisions lab on.
  const group = `e2e-staff-${access.group.slice(-8)}`
  await page.getByRole('button', { name: 'Add policy' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add policy' })
  await dialog.getByLabel('Group', { exact: true }).fill(group)
  await dialog.getByLabel('Description').fill('Staff')
  await dialog.getByRole('group', { name: 'Ambrose and writing' }).getByRole('checkbox', { name: /Any endpoint/ }).check()
  const sorting = dialog.getByRole('group', { name: 'Sorting and decisions' })
  await sorting.getByRole('button', { name: 'Allow model' }).click()
  await sorting.getByLabel('Sorting and decisions: endpoint 1').selectOption(access.endpoint)
  await sorting.getByLabel('Sorting and decisions: model 1').selectOption('e2e-small')
  await sorting.getByLabel('Sorting and decisions: default').selectOption({ label: `${access.endpoint} · e2e-small` })
  await dialog.getByRole('checkbox', { name: /Decisions lab/ }).check()
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(dialog).toBeHidden()

  const card = page.getByTestId(`policy-${group}`)
  await expect(card).toContainText('Staff')
  await expect(card).toContainText('Any endpoint')
  await expect(card).toContainText(`${access.endpoint} · e2e-small`)
  await expect(card).toContainText('Decisions lab')

  // Edit: a new description, and the assistant off.
  await card.getByRole('button', { name: 'edit' }).click()
  const edit = page.getByRole('dialog', { name: `Policy for ${group}` })
  await edit.getByLabel('Description').fill('Staff, no assistant')
  await edit.getByRole('checkbox', { name: /^Ambrose/ }).uncheck()
  await edit.getByRole('button', { name: 'Save' }).click()
  await expect(edit).toBeHidden()
  await expect(card).toContainText('Staff, no assistant')
  await expect(card.getByText(/^Features/).locator('..')).not.toContainText('Ambrose')
  await page.screenshot({ path: shot('access-policies.png'), fullPage: false })

  // Admin → Users: the family user's groups, and their effective access.
  await page.goto('/settings/admin')
  await page.getByRole('searchbox', { name: 'Search users' }).fill(access.family.email)
  await expect(page.getByTestId(`user-groups-${access.family.id}`)).toContainText(access.group)
  await page.getByRole('row').filter({ hasText: access.family.email }).getByRole('button', { name: 'access' }).click()
  const ua = page.getByRole('dialog', { name: `Access for ${access.family.email}` })
  await expect(ua.getByTestId('user-access')).toContainText(access.group)
  await expect(ua.getByTestId('user-access')).toContainText(`${access.endpoint} · e2e-small`)
  // A password-only account: its groups are assigned here.
  await ua.getByLabel('Assigned groups').fill(`${access.group}, ${group}`)
  await ua.getByRole('button', { name: 'Save groups' }).click()
  await expect(ua.getByTestId('user-access')).toContainText(group)
  await expect(ua.getByTestId('user-access')).toContainText('Decisions lab')
})
