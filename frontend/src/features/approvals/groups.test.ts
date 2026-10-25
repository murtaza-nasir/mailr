import { describe, expect, it } from 'vitest'
import { groupByRequester, groupSource, requesterKey, type PendingApproval } from './approvals'

const pa = (id: number, over: Partial<PendingApproval> = {}): PendingApproval => ({
  id, subject: 'assistant', actor: '', action: 'labels.create', target: null, preview: null, status: 'pending', result: null,
  decided_by: null, created_at: null, expires_at: null, decided_at: null, executed_at: null, chat_session_id: null, ...over,
})

describe('approval groups (Allow all)', () => {
  it('groups by chat, else by token', () => {
    expect(requesterKey(pa(1, { chat_session_id: 'c1', subject: 'session:c1' }))).toBe('chat:c1')
    expect(requesterKey(pa(2, { subject: 'token:4' }))).toBe('token:4')
  })
  it('keeps newest-first order and lists pending oldest first', () => {
    const shown = [
      pa(5, { chat_session_id: 'c1' }),
      pa(4, { subject: 'token:2', actor: 'Claude Code' }),
      pa(3, { chat_session_id: 'c1', status: 'approved' }),
      pa(2, { chat_session_id: 'c1' }),
    ]
    const g = groupByRequester(shown)
    expect(g.map((x) => x.key)).toEqual(['chat:c1', 'token:2'])
    expect(g[0].items.map((x) => x.id)).toEqual([5, 3, 2])
    expect(g[0].pending.map((x) => x.id)).toEqual([2, 5])
    expect(groupSource(g[0].items[0])).toBe('this chat')
    expect(groupSource(g[1].items[0])).toBe('Claude Code')
  })
})
