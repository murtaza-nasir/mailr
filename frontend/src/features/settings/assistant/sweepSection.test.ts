// ACT-SURFACES: web.settings
// Settings → Ambrose → Sweep: the rules save through PATCH /me/settings (settings.sweep_rules), and the
// remembered choices are listed, removed one by one (agent.review_choice_remove) or all at once
// (agent.review_choice_clear).
import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls: { method: string; path: string; body?: unknown }[] = []

vi.mock('../../../api/client', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../api/client')>()
  const record = (method: string) => vi.fn(async (path: string, body?: unknown) => {
    calls.push({ method, path, body })
    if (method === 'GET') {
      return { items: [{ id: 4, sender: 'news@paper.example', move: 'archive', count: 2, updated_at: '2026-10-20T10:00:00Z' },
        { id: 5, sender: 'deals@shop.example', move: 'label:12', label: 'Shops', count: 1, updated_at: null }] }
    }
    return method === 'DELETE' ? { removed: path.endsWith('/review-choices') ? 2 : 1 } : {}
  })
  return { ...real, api: { ...real.api, get: record('GET'), patch: record('PATCH'), del: record('DELETE') } }
})

const { SWEEP_RULES_MAX, choiceMoveText, clearChoices, loadChoices, removeChoice, rulesPatch } = await import('./SweepSection')
const { saveSettings } = await import('../save')

beforeEach(() => { calls.length = 0 })

describe('Sweep rules (settings.sweep_rules)', () => {
  it('saves the rules as written, without space around them and at most 2000 characters', async () => {
    expect(rulesPatch('  Trash promotions once delivered.\n')).toEqual({ sweep_rules: 'Trash promotions once delivered.' })
    expect(rulesPatch('x'.repeat(2500)).sweep_rules).toHaveLength(SWEEP_RULES_MAX)
    await saveSettings(new QueryClient(), rulesPatch(' Archive only receipts. '), { quiet: true })
    expect(calls).toEqual([{ method: 'PATCH', path: '/me/settings', body: { sweep_rules: 'Archive only receipts.' } }])
  })
})

describe('Past choices (agent.review_choice_remove, agent.review_choice_clear)', () => {
  it('lists each sender with its move', async () => {
    const { items } = await loadChoices()
    expect(items.map((c) => `${c.sender}: ${choiceMoveText(c)}`)).toEqual(['news@paper.example: Archive', 'deals@shop.example: Label “Shops”'])
    expect(choiceMoveText({ move: 'keep' })).toBe('Keep')
    expect(choiceMoveText({ move: 'trash' })).toBe('Move to Trash')
  })

  it('removes one choice, or clears them all', async () => {
    await removeChoice(4)
    await clearChoices()
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(['DELETE /agent/review-choices/4', 'DELETE /agent/review-choices'])
  })
})
