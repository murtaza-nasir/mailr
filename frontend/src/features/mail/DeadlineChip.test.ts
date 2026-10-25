// ACT-SURFACES: web.list
// mail.dismiss_deadline: the Due chip leaves the cached lists at once (the optimistic half).
import { describe, expect, it } from 'vitest'
import type { ConversationSummary, Page } from '../../api/types'
import { decisionSource } from '../conversation/decisionSource'
import { dueText, withoutDeadline } from './DeadlineChip'

const row = (id: number, names: string[]) => ({ id, signals: names.map((name) => ({ name, date: '2027-12-03' })) }) as unknown as ConversationSummary

describe('withoutDeadline', () => {
  it('drops only the deadline, only on the given conversations', () => {
    const page = { items: [row(1, ['needs_reply', 'deadline']), row(2, ['deadline'])], next_cursor: null, total: 2 } as unknown as Page<ConversationSummary>
    const out = withoutDeadline(page, new Set([1]))
    expect(out.items[0].signals?.map((s) => s.name)).toEqual(['needs_reply'])
    expect(out.items[1]).toBe(page.items[1])
    expect(withoutDeadline(page, new Set([9]))).toBe(page) // nothing to change: same object
  })
  it('names the date', () => {
    expect(dueText('2027-12-03')).toBe('Due Dec 3')
    expect(dueText(null)).toBe('Deadline')
  })
})

describe('decisionSource', () => {
  it('says which kind of model decided', () => {
    expect(decisionSource({ kind: 'smart', target: 'deadline', source: 'decision_model', source_ref: '2026-10-22' })).toBe('Decision model · due 2026-10-22')
    expect(decisionSource({ kind: 'category', target: 'updates', source: 'llm', source_ref: 'qwen' })).toBe('Chat model (qwen)')
    expect(decisionSource({ kind: 'smart', target: 'needs_reply', source: 'heuristic', source_ref: '' })).toBe('Rules')
    expect(decisionSource({ kind: 'label', target: '3', source: 'user', source_ref: '' })).toBe('You')
  })
})
