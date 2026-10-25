import { CheckCircle2, ShieldQuestion, XCircle } from 'lucide-react'
import { useState } from 'react'
import { savedToast } from './save'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { toastError } from '../../stores/ui'
import { t } from '../../i18n'

interface CheckResult {
  ran: boolean
  ok: boolean
  url: string
  via?: string
  reason: string
  checks: { id: string; label: string; ok: boolean; detail: string }[]
  commands: string[]
}

/** Settings → Admin → Calendars → Phones and desktop apps: the instance settings
 *  calendars.phone_access and calendars.phone_url (calendar/phones.py), and the check of the public
 *  address (calendar/phone_check.py).
 *  ACT settings.phone_access @web.settings */
export function PhoneAccessPanel({ enabled, on, url }: { enabled: boolean; on: boolean; url: string }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<string | null>(null)
  const [result, setResult] = useState<CheckResult | null>(null)
  const value = draft ?? url
  const save = useMutation({
    mutationFn: (body: { phone_access?: boolean; phone_url?: string }) => api.put('/admin/calendars', { builtin: true, ...body }),
    onSuccess: (_d, body) => {
      setDraft(null)
      void qc.invalidateQueries({ queryKey: ['admin', 'calendars'] })
      void qc.invalidateQueries({ queryKey: ['me', 'dav'] })
      const message =
        body.phone_access === true
          ? t('settings.phoneAccessAdmin.phoneAccessOnUsersCan')
          : body.phone_access === false
            ? t('settings.phoneAccessAdmin.phoneAccessOffEveryApp')
            : t('settings.phoneAccessAdmin.addressSaved')
      // Every change offers Undo, which puts the value before it back.
      const undo = body.phone_access !== undefined ? { phone_access: !body.phone_access } : { phone_url: url }
      savedToast(message, () => save.mutate(undo))
    },
    onError: (e) => toastError(e, t('common.notSaved')),
  })
  // ACT settings.phone_access_check @web.settings
  const check = useMutation({
    mutationFn: () => api.post<CheckResult>('/admin/calendars/phone-check', {}),
    onSuccess: setResult,
    onError: (e) => toastError(e, t('settings.phoneAccessAdmin.theCheckDidNotRun')),
  })
  if (!enabled) return null
  const dirty = draft !== null && draft.trim() !== url && !save.isPending
  return (
    <div className="gcal" data-testid="phone-access">
      <h3 className="gcal-head">{t('settings.phoneAccessAdmin.phonesAndDesktopApps')}</h3>
      <p className="muted gcal-desc">
        {t('settings.phoneAccessAdmin.usersCanConnectTheCalendar')}
      </p>
      <div className="field">
        <label htmlFor="phone-url">{t('settings.phoneAccessAdmin.publicAddressPhonesUse')}</label>
        <div className="row">
          <input
            id="phone-url"
            className="input"
            value={value}
            placeholder="https://dav.example.com"
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => dirty && save.mutate({ phone_url: value.trim() })}
            onKeyDown={(e) => { if (e.key === 'Enter' && dirty) save.mutate({ phone_url: value.trim() }) }}
            data-testid="phone-url"
          />
        </div>
        <span className="hint">{t('settings.phoneAccessAdmin.aHostNameOfIts')}</span>
      </div>
      <label className="choice">
        <input
          type="checkbox"
          className="checkbox"
          checked={on}
          disabled={save.isPending || (!on && !url)}
          onChange={(e) => save.mutate({ phone_access: e.target.checked })}
          data-testid="phone-access-switch"
        />
        <span>{t('settings.phoneAccessAdmin.letPhonesAndDesktopApps')}</span>
      </label>
      <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
        {on
          ? t('settings.phoneAccessAdmin.onUsersMakeAppPasswords')
          : url
            ? t('settings.phoneAccessAdmin.offAppPasswordsThatUsers')
            : t('settings.phoneAccessAdmin.offEnterThePublicAddress')}
      </div>
      {url && (
        <div style={{ marginTop: 10 }}>
          <button type="button" className="btn tonal" disabled={check.isPending} onClick={() => check.mutate()} data-testid="phone-check">
            <ShieldQuestion /> {check.isPending ? t('common.checking') : t('settings.phoneAccessAdmin.checkThePublicAddress')}
          </button>
          {result && <CheckView r={result} />}
        </div>
      )}
    </div>
  )
}

function CheckView({ r }: { r: CheckResult }) {
  return (
    <div className="stack" style={{ marginTop: 10 }} data-testid="phone-check-result">
      {r.ran ? (
        <>
          <div className={`banner ${r.ok ? '' : 'error'}`}>
            {r.ok ? <CheckCircle2 /> : <XCircle />}
            <span>
              {t('settings.phoneAccessAdmin.checked', { value: r.ok ? t('settings.phoneAccessAdmin.isReachableAndRestrictedAs', { url: r.url }) : t('settings.phoneAccessAdmin.needsAttention', { url: r.url }), via: r.via })}
            </span>
          </div>
          <ul className="phone-check">
            {r.checks.map((c) => (
              <li key={c.id} className={c.ok ? 'ok' : 'bad'}>
                {c.ok ? <CheckCircle2 size={16} aria-label={t('settings.phoneAccessAdmin.passed')} /> : <XCircle size={16} aria-label={t('common.failed')} />}
                <span>
                  <b>{c.label}</b>: {c.detail}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <>
          <div className="banner">
            <ShieldQuestion />
            <span>{r.reason}</span>
          </div>
          <pre className="phone-cmds">{r.commands.join('\n')}</pre>
        </>
      )}
    </div>
  )
}
