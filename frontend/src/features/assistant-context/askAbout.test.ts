// ACT-SURFACES: web.panel web.recording
// recording.ask: "Ask assistant" from the recording sheet attaches the recording, and each turn
// sends it at the top of `context` as focused_recording (agent/screen.py).
import { beforeEach, describe, expect, it } from 'vitest'
import { askContext, askLabel, askSuggestions, useAskAbout } from './askAbout'

describe('askAbout', () => {
  beforeEach(() => useAskAbout.getState().clear())

  it('sends a recording as focused_recording (recording.ask)', () => {
    useAskAbout.getState().ask({ kind: 'recording', cid: 3, rid: 12, title: 'Weekly sync' })
    expect(askContext()).toEqual({ top: { focused_recording: { connector_id: 3, recording_id: 12 } } })
    const about = useAskAbout.getState().about!
    expect(askLabel(about)).toBe('Weekly sync')
    expect(askLabel({ kind: 'recording', cid: 3, rid: 12, title: '' })).toBe('(untitled recording)')
    expect(askSuggestions(about)[0]).toMatch(/decide/)
  })

  it('sends an approval as focused_approval (agent.ask_about on an approval card)', () => {
    const a = { kind: 'approval' as const, id: 41, title: 'Send email' }
    expect(askContext(a)).toEqual({ top: { focused_approval: { id: 41 } } })
    expect(askLabel(a)).toBe('Send email')
    expect(askLabel({ ...a, title: '' })).toBe('This approval')
    expect(askSuggestions(a)[0]).toBe('Why does this need approval?')
  })

  it('keeps events and tasks as before', () => {
    expect(askContext({ kind: 'task', uid: 'u1', title: 'T' })).toEqual({ top: { focused_task: 'u1' } })
    expect(askContext({ kind: 'tasks', uids: ['a', 'b'] })).toEqual({ top: {}, tasks: { selected: ['a', 'b'] } })
    expect(askContext(null)).toEqual({ top: {} })
  })
})
