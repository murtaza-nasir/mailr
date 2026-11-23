// Admin → Integrations → Speakr for everyone (integrations/speakr/auto.py): the Speakr address
// for this server, filled in when people add Speakr and, with single sign-on, connected at each
// sign-in. Check asks that Speakr what it supports, without a token.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useApi } from '../../api/hooks'
import { Spinner } from '../../components/bits'
import { toast, toastError } from '../../stores/ui'
import { Check, Section } from './common'
import { t } from '../../i18n'

interface SpeakrInstance {
  url: string
  auto_connect: boolean
  data_class: 'local_only' | 'normal'
  sso: boolean
}

interface CheckOut {
  reachable: boolean
  supported: boolean
  sso_token_exchange: boolean
  sso_exchange_provisioning: boolean
  exchange_client_allowed: boolean | null
  detail?: string
}

export function checkText(r: CheckOut): { ok: boolean; text: string } {
  if (!r.reachable) return { ok: false, text: t('settings.adminSpeakr.checkUnreachable', { detail: r.detail ?? '' }) }
  if (!r.supported) return { ok: false, text: t('settings.adminSpeakr.checkOld') }
  if (!r.sso_token_exchange) return { ok: false, text: t('settings.adminSpeakr.checkExchangeOff') }
  if (r.exchange_client_allowed === false) return { ok: false, text: t('settings.adminSpeakr.checkClientNotAllowed') }
  return { ok: true, text: t('settings.adminSpeakr.checkReady') }
}

// ACT speakr.connect @web.settings (Speakr for everyone)
export function AdminSpeakrSection() {
  const qc = useQueryClient()
  const q = useApi<SpeakrInstance>(['admin', 'speakr'], '/admin/speakr')
  const [url, setUrl] = useState('')
  const [check, setCheck] = useState<CheckOut | null>(null)
  const [checking, setChecking] = useState(false)
  useEffect(() => { if (q.data) setUrl(q.data.url) }, [q.data])
  const save = useMutation({
    mutationFn: (body: Omit<SpeakrInstance, 'sso'>) => api.put<SpeakrInstance>('/admin/speakr', body),
    onSuccess: (out) => {
      qc.setQueryData(['admin', 'speakr'], out)
      void qc.invalidateQueries({ queryKey: ['speakr', 'auto'] })
      toast({ message: out.url ? t('settings.adminSpeakr.saved') : t('settings.adminSpeakr.removed') })
    },
    onError: (e) => toastError(e, t('common.notSaved')),
  })
  const d = q.data
  const put = (patch: Partial<Omit<SpeakrInstance, 'sso'>>) =>
    d && save.mutate({ url: d.url, auto_connect: d.auto_connect, data_class: d.data_class, ...patch })
  const runCheck = async () => {
    setChecking(true)
    try {
      setCheck(await api.post<CheckOut>('/admin/speakr/check', { url: url.trim() }))
    } catch (e) {
      toastError(e)
    } finally {
      setChecking(false)
    }
  }
  const result = check && checkText(check)
  return (
    <Section title={t('settings.adminSpeakr.title')} desc={t('settings.adminSpeakr.desc')}>
      <div className="field" style={{ maxWidth: 480 }}>
        <label htmlFor="admin-speakr-url">{t('settings.adminSpeakr.address')}</label>
        <div className="row" style={{ gap: 8 }}>
          <input id="admin-speakr-url" className="input" style={{ flex: 1 }} placeholder="https://speakr.example.org"
            value={url} disabled={!d} autoComplete="url"
            onChange={(e) => { setUrl(e.target.value); setCheck(null) }}
            onBlur={() => { if (d && url.trim() !== d.url) put({ url: url.trim(), auto_connect: url.trim() ? d.auto_connect : false }) }} />
          <button type="button" className="btn outlined" data-testid="admin-speakr-check"
            disabled={url.trim().length < 9 || checking} onClick={() => void runCheck()}>
            {checking ? <Spinner small /> : null}{' '}{t('settings.adminSpeakr.check')}
          </button>
        </div>
      </div>
      {result && (
        <div className={`banner ${result.ok ? '' : 'error'}`} role="status" data-testid="admin-speakr-check-result" style={{ marginTop: 8 }}>
          {result.ok ? <CheckCircle2 /> : <AlertTriangle />}
          <span>{result.text}</span>
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <Check checked={!!d?.auto_connect} disabled={!d?.url || !d.sso || save.isPending}
          onChange={(v) => put({ auto_connect: v })}
          label={t('settings.adminSpeakr.autoConnect')}
          desc={d && !d.sso ? t('settings.adminSpeakr.noSso') : t('settings.adminSpeakr.autoConnectDesc')} />
        <Check checked={d?.data_class !== 'normal'} disabled={!d?.url || save.isPending}
          onChange={(v) => put({ data_class: v ? 'local_only' : 'normal' })}
          label={t('settings.speakrSection.keepRecordingsAwayFromHosted')}
          desc={t('settings.adminSpeakr.localDesc')} />
      </div>
    </Section>
  )
}
