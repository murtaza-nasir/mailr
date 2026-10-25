// ACT-SURFACES: web.settings
// The name the user gives the assistant (Settings → Ambrose → About you) shows everywhere the
// assistant is named: the settings group and its search, static texts built when a module loads
// (shortcuts, task labels), server texts kept as a key, and who made a change.
import { afterEach, describe, expect, it } from 'vitest'
import { DEFAULT_ASSISTANT_NAME, rememberAssistantName } from './assistantName'
import { msgText } from './present/serverText'
import { groupById, SETTINGS_GROUPS } from '../../settings/groups'
import { settingsEntries } from '../../settings/palette'
import { noAssistantMessage, settingsVisibility } from '../../settings/visibility'
import { BINDINGS } from '../../../lib/shortcuts'
import { whoLabel } from '../../activity/ActivityPage'
import { actorLabel } from '../../approvals/approvals'
import { CONTEXT_LABELS, SOURCE_LABELS } from '../../settings/assistant/types'

afterEach(() => rememberAssistantName(DEFAULT_ASSISTANT_NAME))

describe('a renamed assistant (web)', () => {
  it('titles the settings group and its description with the name', () => {
    expect(groupById('ambrose')!.title).toBe('Ambrose')
    rememberAssistantName('Jeeves')
    // The same group object, read after the rename: the nav and the section heading follow at once.
    expect(SETTINGS_GROUPS.map((g) => g.title)).toContain('Jeeves')
    expect(SETTINGS_GROUPS.map((g) => g.title)).not.toContain('Ambrose')
    expect(groupById('ambrose')!.desc).toContain('Jeeves')
    expect(groupById('ambrose')!.desc).not.toContain('Ambrose')
    rememberAssistantName('Carl')
    expect(groupById('ambrose')!.title).toBe('Carl')
  })

  it('shows the name in the palette, and a search for "Ambrose" still finds the group', () => {
    rememberAssistantName('Jeeves')
    const entries = settingsEntries(settingsVisibility({ user: { role: 'user' } }), 'Jeeves')
    const about = entries.find((e) => e.id === 'settings:about')!
    expect(about.hint).toContain('Jeeves')
    expect(about.hint).not.toContain('Ambrose')
    const find = (q: string) => entries.filter((e) => `${e.label} ${e.hint} ${e.words}`.toLowerCase().includes(q)).map((e) => e.id)
    expect(find('ambrose')).toContain('settings:about')
    expect(find('jeeves')).toContain('settings:about')
  })

  it('fills the name into static texts read after the rename', () => {
    rememberAssistantName('Jeeves')
    const toggle = BINDINGS.find((b) => b.action === 'toggleAgent')!
    expect(toggle.description).toContain('Jeeves')
    expect(CONTEXT_LABELS.chat).toContain('Jeeves')
    expect(SOURCE_LABELS.agent).toContain('Jeeves')
    expect(noAssistantMessage()).toContain('Jeeves')
    expect(whoLabel({ who: 'ambrose', by: null })).toBe('Jeeves')
    expect(actorLabel({ subject: 'assistant', actor: 'Ambrose' } as never)).toBe('Jeeves')
  })

  it('renders the forward card summary with the name the server sent, else the one the app shows', () => {
    const fallback = 'You forwarded this from a trusted address.'
    expect(msgText({ key: 'approvals.forward.summary', params: { name: 'Jeeves' } }, fallback)).toContain('Jeeves carries out the request')
    rememberAssistantName('Carl')
    // A card stored before the server sent the name.
    expect(msgText({ key: 'approvals.forward.summary' }, fallback)).toContain('Carl carries out the request')
  })
})
