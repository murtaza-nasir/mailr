import { AlertTriangle, CheckCircle2, Copy, Database, DownloadCloud, Lock, RefreshCw } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { qk, useApiList, useMe } from '../../api/hooks'
import type { Connector, ConnectorMode, DiscoverResult, Preset, ServerSettings, TestResult } from '../../api/types'
import { Spinner } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { isValidEmail } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { Check } from './common'
import { accountDraft } from './accountDraft'
import { storeModeUnavailable } from './storeMode'
import { modeLine, modeName } from './syncModes'
import { t, tx, appLocale } from '../../i18n'

type Step = 0 | 1 | 2 | 3

const STEP_NAMES = [t('common.address'), t('common.signIn'), t('settings.addAccountWizard.mode'), t('settings.addAccountWizard.identities')]

const ROLE_NAMES: Record<string, string> = { inbox: t('common.inbox'), sent: t('common.sent'), drafts: t('common.drafts'), junk: t('common.spam'), trash: t('common.trash'), archive: t('common.archive') }

const MODES: { id: ConnectorMode; icon: ReactNode; title: string; desc: string }[] = [
  { id: 'mirror', icon: <RefreshCw size={18} />, title: modeName('mirror'), desc: modeLine('mirror') },
  { id: 'store', icon: <Database size={18} />, title: modeName('store'), desc: modeLine('store') },
  { id: 'import', icon: <DownloadCloud size={18} />, title: modeName('import'), desc: modeLine('import') },
]

export function ServerFields({ label, value, onChange, placeholder }: { label: string; value: ServerSettings; onChange: (v: ServerSettings) => void; placeholder: string }) {
  return (
    <div className="grid-3">
      <div className="field">
        <label>{t('settings.addAccountWizard.server', { label })}</label>
        <input className="input" value={value.host} onChange={(e) => onChange({ ...value, host: e.target.value.trim() })} placeholder={placeholder} aria-label={t('settings.addAccountWizard.server', { label })} />
      </div>
      <div className="field">
        <label>{t('settings.addAccountWizard.port')}</label>
        <input className="input" inputMode="numeric" value={value.port || ''} onChange={(e) => onChange({ ...value, port: Number(e.target.value.replace(/\D/g, '')) })} />
      </div>
      <div className="field">
        <label>{t('settings.addAccountWizard.security')}</label>
        <select className="select" value={value.tls ?? 'ssl'} onChange={(e) => onChange({ ...value, tls: e.target.value as ServerSettings['tls'] })}>
          <option value="ssl">{t('settings.addAccountWizard.sslTls')}</option>
          <option value="starttls">{t('settings.addAccountWizard.starttls')}</option>
          <option value="none">{t('common.none')}</option>
        </select>
      </div>
    </div>
  )
}

export function AddAccountWizard({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const me = useMe().data
  const presets = useApiList<Preset>(['connectors', 'presets'], '/connectors/presets', undefined, { enabled: open })
  const [step, setStep] = useState<Step>(0)
  const [email, setEmail] = useState('')
  const [name, setName] = useState(me?.user.name ?? '')
  const [busy, setBusy] = useState(false)
  const [kind, setKind] = useState('imap')
  const [preset, setPreset] = useState<string>('')
  const [imap, setImap] = useState<ServerSettings>({ host: '', port: 993, tls: 'ssl' })
  const [smtp, setSmtp] = useState<ServerSettings>({ host: '', port: 465, tls: 'ssl' })
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [source, setSource] = useState('')
  const [test, setTest] = useState<TestResult | null>(null)
  const [mode, setMode] = useState<ConnectorMode>('mirror')
  const [retention, setRetention] = useState(14)
  const [localOnly, setLocalOnly] = useState(false)
  const [since, setSince] = useState('')
  const [folders, setFolders] = useState<Set<string>>(new Set())
  const [gmailSpam, setGmailSpam] = useState(true)
  const [gmailTrash, setGmailTrash] = useState(false)
  const [gmailSkip, setGmailSkip] = useState<Set<string>>(new Set())
  const [idents, setIdents] = useState<Set<string>>(new Set())
  const presetInfo = (presets.data ?? []).find((p) => p.id === preset)
  const storeOff = storeModeUnavailable(me?.features)

  const reset = () => {
    setStep(0)
    setTest(null)
    setPassword('')
    setBusy(false)
  }
  const close = () => {
    reset()
    onClose()
  }

  const discover = async () => {
    setBusy(true)
    try {
      const r = await api.post<DiscoverResult>('/connectors/discover', { email: email.trim() })
      if (r.preset) setPreset(r.preset)
      if (r.kind) setKind(r.kind)
      if (r.imap) setImap(r.imap)
      if (r.smtp) setSmtp(r.smtp)
      setSource(r.source ?? '')
      setUsername(email.trim())
      setStep(1)
    } catch (e) {
      // Discovery is best-effort: continue to manual settings.
      setSource('')
      setUsername(email.trim())
      setStep(1)
      toastError(e, t('settings.addAccountWizard.automaticSetupFailedEnterThe'))
    } finally {
      setBusy(false)
    }
  }

  const draft = () =>
    accountDraft({
      kind, email, displayName: name, username, password, preset, imap, smtp, mode, localOnly, retention, since,
      gmail: { spam: gmailSpam, trash: gmailTrash, skip: gmailSkip }, test, folders, identities: idents,
    })

  const runTest = async () => {
    setBusy(true)
    setTest(null)
    try {
      const r = await api.post<TestResult>('/connectors/test', draft())
      setTest(r)
      if (r.ok) {
        const ids = new Set<string>([email.trim().toLowerCase(), ...r.identities.map((x) => x.toLowerCase())])
        setIdents(ids)
        // The server proposes which folders to sync (Trash and Drafts stay off by default).
        setFolders(new Set(r.folders.filter((f) => f.sync ?? !(f.role === 'junk' || f.role === 'trash')).map((f) => f.name)))
      }
    } catch (e) {
      setTest({ ok: false, detail: (e as Error).message, folders: [], identities: [], extensions: [] })
    } finally {
      setBusy(false)
    }
  }

  const create = async () => {
    setBusy(true)
    try {
      // The server creates the chosen send-as identities (named from display_name) and
      // links existing ones to this account.
      await api.post<Connector>('/connectors', draft())
      await qc.invalidateQueries({ queryKey: ['connectors'] })
      await qc.invalidateQueries({ queryKey: qk.me })
      toast({ message: t('settings.addAccountWizard.addedSyncingHasStarted', { email }) })
      close()
    } catch (e) {
      toastError(e, t('settings.addAccountWizard.couldNotAddAccount'))
    } finally {
      setBusy(false)
    }
  }

  const canNext0 = isValidEmail(email)
  const canTest = imap.host && imap.port && username && password
  const footer = (
    <>
      {step > 0 && (
        <button type="button" className="btn text" onClick={() => setStep((step - 1) as Step)} disabled={busy}>
          {t('common.back')}
        </button>
      )}
      <span className="spacer" />
      <button type="button" className="btn text" onClick={close}>
        {t('common.cancel')}
      </button>
      {step === 0 && (
        <button type="button" className="btn filled" disabled={!canNext0 || busy} onClick={() => void discover()}>
          {busy ? <Spinner small /> : null}{' '}{t('common.next')}
        </button>
      )}
      {step === 1 && (
        <>
          <button type="button" className="btn outlined" disabled={!canTest || busy} onClick={() => void runTest()}>
            {busy ? <Spinner small /> : null}{' '}{t('settings.addAccountWizard.testConnection')}
          </button>
          <button type="button" className="btn filled" disabled={!test?.ok || busy} onClick={() => setStep(2)}>
            {t('common.next')}
          </button>
        </>
      )}
      {step === 2 && (
        <button type="button" className="btn filled" onClick={() => setStep(3)}>
          {t('common.next')}
        </button>
      )}
      {step === 3 && (
        <button type="button" className="btn filled" disabled={busy} onClick={() => void create()}>
          {busy ? <Spinner small /> : null}{' '}{t('settings.addAccountWizard.addAccount')}
        </button>
      )}
    </>
  )

  return (
    <Dialog open={open} onClose={close} title={t('settings.addAccountWizard.addAMailAccount')} wide modal footer={footer}>
      <div className="wizard-steps" aria-label={t('settings.addAccountWizard.progress')}>
        {STEP_NAMES.map((n, i) => (
          <span key={n} className={`wizard-step${i === step ? ' on' : ''}${i < step ? ' done' : ''}`}>
            <span className="num">{i < step ? '✓' : i + 1}</span>
            {n}
          </span>
        ))}
      </div>

      {step === 0 && (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault()
            if (canNext0) void discover()
          }}
        >
          <p className="muted" style={{ margin: 0 }}>
            {t('settings.addAccountWizard.enterTheAddressOfThe')}
          </p>
          <div className="field">
            <label htmlFor="acct-email">{t('settings.addAccountWizard.emailAddress')}</label>
            <input id="acct-email" className="input" type="email" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} placeholder={t('settings.addAccountWizard.youExampleCom')} />
          </div>
          <div className="field">
            <label htmlFor="acct-name">{t('settings.addAccountWizard.yourNameForOutgoingMail')}</label>
            <input id="acct-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
        </form>
      )}

      {step === 1 && (
        <div className="stack">
          {source && (
            <div className="banner">
              <CheckCircle2 />
              <span>
                {t('settings.addAccountWizard.settingsFoundCheckThemAnd', { value: presetInfo ? t('settings.addAccountWizard.for', { presetInfoName: presetInfo.name }) : '', source })}
              </span>
            </div>
          )}
          {presetInfo?.notes && (
            <div className="banner warn">
              <AlertTriangle />
              <span>{presetInfo.notes}</span>
            </div>
          )}
          <div className="field">
            <label>{t('settings.addAccountWizard.provider')}</label>
            <select className="select" value={preset} onChange={(e) => {
              const p = (presets.data ?? []).find((x) => x.id === e.target.value)
              setPreset(e.target.value)
              if (p?.imap) setImap(p.imap)
              if (p?.smtp) setSmtp(p.smtp)
              if (p?.kind) setKind(p.kind)
            }}>
              <option value="">{t('settings.addAccountWizard.otherManualSettings')}</option>
              {(presets.data ?? []).filter((p) => p.id !== 'generic').map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <ServerFields label={t('common.incomingImap')} value={imap} onChange={setImap} placeholder="imap.example.com" />
          <ServerFields label={t('common.outgoingSmtp')} value={smtp} onChange={setSmtp} placeholder="smtp.example.com" />
          <div className="grid-2">
            <div className="field">
              <label htmlFor="acct-user">{t('common.username')}</label>
              <input id="acct-user" className="input" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="acct-pass">{t('settings.addAccountWizard.passwordOrAppPassword')}</label>
              <input id="acct-pass" className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              <span className="hint">
                <Lock size={11} />{' '}{t('settings.addAccountWizard.storedEncryptedUseAnApp')}
              </span>
            </div>
          </div>
          {test && (
            <div className={`banner ${test.ok ? '' : 'error'}`} role="status">
              {test.ok ? <CheckCircle2 /> : <AlertTriangle />}
              <span>
                {test.ok ? t('settings.addAccountWizard.connectedFoldersFound', { count: test.folders.length }) : t('settings.addAccountWizard.connectionFailed', { detail: test.detail })}
                {test.ok && test.detail ? ` ${test.detail}` : ''}
              </span>
            </div>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="stack">
          <div className="mode-cards" role="radiogroup" aria-label={t('settings.addAccountWizard.accountMode')}>
            {MODES.map((m) => {
              // Store mode while the server's mail export is off: shown, with the reason, but not chosen.
              const off = m.id === 'store' ? storeOff : null
              return (
                <button key={m.id} type="button" role="radio" aria-checked={mode === m.id} aria-pressed={mode === m.id} className="mode-card" disabled={!!off} onClick={() => setMode(m.id)} data-testid={`mode-${m.id}`}>
                  <span className="t">
                    {m.icon}
                    {m.title}
                  </span>
                  <span className="d">{off ?? m.desc}</span>
                </button>
              )
            })}
          </div>
          {mode === 'store' && (
            <div className="banner warn">
              <AlertTriangle />
              <span>
                {tx('settings.addAccountWizard.upstreamDeletionStaysOffUntil', undefined, { b: (c) => <b>{c}</b> })}
              </span>
            </div>
          )}
          {mode === 'store' && (
            <div className="field" style={{ maxWidth: 280 }}>
              <label htmlFor="acct-ret">{t('settings.addAccountWizard.retentionAtTheProviderDays')}</label>
              <input id="acct-ret" className="input" inputMode="numeric" value={retention} onChange={(e) => setRetention(Math.max(1, Number(e.target.value.replace(/\D/g, '')) || 1))} />
            </div>
          )}
          <div className="field" style={{ maxWidth: 280 }}>
            <label htmlFor="acct-since">{t('settings.addAccountWizard.importHistorySinceOptional')}</label>
            <input id="acct-since" className="input" type="date" value={since} onChange={(e) => setSince(e.target.value)} />
            <span className="hint">{t('settings.addAccountWizard.leaveEmptyToImportEverything')}</span>
          </div>
          {kind === 'gmail' && test?.ok ? (
            <GmailOptions
              folders={test.folders}
              spam={gmailSpam}
              trash={gmailTrash}
              skip={gmailSkip}
              onSpam={setGmailSpam}
              onTrash={setGmailTrash}
              onSkip={setGmailSkip}
            />
          ) : (
            test &&
            test.folders.length > 0 && (
              <div className="field">
                <span className="field-label">{t('settings.addAccountWizard.foldersToImport')}</span>
                <div className="scope-grid">
                  {test.folders.map((f) => (
                    <Check
                      key={f.name}
                      checked={folders.has(f.name)}
                      onChange={(v) => {
                        const n = new Set(folders)
                        if (v) n.add(f.name)
                        else n.delete(f.name)
                        setFolders(n)
                      }}
                      label={`${f.name}${folderNote(f.target)}${f.count !== undefined ? ` (${f.count})` : ''}`}
                    />
                  ))}
                </div>
              </div>
            )
          )}
          <div className="card">
            <Check
              checked={localOnly}
              onChange={setLocalOnly}
              label={
                <b>
                  <Lock size={14} style={{ verticalAlign: -2 }} />{' '}{t('common.localModelsOnly')}
                </b>
              }
              desc={t('settings.addAccountWizard.mailFromThisAccountIs')}
            />
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="stack">
          <p className="muted" style={{ margin: 0 }}>
            {t('settings.addAccountWizard.chooseTheAddressesYouSend')}
          </p>
          {[...new Set([email.trim().toLowerCase(), ...(test?.identities ?? []).map((x) => x.toLowerCase())])].map((addr) => (
            <Check
              key={addr}
              checked={idents.has(addr)}
              onChange={(v) => {
                const n = new Set(idents)
                if (v) n.add(addr)
                else n.delete(addr)
                setIdents(n)
              }}
              label={addr}
            />
          ))}
          <div className="card" style={{ fontSize: 13 }}>
            <div className="row">
              <Copy size={14} /> <b>{t('common.summary')}</b>
            </div>
            <ul style={{ margin: '8px 0 0', paddingInlineStart: 20, color: 'var(--text-2)' }}>
              <li>
                {t('settings.addAccountWizard.via', { email, host: imap.host, port: imap.port, host2: smtp.host, port2: smtp.port })}
              </li>
              <li>{t('settings.addAccountWizard.mode2', { title: MODES.find((m) => m.id === mode)?.title })}</li>
              <li>{localOnly ? t('common.localModelsOnly') : t('settings.addAccountWizard.normalDataClass')}</li>
              <li>{since ? t('settings.addAccountWizard.historySince', { since }) : t('settings.addAccountWizard.fullHistory')}</li>
            </ul>
          </div>
        </div>
      )}
    </Dialog>
  )
}


/** How a provider folder is stored, in plain words (empty when it simply becomes a label). */
function folderNote(target?: [string, string] | null): string {
  if (!target) return ''
  if (target[0] === 'role' && target[1] === 'virtual') return t('settings.addAccountWizard.aViewNotImportedSeparately')
  if (target[0] === 'role') return ` → ${ROLE_NAMES[target[1]] ?? target[1]}`
  return ''
}

function GmailOptions({
  folders,
  spam,
  trash,
  skip,
  onSpam,
  onTrash,
  onSkip,
}: {
  folders: TestResult['folders']
  spam: boolean
  trash: boolean
  skip: Set<string>
  onSpam: (v: boolean) => void
  onTrash: (v: boolean) => void
  onSkip: (v: Set<string>) => void
}) {
  const count = (pred: (f: TestResult['folders'][number]) => boolean) => folders.find(pred)?.messages
  const all = count((f) => f.target?.[0] === 'gmail' && f.target[1] === 'all')
  const spamN = count((f) => f.target?.[1] === 'junk')
  const trashN = count((f) => f.target?.[1] === 'trash')
  const labels = folders.filter((f) => f.kind === 'label').sort((a, b) => a.name.localeCompare(b.name))
  const n = (x?: number) => (x === undefined ? '' : ` (${x.toLocaleString(appLocale())})`)
  return (
    <div className="field">
      <span className="field-label">{t('settings.addAccountWizard.whatToImport')}</span>
      <p className="hint">
        {t('settings.addAccountWizard.allMailComesInOnce', { n: n(all) })}
      </p>
      <Check checked={spam} onChange={onSpam} label={t('settings.addAccountWizard.spam', { n: n(spamN) })} desc={t('settings.addAccountWizard.alsoTeachesMailrsSpamFilter')} />
      <Check checked={trash} onChange={onTrash} label={t('settings.addAccountWizard.trash', { n: n(trashN) })} desc={t('settings.addAccountWizard.recentlyDeletedMailGmailEmpties')} />
      {labels.length > 0 && (
        <>
          <span className="field-label" style={{ marginTop: 12 }}>
            {t('settings.addAccountWizard.skipMailWithTheseLabels')}
          </span>
          <div className="scope-grid">
            {labels.map((f) => (
              <Check
                key={f.name}
                checked={skip.has(f.name)}
                onChange={(v) => {
                  const next = new Set(skip)
                  if (v) next.add(f.name)
                  else next.delete(f.name)
                  onSkip(next)
                }}
                label={`${f.name}${n(f.messages)}`}
              />
            ))}
          </div>
          <span className="hint">{t('settings.addAccountWizard.usefulForArchivesYouDo')}</span>
        </>
      )}
    </div>
  )
}
