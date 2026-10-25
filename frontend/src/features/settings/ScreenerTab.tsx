// Settings → Mail → Spam & Screener (web.settings; backend core/screener.py, PUT /screener/settings): turn the
// sender screener on or off, choose the accounts it covers, and change or undo past decisions.

import { Link } from 'react-router'
import { LoadFailed, Spinner, Switch, failedEmpty } from '../../components/bits'
import { decisionText, useForget, useSaveScreenerSettings, useScreenerSettings } from '../mail/screener'
import { Check, Section, SetRow } from './common'
import { savedToast } from './save'
import '../mail/screener.css'
import { t, tx } from '../../i18n'

export function ScreenerTab() {
  const q = useScreenerSettings()
  const save = useSaveScreenerSettings()
  const forget = useForget()
  if (q.isLoading) return <Spinner />
  if (failedEmpty(q) || !q.data) return <LoadFailed what={t('settings.screenerTab.theScreenerSettings')} q={q} />
  const s = q.data
  // Every change saves at once with Undo. Undo of "off" turns the Screener on again; mail it
  // already released stays in the Inbox.
  const change = (body: { enabled?: boolean; accounts?: Record<string, boolean> }, undo: typeof body, message: string) =>
    save.mutate(body, { onSuccess: () => savedToast(message, () => save.mutate(undo, { onSuccess: () => savedToast(t('common.changeUndone')) })) })
  return (
    <Section title={t('common.screener')}>
      <p className="settings-intro">
        {t('settings.screenerTab.theScreenerKeepsMailFrom')}
      </p>

      <SetRow k={t('settings.screenerTab.screener')} sub={t('settings.screenerTab.offByDefault')}>
        {/* ACT screener.configure @web.settings */}
        <label className="choice">
          <Switch checked={s.enabled} disabled={save.isPending} onChange={(on) => change({ enabled: on }, { enabled: !on }, on ? t('settings.screenerTab.screenerOn') : t('settings.screenerTab.screenerOff'))} label={t('settings.screenerTab.screenFirstTimeSenders')} />
          <span>
            {t('settings.screenerTab.screenFirstTimeSenders')}
            <div className="desc">
              {s.enabled
                ? <>{tx('settings.screenerTab.newSendersWaitInMail', undefined, { link: (c) => <Link to="/mail/screener">{c}</Link> })}</>
                : t('settings.screenerTab.mailFromNewSendersArrives')}
            </div>
          </span>
        </label>
      </SetRow>

      <SetRow k={t('settings.screenerTab.accounts')} sub={t('settings.screenerTab.personalAccountsByDefault')}>
        {s.accounts.length === 0 ? (
          <p className="hint">{t('settings.screenerTab.noMailAccountsYet')}</p>
        ) : (
          <div className="screener-accounts">
            {s.accounts.map((a) => (
              <Check
                key={a.id}
                checked={a.screened}
                disabled={save.isPending}
                onChange={(on) => change({ accounts: { [String(a.id)]: on } }, { accounts: { [String(a.id)]: a.screened } }, t('common.saved2'))}
                label={<>{a.name}{a.address && a.address !== a.name ? <span className="muted"> · {a.address}</span> : null}</>}
                desc={a.chosen ? t('settings.screenerTab.yourChoice') : t('settings.screenerTab.default', { value: a.default ? 'screened' : t('settings.screenerTab.notScreened'), reason: a.reason })}
              />
            ))}
          </div>
        )}
        <p className="hint">{t('settings.screenerTab.universityWorkAndLocalOnly')}</p>
      </SetRow>

      <SetRow k={t('settings.screenerTab.decisions')} sub={t('settings.screenerTab.allowedBlockedAndLabelledSenders')}>
        {s.decided.length === 0 ? (
          <p className="hint">{t('settings.screenerTab.noDecisionsYet')}</p>
        ) : (
          <ul className="screener-decided" aria-label={t('settings.screenerTab.screenerDecisions')}>
            {s.decided.map((d) => (
              <li key={d.id}>
                <span className="key">{d.kind === 'domain' ? t('settings.screenerTab.everyoneAt', { key: d.key }) : d.key}</span>
                <span className="muted">{decisionText(d)}</span>
                {/* ACT screener.forget @web.settings */}
                <button type="button" className="btn text sm" disabled={forget.isPending} onClick={() => forget.mutate(d)}
                  aria-label={t('settings.screenerTab.screenAgain', { key: d.key })}>
                  {t('settings.screenerTab.screenAgain2')}
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="hint">{t('settings.screenerTab.screenAgainUndoesADecision')}</p>
      </SetRow>
    </Section>
  )
}
