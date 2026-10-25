// web.settings: simple and advanced Settings, and the access policy's switches.
import { describe, expect, it } from 'vitest'
import type { AccessInfo } from '../../api/types'
import { noAssistantMessage, filtersTitle, localOnlyChoice, pageVisible, reachable, settingsVisibility } from './visibility'

const access = (features: Partial<AccessInfo['features']>, message: string | null = null): AccessInfo => ({
  unrestricted: false, groups: ['family'], source: 'idp', policies: ['family'],
  features: { assistant: false, choose_models: false, lab: false, api_tokens: false, local_only_tokens: false, hosted_models: false, ...features },
  tasks: { chat: { all: false, allowed: [], default: null }, classify: { all: false, allowed: [], default: null } },
  needs_endpoints: false, message,
})
const user = { role: 'user' as const }

describe('settingsVisibility (web.settings)', () => {
  it('shows an admin everything, whatever the mode and policy', () => {
    const v = settingsVisibility({ user: { role: 'admin' }, settings: { advanced_settings: false }, access: access({}) })
    expect(Object.values(v.tabs).every(Boolean)).toBe(true)
    expect(v.filters && v.agentTools && v.learningDetail && v.assistant).toBe(true)
    expect(Object.values(v.parts).every(Boolean)).toBe(true)
    expect(v.message).toBeNull()
  })

  it('hides the advanced parts in simple mode', () => {
    const v = settingsVisibility({ user, settings: {}, access: access({ assistant: true, lab: true, api_tokens: true }) })
    expect(v.advanced).toBe(false)
    expect(v.tabs.agents).toBe(false) // Activity is under You, for everyone
    expect(pageVisible(v, 'agents', 'agents')).toBe(false)
    expect(pageVisible(v, 'you', 'activity')).toBe(true)
    expect(v.filters).toBe(false)
    expect(v.learningDetail).toBe(false)
    expect(v.parts.lab).toBe(false)
    expect(v.parts.orders).toBe(true)
    expect(v.tabs.mail).toBe(true) // blocked addresses stay
    expect(filtersTitle(v)).toBe('Blocked addresses')
    expect(v.tabs.admin).toBe(false)
  })

  it('shows the advanced parts the policy allows in advanced mode', () => {
    const s = { advanced_settings: true }
    const all = settingsVisibility({ user, settings: s, access: access({ assistant: true, lab: true, api_tokens: true }) })
    expect(all.tabs.agents && all.agentTools && all.filters && all.learningDetail && all.parts.lab).toBe(true)
    expect(filtersTitle(all)).toBe('Filters & blocking')
    const none = settingsVisibility({ user, settings: s, access: access({ assistant: true }) })
    expect(pageVisible(none, 'agents', 'agents')).toBe(false)
    expect(none.agentTools).toBe(false)
    expect(none.parts.lab).toBe(false)
    expect(none.filters).toBe(true)
  })

  it('hides the assistant parts and gives the message when the assistant is off', () => {
    const v = settingsVisibility({ user, settings: { advanced_settings: true }, access: access({}, 'Ask your admin.') })
    expect(v.assistant).toBe(false)
    expect(v.message).toBe('Ask your admin.')
    for (const p of ['about', 'orders', 'skills', 'memory', 'people'] as const) expect(v.parts[p]).toBe(false)
    expect(v.parts.models).toBe(true)
    expect(v.parts.importance).toBe(true)
    expect(settingsVisibility({ user, access: access({}) }).message).toBe(noAssistantMessage())
  })

  it('allows everything when the server sends no access (an older server)', () => {
    const v = settingsVisibility({ user, settings: { advanced_settings: true } })
    expect(v.tabs.agents && v.parts.lab && v.assistant).toBe(true)
  })

  it('opens a hidden group from a link, but never Admin for a user', () => {
    const v = settingsVisibility({ user, settings: {}, access: access({}) })
    expect(reachable(v, 'agents')).toBe(true)
    expect(reachable(v, 'admin')).toBe(false)
    expect(reachable(v, 'nonsense')).toBe(false)
    expect(reachable(settingsVisibility({ user: { role: 'admin' } }), 'admin')).toBe(true)
  })

  it("shows Ambrose's pages the assistant allows", () => {
    const v = settingsVisibility({ user, settings: {}, access: access({}) })
    expect(pageVisible(v, 'ambrose', 'about')).toBe(false)
    expect(pageVisible(v, 'ambrose', 'models')).toBe(true)
    expect(pageVisible(v, 'ambrose', 'briefs')).toBe(true)
    expect(pageVisible(v, 'mail', 'filters')).toBe(true)
  })
})

describe('the local-only box of the Create token dialog', () => {
  it('follows the policy switch, and admins are unrestricted', () => {
    const s = { advanced_settings: true }
    expect(settingsVisibility({ user, settings: s, access: access({ api_tokens: true }) }).localOnlyTokens).toBe(false)
    expect(settingsVisibility({ user, settings: s, access: access({ api_tokens: true, local_only_tokens: true }) }).localOnlyTokens).toBe(true)
    expect(settingsVisibility({ user: { role: 'admin' }, settings: s, access: access({}) }).localOnlyTokens).toBe(true)
  })

  it('is hidden without a local-only account, a note without the switch, a checkbox with it', () => {
    expect(localOnlyChoice(0, true)).toBe('hidden')
    expect(localOnlyChoice(0, false)).toBe('hidden')
    expect(localOnlyChoice(2, false)).toBe('note')
    expect(localOnlyChoice(1, true)).toBe('checkbox')
  })
})
