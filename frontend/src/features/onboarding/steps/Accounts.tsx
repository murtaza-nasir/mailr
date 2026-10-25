// Step 2: connect one or more mail accounts. The provider is found from the address (and the
// server's lookup); Gmail, iCloud, Yahoo and Fastmail get step-by-step app-password help. The
// history choice brings recent mail in first. The signature is set here too.
// Same API as the add-account wizard (Settings → Mail → Mail accounts): discover, then create (which tests).

import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, ChevronDown, ExternalLink, KeyRound, Lock, Mail, Plus, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../../api/client'
import { qk, useApiList, useMe } from '../../../api/hooks'
import type { Connector, DiscoverResult, ServerSettings } from '../../../api/types'
import { Spinner } from '../../../components/bits'
import { isValidEmail } from '../../../lib/format'
import { toast, toastError } from '../../../stores/ui'
import { accountDraft } from '../../settings/accountDraft'
import { ServerFields } from '../../settings/AddAccountWizard'
import { ChoiceCard, StepFrame } from '../guide'
import { useGuide } from '../guideContext'
import { importSummary } from '../importProgress'
import { chatApi } from '../api'
import { HISTORY_CHOICES, PASSWORD_GUIDES, historyConfig, providerFromPreset, type HistoryChoice, type Provider } from '../steps'
import { firstName } from './Welcome'
import { modeName } from '../../settings/syncModes'
import { t } from '../../../i18n'

const MAIL_KINDS = new Set(['imap', 'gmail', 'm365', 'file'])

function AccountRow({ c }: { c: Connector }) {
  const s = importSummary([c])
  const err = c.status === 'error'
  return (
    <li className={`ob-account${err ? ' error' : ''}`} data-testid="ob-account">
      <span className="ob-account-icon" aria-hidden>
        {err ? <AlertTriangle /> : <CheckCircle2 />}
      </span>
      <span className="ob-account-text">
        <b>{c.name}</b>
        <span className="ob-hint">
          {err
            ? t('onboarding.steps.accounts.thereIsAProblem', { statusDetail: c.status_detail })
            : s.phase === 'recent'
              ? t('onboarding.steps.accounts.connectedYourRecentMailIs')
              : s.phase === 'older'
                ? t('onboarding.steps.accounts.connectedYourRecentMailIs2')
                : t('onboarding.steps.accounts.connected')}
        </span>
      </span>
    </li>
  )
}

type Phase = 'address' | 'password'

type LinkChoice = 'mirror' | 'import'

/** What connecting does to the old account. Mirror writes back; import only copies. */
export const LINK_CHOICES: { id: LinkChoice; title: string; desc: string }[] = [
  {
    id: 'mirror',
    title: modeName('mirror'),
    desc: t('onboarding.steps.accounts.recommendedYourOldAccountKeeps'),
  },
  {
    id: 'import',
    title: modeName('import'),
    desc: t('onboarding.steps.accounts.yourMailIsCopiedHere'),
  },
]

function AddAccount({ onAdded, first }: { onAdded: () => void; first: boolean }) {
  const qc = useQueryClient()
  const me = useMe().data!
  const [phase, setPhase] = useState<Phase>('address')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [found, setFound] = useState<DiscoverResult | null>(null)
  const [imap, setImap] = useState<ServerSettings>({ host: '', port: 993, tls: 'ssl' })
  const [smtp, setSmtp] = useState<ServerSettings>({ host: '', port: 465, tls: 'ssl' })
  const [showServers, setShowServers] = useState(false)
  const [history, setHistory] = useState<HistoryChoice>('recent_then_all')
  const [link, setLink] = useState<LinkChoice>('mirror')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const provider: Provider = providerFromPreset(found?.preset, email)
  const guide = PASSWORD_GUIDES[provider]
  const unsupported = found?.supported === false || provider === 'outlook'

  const lookUp = async () => {
    setBusy(true)
    setError('')
    try {
      const r = await api.post<DiscoverResult>('/connectors/discover', { email: email.trim() })
      setFound(r)
      if (r.imap) setImap(r.imap)
      if (r.smtp) setSmtp(r.smtp)
      // No settings found: show the server fields at once.
      setShowServers(!r.imap?.host)
    } catch {
      setFound({ source: undefined })
      setShowServers(true)
    } finally {
      setBusy(false)
      setPhase('password')
    }
  }

  const connect = async () => {
    setBusy(true)
    setError('')
    const h = historyConfig(history)
    try {
      await api.post<Connector>(
        '/connectors',
        accountDraft({
          kind: found?.kind === 'gmail' || provider === 'gmail' ? 'gmail' : 'imap',
          email,
          displayName: me.user.name ?? '',
          username: email,
          // App passwords are often shown with spaces; they work without them.
          password: guide ? password.replace(/\s+/g, '') : password,
          preset: found?.preset ?? '',
          imap,
          smtp,
          // Mirror writes read state, archive, labels and deletes back to the provider.
          mode: link,
          localOnly: false,
          retention: 14,
          since: h.since ?? '',
          backfill: h.backfill,
        }) as Record<string, unknown>,
      )
      await qc.invalidateQueries({ queryKey: ['connectors'] })
      await qc.invalidateQueries({ queryKey: qk.me })
      toast({ message: t('onboarding.steps.accounts.isConnectedYourMailIs', { email: email.trim() }) })
      setPhase('address')
      setEmail('')
      setPassword('')
      setFound(null)
      onAdded()
    } catch (e) {
      setError(friendlyError((e as Error).message, provider))
    } finally {
      setBusy(false)
    }
  }

  if (phase === 'address') {
    return (
      <form
        className="ob-form"
        onSubmit={(e) => {
          e.preventDefault()
          if (isValidEmail(email)) void lookUp()
        }}
      >
        <div className="field ob-field">
          <label htmlFor="ob-email">{first ? t('onboarding.steps.accounts.yourEmailAddress') : t('onboarding.steps.accounts.anotherEmailAddress')}</label>
          <input id="ob-email" className="input ob-input" type="email" inputMode="email" autoComplete="email" placeholder={t('onboarding.steps.accounts.youGmailCom')} value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <button type="submit" className="btn filled ob-big" disabled={!isValidEmail(email) || busy}>
          {busy ? <Spinner small /> : <Mail />}{' '}{t('common.next')}
        </button>
      </form>
    )
  }

  return (
    <div className="ob-form" data-testid="ob-connect" data-provider={provider}>
      <div className="ob-found">
        <Mail />
        <span>
          <b>{email.trim()}</b>
          {guide ? <span className="ob-hint"> · {guide.name}</span> : null}
        </span>
        <button type="button" className="btn text sm" onClick={() => setPhase('address')}>
          {t('onboarding.steps.accounts.change')}
        </button>
      </div>

      {unsupported ? (
        <div className="banner warn" role="status">
          <AlertTriangle />
          <span>
            {t('onboarding.steps.accounts.askThePersonWhoRuns', { value: provider === 'outlook' || found?.preset === 'office365'
              ? t('onboarding.steps.accounts.outlookHotmailLiveAndMsn')
              : found?.notes || t('onboarding.steps.accounts.thisKindOfAccountCannot') })}
          </span>
        </div>
      ) : (
        <>
          {guide && (
            <div className="ob-guide" data-testid="password-guide">
              <p className="ob-why">
                <ShieldCheck /> {guide.why}
              </p>
              {guide.before && (
                <details className="ob-before">
                  <summary>
                    {guide.before.title} <ChevronDown aria-hidden />
                  </summary>
                  <ol>
                    {guide.before.steps.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ol>
                  <a className="btn outlined" href={guide.before.link} target="_blank" rel="noreferrer">
                    {guide.before.linkText} <ExternalLink />
                  </a>
                </details>
              )}
              <ol className="ob-guide-steps">
                {guide.steps.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ol>
              <a className="btn tonal ob-big" href={guide.link} target="_blank" rel="noreferrer">
                <KeyRound /> {guide.linkText} <ExternalLink />
              </a>
            </div>
          )}
          <div className="field ob-field">
            <label htmlFor="ob-pass">{guide ? t('onboarding.steps.accounts.pasteTheAppPasswordHere') : t('onboarding.steps.accounts.yourEmailPassword')}</label>
            <input
              id="ob-pass"
              className="input ob-input"
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <span className="ob-hint">
              <Lock size={12} /> {t('onboarding.steps.accounts.itIsStoredEncryptedAnd', { value: guide ? guide.looks + ' ' : '' })}
            </span>
          </div>

          <fieldset className="ob-fieldset">
            <legend>{t('onboarding.steps.accounts.howMuchMailToBring')}</legend>
            <div className="ob-choices" role="radiogroup" aria-label={t('onboarding.steps.accounts.howMuchMailToBring')}>
              {HISTORY_CHOICES.map((c) => (
                <ChoiceCard key={c.id} on={history === c.id} onSelect={() => setHistory(c.id)} title={c.title} desc={c.desc} name={c.title} />
              ))}
            </div>
          </fieldset>

          <fieldset className="ob-fieldset">
            <legend>{t('onboarding.steps.accounts.yourOldAccount')}</legend>
            <div className="ob-choices" role="radiogroup" aria-label={t('onboarding.steps.accounts.yourOldAccount')} data-testid="ob-link">
              {LINK_CHOICES.map((c) => (
                <ChoiceCard key={c.id} on={link === c.id} onSelect={() => setLink(c.id)} title={c.title} desc={c.desc} name={c.title} />
              ))}
            </div>
          </fieldset>

          {!guide && (
            <details className="ob-servers" open={showServers} onToggle={(e) => setShowServers((e.target as HTMLDetailsElement).open)}>
              <summary>{t('onboarding.steps.accounts.serverSettings', { value: found?.imap?.host ? t('onboarding.steps.accounts.foundAutomatically') : '' })}</summary>
              <ServerFields label={t('common.incomingImap')} value={imap} onChange={setImap} placeholder="imap.example.com" />
              <ServerFields label={t('common.outgoingSmtp')} value={smtp} onChange={setSmtp} placeholder="smtp.example.com" />
            </details>
          )}

          {error && (
            <div className="banner error" role="alert">
              <AlertTriangle />
              <span>{error}</span>
            </div>
          )}
          <button type="button" className="btn filled ob-big" disabled={!password || !imap.host || busy} onClick={() => void connect()}>
            {busy ? <Spinner small /> : <CheckCircle2 />} {busy ? t('onboarding.steps.accounts.connecting') : t('common.connect')}
          </button>
        </>
      )}
    </div>
  )
}

/** A server's refusal in plain words. */
export function friendlyError(detail: string, provider: Provider): string {
  const d = detail.toLowerCase()
  if (/auth|login|password|credential|invalid/.test(d)) {
    return provider === 'gmail'
      ? t('onboarding.steps.accounts.googleDidNotAcceptThat')
      : t('onboarding.steps.accounts.thePasswordWasNotAccepted')
  }
  if (/did not answer|timed out|timeout|could not connect|resolve|unreachable/.test(d)) return t('onboarding.steps.accounts.mailrCouldNotReachThe')
  return t('onboarding.steps.accounts.thatDidNotWork', { detail })
}

function Signature() {
  const me = useMe().data!
  const qc = useQueryClient()
  const has = me.identities.find((i) => i.signature_html)
  const [text, setText] = useState(() => t('onboarding.steps.accounts.bestWishes', { firstName: firstName(me.user.name) || me.user.name || '' }))
  const [saved, setSaved] = useState(!!has)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await chatApi.save({ profile: {}, memories: [], signature: text, source: 'fields' })
      await qc.invalidateQueries({ queryKey: qk.me })
      setSaved(true)
      toast({ message: t('onboarding.steps.accounts.signatureSaved') })
    } catch (e) {
      toastError(e, t('onboarding.steps.accounts.theSignatureWasNotSaved'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="ob-signature">
      <label htmlFor="ob-sig" className="ob-label">
        {t('onboarding.steps.accounts.yourSignature')}
      </label>
      <span className="ob-hint">{t('onboarding.steps.accounts.addedUnderEveryEmailYou')}</span>
      <textarea id="ob-sig" className="textarea ob-input" rows={3} value={text} onChange={(e) => { setText(e.target.value); setSaved(false) }} />
      <button type="button" className="btn tonal" disabled={busy || saved || !text.trim()} onClick={() => void save()}>
        {busy ? <Spinner small /> : saved ? <CheckCircle2 /> : null} {saved ? t('common.saved') : t('onboarding.steps.accounts.saveSignature')}
      </button>
    </div>
  )
}

export function AccountsStep() {
  const g = useGuide()
  const me = useMe().data!
  const conns = useApiList<Connector>(['connectors'], '/connectors', undefined, { refetchInterval: 10_000 })
  const mail = (conns.data ?? []).filter((c) => MAIL_KINDS.has(c.kind))
  const [adding, setAdding] = useState(false)
  const showForm = mail.length === 0 || adding
  return (
    <StepFrame
      icon={<Mail />}
      title={t('onboarding.steps.accounts.connectYourEmail')}
      lead={t('onboarding.steps.accounts.youSeeTheMailFrom')}
      skip={mail.length === 0}
      // Before an account is connected, the form's own Next is the one way on (besides Skip).
      primary={mail.length ? { label: t('common.continue') } : null}
    >
      {mail.length > 0 && (
        <ul className="ob-accounts" aria-label={t('onboarding.steps.accounts.connectedAccounts')}>
          {mail.map((c) => (
            <AccountRow key={c.id} c={c} />
          ))}
        </ul>
      )}
      {showForm ? (
        <AddAccount first={mail.length === 0} onAdded={() => setAdding(false)} />
      ) : (
        <button type="button" className="btn outlined ob-big" onClick={() => setAdding(true)}>
          <Plus />{' '}{t('onboarding.steps.accounts.addAnotherAccount')}
        </button>
      )}
      {me.identities.length > 0 && <Signature />}
      {g.state.has_accounts === false && mail.length === 0 && <p className="ob-hint ob-center">{t('onboarding.steps.accounts.noAccountYetYouCan')}</p>}
    </StepFrame>
  )
}
