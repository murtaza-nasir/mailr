import { describe, expect, it } from 'vitest'
import { activitySummary, fallbackLabel, humanize, stepIcon, stepResult, truncate, type Step } from './steps'

const s = (status: Step['status']): Step => ({ id: String(Math.random()), name: 'x', label: 'x', status })

describe('step labels', () => {
  it('describes tool calls in plain words', () => {
    expect(fallbackLabel('search_conversations', { q: 'budget' })).toBe('Searching your mail for “budget”')
    expect(fallbackLabel('search_conversations', {})).toBe('Looking through your mail')
    expect(fallbackLabel('find_passages', { q: 'deadline' })).toBe('Finding passages about “deadline”')
    expect(fallbackLabel('get_person', { email: 'a@b.org' })).toBe('Looking up a@b.org')
    expect(fallbackLabel('create_event', {})).toBe('Create event')
  })
  it('truncates long queries and picks icons', () => {
    expect(truncate('a'.repeat(100), 10)).toHaveLength(10)
    expect(stepIcon('get_conversation')).toBe('read')
    expect(stepIcon('create_task')).toBe('task')
    expect(stepIcon('unknown_tool')).toBe('other')
    expect(humanize('list_labels')).toBe('List labels')
  })
})

describe('activitySummary', () => {
  it('summarises a finished turn', () => {
    expect(activitySummary([s('done'), s('done')], 11.24, 6)).toBe('Looked at 6 conversations · 2 steps · 11.2 s')
    expect(activitySummary([s('done'), s('failed')], 72, null)).toBe('2 steps · 1 failed · 72 s')
    expect(activitySummary([s('done')], null, 1)).toBe('Looked at 1 conversation · 1 step')
  })
})

describe('stepResult', () => {
  const st = (id: string, name: string, status: Step['status'], summary?: string, error?: string): Step => ({ id, name, label: name, status, summary, error })
  it('never shows a tool error on the step line', () => {
    const bad = st('1', 'send_draft', 'failed', undefined, "'draft_id' is required")
    expect(stepResult(bad, [bad])).toBeUndefined()
  })
  it('says retried when a later call of the same tool worked', () => {
    const bad = st('1', 'get_draft', 'failed', undefined, "'draft_id' is required")
    const ok = st('2', 'get_draft', 'done', 'draft read')
    expect(stepResult(bad, [bad, ok])).toBe('retried')
    expect(stepResult(ok, [bad, ok])).toBe('draft read')
  })
})
