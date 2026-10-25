// ACT-SURFACES: web.settings
// The one map of Settings (groups.ts): every section has a component, ids are unique, old
// addresses redirect, and a search finds every user setting of tools/registry/settings.
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { allSections, findSection, FORMER_NAMES, legacyTarget, OUTSIDE_SETTINGS, SETTINGS_GROUPS } from './groups'
import { settingsEntries } from './palette'
import { SECTION_COMPONENTS } from './sections'
import { settingsVisibility } from './visibility'

const REGISTRY = join(dirname(fileURLToPath(import.meta.url)), '../../../../tools/registry/settings')

/** `key: …` lines of the registry, without the internal ones. */
function registryKeys(): string[] {
  const out: string[] = []
  for (const f of readdirSync(REGISTRY).filter((x) => x.endsWith('.yaml'))) {
    for (const line of readFileSync(join(REGISTRY, f), 'utf8').split('\n')) {
      const m = /^([a-z_][a-z0-9_.]*):\s*(.*)$/.exec(line)
      if (m && !m[2].startsWith('{internal:')) out.push(m[1])
    }
  }
  return out
}

describe('the settings map (web.settings)', () => {
  it('gives every section a unique id and a component', () => {
    const ids = allSections().map((s) => s.section.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const id of ids) expect(SECTION_COMPONENTS[id], id).toBeTruthy()
    expect(Object.keys(SECTION_COMPONENTS).sort()).toEqual([...ids].sort())
  })

  it('has the nine groups in order', () => {
    expect(SETTINGS_GROUPS.map((g) => g.title)).toEqual([
      'You', 'Look', 'Mail', 'Calendar & tasks', 'Notes & tables', 'Notifications', 'Ambrose', 'Agents & API', 'Admin',
    ])
  })

  it('maps every user setting of the registry to a section', () => {
    const mapped = new Set(allSections().flatMap((s) => s.section.keys ?? []))
    const missing = registryKeys().filter((k) => !mapped.has(k) && !(k in OUTSIDE_SETTINGS))
    expect(missing).toEqual([])
  })

  it('finds every user setting from a search', () => {
    const admin = settingsEntries(settingsVisibility({ user: { role: 'admin' } }))
    const find = (q: string) => admin.filter((e) => q.toLowerCase().split(/\s+/).every((w) => `${e.label} ${e.hint} ${e.words}`.toLowerCase().includes(w))).map((e) => e.id)
    expect(find('time zone')).toContain('settings:region')
    expect(find('currency')).toContain('settings:region')
    expect(find('signature')).toContain('settings:writing')
    expect(find('dark')).toContain('settings:light-dark')
    expect(find('reading pane')).toContain('settings:this-layout')
    expect(find('spam')).toContain('settings:spam')
    expect(find('vacation')).toContain('settings:away')
  })

  it('finds renamed sections by their former names', () => {
    const ids = new Set(allSections().map((s) => s.section.id))
    for (const id of Object.keys(FORMER_NAMES)) expect(ids.has(id), id).toBe(true)
    const admin = settingsEntries(settingsVisibility({ user: { role: 'admin' } }), 'Jarvis')
    const find = (q: string) => admin.filter((e) => q.toLowerCase().split(/\s+/).every((w) => `${e.label} ${e.hint} ${e.words}`.toLowerCase().includes(w))).map((e) => e.id)
    expect(find('which model does what')).toContain('settings:admin-model-defaults')
    expect(find('speak for you')).toContain('settings:trusted-addresses')
    expect(find('addresses that speak for you')).toContain('settings:trusted-addresses')
    expect(find('what ambrose knows')).toContain('settings:memory')
    expect(find('get to know you')).toContain('settings:about')
    expect(find('filters and blocked')).toContain('settings:filters')
    expect(find('what notifies you')).toContain('settings:notifications')
    // The new names find them too.
    expect(find('models per task')).toContain('settings:admin-model-defaults')
    expect(find('trusted addresses')).toContain('settings:trusted-addresses')
  })

  it('leaves Admin out of a member’s search', () => {
    const member = settingsEntries(settingsVisibility({ user: { role: 'user' } }))
    expect(member.some((e) => e.id === 'settings:users')).toBe(false)
    expect(member.some((e) => e.id === 'settings:region')).toBe(true)
  })
})

describe('old settings addresses', () => {
  const to = (path: string) => {
    const u = new URL(path, 'http://x')
    const [, , tab, page] = u.pathname.split('/')
    return legacyTarget(tab, page, u.search, u.hash)
  }

  it('redirects each old tab to its new place', () => {
    expect(to('/settings/general')).toBe('/settings/mail/reading')
    expect(to('/settings/inbox')).toBe('/settings/mail/inbox')
    expect(to('/settings/labels')).toBe('/settings/mail/labels')
    expect(to('/settings/accounts')).toBe('/settings/mail/accounts')
    expect(to('/settings/templates')).toBe('/settings/mail/writing#templates')
    expect(to('/settings/spam')).toBe('/settings/mail/spam')
    expect(to('/settings/screener')).toBe('/settings/mail/spam#screener')
    expect(to('/settings/access')).toBe('/settings/admin/access')
    expect(to('/settings/assistant')).toBe('/settings/ambrose/about')
    expect(to('/settings/activity')).toBe('/settings/you/activity')
    expect(to('/settings/agents/activity')).toBe('/settings/you/activity')
  })

  it('keeps queries and anchors', () => {
    expect(to('/settings/filters?new=from%3Aa%40example.com')).toBe('/settings/mail/filters?new=from%3Aa%40example.com')
    expect(to('/settings/people?person=ross%40example.com')).toBe('/settings/mail/people?person=ross%40example.com')
    expect(to('/settings/accounts?add=1')).toBe('/settings/mail/accounts?add=1')
    expect(to('/settings/accounts#speakr')).toBe('/settings/mail/accounts#speakr')
    expect(to('/settings/assistant?section=models')).toBe('/settings/ambrose/models')
    expect(to('/settings/assistant?section=orders&run=5')).toBe('/settings/ambrose/orders?run=5')
    expect(to('/settings/assistant?part=lab')).toBe('/settings/ambrose/lab')
  })

  it('leaves the new addresses and the groups that kept their name alone', () => {
    expect(to('/settings/agents#approvals')).toBeNull()
    expect(to('/settings/calendar')).toBeNull()
    expect(to('/settings/notifications')).toBeNull()
    expect(to('/settings/admin')).toBeNull()
    expect(to('/settings/mail/inbox')).toBeNull()
    expect(to('/settings')).toBeNull()
  })

  it('sends a section id used as a page to its page', () => {
    expect(to('/settings/agents/approvals')).toBe('/settings/agents/agents#approvals')
    expect(to('/settings/mail/vip')).toBe('/settings/mail/inbox#vip')
    expect(to('/settings/nonsense')).toBe('/settings')
  })

  it('knows where each section is', () => {
    expect(findSection('region')?.page.id).toBe('region')
    expect(findSection('vip')?.group.id).toBe('mail')
  })
})
