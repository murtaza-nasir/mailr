import { describe, expect, it } from 'vitest'
import type { Citation } from './citations'
import { allSteps, applyEvent, copyText, foldRepeatGroups, fromReplay, markWaiting, thinkingNow, turnText, type StepsItem, type TimelineTurn } from './timeline'
import { foldRepeats } from './steps'
import { withApproval, type Turn } from './useAssistant'
import { atBottom } from './useFollowScroll'

const empty = (): TimelineTurn => ({ items: [], citations: [] })

function run(events: [string, Record<string, unknown>][]): TimelineTurn {
  return events.reduce((t, [type, d]) => applyEvent(t, type, d), empty())
}

describe('assistant timeline', () => {
  it('replaces a reply the claim check rewrote, and marks one it could not confirm', () => {
    // The server streams the first reply, asks the model again, and the final event replaces it.
    const fixed = run([
      ['token', { text: 'Done — the reminder is set.', segment: 1 }],
      ['final', { segment: 1, content: 'I could not set the reminder.', citations: [] }],
    ])
    expect(turnText(fixed)).toBe('I could not set the reminder.')
    expect(fixed.unconfirmed).toBeUndefined()
    const flagged = run([['final', { segment: 0, content: 'Done.', citations: [], unconfirmed: true }]])
    expect(flagged.unconfirmed).toBe(true)
  })

  it('keeps text written before tool calls, in order', () => {
    const t = run([
      ['token', { text: 'I can’t create Mailr rules', segment: 0 }],
      ['token', { text: ', but let me check.', segment: 0 }],
      ['segment', { segment: 0, content: 'I can’t create Mailr rules, but let me check.', citations: [] }],
      ['tool_start', { id: 'a', name: 'search_conversations', label: 'Searching', segment: 0 }],
      ['tool', { id: 'a', ok: true, summary: '3 conversations', count: 3, segment: 0 }],
      ['token', { text: 'Found ', segment: 1 }],
      ['tool_start', { id: 'b', name: 'get_conversation', label: 'Reading', segment: 1 }],
      ['tool', { id: 'b', ok: false, summary: 'not found', segment: 1 }],
      ['final', { segment: 2, content: 'Here it is.', citations: [] }],
    ])
    expect(t.items.map((i) => `${i.kind}:${i.segment}`)).toEqual(['text:0', 'steps:0', 'text:1', 'steps:1', 'text:2'])
    expect(turnText(t)).toBe('I can’t create Mailr rules, but let me check.\n\nFound \n\nHere it is.')
    expect(allSteps(t).map((s) => s.status)).toEqual(['done', 'failed'])
  })

  it('does not overwrite earlier segments with the final answer', () => {
    const t = run([
      ['token', { text: 'First.', segment: 0 }],
      ['tool_start', { id: 'a', name: 'x', segment: 0 }],
      ['final', { segment: 1, content: 'Second.', citations: [], stop_reason: 'rounds' }],
    ])
    expect(t.items[0]).toMatchObject({ kind: 'text', raw: 'First.' })
    expect(t.items[2]).toMatchObject({ kind: 'text', final: 'Second.' })
    expect(t.stopReason).toBe('rounds')
  })

  it('marks the running step as waiting for approval', () => {
    const t = markWaiting(run([['tool_start', { id: 'a', name: 'manage_filter', segment: 0 }]]), 7)
    expect(allSteps(t)[0]).toMatchObject({ status: 'waiting', approvalId: 7 })
  })

  it('rebuilds the timeline from a stored chat, old and new format', () => {
    const items = fromReplay(
      [{ type: 'text', content: 'Let me look.' }, { type: 'steps', steps: [{ id: 'a', name: 'x', label: 'x', status: 'done' }] },
        { type: 'text', content: 'Done.' }], 'Done.', [])
    expect(items.map((i) => `${i.kind}:${i.segment}`)).toEqual(['text:0', 'steps:0', 'text:1'])
    const old = fromReplay(undefined, 'Answer', [{ id: 'a', name: 'x', label: 'x', ok: false } as never])
    expect(old.map((i) => i.kind)).toEqual(['steps', 'text'])
    expect(old[0].kind === 'steps' && old[0].steps[0].status).toBe('failed')
  })

  it('copies a reply as Markdown with plain citations and sources', () => {
    const c = { n: 1, subject: 'Dinner', from: 'Cleo', date: '2026-09-20T10:00:00' } as Citation
    expect(copyText('Thai food [1](mailr-cite:1).', [c])).toBe('Thai food [1].\n\nSources:\n[1] Dinner — Cleo (2026-09-20)')
    expect(copyText('No cites.', [c])).toBe('No cites.')
  })
})

describe('approvals and continuations in the timeline', () => {
  it('places the card after the steps that asked and continues after it', () => {
    const t = run([
      ['segment', { segment: 0, content: 'I will trash them.', citations: [] }],
      ['tool_start', { id: 'a', name: 'modify_conversations', label: 'Moving 6 conversations to Trash', icon: 'label', segment: 0 }],
      ['tool', { id: 'a', ok: true, summary: 'waiting for your approval', segment: 0 }],
      ['approval_required', { approval_id: 41, segment: 0 }],
      ['final', { segment: 1, content: 'Approve the card above.', citations: [] }],
      ['resume', { segment: 2, text: 'After your approval' }],
      ['final', { segment: 2, content: 'Done: all 6 are in Trash.', citations: [] }],
    ])
    expect(t.items.map((i) => i.kind)).toEqual(['text', 'steps', 'approval', 'text', 'resume', 'text'])
    expect(turnText(t)).toBe('I will trash them.\n\nApprove the card above.\n\nDone: all 6 are in Trash.')
    expect(allSteps(t)[0].icon).toBe('label')
  })

  it('replays approval and resume items from the server', () => {
    const items = fromReplay([
      { type: 'steps', steps: [{ id: 'a', name: 'modify_conversations', label: 'Moving', status: 'done', approval_id: 41 }] },
      { type: 'approval', approval: { id: 41 } },
      { type: 'text', content: 'Approve it.' },
      { type: 'resume', text: 'After your decision on “Move 6 messages to Trash”' },
      { type: 'text', content: 'Done.' },
    ], 'Done.', [])
    expect(items.map((i) => i.kind)).toEqual(['steps', 'approval', 'text', 'resume', 'text'])
    expect(items[0].kind === 'steps' && items[0].steps[0].approvalId).toBe(41)
  })

  it('updates a card in place with the outcome', () => {
    const turns: Turn[] = [{ role: 'assistant', items: [{ kind: 'approval', segment: 0, id: 41 }], citations: [], drafts: [], refs: [], configs: [], draftEdits: [],
      approvals: [{ id: 41, action: 'mail.trash', title: 'Move 6 messages to Trash', status: 'pending' }], running: false, status: 'done', startedAt: 0 }]
    const out = withApproval(turns, 41, 'executed', { id: 41, action: 'mail.trash', status: 'executed', outcome: { state: 'executed', text: 'Approved · moved 6 messages to Trash' } })
    const a = out[0].role === 'assistant' ? out[0].approvals[0] : null
    expect(a?.status).toBe('executed')
    expect(a?.outcome?.text).toBe('Approved · moved 6 messages to Trash')
  })

  it('shows Thinking… at the end of the running turn only while waiting on the model', () => {
    const base = { citations: [], running: true }
    expect(thinkingNow({ ...base, items: [] })).toBe(true)
    expect(thinkingNow({ ...base, items: [{ kind: 'text', segment: 0, raw: 'Typing', final: null }] })).toBe(false)
    expect(thinkingNow({ ...base, items: [{ kind: 'text', segment: 0, raw: 'Done', final: 'Done' }] })).toBe(true)
    expect(thinkingNow({ ...base, items: [{ kind: 'steps', segment: 0, steps: [{ id: 'a', name: 'x', label: 'x', status: 'running' }] }] })).toBe(false)
    expect(thinkingNow({ ...base, items: [{ kind: 'approval', segment: 0, id: 1 }], approvals: [{ id: 1, status: 'pending' }] })).toBe(false)
    expect(thinkingNow({ ...base, running: false, items: [] })).toBe(false)
  })

  it('counts the bottom of the log with some slack', () => {
    expect(atBottom(560, 1000, 400)).toBe(true)
    expect(atBottom(500, 1000, 400)).toBe(false)
  })
})

describe('repeated calls (the owner’s note of 5 Oct 2026)', () => {
  const search = { name: 'search_tasks', label: 'Searching your tasks', args: { text: 'deadline' } }
  it('folds a live repeat into the step it repeats: one line, ×N', () => {
    const events: [string, Record<string, unknown>][] = [
      ['tool_start', { id: 'a', ...search, segment: 0 }],
      ['tool', { id: 'a', ...search, ok: true, summary: '0 tasks', count: 0, segment: 0 }],
    ]
    for (let i = 1; i <= 15; i++) {
      events.push(['tool_start', { id: `r${i}`, ...search, segment: i }])
      events.push(['tool', { id: `r${i}`, ...search, ok: true, summary: 'same result as before', repeat_of: 'a', segment: i }])
    }
    const shown = foldRepeatGroups(run(events).items)
    expect(shown).toHaveLength(1)
    const rows = foldRepeats((shown[0] as StepsItem).steps)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ id: 'a', times: 16, summary: '0 tasks' })
  })
  it('keeps groups apart when text lies between, or when a group also does something new', () => {
    const t = run([
      ['tool', { id: 'a', ...search, ok: true, summary: '0 tasks', segment: 0 }],
      ['segment', { segment: 1, content: 'Let me look again.', citations: [] }],
      ['tool', { id: 'b', ...search, ok: true, repeat_of: 'a', segment: 1 }],
      ['tool', { id: 'c', name: 'list_calendar', label: 'Checking your calendar', ok: true, segment: 2 }],
      ['tool', { id: 'd', ...search, ok: true, repeat_of: 'a', segment: 2 }],
    ])
    expect(foldRepeatGroups(t.items).map((i) => i.kind)).toEqual(['steps', 'text', 'steps', 'steps'])
  })
  it('finds the repeats of an older server in a stored turn ("already done above")', () => {
    const done = { key: 'approvals.result.already_done', params: {} }
    const items = fromReplay(
      [
        { type: 'steps', steps: [{ id: 'a', ...search, status: 'done', summary: '0 tasks' } as never] },
        ...[1, 2, 3].map((i) => ({ type: 'steps' as const, steps: [{ id: `r${i}`, ...search, status: 'done', summary: 'already done above', summary_msg: done } as never] })),
        { type: 'text', content: 'Nothing is due.' },
      ],
      'Nothing is due.', [],
    )
    const shown = foldRepeatGroups(items)
    expect(shown.map((i) => i.kind)).toEqual(['steps', 'text'])
    expect(foldRepeats((shown[0] as StepsItem).steps)).toMatchObject([{ id: 'a', times: 4 }])
  })
})
