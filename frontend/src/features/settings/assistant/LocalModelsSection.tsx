// Settings → Ambrose → Models: "Local models only for me" and Model use
// (api/model_prefs.py, intel/locality.py, intel/usage.py). The switch can only make things stricter;
// "Local models only for everyone" (Admin → Models) locks it on, and the lock says why. Nothing locks it off.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, Lock } from 'lucide-react'
import { api } from '../../../api/client'
import { Skeleton, Switch } from '../../../components/bits'
import { toastError } from '../../../stores/ui'
import { Section } from '../common'
import { t, appLocale } from '../../../i18n'
import { useAssistantName } from '../../rail/assistant/useAssistantName'

export interface LocalModelsState {
  household: 'per_user' | 'local_all'
  mine: boolean
  effective: boolean
  locked: boolean
  reason: string | null
  /** The kinds of work with no local model for this person: they stop while the rule holds. */
  no_local: { task: string; label: string }[]
}

export interface ModelUseRow {
  feature: string
  label: string
  endpoint: string
  model: string
  local: boolean
  calls: number
  last: string
}

export const LOCAL_KEY = ['me', 'local-models'] as const
const USE_KEY = ['me', 'model-use'] as const

/** ACT settings.local_models_me @web.settings */
export function LocalModelsSection() {
  const assistantName = useAssistantName()
  const qc = useQueryClient()
  const q = useQuery<LocalModelsState>({ queryKey: LOCAL_KEY, queryFn: () => api.get('/me/local-models') })
  const set = useMutation({
    mutationFn: (on: boolean) => api.put<LocalModelsState>('/me/local-models', { on }),
    onSuccess: (data) => {
      qc.setQueryData(LOCAL_KEY, data)
      void qc.invalidateQueries({ queryKey: ['me', 'models'] })
    },
    onError: (e) => toastError(e, t('common.notChanged')),
  })
  const s = q.data
  return (
    <Section
      title={t('common.localModelsOnly')}
      desc={<>{t('settings.assistant.localModelsSection.withThisOnAndEvery', { name: assistantName })}</>}
    >
      {!s ? <Skeleton height={44} /> : (
        <div className="lm-switch" data-testid="local-models">
          <label className="choice">
            <Switch checked={s.effective} disabled={s.locked || set.isPending} onChange={(on) => set.mutate(on)} label={t('settings.assistant.localModelsSection.localModelsOnlyForMe')} />
            <span>
              {t('settings.assistant.localModelsSection.localModelsOnlyForMe')}
              <div className="desc">
                {s.locked ? (
                  <span data-testid="local-models-reason"><Lock size={12} aria-hidden /> {s.reason}</span>
                ) : s.effective ? t('settings.assistant.localModelsSection.onYourWorkStaysOn') : t('settings.assistant.localModelsSection.offHostedModelsMayBe')}
              </div>
            </span>
          </label>
          {s.effective && s.no_local.length > 0 && (
            <p className="muted lm-note" data-testid="local-models-missing">
              {t('settings.assistant.localModelsSection.notAvailableWithLocalModels', { count: s.no_local.length, map: s.no_local.map((x) => x.label.toLowerCase()).join(', ') })}
            </p>
          )}
        </div>
      )}
    </Section>
  )
}

/** ACT settings.model_use @web.settings */
export function ModelUseSection() {
  const q = useQuery<{ days: number; rows: ModelUseRow[] }>({ queryKey: USE_KEY, queryFn: () => api.get('/me/model-use') })
  const rows = q.data?.rows ?? []
  return (
    <Section
      title={t('settings.assistant.localModelsSection.modelUse')}
      desc={<>{t('settings.assistant.localModelsSection.whichModelDidEachKind', { count: q.data?.days ?? 30 })}</>}
    >
      {q.isLoading ? <Skeleton height={80} /> : rows.length === 0 ? (
        <p className="muted" data-testid="model-use-empty">{t('settings.assistant.localModelsSection.noModelHasWorkedOn')}</p>
      ) : (
        <div className="table-wrap">
          <table className="table lm-use" data-testid="model-use">
            <thead>
              <tr><th>{t('common.work')}</th><th>{t('settings.assistant.localModelsSection.model')}</th><th>{t('settings.assistant.localModelsSection.where')}</th><th className="num">{t('settings.assistant.localModelsSection.uses')}</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.feature}/${r.endpoint}/${r.model}`}>
                  <td>{r.label}</td>
                  <td><span className="lm-model">{r.model || r.endpoint}</span> <span className="muted">· {r.endpoint}</span></td>
                  <td>
                    <span className={`chip-where ${r.local ? 'local' : 'hosted'}`}>
                      {r.local ? <Lock size={12} aria-hidden /> : <Cloud size={12} aria-hidden />} {r.local ? t('common.local') : t('common.hosted')}
                    </span>
                  </td>
                  <td className="num">{r.calls.toLocaleString(appLocale())}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}
