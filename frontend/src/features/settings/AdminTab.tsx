import { Lock, Plus, Send, UserPlus } from 'lucide-react'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../../api/client'
import { useApi, useApiList, useMe } from '../../api/hooks'
import type { AdminUser, LastOutcome, ModelEndpoint } from '../../api/types'
import { Link } from 'react-router'
import { Skeleton, Switch } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { Check, Radio, Section } from './common'
import { savedToast, useTextDraft } from './save'
import { UserAccessDialog } from './access/UserAccessDialog'
import { GroupCalendarsPanel } from './GroupCalendars'
import { PhoneAccessPanel } from './PhoneAccessAdmin'
import { PresetsList, setupText } from './PrepareUser'
import { InviteDialog, InvitesList } from './InviteMember'
import { ManagedAccountsDialog, ManagedSwitch, useManagedSetting } from './ManagedAccounts'
import { outcomeText } from './DiagnosticsAdmin'
import { t as tr, tx, appLocale } from '../../i18n'
import { assistantNameNow } from '../rail/assistant/assistantName'
import { useAssistantName } from '../rail/assistant/useAssistantName'

// Settings → Admin (groups.ts): each section below is a page or part of one. Every change saves
// at once with Undo; a text field saves when you leave it.

/** PUT /admin/policies with Undo; `undo` is the body that puts the old values back. */
function useAdminPolicies() {
  const qc = useQueryClient()
  const put = (body: Record<string, unknown>, undo: Record<string, unknown> | null, message: string): void => {
    api
      .put('/admin/policies', body)
      .then(() => {
        void qc.invalidateQueries({ queryKey: ['admin', 'policies'] })
        savedToast(undo ? message : tr('common.changeUndone'), undo ? () => put(undo, null, message) : undefined)
      })
      .catch((e) => toastError(e))
  }
  return put
}

const USERS_PAGE = 25

export function UsersSection() {
  const assistantName = useAssistantName()
  const qc = useQueryClient()
  const me = useMe().data
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const users = useQuery<{ items: AdminUser[]; total: number }>({
    queryKey: ['admin', 'users', search, offset],
    queryFn: ({ signal }) => api.get('/admin/users', { q: search, offset, limit: USERS_PAGE }, signal),
    placeholderData: (prev) => prev,
  })
  const [creating, setCreating] = useState(false)
  const [inviting, setInviting] = useState(false)
  const [resetting, setResetting] = useState<AdminUser | null>(null)
  const [deleting, setDeleting] = useState<AdminUser | null>(null)
  const [viewing, setViewing] = useState<AdminUser | null>(null)
  const [accounts, setAccounts] = useState<AdminUser | null>(null)
  const managedOn = !!useManagedSetting().data?.enabled
  const done = () => void qc.invalidateQueries({ queryKey: ['admin', 'users'] })
  const patch = (u: AdminUser, body: Partial<AdminUser>) =>
    api
      .patch(`/admin/users/${u.id}`, body)
      .then(done)
      .catch((e) => toastError(e))
  const total = users.data?.total ?? 0
  const rows = users.data?.items ?? []
  return (
    <Section
      title={tr('common.users')}
      desc={<>{tx('settings.adminTab.eachUserHasTheirOwn', { name: assistantName }, { link: (c) => <Link to="/settings/admin/access">{c}</Link> })}</>}
      actions={
        <span className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <button type="button" className="btn filled" onClick={() => setInviting(true)}>
            <Send />{' '}{tr('settings.adminTab.inviteSomeone')}
          </button>
          <button type="button" className="btn tonal" onClick={() => setCreating(true)}>
            <UserPlus />{' '}{tr('settings.adminTab.addUser')}
          </button>
        </span>
      }
    >
      <ManagedSwitch />
      <div className="row" style={{ gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
        <input
          className="input"
          style={{ maxWidth: 320 }}
          type="search"
          placeholder={tr('settings.adminTab.searchByEmailOrName')}
          aria-label={tr('settings.adminTab.searchUsers')}
          value={search}
          onChange={(e) => {
            setSearch(e.target.value)
            setOffset(0)
          }}
        />
        <span className="muted" style={{ fontSize: 13 }}>
          {total === 0 ? tr('settings.adminTab.noUsers') : tr('settings.adminTab.of', { value: offset + 1, min: Math.min(offset + USERS_PAGE, total), total: total.toLocaleString(appLocale()) })}
        </span>
        <span className="row" style={{ gap: 4 }}>
          <button type="button" className="btn text sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - USERS_PAGE))}>
            {tr('settings.adminTab.previous')}
          </button>
          <button type="button" className="btn text sm" disabled={offset + USERS_PAGE >= total} onClick={() => setOffset(offset + USERS_PAGE)}>
            {tr('common.next')}
          </button>
        </span>
      </div>
      {users.isLoading ? (
        <Skeleton height={80} />
      ) : (
        <div className="table-wrap"><table className="table">
          <thead>
            <tr>
              <th>{tr('settings.adminTab.user')}</th>
              <th>{tr('settings.adminTab.role')}</th>
              <th>{tr('common.groups')}</th>
              <th>{tr('settings.adminTab.lastSignIn')}</th>
              <th>{tr('common.setup')}</th>
              <th>{tr('settings.adminTab.localOnlyAgents')}</th>
              <th>{tr('settings.adminTab.enabled')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.id}>
                <td>
                  <b>{u.name || u.email}</b>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {u.email}
                  </div>
                </td>
                <td>
                  <select className="select" style={{ width: 110, minHeight: 32, padding: '4px 28px 4px 8px' }} value={u.role} disabled={u.id === me?.user.id} onChange={(e) => void patch(u, { role: e.target.value as AdminUser['role'] })} aria-label={tr('settings.adminTab.roleOf', { email: u.email })}>
                    <option value="user">{tr('settings.adminTab.user2')}</option>
                    <option value="admin">{tr('settings.adminTab.admin2')}</option>
                  </select>
                </td>
                <td data-testid={`user-groups-${u.id}`}>
                  {u.role === 'admin' ? <span className="muted">{tr('settings.adminTab.notRestricted')}</span> : u.groups?.length ? (
                    <span className="acc-groups">{u.groups.map((g) => <span key={g} className="pill">{g}</span>)}</span>
                  ) : <span className="muted">{tr('settings.adminTab.none')}</span>}
                  {u.groups_source && u.role !== 'admin' && (
                    <div className="muted" style={{ fontSize: 12 }}>{u.groups_source === 'idp' ? tr('settings.adminTab.fromTheIdentityProvider') : tr('settings.adminTab.assignedHere')}</div>
                  )}
                </td>
                <td>{u.last_login_at ? formatRelative(u.last_login_at) : 'never'}</td>
                <td data-testid={`user-setup-${u.id}`}>{setupText(u.setup)}</td>
                <td data-testid={`user-local-tokens-${u.id}`}><LocalOnlyTokens tokens={u.local_only_tokens ?? []} /></td>
                <td>
                  <Switch checked={!u.disabled} disabled={u.id === me?.user.id} onChange={(v) => void patch(u, { disabled: !v })} label={tr('settings.adminTab.canSignIn', { email: u.email })} />
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button type="button" className="btn text sm" onClick={() => setViewing(u)}>
                    {tr('settings.adminTab.access')}
                  </button>
                  {managedOn && u.id !== me?.user.id && !u.disabled && (
                    <button type="button" className="btn text sm" onClick={() => setAccounts(u)}>
                      {tr('settings.adminTab.mailAccounts')}
                    </button>
                  )}
                  <button type="button" className="btn text sm" onClick={() => setResetting(u)}>
                    {tr('settings.adminTab.resetPassword')}
                  </button>
                  <button type="button" className="btn text sm" disabled={u.id === me?.user.id} onClick={() => setDeleting(u)}>
                    {tr('common.delete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      <InvitesList />
      <PresetsList />
      {inviting && <InviteDialog onClose={() => setInviting(false)} />}
      {creating && <UserDialog onClose={() => setCreating(false)} />}
      {resetting && <ResetPasswordDialog user={resetting} onClose={() => setResetting(null)} />}
      {viewing && <UserAccessDialog user={viewing} onClose={() => setViewing(null)} />}
      {accounts && <ManagedAccountsDialog user={accounts} onClose={() => setAccounts(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={tr('settings.adminTab.delete', { email: deleting?.email })}
        body={tr('settings.adminTab.thisDeletesTheUserTheir')}
        confirmLabel={tr('settings.adminTab.deleteUser')}
        danger
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          const u = deleting!
          setDeleting(null)
          api
            .del(`/admin/users/${u.id}?confirm=${encodeURIComponent(u.email)}`)
            .then(() => {
              done()
              toast({ message: tr('settings.adminTab.userDeleted') })
            })
            .catch((e) => toastError(e))
        }}
      />
    </Section>
  )
}

/** A user's tokens marked local-only (the user's own statement that the agent runs on their
 *  infrastructure; mailr cannot check it). Names and dates only, never what a token read. */
function LocalOnlyTokens({ tokens }: { tokens: NonNullable<AdminUser['local_only_tokens']> }) {
  if (!tokens.length) return <span className="muted">{tr('settings.adminTab.none')}</span>
  return (
    <div className="stack" style={{ gap: 4 }}>
      {tokens.map((t) => (
        <div key={t.id}>
          <span className={`pill${t.in_effect ? ' warn' : ''}`} data-tip={t.in_effect ? undefined : tr('settings.adminTab.notInEffectThePolicy')}>
            <Lock size={11} />&nbsp;{t.name}{t.in_effect ? '' : ' (off)'}
          </span>
          <div className="muted" style={{ fontSize: 12 }}>
            {tr('settings.adminTab.createdUsed', { relative: formatRelative(t.created_at), value: t.last_used_at ? formatRelative(t.last_used_at) : 'never' })}
          </div>
        </div>
      ))}
    </div>
  )
}

function UserDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState<'user' | 'admin'>('user')
  const [password, setPassword] = useState('')
  const create = useMutation({
    mutationFn: () => api.post('/admin/users', { email: email.trim(), name: name.trim(), role, password: password || null }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['admin', 'users'] })
      toast({ message: tr('settings.adminTab.userCreated') })
      onClose()
    },
    onError: (e) => toastError(e, tr('settings.adminTab.userNotCreated')),
  })
  return (
    <Dialog
      open
      onClose={onClose}
      title={tr('settings.adminTab.addUser')}
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {tr('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!email.includes('@') || create.isPending} onClick={() => create.mutate()}>
            {tr('common.create')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="grid-2">
          <div className="field">
            <label htmlFor="nu-email">{tr('settings.adminTab.emailSignIn')}</label>
            <input id="nu-email" className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
          </div>
          <div className="field">
            <label htmlFor="nu-name">{tr('common.name')}</label>
            <input id="nu-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
        </div>
        <div className="field">
          <label htmlFor="nu-pass">{tr('settings.adminTab.initialPasswordLeaveEmptyFor')}</label>
          <input id="nu-pass" className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <div>
          <Radio name="nu-role" value="user" current={role} onChange={setRole} label={tr('settings.adminTab.user')} />
          <Radio name="nu-role" value="admin" current={role} onChange={setRole} label={tr('settings.adminTab.admin')} desc={tr('settings.adminTab.managesUsersModelEndpointsAnd')} />
        </div>
      </div>
    </Dialog>
  )
}

function ResetPasswordDialog({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const [password, setPassword] = useState('')
  const reset = useMutation({
    mutationFn: () => api.post(`/admin/users/${user.id}/password`, { password }),
    onSuccess: () => {
      toast({ message: tr('settings.adminTab.passwordResetFor', { email: user.email }) })
      onClose()
    },
    onError: (e) => toastError(e),
  })
  return (
    <Dialog
      open
      onClose={onClose}
      title={tr('settings.adminTab.resetPasswordFor', { email: user.email })}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {tr('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={password.length < 10 || reset.isPending} onClick={() => reset.mutate()}>
            {tr('settings.adminTab.reset')}
          </button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="rp-pass">{tr('settings.adminTab.newPasswordAtLeast10')}</label>
        <input id="rp-pass" className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
      </div>
    </Dialog>
  )
}

/** The model tasks (backend intel/llm.TASKS): the same names as Ambrose → Models. */
const TASK_LABEL: Record<string, string> = { get chat() { return tr('settings.adminTab.andWriting', { name: assistantNameNow() }) }, classify: tr('settings.adminTab.sortingAndDecisions'), embed: tr('settings.adminTab.searchIndex'), transcribe: tr('settings.adminTab.speechToText') }
const CAN: Record<string, string[]> = { openai: ['chat', 'classify', 'embed', 'transcribe'], jev: ['classify'] }

/** How Admin → Models labels an endpoint's locality: a local one shows whether its addresses are
 *  private now (the check made on every connection, core/outbound.py). */
export function localityText(m: Pick<ModelEndpoint, 'locality' | 'locality_check'>): { text: string; ok: boolean } {
  if (m.locality !== 'local') return { text: tr('settings.adminTab.localityHosted'), ok: false }
  if (!m.locality_check || m.locality_check.ok) return { text: tr('settings.adminTab.localityChecked'), ok: true }
  return { text: tr('settings.adminTab.localityCheckFailed', { error: m.locality_check.error ?? '' }), ok: false }
}

interface TaskDefaults {
  task: string
  label: string
  desc: string
  default: { endpoint: string; model: string } | null
  local: { endpoint: string; model: string } | null
  /** `checked` and `failed` (Search index only): the models that made a search embedding when
   *  chosen, and those that did not. */
  options: { endpoint: string; models: string[]; locality: string; adapter: string; checked?: string[]; failed?: string[] }[]
  health?: LastOutcome | null
}

interface ModelChoice { value: string; label: string }

/** A task's choices for one column. For Search index, only models that passed the embedding check
 *  and the current value are offered plainly; models that failed it are listed under "Does not make
 *  embeddings" and cannot be chosen; the rest are under "Not checked yet", and choosing one runs the
 *  check (PUT /admin/models/defaults/embed refuses a model that makes no embeddings). */
export function modelChoices(t: TaskDefaults, localOnly: boolean, current: string): { shown: ModelChoice[]; unchecked: ModelChoice[]; failed: ModelChoice[] } {
  const shown: ModelChoice[] = []
  const unchecked: ModelChoice[] = []
  const failed: ModelChoice[] = []
  for (const o of t.options) {
    if (localOnly && o.locality !== 'local') continue
    for (const m of o.models.length ? o.models : ['']) {
      const c = { value: `${o.endpoint}\u0000${m}`, label: `${o.endpoint} · ${m} (${o.locality})` }
      if (!o.checked || o.checked.includes(m) || c.value === current) shown.push(c)
      else if (o.failed?.includes(m)) failed.push(c)
      else unchecked.push(c)
    }
  }
  return { shown, unchecked, failed }
}

type LocalPolicy = 'per_user' | 'local_all'

/** Admin → Hosted models for everyone (intel/locality.py).
 *  ACT settings.local_models_household @web.settings */
export function LocalPolicySection() {
  const assistantName = useAssistantName()
  const qc = useQueryClient()
  const q = useQuery<{ policy: LocalPolicy }>({ queryKey: ['admin', 'models', 'local-policy'], queryFn: () => api.get('/admin/models/local-policy') })
  const set = useMutation({
    mutationFn: (policy: LocalPolicy) => api.put<{ policy: LocalPolicy }>('/admin/models/local-policy', { policy }),
    // The choice shows at once; a refusal puts the saved one back.
    onMutate: (policy) => {
      const before = qc.getQueryData<{ policy: LocalPolicy }>(['admin', 'models', 'local-policy'])
      qc.setQueryData(['admin', 'models', 'local-policy'], { ...before, policy })
      return before
    },
    onError: (e, _p, before) => {
      qc.setQueryData(['admin', 'models', 'local-policy'], before)
      toastError(e, tr('common.notSaved'))
    },
    onSuccess: (data, _policy, before) => {
      qc.setQueryData(['admin', 'models', 'local-policy'], data)
      void qc.invalidateQueries({ queryKey: ['me', 'local-models'] })
      const was = before?.policy
      savedToast(tr('common.saved2'), was && was !== data.policy ? () => set.mutate(was) : undefined)
    },
  })
  const cur = q.data?.policy ?? 'per_user'
  return (
    <Section
      title={tr('settings.adminTab.hostedModelsForEveryone')}
      desc={<>{tr('settings.adminTab.whetherMailNotesAndTables')}</>}
    >
      {!q.data ? <Skeleton height={96} /> : (
        <div data-testid="local-policy">
          <Radio name="local-policy" value="per_user" current={cur} onChange={(v) => set.mutate(v)} label={tr('settings.adminTab.eachPersonChooses')}
            desc={tr('settings.adminTab.hostedModelsAreUsedWhere', { name: assistantName })} />
          <Radio name="local-policy" value="local_all" current={cur} onChange={(v) => set.mutate(v)} label={tr('settings.adminTab.localModelsOnlyForEveryone')}
            desc={tr('settings.adminTab.nothingGoesToAHosted')} />
        </div>
      )}
    </Section>
  )
}

/** Admin → Models per task: one server default and one local-only model per task. */
export function DefaultsSection() {
  const assistantName = useAssistantName()
  const qc = useQueryClient()
  const q = useQuery<{ tasks: TaskDefaults[] }>({ queryKey: ['admin', 'models', 'defaults'], queryFn: () => api.get('/admin/models/defaults') })
  // A Search index model the server refused (no embeddings), shown under its row.
  const [refused, setRefused] = useState<{ task: string; message: string } | null>(null)
  const set = useMutation({
    mutationFn: ({ t, def, local }: { t: TaskDefaults; def?: string; local?: string; undo?: boolean }) => {
      const cur = (x: { endpoint: string; model: string } | null) => (x ? `${x.endpoint}\u0000${x.model}` : '')
      const [endpoint, model] = (def ?? cur(t.default)).split('\u0000')
      const [lEndpoint, lModel] = (local ?? cur(t.local)).split('\u0000')
      return api.put(`/admin/models/defaults/${t.task}`, {
        endpoint: endpoint || null, model: model || null, local_endpoint: lEndpoint || null, local_model: lModel || null,
      })
    },
    onMutate: () => setRefused(null),
    onSuccess: (_d, v) => {
      void qc.invalidateQueries({ queryKey: ['admin', 'models'] })
      void qc.invalidateQueries({ queryKey: ['me', 'models'] })
      const key = (x: { endpoint: string; model: string } | null) => (x ? `${x.endpoint}\u0000${x.model}` : '')
      // Undo puts the task's old default and local model back.
      if (!v.undo) savedToast(tr('common.saved2'), () => set.mutate({ t: v.t, def: key(v.t.default), local: key(v.t.local), undo: true }))
      else savedToast(tr('common.changeUndone'))
    },
    onError: (e, v) => {
      if (v.t.task === 'embed' && e instanceof ApiError && e.status === 400) {
        setRefused({ task: v.t.task, message: e.message })
        // The refused model now shows under "Does not make embeddings".
        void qc.invalidateQueries({ queryKey: ['admin', 'models', 'defaults'] })
      } else toastError(e, tr('settings.adminTab.couldNotChangeTheDefault'))
    },
  })
  const options = (t: TaskDefaults, localOnly: boolean, current: string) => {
    const { shown, unchecked, failed } = modelChoices(t, localOnly, current)
    return (
      <>
        {shown.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        {unchecked.length > 0 && (
          <optgroup label={tr('settings.adminTab.notCheckedYet')}>
            {unchecked.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </optgroup>
        )}
        {failed.length > 0 && (
          <optgroup label={tr('settings.adminTab.doesNotMakeEmbeddings')} disabled>
            {failed.map((o) => <option key={o.value} value={o.value} disabled>{o.label}</option>)}
          </optgroup>
        )}
      </>
    )
  }
  const cur = (x: { endpoint: string; model: string } | null) => (x ? `${x.endpoint}\u0000${x.model}` : '')
  return (
    <Section
      title={tr('settings.adminTab.whichModelDoesWhat')}
      desc={<>{tx('settings.adminTab.forEachKindOfWork', { name: assistantName }, { link: (c) => <Link to="/settings/admin/access">{c}</Link>, link2: (c) => <Link to="/settings/ambrose/models">{c}</Link> })}</>}
    >
      {q.isLoading || !q.data ? <Skeleton height={80} /> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>{tr('common.work')}</th><th>{tr('common.default')}</th><th>{tr('settings.adminTab.localOnlyMail2')}</th></tr></thead>
          <tbody>
            {q.data.tasks.map((t) => (
              <tr key={t.task}>
                <td>
                  <b>{t.label}</b><div className="muted" style={{ fontSize: 12, maxWidth: 320 }}>{t.desc}</div>
                  {t.health && (() => {
                    // The last success and the last error of this work, in plain words.
                    const o = outcomeText(t.health)
                    return <div className={`model-test ${o.tone}`} data-testid={`task-health-${t.task}`}>{o.text}</div>
                  })()}
                  {refused?.task === t.task && <div className="model-test err" role="alert" data-testid={`task-refused-${t.task}`}>{refused.message}</div>}
                </td>
                <td>
                  <select className="select" aria-label={tr('settings.adminTab.defaultFor', { label: t.label })} disabled={set.isPending}
                    value={cur(t.default)}
                    onChange={(e) => set.mutate({ t, def: e.target.value })}>
                    <option value="">{tr('common.none')}</option>
                    {options(t, false, cur(t.default))}
                  </select>
                </td>
                <td>
                  <select className="select" aria-label={tr('settings.adminTab.localOnlyModelFor', { label: t.label })} disabled={set.isPending}
                    value={cur(t.local)}
                    onChange={(e) => set.mutate({ t, local: e.target.value })}>
                    <option value="">{t.default && t.options.find((o) => o.endpoint === t.default!.endpoint)?.locality === 'local' ? tr('settings.adminTab.sameAsDefault') : tr('settings.adminTab.anyLocalDefault')}</option>
                    {options(t, true, cur(t.local))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </Section>
  )
}

interface TestResult {
  ok: boolean
  kind: 'chat' | 'embed' | 'decide' | 'transcribe'
  model: string
  latency_ms: number | null
  reply?: string
  error?: string
}

/** The Test button's answer in one line. */
export function testText(r: TestResult): string {
  const what = r.kind === 'embed' ? 'embedding' : r.kind === 'decide' ? 'decision' : 'reply'
  if (r.ok) return tr('settings.adminTab.worksAnsweredInMs', { model: r.model, latencyMs: r.latency_ms, what, reply: r.reply })
  return `${r.error}${r.latency_ms != null ? tr('settings.adminTab.afterMs', { latencyMs: r.latency_ms }) : ''}`
}

export function ModelsSection() {
  const qc = useQueryClient()
  const q = useApiList<ModelEndpoint>(['admin', 'models'], '/admin/models')
  const [editing, setEditing] = useState<Partial<ModelEndpoint> | null>(null)
  const [deleting, setDeleting] = useState<ModelEndpoint | null>(null)
  // ACT settings.model_test @web.settings
  const [tests, setTests] = useState<Record<number, TestResult | 'running'>>({})
  const runTest = (m: ModelEndpoint) => {
    setTests((t) => ({ ...t, [m.id]: 'running' }))
    api
      .post<TestResult>(`/admin/models/${m.id}/test`, {})
      .then((r) => {
        setTests((t) => ({ ...t, [m.id]: r }))
        void qc.invalidateQueries({ queryKey: ['admin', 'models'] })
        void qc.invalidateQueries({ queryKey: ['admin', 'diagnostics'] })
      })
      .catch((e) => {
        setTests((t) => {
          const { [m.id]: _gone, ...rest } = t
          return rest
        })
        toastError(e, tr('settings.adminTab.theTestCouldNotRun'))
      })
  }
  return (
    <Section
      title={tr('settings.adminTab.modelEndpoints')}
      desc={<>{tr('settings.adminTab.whereTheModelsAreOpenai')}</>}
      actions={
        <button type="button" className="btn filled" onClick={() => setEditing({ name: '', base_url: '', locality: 'local', adapter: 'openai', models: [], defaults: {}, enabled: true })}>
          <Plus />{' '}{tr('settings.adminTab.addEndpoint')}
        </button>
      }
    >
      {q.isLoading ? (
        <Skeleton height={80} />
      ) : (q.data ?? []).length === 0 ? (
        <p className="muted">{tr('settings.adminTab.noModelEndpointsSearchRules')}</p>
      ) : (
        <div className="table-wrap"><table className="table">
          <thead>
            <tr>
              <th>{tr('common.name')}</th>
              <th>{tr('settings.adminTab.locality')}</th>
              <th>{tr('settings.adminTab.usedFor')}</th>
              <th>{tr('settings.adminTab.enabled')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(q.data ?? []).map((m) => (
              <tr key={m.id}>
                <td>
                  <b>{m.name}</b>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {m.base_url} · {m.adapter}
                  </div>
                  {(() => {
                    const t = tests[m.id]
                    if (t === 'running') return <div className="model-test" role="status">{tr('common.testing')}</div>
                    if (t) return <div className={`model-test ${t.ok ? 'ok' : 'err'}`} role="status" data-testid={`model-test-${m.id}`}>{testText(t)}</div>
                    if (m.health) {
                      const o = outcomeText(m.health)
                      return <div className={`model-test ${o.tone}`}>{o.text}</div>
                    }
                    return null
                  })()}
                </td>
                <td>
                  {(() => {
                    const l = localityText(m)
                    return <span className={`pill ${l.ok ? 'ok' : 'warn'}`} data-testid={`model-locality-${m.id}`}>{l.text}</span>
                  })()}
                </td>
                <td style={{ fontSize: 12.5 }}>
                  {Object.entries(m.defaults ?? {})
                    .map(([k, v]) => tr('settings.adminTab.text', { taskLabel: TASK_LABEL[k.replace('.local', '')] ?? k, value: k.endsWith('.local') ? tr('settings.adminTab.localOnlyMail') : '', v }))
                    .join(' · ') || <span className="muted">{tr('settings.adminTab.notUsedYet')}</span>}
                </td>
                <td>
                  <Switch
                    checked={m.enabled}
                    onChange={(v) =>
                      void api
                        .patch(`/admin/models/${m.id}`, { name: m.name, base_url: m.base_url, locality: m.locality, adapter: m.adapter, models: m.models, enabled: v })
                        .then(() => qc.invalidateQueries({ queryKey: ['admin', 'models'] }))
                        .catch((e) => toastError(e))
                    }
                    label={tr('settings.adminTab.enabled')}
                  />
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button type="button" className="btn text sm" disabled={tests[m.id] === 'running'} onClick={() => runTest(m)} aria-label={tr('settings.adminTab.test', { itemName: m.name })}>
                    {tr('settings.adminTab.test2')}
                  </button>
                  <button type="button" className="btn text sm" onClick={() => setEditing(m)}>
                    {tr('common.edit')}
                  </button>
                  {!m.from_env && (
                    <button type="button" className="btn text sm" onClick={() => setDeleting(m)}>
                      {tr('common.delete')}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {editing && <ModelDialog model={editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={tr('settings.adminTab.delete2', { deletingName: deleting?.name })}
        body={tr('settings.adminTab.tasksMappedToThisEndpoint')}
        confirmLabel={tr('common.delete2')}
        danger
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          const m = deleting!
          setDeleting(null)
          api
            .del(`/admin/models/${m.id}`)
            .then(() => void qc.invalidateQueries({ queryKey: ['admin', 'models'] }))
            .catch((e) => toastError(e))
        }}
      />
    </Section>
  )
}

function ModelDialog({ model, onClose }: { model: Partial<ModelEndpoint>; onClose: () => void }) {
  const qc = useQueryClient()
  const [m, setM] = useState<Partial<ModelEndpoint>>(model)
  const [apiKey, setApiKey] = useState('')
  const [modelsText, setModelsText] = useState((model.models ?? []).join(', '))
  const discovered = useQuery({
    queryKey: ['admin', 'models', 'probe', m.base_url],
    queryFn: () => api.post<{ models: string[] }>('/admin/models/probe', { base_url: m.base_url, api_key: apiKey || null, id: model.id ?? null }),
    enabled: false,
    retry: false,
  })
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: m.name?.trim(),
        base_url: m.base_url?.trim(),
        locality: m.locality,
        adapter: m.adapter,
        models: modelsText.split(/[\s,]+/).filter(Boolean),
        enabled: m.enabled ?? true,
        ...(apiKey ? { api_key: apiKey } : {}),
      }
      return model.id ? api.patch(`/admin/models/${model.id}`, body) : api.post('/admin/models', body)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['admin', 'models'] })
      toast({ message: tr('settings.adminTab.endpointSaved') })
      onClose()
    },
    onError: (e) => toastError(e, tr('common.notSaved')),
  })
  return (
    <Dialog
      open
      onClose={onClose}
      title={model.id ? tr('settings.adminTab.editModelEndpoint') : tr('settings.adminTab.addModelEndpoint')}
      wide
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {tr('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!m.name?.trim() || !m.base_url?.trim() || save.isPending} onClick={() => save.mutate()}>
            {tr('common.save')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="grid-2">
          <div className="field">
            <label htmlFor="me-name">{tr('common.name')}</label>
            <input id="me-name" className="input" value={m.name ?? ''} onChange={(e) => setM({ ...m, name: e.target.value })} placeholder={tr('settings.adminTab.localVllm')} />
          </div>
          <div className="field">
            <label htmlFor="me-adapter">{tr('settings.adminTab.api')}</label>
            <select id="me-adapter" className="select" value={m.adapter ?? 'openai'} disabled={!!model.from_env} onChange={(e) => setM({ ...m, adapter: e.target.value })}>
              <option value="openai">{tr('settings.adminTab.openaiCompatible')}</option>
              <option value="jev">{tr('settings.adminTab.jevTypesafe')}</option>
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="me-url">{tr('settings.adminTab.baseUrl')}</label>
          <input id="me-url" className="input" value={m.base_url ?? ''} readOnly={!!model.from_env} onChange={(e) => setM({ ...m, base_url: e.target.value })} placeholder="http://models.local:8000/v1" />
          {model.from_env && <span className="muted" style={{ fontSize: 12.5 }} data-testid="model-from-env">{tr('settings.adminTab.setUpByMailrAsrUrl')}</span>}
        </div>
        <div className="field">
          <label htmlFor="me-key">{tr('settings.adminTab.apiKey', { value: model.id ? tr('settings.adminTab.leaveEmptyToKeep') : '(optional)' })}</label>
          <input id="me-key" className="input" type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        </div>
        <div>
          <Radio name="me-loc" value="local" current={m.locality ?? 'local'} disabled={!!model.from_env} onChange={(v) => setM({ ...m, locality: v })} label={tr('common.local')} desc={tr('settings.adminTab.aModelServerOnYour')} />
          <Radio name="me-loc" value="hosted" current={m.locality ?? 'local'} disabled={!!model.from_env} onChange={(v) => setM({ ...m, locality: v })} label={tr('common.hosted')} desc={tr('settings.adminTab.aThirdPartyServiceNever')} />
        </div>
        <div className="field">
          <label htmlFor="me-models">{tr('settings.adminTab.modelsCommaSeparated')}</label>
          <div className="row">
            <input id="me-models" className="input" value={modelsText} onChange={(e) => setModelsText(e.target.value)} placeholder="qwen3-8b" />
            <button type="button" className="btn outlined" disabled={!m.base_url} onClick={() => void discovered.refetch().then((r) => r.data && setModelsText(r.data.models.join(', ')))}>
              {tr('settings.adminTab.discover')}
            </button>
          </div>
          {discovered.isError && <span className="error">{(discovered.error as Error).message}</span>}
        </div>
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>
          {tr('settings.adminTab.canDoChooseWhatIt', { map: (CAN[m.adapter ?? 'openai'] ?? []).map((t) => TASK_LABEL[t]).join(', ') })}
        </p>
        <Check checked={m.enabled ?? true} onChange={(v) => setM({ ...m, enabled: v })} label={tr('settings.adminTab.enabled')} />
      </div>
    </Dialog>
  )
}

type SharingPolicy = 'anyone' | 'groups' | 'nobody'
// One policy covers calendars, notes, lists and tables.
const SHARING_SAVED: Record<SharingPolicy, string> = {
  anyone: tr('settings.adminTab.usersCanShareCalendarsNotes'),
  groups: tr('settings.adminTab.usersCanShareCalendarsNotes2'),
  nobody: tr('settings.adminTab.sharingOfCalendarsNotesAnd'),
}

/** The instance settings calendars.builtin, calendars.builtin_who (calendar/builtin.py) and
 *  calendars.sharing (calendar/sharing.py), and the group calendars (GroupCalendars.tsx).
 *  ACT settings.builtin_calendars @web.settings */
type CalBody = { builtin: boolean; who?: 'everyone' | 'groups'; sharing?: SharingPolicy }

export function CalendarsSection() {
  const qc = useQueryClient()
  const q = useApi<{ builtin: boolean; who?: 'everyone' | 'groups'; sharing?: SharingPolicy; connectors: number; users: number; phone_access?: boolean; phone_url?: string }>(['admin', 'calendars'], '/admin/calendars')
  const save = useMutation({
    mutationFn: ({ undo: _undo, ...body }: CalBody & { undo?: CalBody }) => api.put('/admin/calendars', body),
    onSuccess: (_d, body) => {
      void qc.invalidateQueries({ queryKey: ['admin', 'calendars'] })
      void qc.invalidateQueries({ queryKey: ['connectors'] })
      const who = body.who ?? q.data?.who ?? 'everyone'
      const message = body.sharing
        ? SHARING_SAVED[body.sharing]
        : !body.builtin
          ? tr('settings.adminTab.builtInCalendarsOffTheir')
          : who === 'groups'
            ? tr('settings.adminTab.builtInCalendarsOnFor')
            : tr('settings.adminTab.builtInCalendarsOnEvery')
      const undo = body.undo
      savedToast(message, undo ? () => save.mutate(undo) : undefined)
    },
    onError: (e) => toastError(e),
  })
  const on = !!q.data?.builtin
  const who = q.data?.who ?? 'everyone'
  const sharing = q.data?.sharing ?? 'anyone'
  const change = (body: CalBody) => save.mutate({ ...body, undo: { builtin: on, who, sharing } })
  return (
    <Section
      title={tr('common.calendars')}
      desc={tr('settings.adminTab.builtInCalendarsGiveUsers')}
    >
      <label className="choice">
        <input type="checkbox" className="checkbox" checked={on} disabled={!q.data || save.isPending} onChange={(e) => change({ builtin: e.target.checked })} data-testid="builtin-calendars" />
        <span>{tr('settings.adminTab.giveUsersAMailrCalendar')}</span>
      </label>
      {on && (
        <div className="gcal-who" role="radiogroup" aria-label={tr('settings.adminTab.whoGetsOne')}>
          <Radio name="cal-who" value="everyone" current={who} onChange={(v) => change({ builtin: true, who: v })} label={tr('settings.adminTab.everyone')} />
          <Radio
            name="cal-who"
            value="groups"
            current={who}
            onChange={(v) => change({ builtin: true, who: v })}
            label={tr('settings.adminTab.membersOfChosenGroups')}
            desc={<>{tx('settings.adminTab.turnOnMembersGetA', undefined, { link: (c) => <Link to="/settings/admin/access">{c}</Link> })}</>}
          />
        </div>
      )}
      <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
        {on
          ? tr('settings.adminTab.onOfUsersHaveOne', { connectors: q.data?.connectors ?? 0, count: q.data?.users ?? 0 })
          : tr('settings.adminTab.offTurningItOnGives')}
      </div>
      {/* The one sharing policy (calendar/sharing.py, notes/sharing.py): calendars, notes and tables.
          Shown with the calendars off too, because notes and tables follow it.
          ACT settings.calendar_sharing @web.settings */}
      <div className="gcal" role="radiogroup" aria-label={tr('settings.adminTab.sharingBetweenUsers')} data-testid="calendar-sharing">
        <h3 className="gcal-head">{tr('settings.adminTab.sharingBetweenUsersCalendarsNotes')}</h3>
        <p className="muted gcal-desc">{tr('settings.adminTab.usersCanShareTheirOwn')}</p>
        <Radio name="cal-sharing" value="anyone" current={sharing} onChange={(v) => change({ builtin: on, sharing: v })} label={tr('settings.adminTab.withAnyoneOnThisServer')} />
        <Radio name="cal-sharing" value="groups" current={sharing} onChange={(v) => change({ builtin: on, sharing: v })} label={tr('settings.adminTab.onlyWithPeopleInThe')} desc={tr('settings.adminTab.groupsComeFromYourSign')} />
        <Radio name="cal-sharing" value="nobody" current={sharing} onChange={(v) => change({ builtin: on, sharing: v })} label={tr('settings.adminTab.nobody')} desc={tr('settings.adminTab.usersCanStillStopThe')} />
      </div>
      <GroupCalendarsPanel enabled={on} />
      <PhoneAccessPanel enabled={on} on={!!q.data?.phone_access} url={q.data?.phone_url ?? ''} />
    </Section>
  )
}

export function ImageProxySection() {
  const q = useApi<{
    image_proxy: boolean
    image_proxy_egress: string
    image_proxy_egress_env?: boolean
    require_egress?: boolean
    egress_check?: { ok: boolean; state: string; detail: string; checked_at: number | null }
  }>(['admin', 'policies'], '/admin/policies')
  const put = useAdminPolicies()
  const fromEnv = !!q.data?.image_proxy_egress_env
  const required = !!q.data?.require_egress
  const check = q.data?.egress_check
  const savedEgress = q.data?.image_proxy_egress ?? ''
  const egress = useTextDraft(savedEgress, (v) => {
    if (v.trim() !== savedEgress) put({ image_proxy_egress: v.trim() }, { image_proxy_egress: savedEgress }, 'Egress proxy saved.')
  })
  const on = q.data?.image_proxy ?? true
  return (
    <Section
      title={tr('settings.adminTab.imageProxy')}
      desc={tr('settings.adminTab.remoteImagesInMailAre')}
    >
      <label className="choice">
        <input type="checkbox" className="checkbox" checked={on} disabled={!q.data || required}
          onChange={(e) => put({ image_proxy: e.target.checked }, { image_proxy: on }, e.target.checked ? 'Images load through Mailr.' : 'Image proxy off.')} />
        <span>{tr('settings.adminTab.loadImagesThroughMailr')}</span>
      </label>
      {required && (
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          {tr('settings.adminTab.alwaysOnTheServerRequires')}
        </div>
      )}
      <div className="field" style={{ maxWidth: 480, marginTop: 12 }}>
        <label htmlFor="img-egress">{tr('settings.adminTab.egressProxyOptional')}</label>
        <input
          id="img-egress"
          className="input"
          value={egress.value}
          onChange={(e) => egress.set(e.target.value)}
          onBlur={egress.flush}
          placeholder="http://vpn-host:8888"
          disabled={fromEnv || !q.data}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          {fromEnv
            ? tr('settings.adminTab.setByTheServerEnvironment')
            : <>{tr('settings.adminTab.anHttpProxyForImage')}</>}
        </div>
      </div>
      {check && check.state !== 'off' && (
        <div className={check.ok ? 'muted' : 'banner error'} style={{ fontSize: 13, marginTop: 12 }} data-testid="egress-check">
          {tr('settings.adminTab.egressCheck', { detail: check.detail, value: check.checked_at ? ` (${new Date(check.checked_at * 1000).toLocaleTimeString(appLocale())})` : '' })}
        </div>
      )}
    </Section>
  )
}

export function PoliciesSection() {
  const q = useApi<{ local_only_domains: string[] }>(['admin', 'policies'], '/admin/policies')
  const put = useAdminPolicies()
  const saved = (q.data?.local_only_domains ?? []).join('\n')
  const parse = (v: string) => v.split(/\s+/).map((x) => x.trim().toLowerCase()).filter(Boolean)
  const text = useTextDraft(saved, (v) => {
    if (parse(v).join('\n') !== parse(saved).join('\n')) put({ local_only_domains: parse(v) }, { local_only_domains: parse(saved) }, 'Local-only domains saved.')
  }, 1500)
  return (
    <Section title={tr('settings.adminTab.dataPolicies')} desc={tr('settings.adminTab.accountsAtTheseDomainsAre')}>
      <div className="field" style={{ maxWidth: 480 }}>
        <label htmlFor="pol-domains">{tr('settings.adminTab.localOnlyDomainsOnePer')}</label>
        <textarea id="pol-domains" className="textarea" rows={5} value={text.value} disabled={!q.data} onChange={(e) => text.set(e.target.value)} onBlur={text.flush} placeholder={tr('settings.adminTab.exampleEduEdu')} />
      </div>
    </Section>
  )
}
