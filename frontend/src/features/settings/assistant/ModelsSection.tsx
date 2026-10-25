import { assistantNameNow } from '../../rail/assistant/useAssistantName'
// Settings → Ambrose → Models: which endpoint and model each task uses (api/model_prefs.py).
// Admins register endpoints under Settings → Admin; here each user picks per task. The server lists
// only the models the user's access policy allows; `locked` (model choice off in the policy) shows
// the models read-only.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, HardDrive, Lock, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router'
import { api } from '../../../api/client'
import { useMe } from '../../../api/hooks'
import { Skeleton } from '../../../components/bits'
import { toastError } from '../../../stores/ui'
import { Section } from '../common'
import { t as tr, tx } from '../../../i18n'

interface Where {
  endpoint: string
  model: string
  locality: string
}

interface TaskModels {
  task: string
  label: string
  desc: string
  choice: { endpoint: string; model: string } | null
  options: { endpoint: string; models: string[]; default_model: string | null; locality: string; adapter: string }[]
  /** The access policy does not let the user choose (shown read-only). */
  locked?: boolean
  effective: Where | null
  effective_local_only: Where | null
}

interface ModelsOut {
  tasks: TaskModels[]
  /** Model choice is off in the access policy. */
  locked?: boolean
  /** The access policy limits the models (not an admin). */
  restricted?: boolean
  /** The no-assistant message, when the assistant is off. */
  message?: string | null
  embed: Where | null
}

const KEY = ['me', 'models'] as const

function Place({ w }: { w: Where | null }) {
  if (!w) return <span className="muted">{tr('settings.assistant.modelsSection.noneAvailable')}</span>
  return (
    <span className="mp-place">
      {w.locality === 'local' ? <HardDrive size={14} aria-label={tr('common.local')} /> : <Cloud size={14} aria-label={tr('common.hosted')} />}
      {w.endpoint} · {w.model}
    </span>
  )
}

export function ModelsSection() {
  const qc = useQueryClient()
  const admin = useMe().data?.user.role === 'admin'
  const q = useQuery<ModelsOut>({ queryKey: KEY, queryFn: () => api.get('/me/models') })
  const set = useMutation({
    mutationFn: ({ task, value }: { task: string; value: string }) => {
      const [endpoint, model] = value ? value.split('\u0000') : [null, null]
      return api.put<ModelsOut>(`/me/models/${task}`, { endpoint, model })
    },
    onSuccess: (data) => qc.setQueryData(KEY, data),
    onError: (e) => toastError(e, tr('settings.assistant.modelsSection.couldNotChangeTheModel')),
  })

  return (
    <Section
      title={tr('common.models')}
      desc={q.data?.locked
        ? <>{tr('settings.assistant.modelsSection.whichModelDoesEachKind')}</>
        : <>{tr('settings.assistant.modelsSection.whichModelDoesEachKind2', { value: q.data?.restricted ? tr('settings.assistant.modelsSection.theListShowsTheModels') : '' })} {admin ? <>{tx('settings.assistant.modelsSection.endpointsAndServerDefaultsAre', undefined, { link: (c) => <Link to="/settings/admin">{c}</Link> })}</> : null}</>}
    >
      {q.isLoading || !q.data ? <Skeleton width="60%" /> : (
        <div className="mp-list">
          {q.data.tasks.map((t) => {
            const locked = Boolean(t.locked ?? q.data.locked)
            const current = t.choice ? `${t.choice.endpoint}\u0000${t.choice.model}` : ''
            const fallback = t.effective_local_only
            const differs = fallback && (!t.effective || fallback.endpoint !== t.effective.endpoint)
            return (
              <div key={t.task} className="mp-row">
                <div className="mp-head">
                  <b>{t.label}</b>
                  <span className="muted">{t.desc}</span>
                </div>
                {locked ? (
                  <div className="mp-locked" data-testid={`model-locked-${t.task}`}>
                    <Lock size={14} aria-hidden /> <Place w={t.effective} /> <span className="muted">{tr('settings.assistant.modelsSection.setByYourAdministrator')}</span>
                  </div>
                ) : (
                <select className="input" aria-label={tr('settings.assistant.modelsSection.modelFor', { label: t.label })} value={current} disabled={set.isPending}
                  onChange={(e) => set.mutate({ task: t.task, value: e.target.value })}>
                  <option value="">{tr('settings.assistant.modelsSection.serverDefault', { value: t.choice ? '' : t.effective ? ` (${t.effective.endpoint} · ${t.effective.model})` : '' })}</option>
                  {t.options.flatMap((o) => (o.models.length ? o.models : [o.default_model ?? '']).map((m) => (
                    <option key={`${o.endpoint}/${m}`} value={`${o.endpoint}\u0000${m}`}>
                      {o.endpoint} · {m}{o.locality === 'local' ? tr('settings.assistant.modelsSection.localSuffix') : tr('settings.assistant.modelsSection.hostedSuffix')}{o.adapter === 'jev' ? tr('settings.assistant.modelsSection.decisionsOnly') : ''}
                    </option>
                  )))}
                </select>
                )}
                {locked && t.options.length > 0 && (
                  <div className="mp-now muted" data-testid={`model-allowed-${t.task}`}>
                    {tr('settings.assistant.modelsSection.allowed', { flatMap: t.options.flatMap((o) => (o.models.length ? o.models : [o.default_model ?? '']).map((m) => `${o.endpoint} · ${m}`)).join(', ') })}
                  </div>
                )}
                <div className="mp-now muted">
                  {!locked && <>{tr('settings.assistant.modelsSection.now')}{' '}<Place w={t.effective} /></>}
                  {fallback && !differs && <span data-testid={`model-local-ok-${t.task}`}>{locked ? tr('settings.assistant.modelsSection.localOnlyMail') : tr('settings.assistant.modelsSection.localOnlyMail2')} <ShieldCheck size={14} aria-hidden /> {tr('settings.assistant.modelsSection.theSameLocalModel')}</span>}
                  {differs && <>{locked ? tr('settings.assistant.modelsSection.localOnlyMail') : tr('settings.assistant.modelsSection.localOnlyMail2')} <ShieldCheck size={14} aria-hidden /> <Place w={fallback} /></>}
                  {!fallback && <>{locked ? tr('settings.assistant.modelsSection.localOnlyMail') : tr('settings.assistant.modelsSection.localOnlyMail2')} <span className="mp-warn">{tr('settings.assistant.modelsSection.noLocalModelThatMail')}</span>{' '}
                    {/* Who can fix it: an admin adds a local model; a member asks for one. */}
                    {admin ? <Link to="/settings/admin/models">{tr('settings.assistant.modelsSection.addALocalModel')}</Link> : <span>{tr('settings.assistant.modelsSection.askForALocalModel')}</span>}</>}
                </div>
              </div>
            )
          })}
          <div className="mp-row">
            <div className="mp-head">
              <b>{tr('settings.assistant.modelsSection.searchIndex')}</b>
              <span className="muted">{tr('settings.assistant.modelsSection.embeddingsForSearchAndS', { name: assistantNameNow() })}</span>
            </div>
            <div className="mp-now muted">{tr('settings.assistant.modelsSection.now')}{' '}<Place w={q.data.embed} /></div>
          </div>
        </div>
      )}
    </Section>
  )
}
