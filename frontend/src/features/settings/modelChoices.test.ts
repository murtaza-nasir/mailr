// Admin → Models per task: the Search index offers checked models (and the current one) plainly,
// models that failed the check under "Does not make embeddings", the rest under "Not checked yet";
// other tasks offer every model.
import { describe, expect, it } from 'vitest'
import { modelChoices } from './AdminTab'

const opt = (endpoint: string, models: string[], locality: string, checked?: string[], failed?: string[]) => ({ endpoint, models, locality, adapter: 'openai', checked, failed })
const row = (task: string, options: ReturnType<typeof opt>[]) => ({ task, label: task, desc: '', default: null, local: null, options })
const v = (e: string, m: string) => `${e}\u0000${m}`

describe('modelChoices', () => {
  it('lists only checked models and the current value for the search index', () => {
    const t = row('embed', [opt('gpu', ['qwen', 'e5'], 'local', ['e5']), opt('cloud', ['gpt', 'te-3'], 'hosted', [])])
    const c = modelChoices(t, false, v('cloud', 'gpt'))
    expect(c.shown.map((o) => o.value)).toEqual([v('gpu', 'e5'), v('cloud', 'gpt')])
    expect(c.unchecked.map((o) => o.value)).toEqual([v('gpu', 'qwen'), v('cloud', 'te-3')])
    expect(c.shown[0].label).toBe('gpu · e5 (local)')
  })
  it('keeps local endpoints only for the local-only column', () => {
    const t = row('embed', [opt('gpu', ['qwen', 'e5'], 'local', ['e5']), opt('cloud', ['te-3'], 'hosted', ['te-3'])])
    const c = modelChoices(t, true, '')
    expect(c.shown.map((o) => o.value)).toEqual([v('gpu', 'e5')])
    expect(c.unchecked.map((o) => o.value)).toEqual([v('gpu', 'qwen')])
  })
  it('lists models that failed the check apart, unless one is the current value', () => {
    const t = row('embed', [opt('gpu', ['qwen', 'e5', 'bge'], 'local', ['e5'], ['qwen', 'bge'])])
    const c = modelChoices(t, false, v('gpu', 'bge'))
    expect(c.shown.map((o) => o.value)).toEqual([v('gpu', 'e5'), v('gpu', 'bge')])
    expect(c.failed.map((o) => o.value)).toEqual([v('gpu', 'qwen')])
    expect(c.unchecked).toEqual([])
  })
  it('offers every model for chat and sorting', () => {
    const t = row('chat', [opt('gpu', ['qwen', 'e5'], 'local')])
    const c = modelChoices(t, false, '')
    expect(c.shown.map((o) => o.value)).toEqual([v('gpu', 'qwen'), v('gpu', 'e5')])
    expect(c.unchecked).toEqual([])
    expect(c.failed).toEqual([])
  })
})
