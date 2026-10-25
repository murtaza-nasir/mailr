// Admin → Users → access: a user's groups (from the identity provider, or assigned by an admin to
// a password-only account) and what their policies allow (GET /admin/users/{id}/access).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../../api/client'
import type { AccessInfo, AdminUser } from '../../../api/types'
import { Skeleton } from '../../../components/bits'
import { Dialog } from '../../../components/Dialog'
import { formatRelative } from '../../../lib/format'
import { toast, toastError } from '../../../stores/ui'
import { ACCESS_KEY, POLICY_TASKS, parseGroups, type AccessOverview, type UserAccess } from './policyForm'
import { t as tr, tx } from '../../../i18n'
import { assistantNameNow } from '../../rail/assistant/assistantName'

const TASK_LABEL = { get chat() { return tr('settings.access.userAccessDialog.andWriting', { name: assistantNameNow() }) }, classify: tr('settings.access.userAccessDialog.sortingAndDecisions') } as const

export function AccessSummary({ access }: { access: AccessInfo }) {
  const overview = useQuery<AccessOverview>({ queryKey: ACCESS_KEY, queryFn: () => api.get('/admin/access') }).data
  if (access.unrestricted) return <p>{tr('settings.access.userAccessDialog.adminsAreNeverRestrictedEvery')}</p>
  const label = (k: string) => overview?.features.find((f) => f.key === k)?.label ?? k
  const on = Object.entries(access.features).filter(([, v]) => v).map(([k]) => label(k))
  return (
    <dl className="acc-summary" data-testid="user-access">
      <div>
        <dt>{tr('settings.access.userAccessDialog.policiesThatApply')}</dt>
        <dd>{access.policies.length ? access.policies.join(', ') : tr('common.none')}</dd>
      </div>
      <div>
        <dt>{tr('settings.access.userAccessDialog.features')}</dt>
        <dd>{on.length ? on.join(', ') : tr('common.none')}</dd>
      </div>
      {POLICY_TASKS.map((t) => {
        const ti = access.tasks[t]
        return (
          <div key={t}>
            <dt>{TASK_LABEL[t]}</dt>
            <dd>
              {ti.all ? tr('settings.access.userAccessDialog.everyModel') : ti.allowed.length ? ti.allowed.map((a) => `${a.endpoint} · ${a.models ? a.models.join(', ') : tr('settings.access.userAccessDialog.anyModel')}`).join('; ') : tr('common.none')}
              {ti.default && <span className="muted">{' '}{tr('settings.access.userAccessDialog.default', { endpoint: ti.default.endpoint, model: ti.default.model ?? tr('common.itsDefaultModel') })}</span>}
            </dd>
          </div>
        )
      })}
      {access.needs_endpoints && (
        <div>
          <dt>{tr('common.note')}</dt>
          <dd>{tr('settings.access.userAccessDialog.aPolicyOfThisUser')}</dd>
        </div>
      )}
      {access.message && (
        <div>
          <dt>{tr('settings.access.userAccessDialog.theUserSees')}</dt>
          <dd>{access.message}</dd>
        </div>
      )}
    </dl>
  )
}

export function UserAccessDialog({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const qc = useQueryClient()
  const key = ['admin', 'users', user.id, 'access']
  const q = useQuery<UserAccess>({ queryKey: key, queryFn: () => api.get(`/admin/users/${user.id}/access`) })
  const [text, setText] = useState<string | null>(null)
  const d = q.data
  const draft = text ?? (d?.assigned_groups ?? []).join(', ')
  const save = useMutation({
    mutationFn: () => api.put<UserAccess>(`/admin/users/${user.id}/groups`, { groups: parseGroups(draft) }),
    onSuccess: (res) => {
      qc.setQueryData(key, res)
      setText(null)
      void qc.invalidateQueries({ queryKey: ['admin', 'users'] })
      void qc.invalidateQueries({ queryKey: ACCESS_KEY })
      toast({ message: tr('settings.access.userAccessDialog.groupsSaved') })
    },
    onError: (e) => toastError(e, tr('settings.access.userAccessDialog.groupsNotSaved')),
  })
  const dirty = d ? JSON.stringify(parseGroups(draft)) !== JSON.stringify([...d.assigned_groups].sort()) : false
  return (
    <Dialog open wide onClose={onClose} title={tr('settings.access.userAccessDialog.accessFor', { email: user.email })}
      footer={<button type="button" className="btn text" onClick={onClose}>{tr('common.close')}</button>}>
      {q.isLoading || !d ? <Skeleton height={80} /> : (
        <div className="stack">
          <p>
            {d.source === 'idp' ? (
              <>{tx('settings.access.userAccessDialog.groupsFromTheIdentityProvider', { value: d.idp_source ? ` (${d.idp_source})` : '', value2: d.idp_groups_at ? tr('settings.access.userAccessDialog.lastReported', { relative: formatRelative(d.idp_groups_at) }) : '', value3: d.groups.length ? d.groups.join(', ') : 'none' }, { b: (c) => <b>{c}</b> })}</>
            ) : (
              <>{tx('settings.access.userAccessDialog.groupsAssignedHere', { value: d.groups.length ? d.groups.join(', ') : 'none' }, { b: (c) => <b>{c}</b> })}</>
            )}
          </p>
          {d.can_assign && (
            <div className="field">
              <label htmlFor="ua-groups">{tr('settings.access.userAccessDialog.assignedGroups')}</label>
              <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                <input id="ua-groups" className="input" style={{ flex: 1, minWidth: 200 }} list="ua-policy-groups" value={draft}
                  placeholder={tr('settings.access.userAccessDialog.staffMembers')} onChange={(e) => setText(e.target.value)} />
                <button type="button" className="btn filled" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
                  {tr('settings.access.userAccessDialog.saveGroups')}
                </button>
              </div>
              <PolicyGroupList />
              <span className="hint">{tr('settings.access.userAccessDialog.thisAccountHasNoIdentity')}</span>
            </div>
          )}
          <h3 className="sub-h">{tr('settings.access.userAccessDialog.whatThisUserMayUse')}</h3>
          <AccessSummary access={d.access} />
        </div>
      )}
    </Dialog>
  )
}

function PolicyGroupList() {
  const overview = useQuery<AccessOverview>({ queryKey: ACCESS_KEY, queryFn: () => api.get('/admin/access') }).data
  return (
    <datalist id="ua-policy-groups">
      {(overview?.policies ?? []).map((p) => <option key={p.id} value={p.group} />)}
    </datalist>
  )
}
