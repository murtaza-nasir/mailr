// Admin → Access: the access policy types (backend api/access_routes.py) and the editor form,
// converted to and from the API body. Endpoints go to the API by name.

import type { AccessFeature, AccessInfo, AccessTask } from '../../../api/types'
import { t as tr } from '../../../i18n'

export const POLICY_TASKS: AccessTask[] = ['chat', 'classify']
export const ACCESS_KEY = ['admin', 'access'] as const

export interface PolicyEntry {
  endpoint_id: number
  /** null when the endpoint was deleted (missing). */
  endpoint: string | null
  /** null: any model of that endpoint. */
  model: string | null
  locality: string | null
  missing: boolean
}

export interface PolicyTask {
  any: boolean
  allowed: PolicyEntry[]
  default: PolicyEntry | null
}

export interface Policy {
  id: number
  group: string
  /** The built-in Everyone policy (group "*"): for every user in no group with a policy. */
  builtin?: boolean
  /** "Everyone" for the built-in policy, else the group name. */
  label?: string
  description: string
  models: Record<AccessTask, PolicyTask>
  features: Record<AccessFeature, boolean>
  /** Non-empty: the seed could not resolve endpoints; task → what to pick. */
  needs_endpoints: Partial<Record<AccessTask, string>>
  members: number
  updated_at: string | null
}

export interface GroupSeen {
  name: string
  source: 'oidc' | 'saml' | 'scim' | 'assigned'
  first_seen_at: string | null
  last_seen_at: string | null
  users: number
  has_policy: boolean
}

export interface FeatureMeta {
  key: AccessFeature
  label: string
  default: boolean
  desc: string
}

export interface TaskOption {
  endpoint_id: number
  endpoint: string
  models: string[]
  default_model: string | null
  locality: string
  adapter: string
  enabled: boolean
}

export interface AccessOverview {
  policies: Policy[]
  groups_seen: GroupSeen[]
  features: FeatureMeta[]
  tasks: { task: AccessTask; label: string; desc: string; options: TaskOption[] }[]
}

export interface UserAccess {
  user_id: number
  role: 'admin' | 'user'
  groups: string[]
  source: 'idp' | 'assigned'
  idp_groups: string[] | null
  idp_source: string | null
  idp_groups_at: string | null
  assigned_groups: string[]
  can_assign: boolean
  access: AccessInfo
}

// ---------- the form

export interface FormEntry {
  endpoint: string
  /** null: any model of the endpoint. */
  model: string | null
}

export interface TaskForm {
  any: boolean
  allowed: FormEntry[]
  default: FormEntry | null
}

export interface PolicyForm {
  group: string
  description: string
  models: Record<AccessTask, TaskForm>
  features: Record<AccessFeature, boolean>
}

export interface PolicyBody {
  group: string
  description: string
  models: Record<AccessTask, { any: boolean; allowed: FormEntry[]; default: FormEntry | null }>
  features: Record<AccessFeature, boolean>
}

const toEntry = (e: PolicyEntry | null): FormEntry | null => (e && !e.missing && e.endpoint ? { endpoint: e.endpoint, model: e.model } : null)

/** The form for a policy, or a new policy (switches at their defaults) for `group`. Entries whose
 *  endpoint was deleted are left out. */
export function policyToForm(p: Policy | null, features: FeatureMeta[], group = ''): PolicyForm {
  const models = {} as Record<AccessTask, TaskForm>
  for (const t of POLICY_TASKS) {
    const m = p?.models[t]
    models[t] = {
      any: m?.any ?? false,
      allowed: (m?.allowed ?? []).map(toEntry).filter((x): x is FormEntry => x !== null),
      default: toEntry(m?.default ?? null),
    }
  }
  const feats = {} as Record<AccessFeature, boolean>
  for (const f of features) feats[f.key] = p ? Boolean(p.features[f.key]) : f.default
  return { group: p?.group ?? group, description: p?.description ?? '', models, features: feats }
}

export const sameEntry = (a: FormEntry, b: FormEntry) => a.endpoint === b.endpoint && a.model === b.model

/** Whether the default is one of the allowed models (an "any model" entry covers every model of its endpoint). */
export function defaultAllowed(t: TaskForm): boolean {
  if (!t.default || t.any) return true
  const d = t.default
  return t.allowed.some((a) => a.endpoint === d.endpoint && (a.model === null || a.model === d.model))
}

/** What is wrong with the form, in plain words, or null. */
export function validatePolicy(f: PolicyForm, taskLabels: Partial<Record<AccessTask, string>> = {}): string | null {
  if (!f.group.trim()) return tr('settings.access.policyForm.enterTheGroupNameAs')
  for (const t of POLICY_TASKS) {
    if (!defaultAllowed(f.models[t])) return tr('settings.access.policyForm.theDefaultForMustBe', { taskLabels: taskLabels[t] ?? t })
    if (f.models[t].allowed.some((a) => !a.endpoint)) return tr('settings.access.policyForm.pickAnEndpointForEach')
  }
  return null
}

/** The API body (POST and PUT /admin/access/policies): trimmed, duplicates dropped. With "Any
 *  endpoint" the list is not sent. */
export function formToBody(f: PolicyForm): PolicyBody {
  const models = {} as PolicyBody['models']
  for (const t of POLICY_TASKS) {
    const m = f.models[t]
    const allowed: FormEntry[] = []
    if (!m.any) {
      for (const a of m.allowed) {
        const e = { endpoint: a.endpoint, model: a.model || null }
        if (e.endpoint && !allowed.some((x) => sameEntry(x, e))) allowed.push(e)
      }
    }
    models[t] = { any: m.any, allowed, default: m.default ? { endpoint: m.default.endpoint, model: m.default.model || null } : null }
  }
  return { group: f.group.trim(), description: f.description.trim(), models, features: { ...f.features } }
}

/** The default choices for a task: each allowed entry, and for an "any model" entry each model its
 *  endpoint lists. With "Any endpoint", every model of every option. */
export function defaultChoices(t: TaskForm, options: TaskOption[]): FormEntry[] {
  const out: FormEntry[] = []
  const add = (e: FormEntry) => {
    if (!out.some((x) => sameEntry(x, e))) out.push(e)
  }
  const expand = (endpoint: string, model: string | null) => {
    add({ endpoint, model })
    if (model === null) for (const m of options.find((o) => o.endpoint === endpoint)?.models ?? []) add({ endpoint, model: m })
  }
  if (t.any) for (const o of options.filter((x) => x.enabled)) expand(o.endpoint, null)
  else for (const a of t.allowed) if (a.endpoint) expand(a.endpoint, a.model)
  return out
}

/** "endpoint · model", or "endpoint · any model" / "its default model". */
export function entryLabel(e: FormEntry, asDefault = false): string {
  return `${e.endpoint} · ${e.model ?? (asDefault ? tr('common.itsDefaultModel') : tr('settings.access.policyForm.anyModel'))}`
}

/** A short line for a policy's models of one task. */
export function taskSummary(t: PolicyTask): string {
  if (t.any) return tr('settings.access.policyForm.anyEndpoint')
  const names = t.allowed.map((a) => (a.missing || !a.endpoint ? tr('settings.access.policyForm.aDeletedEndpoint') : `${a.endpoint} · ${a.model ?? tr('settings.access.policyForm.anyModel')}`))
  return names.length ? names.join(', ') : tr('common.none')
}

/** The groups typed in Admin → Users → access: comma or newline separated, trimmed, without
 *  duplicates, sorted (the server stores them sorted). */
export function parseGroups(text: string): string[] {
  return [...new Set(text.split(/[,\n]/).map((g) => g.trim()).filter(Boolean))].sort()
}
