// web.settings: Admin → Access, the policy form and the API body.
import { describe, expect, it } from 'vitest'
import { defaultAllowed, parseGroups, defaultChoices, formToBody, policyToForm, taskSummary, validatePolicy, type FeatureMeta, type Policy, type TaskOption } from './policyForm'

const FEATURES: FeatureMeta[] = [
  { key: 'assistant', label: 'Assistant', default: true, desc: '' },
  { key: 'choose_models', label: 'Choose models', default: false, desc: '' },
  { key: 'lab', label: 'Decisions lab', default: false, desc: '' },
  { key: 'api_tokens', label: 'Agents & API tokens', default: false, desc: '' },
  { key: 'local_only_tokens', label: 'Mark agents as local-only', default: false, desc: '' },
  { key: 'hosted_models', label: 'Hosted models', default: false, desc: '' },
]
const entry = (endpoint: string | null, model: string | null, missing = false) => ({ endpoint_id: 1, endpoint, model, locality: 'local', missing })
const POLICY: Policy = {
  id: 7, group: 'family', description: 'Kids',
  models: {
    chat: { any: false, allowed: [entry('local', 'small'), entry(null, null, true)], default: entry('local', 'small') },
    classify: { any: true, allowed: [], default: null },
  },
  features: { assistant: true, choose_models: false, lab: true, api_tokens: false, local_only_tokens: false, hosted_models: false },
  needs_endpoints: {}, members: 3, updated_at: null,
}
const OPTIONS: TaskOption[] = [
  { endpoint_id: 1, endpoint: 'local', models: ['small', 'big'], default_model: 'small', locality: 'local', adapter: 'openai', enabled: true },
  { endpoint_id: 2, endpoint: 'cloud', models: ['gpt'], default_model: null, locality: 'hosted', adapter: 'openai', enabled: true },
  { endpoint_id: 3, endpoint: 'off', models: ['x'], default_model: null, locality: 'local', adapter: 'openai', enabled: false },
]

describe('policyToForm / formToBody (web.settings)', () => {
  it('starts a new policy with the switch defaults and the group asked for', () => {
    const f = policyToForm(null, FEATURES, 'staff')
    expect(f.group).toBe('staff')
    expect(f.features).toEqual({ assistant: true, choose_models: false, lab: false, api_tokens: false, local_only_tokens: false, hosted_models: false })
    expect(f.models.chat).toEqual({ any: false, allowed: [], default: null })
  })

  it('round-trips a policy by endpoint name and drops deleted endpoints', () => {
    const f = policyToForm(POLICY, FEATURES)
    expect(f.models.chat.allowed).toEqual([{ endpoint: 'local', model: 'small' }])
    expect(f.models.chat.default).toEqual({ endpoint: 'local', model: 'small' })
    expect(formToBody(f)).toEqual({
      group: 'family', description: 'Kids',
      models: {
        chat: { any: false, allowed: [{ endpoint: 'local', model: 'small' }], default: { endpoint: 'local', model: 'small' } },
        classify: { any: true, allowed: [], default: null },
      },
      features: POLICY.features,
    })
  })

  it('trims, drops duplicates and empty rows, and sends no list with Any endpoint', () => {
    const f = policyToForm(null, FEATURES, '  staff  ')
    f.description = ' x '
    f.models.chat.allowed = [{ endpoint: 'local', model: 'small' }, { endpoint: 'local', model: 'small' }, { endpoint: '', model: null }, { endpoint: 'cloud', model: '' }]
    f.models.classify = { any: true, allowed: [{ endpoint: 'local', model: null }], default: { endpoint: 'local', model: '' } }
    const b = formToBody(f)
    expect(b.group).toBe('staff')
    expect(b.description).toBe('x')
    expect(b.models.chat.allowed).toEqual([{ endpoint: 'local', model: 'small' }, { endpoint: 'cloud', model: null }])
    expect(b.models.classify).toEqual({ any: true, allowed: [], default: { endpoint: 'local', model: null } })
  })
})

describe('validatePolicy and the default (web.settings)', () => {
  it('needs a group name', () => {
    expect(validatePolicy(policyToForm(null, FEATURES))).toMatch(/group name/)
  })
  it('needs the default among the allowed models; an any-model entry covers its endpoint', () => {
    const f = policyToForm(POLICY, FEATURES)
    expect(validatePolicy(f)).toBeNull()
    f.models.chat.default = { endpoint: 'local', model: 'big' }
    expect(defaultAllowed(f.models.chat)).toBe(false)
    expect(validatePolicy(f, { chat: 'Ambrose and writing' })).toBe('The default for “Ambrose and writing” must be one of the allowed models.')
    f.models.chat.allowed.push({ endpoint: 'local', model: null })
    expect(validatePolicy(f)).toBeNull()
  })
  it('offers each allowed model, every model of an any-model entry, and every enabled endpoint with Any', () => {
    expect(defaultChoices({ any: false, allowed: [{ endpoint: 'local', model: null }, { endpoint: 'cloud', model: 'gpt' }], default: null }, OPTIONS))
      .toEqual([{ endpoint: 'local', model: null }, { endpoint: 'local', model: 'small' }, { endpoint: 'local', model: 'big' }, { endpoint: 'cloud', model: 'gpt' }])
    const any = defaultChoices({ any: true, allowed: [], default: null }, OPTIONS)
    expect(any.map((e) => e.endpoint)).not.toContain('off')
    expect(any).toContainEqual({ endpoint: 'cloud', model: 'gpt' })
  })
  it('summarises a task', () => {
    expect(taskSummary(POLICY.models.chat)).toBe('local · small, a deleted endpoint')
    expect(taskSummary(POLICY.models.classify)).toBe('Any endpoint')
    expect(taskSummary({ any: false, allowed: [], default: null })).toBe('None')
  })
})

describe('parseGroups (web.settings)', () => {
  it('splits on commas and lines, trims, and drops duplicates and empties', () => {
    expect(parseGroups(' staff, family\nfamily ,, ')).toEqual(['family', 'staff'])
    expect(parseGroups('')).toEqual([])
  })
})
