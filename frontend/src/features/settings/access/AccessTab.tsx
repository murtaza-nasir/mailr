// Settings → Admin → Access policies: access policies per identity-provider group, and the groups seen at
// sign-ins (backend api/access_routes.py, core/access.py). Admins are never restricted.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../../api/client'
import type { AccessTask } from '../../../api/types'
import { Skeleton } from '../../../components/bits'
import { ConfirmDialog, Dialog } from '../../../components/Dialog'
import { formatRelative } from '../../../lib/format'
import { toast, toastError } from '../../../stores/ui'
import { Check, Section } from '../common'
import {
  ACCESS_KEY, POLICY_TASKS, defaultChoices, entryLabel, formToBody, policyToForm, sameEntry, taskSummary, validatePolicy,
  type AccessOverview, type FormEntry, type Policy, type PolicyForm, type TaskForm, type TaskOption,
} from './policyForm'
import { t as tr, tx } from '../../../i18n'
import { useAssistantName } from '../../rail/assistant/useAssistantName'

const SOURCE: Record<string, string> = { oidc: tr('settings.access.accessTab.signInOidc'), saml: 'SAML', scim: 'SCIM', assigned: tr('settings.access.accessTab.assignedByAnAdmin') }

export function AccessTab() {
  const assistantName = useAssistantName()
  const q = useQuery<AccessOverview>({ queryKey: ACCESS_KEY, queryFn: () => api.get('/admin/access') })
  const [editing, setEditing] = useState<{ policy: Policy | null; group: string } | null>(null)
  const [deleting, setDeleting] = useState<Policy | null>(null)
  const qc = useQueryClient()
  const del = useMutation({
    mutationFn: (p: Policy) => api.del(`/admin/access/policies/${p.id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ACCESS_KEY })
      void qc.invalidateQueries({ queryKey: ['admin', 'users'] })
      toast({ message: tr('settings.access.accessTab.policyDeleted') })
    },
    onError: (e) => toastError(e, tr('settings.access.accessTab.policyNotDeleted')),
  })
  const data = q.data
  const label = (t: AccessTask) => data?.tasks.find((x) => x.task === t)?.label ?? t

  return (
    <>
      <Section
        title={tr('settings.access.accessTab.groupPolicies')}
        desc={<>{tx('settings.access.accessTab.yourIdentityProviderDecidesWho', { name: assistantName }, { b: (c) => <b>{c}</b> })}</>}
        actions={
          <button type="button" className="btn filled" disabled={!data} onClick={() => setEditing({ policy: null, group: '' })}>
            <Plus />{' '}{tr('settings.access.accessTab.addPolicy')}
          </button>
        }
      >
        {q.isLoading ? <Skeleton height={80} /> : q.isError ? (
          <div className="banner error">{(q.error as Error).message}</div>
        ) : !data?.policies.length ? (
          <p className="muted">{tr('settings.access.accessTab.noPoliciesYetWithoutA')}</p>
        ) : (
          <div className="acc-policies">
            {data.policies.map((p) => (
              <div key={p.id} className="acc-policy" data-testid={`policy-${p.group}`}>
                <div className="acc-policy-head">
                  <b>{p.label ?? p.group}</b>
                  {p.builtin && <span className="pill">{tr('settings.access.accessTab.builtIn')}</span>}
                  <span className="muted">{tr('settings.access.accessTab.users', { count: p.members })}</span>
                  {Object.keys(p.needs_endpoints).length > 0 && (
                    <span className="pill warn"><AlertTriangle size={12} aria-hidden />{' '}{tr('settings.access.accessTab.needsEndpoints')}</span>
                  )}
                  <span style={{ flex: 1 }} />
                  <button type="button" className="btn text sm" onClick={() => setEditing({ policy: p, group: p.group })}>{tr('common.edit')}</button>
                  {!p.builtin && <button type="button" className="btn text sm" onClick={() => setDeleting(p)}>{tr('common.delete')}</button>}
                </div>
                {p.description && <div className="muted">{p.description}</div>}
                {Object.entries(p.needs_endpoints).map(([t, what]) => (
                  <div key={t} className="acc-needs">
                    <AlertTriangle size={14} aria-hidden /> {label(t as AccessTask)}: {what}
                  </div>
                ))}
                <dl className="acc-summary">
                  {POLICY_TASKS.map((t) => (
                    <div key={t}>
                      <dt>{label(t)}</dt>
                      <dd>
                        {taskSummary(p.models[t])}
                        {p.models[t].default && p.models[t].default!.endpoint && (
                          <span className="muted">{' '}{tr('settings.access.accessTab.default2', { endpoint: p.models[t].default!.endpoint, model: p.models[t].default!.model ?? tr('common.itsDefaultModel') })}</span>
                        )}
                      </dd>
                    </div>
                  ))}
                  <div>
                    <dt>{tr('settings.access.accessTab.features')}</dt>
                    <dd>
                      {data.features.filter((f) => p.features[f.key]).map((f) => f.label).join(', ') || tr('common.none')}
                    </dd>
                  </div>
                </dl>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title={tr('settings.access.accessTab.groupsSeen')} desc={tr('settings.access.accessTab.theGroupsYourIdentityProvider')}>
        {q.isLoading ? <Skeleton height={60} /> : !data?.groups_seen.length ? (
          <p className="muted">{tr('settings.access.accessTab.noGroupsYetTheyAppear')}</p>
        ) : (
          <div className="table-wrap">
            <table className="table" data-testid="groups-seen">
              <thead>
                <tr>
                  <th>{tr('common.group')}</th>
                  <th>{tr('common.from')}</th>
                  <th>{tr('common.users')}</th>
                  <th>{tr('settings.access.accessTab.lastSeen')}</th>
                  <th>{tr('settings.access.accessTab.policy')}</th>
                </tr>
              </thead>
              <tbody>
                {data.groups_seen.map((g) => (
                  <tr key={g.name}>
                    <td><b>{g.name}</b></td>
                    <td>{SOURCE[g.source] ?? g.source}</td>
                    <td>{g.users}</td>
                    <td>{g.last_seen_at ? formatRelative(g.last_seen_at) : '—'}</td>
                    <td>
                      {g.has_policy ? <span className="pill ok">{tr('settings.access.accessTab.hasAPolicy')}</span> : (
                        <button type="button" className="btn text sm" onClick={() => setEditing({ policy: null, group: g.name })}>
                          {tr('settings.access.accessTab.createPolicy')}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {editing && data && <PolicyDialog overview={data} policy={editing.policy} group={editing.group} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={tr('settings.access.accessTab.deleteThePolicyFor', { group: deleting?.group })}
        body={tr('settings.access.accessTab.membersOfThisGroupLose')}
        confirmLabel={tr('settings.access.accessTab.deletePolicy')}
        danger
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          const p = deleting!
          setDeleting(null)
          del.mutate(p)
        }}
      />
    </>
  )
}

function PolicyDialog({ overview, policy, group, onClose }: { overview: AccessOverview; policy: Policy | null; group: string; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState<PolicyForm>(() => policyToForm(policy, overview.features, group))
  const labels = Object.fromEntries(overview.tasks.map((t) => [t.task, t.label])) as Partial<Record<AccessTask, string>>
  const problem = validatePolicy(form, labels)
  const save = useMutation({
    mutationFn: () => {
      const body = formToBody(form) as unknown as Record<string, unknown>
      return policy ? api.put(`/admin/access/policies/${policy.id}`, body) : api.post('/admin/access/policies', body)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ACCESS_KEY })
      void qc.invalidateQueries({ queryKey: ['admin', 'users'] })
      toast({ message: policy ? tr('settings.access.accessTab.policySaved') : tr('settings.access.accessTab.policyCreated') })
      onClose()
    },
    onError: (e) => toastError(e, tr('settings.access.accessTab.policyNotSaved')),
  })
  const setTask = (t: AccessTask, v: TaskForm) => setForm((f) => ({ ...f, models: { ...f.models, [t]: v } }))

  return (
    <Dialog
      open
      wide
      modal
      onClose={onClose}
      title={policy ? tr('settings.access.accessTab.policyFor', { policyLabel: policy.label ?? policy.group }) : tr('settings.access.accessTab.addPolicy')}
      footer={
        <>
          {problem && form.group.trim() && <span className="acc-problem">{problem}</span>}
          <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
          <button type="button" className="btn filled" disabled={!!problem || save.isPending} onClick={() => save.mutate()}>
            {policy ? tr('common.save') : tr('common.create')}
          </button>
        </>
      }
    >
      <div className="stack acc-form">
        <div className="grid-2">
          <div className="field">
            <label htmlFor="acc-group">{tr('common.group')}</label>
            <input id="acc-group" className="input" list="acc-groups-seen" value={policy?.builtin ? 'Everyone' : form.group} autoFocus={!policy}
              readOnly={!!policy?.builtin} onChange={(e) => setForm({ ...form, group: e.target.value })} />
            <datalist id="acc-groups-seen">
              {overview.groups_seen.filter((g) => !g.has_policy).map((g) => <option key={g.name} value={g.name} />)}
            </datalist>
            <span className="muted acc-hint">{policy?.builtin ? tr('settings.access.accessTab.everyoneWhoIsNotIn') : tr('settings.access.accessTab.theNameExactlyAsYour')}</span>
          </div>
          <div className="field">
            <label htmlFor="acc-desc">{tr('common.description')}</label>
            <input id="acc-desc" className="input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
        </div>
        {policy && Object.keys(policy.needs_endpoints).length > 0 && (
          <div className="banner warn">
            <AlertTriangle aria-hidden />
            <span>
              {tr('settings.access.accessTab.needsEndpoints2', { map: Object.entries(policy.needs_endpoints).map(([t, what]) => `${labels[t as AccessTask] ?? t}: ${what}`).join(' ') })}
            </span>
          </div>
        )}
        {overview.tasks.map((t) => (
          <TaskEditor key={t.task} label={t.label} desc={t.desc} options={t.options} value={form.models[t.task]} onChange={(v) => setTask(t.task, v)} />
        ))}
        <fieldset className="acc-features">
          <legend>{tr('settings.access.accessTab.features')}</legend>
          {overview.features.map((f) => (
            <Check key={f.key} checked={form.features[f.key]} label={f.label} desc={f.desc}
              onChange={(v) => setForm({ ...form, features: { ...form.features, [f.key]: v } })} />
          ))}
        </fieldset>
      </div>
    </Dialog>
  )
}

const KEY_SEP = '\u0000'
const entryKey = (e: FormEntry | null) => (e ? `${e.endpoint}${KEY_SEP}${e.model ?? ''}` : '')
const keyEntry = (k: string): FormEntry | null => {
  if (!k) return null
  const [endpoint, model] = k.split(KEY_SEP)
  return { endpoint, model: model || null }
}

function TaskEditor({ label, desc, options, value, onChange }: { label: string; desc: string; options: TaskOption[]; value: TaskForm; onChange: (v: TaskForm) => void }) {
  const choices = defaultChoices(value, options)
  const setRow = (i: number, e: FormEntry) => {
    const allowed = value.allowed.slice()
    const old = allowed[i]
    allowed[i] = e
    // A default that named the changed row follows it.
    const def = value.default && old && sameEntry(value.default, old) ? e : value.default
    onChange({ ...value, allowed, default: def })
  }
  const remove = (i: number) => {
    const gone = value.allowed[i]
    const allowed = value.allowed.filter((_, j) => j !== i)
    const def = value.default && gone && value.default.endpoint === gone.endpoint && !allowed.some((a) => a.endpoint === gone.endpoint) ? null : value.default
    onChange({ ...value, allowed, default: def })
  }
  const enabled = options.filter((o) => o.enabled)
  return (
    <fieldset className="acc-task" data-testid={`task-${label}`}>
      <legend>{label}</legend>
      <p className="muted acc-hint">{desc}</p>
      <Check checked={value.any} onChange={(any) => onChange({ ...value, any })} label={tr('settings.access.accessTab.anyEndpoint')}
        desc={tr('settings.access.accessTab.everyEndpointSetUpFor')} />
      {!value.any && (
        <div className="acc-rows">
          {value.allowed.length === 0 && <p className="muted acc-hint">{tr('settings.access.accessTab.noModelsAllowedForThis')}</p>}
          {value.allowed.map((a, i) => {
            const opt = options.find((o) => o.endpoint === a.endpoint)
            return (
              <div key={i} className="acc-row">
                <select className="select" aria-label={tr('settings.access.accessTab.endpoint', { label, value: i + 1 })} value={a.endpoint}
                  onChange={(e) => setRow(i, { endpoint: e.target.value, model: null })}>
                  {!opt && <option value={a.endpoint}>{a.endpoint || tr('settings.access.accessTab.pickAnEndpoint')}</option>}
                  {options.map((o) => (
                    <option key={o.endpoint_id} value={o.endpoint}>
                      {o.endpoint} ({o.locality}){o.enabled ? '' : ' · disabled'}
                    </option>
                  ))}
                </select>
                <select className="select" aria-label={tr('settings.access.accessTab.model', { label, value: i + 1 })} value={a.model ?? ''}
                  onChange={(e) => setRow(i, { endpoint: a.endpoint, model: e.target.value || null })}>
                  <option value="">{tr('settings.access.accessTab.anyModel')}</option>
                  {(opt?.models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
                  {a.model && !opt?.models.includes(a.model) && <option value={a.model}>{a.model}</option>}
                </select>
                <button type="button" className="icon-btn sm" aria-label={tr('settings.access.accessTab.remove', { label, value: i + 1 })} data-tip={tr('common.remove')} onClick={() => remove(i)}>
                  <Trash2 size={16} />
                </button>
              </div>
            )
          })}
          <button type="button" className="btn text sm" disabled={!enabled.length}
            onClick={() => onChange({ ...value, allowed: [...value.allowed, { endpoint: enabled[0].endpoint, model: enabled[0].models[0] ?? null }] })}>
            <Plus size={16} />{' '}{tr('settings.access.accessTab.allowAModel')}
          </button>
          {!options.length && <p className="muted acc-hint">{tr('settings.access.accessTab.noEndpointCanDoThis')}</p>}
        </div>
      )}
      <div className="field acc-default">
        <label>
          {tr('common.default')}
          <select className="select" aria-label={tr('settings.access.accessTab.default', { label })} value={entryKey(value.default)}
            onChange={(e) => onChange({ ...value, default: keyEntry(e.target.value) })}>
            <option value="">{tr('settings.access.accessTab.theServerDefaultForThis')}</option>
            {value.default && !choices.some((c) => sameEntry(c, value.default!)) && (
              <option value={entryKey(value.default)}>{tr('settings.access.accessTab.notAllowed', { entryLabel: entryLabel(value.default, true) })}</option>
            )}
            {choices.map((c) => <option key={entryKey(c)} value={entryKey(c)}>{entryLabel(c, true)}</option>)}
          </select>
        </label>
      </div>
    </fieldset>
  )
}
