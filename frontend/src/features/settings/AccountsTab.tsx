import { AlertTriangle, Copy, KeyRound, Lock, Monitor, Pause, Play, Plus, RefreshCw, RotateCcw, ShieldAlert, Smartphone, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { qk, useApi, useApiList, useMe } from '../../api/hooks'
import type { Connector, Identity, SessionInfo } from '../../api/types'
import { Avatar, Skeleton, LoadFailed, failedEmpty } from '../../components/bits'
import { resetMissing } from '../../lib/avatars'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { formatRelative } from '../../lib/format'
import { deviceName } from '../../lib/userAgent'
import { toast, toastError } from '../../stores/ui'
import { EditorArea, FormatBar, useMailEditor } from '../compose/Editor'
import { AddAccountWizard } from './AddAccountWizard'
import { Check, Section } from './common'
import { healthTone } from './DiagnosticsAdmin'
import { backupUnchecked, storeModeUnavailable } from './storeMode'
import { modeLine, modeName, providerName } from './syncModes'
import { AccountColourButton } from './AccountColour'
import { SentCopiesFields, SentCopiesRetry, sentCopiesWarning, type SaveSent } from './SentCopies'
import { useMailAccounts } from '../layout/accounts'
import { RecoveryReview } from './RecoveryReview'
import { t, tx } from '../../i18n'

function pillText(c: Connector): string {
  if (c.health) return c.health.text
  return c.status === 'idle' ? t('common.upToDate') : c.status === 'syncing' ? t('settings.accountsTab.syncing') : c.status === 'error' ? t('settings.accountsTab.error') : c.status === 'paused' ? t('common.paused') : t('settings.accountsTab.new')
}

function statusPill(c: Connector) {
  // The server's plain words (backend core/accounthealth.py), else the raw status.
  if (c.health) return <span className={`pill ${healthTone(c.health.state)}`} data-testid="account-health">{c.health.text}</span>
  const cls = c.status === 'error' ? 'err' : c.status === 'paused' ? 'warn' : c.status === 'syncing' ? 'info' : 'ok'
  return <span className={`pill ${cls}`}>{pillText(c)}</span>
}

/** One line over the member's mail accounts: the first that needs a look, or the accounts whose
 *  sync is running or behind, from the health their rows show. Nothing while every row is up to
 *  date: "Your account is syncing" above a row that said "Up to date" read as two answers. */
export function healthSummary(accounts: Connector[]): { state: string; text: string } | null {
  const rows = accounts.filter((c) => c.health)
  if (!rows.length) return null
  const bad = rows.filter((c) => ['signin', 'error', 'stale'].includes(c.health!.state))
  if (!bad.length) {
    const syncing = rows.filter((c) => c.health!.state === 'syncing')
    if (!syncing.length) return null
    if (syncing.length < rows.length) return { state: 'syncing', text: syncing.map((c) => `${c.name}: ${c.health!.text}.`).join(' ') }
    const last = rows.map((c) => c.last_sync_at).filter(Boolean).sort().pop()
    return { state: 'syncing', text: (t('settings.accountsTab.allAccountsAreSyncing', { count: rows.length })) + (last ? t('settings.accountsTab.lastSynced', { relative: formatRelative(last) }) : '') }
  }
  const first = bad.find((c) => c.health!.state === 'signin') ?? bad[0]
  return { state: first.health!.state, text: `${first.name}: ${first.health!.text}.${bad.length > 1 ? t('settings.accountsTab.moreNeedALook', { count: bad.length - 1 }) : ''}` }
}

/** Settings → Mail → Mail accounts: the accounts, with the add-account wizard (?add=1 opens it). */
export function MailAccountsSection() {
  const [params, setParams] = useSearchParams()
  const [wizard, setWizard] = useState(params.get('add') === '1')
  useEffect(() => {
    if (params.get('add') === '1') {
      params.delete('add')
      setParams(params, { replace: true })
    }
  }, [params, setParams])

  return (
    <>
      <ConnectorsSection onAdd={() => setWizard(true)} />
      <AddAccountWizard open={wizard} onClose={() => setWizard(false)} />
    </>
  )
}

// ---------- identities ----------

export function IdentitiesSection() {
  const me = useMe().data
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Partial<Identity> | null>(null)
  const [deleting, setDeleting] = useState<Identity | null>(null)
  const identities = me?.identities ?? []
  const done = () => void qc.invalidateQueries({ queryKey: qk.me })
  const makeDefault = useMutation({
    mutationFn: (id: number) => api.patch(`/identities/${id}`, { is_default: true }),
    onSuccess: done,
    onError: (e) => toastError(e),
  })
  return (
    <Section
      title={t('settings.accountsTab.sendMailAs')}
      desc={t('settings.accountsTab.addressesYouSendFromEach')}
      actions={
        <button type="button" className="btn tonal" onClick={() => setEditing({ name: me?.user.name ?? '', email: '', reply_to: '', signature_html: '' })}>
          <Plus />{' '}{t('settings.accountsTab.addAnotherAddress')}
        </button>
      }
    >
      {identities.length === 0 ? (
        <p className="muted">{t('settings.accountsTab.noSendAsAddressesYet')}</p>
      ) : (
        <div className="table-wrap"><table className="table">
          <tbody>
            {identities.map((i) => (
              <tr key={i.id}>
                <td style={{ width: 56 }}>
                  <IdentityPicture identity={i} />
                </td>
                <td>
                  <b>{i.name}</b> &lt;{i.email}&gt;
                  {i.reply_to && <div className="muted">{t('settings.accountsTab.replyTo', { replyTo: i.reply_to })}</div>}
                </td>
                <td style={{ width: 160 }}>{i.is_default ? <span className="pill info">{t('settings.accountsTab.default')}</span> : <button type="button" className="btn text sm" onClick={() => makeDefault.mutate(i.id)}>{t('settings.accountsTab.makeDefault')}</button>}</td>
                <td style={{ width: 170 }}>
                  <button type="button" className="btn text sm" onClick={() => setEditing(i)}>
                    {t('settings.accountsTab.editInfo')}
                  </button>
                  <button type="button" className="btn text sm" onClick={() => setDeleting(i)} disabled={i.is_default && identities.length > 1}>
                    {t('common.delete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {editing && <IdentityDialog identity={editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={t('settings.accountsTab.deleteAddress')}
        body={t('settings.accountsTab.stopSendingAsMailAlready', { email: deleting?.email })}
        confirmLabel={t('common.delete2')}
        danger
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          const d = deleting
          setDeleting(null)
          if (d)
            api
              .del(`/identities/${d.id}`)
              .then(done)
              .catch((e) => toastError(e, t('settings.accountsTab.couldNotDelete')))
        }}
      />
    </Section>
  )
}

/** Picture for an address you send from (shown in the top bar and on your messages). */
function IdentityPicture({ identity }: { identity: Identity }) {
  const [ver, setVer] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const qc = useQueryClient()
  const me = useMe().data
  // Without its own picture the address shows your account picture (Your picture, above).
  const src = identity.has_avatar || ver > 0 ? `/api/v1/identities/${identity.id}/avatar?v=${ver}` : me?.picture?.has ? `/api/v1/me/picture?v=${me.picture.version}` : null
  const upload = async (f: File) => {
    const fd = new FormData()
    fd.append('file', f)
    try {
      await api.put(`/identities/${identity.id}/avatar`, fd)
      resetMissing()
      setVer((v) => v + 1)
      void qc.invalidateQueries({ queryKey: qk.me })
      void qc.invalidateQueries({ queryKey: ['pictures', 'local'] })
      toast({ message: t('settings.accountsTab.pictureUpdated') })
    } catch (e) {
      toastError(e, t('settings.accountsTab.pictureNotSaved'))
    }
  }
  return (
    <span className="identity-pic">
      <button type="button" className="avatar-btn" aria-label={t('settings.accountsTab.changePictureFor', { email: identity.email })} data-tip={t('settings.accountsTab.changePicture')} onClick={() => input.current?.click()}>
        <Avatar key={ver} who={identity} src={src} />
      </button>
      <input
        ref={input}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) void upload(f)
        }}
      />
    </span>
  )
}

function IdentityDialog({ identity, onClose }: { identity: Partial<Identity>; onClose: () => void }) {
  const qc = useQueryClient()
  const connectors = useApiList<Connector>(['connectors'], '/connectors')
  const [v, setV] = useState<Partial<Identity>>(identity)
  const [sig, setSig] = useState(identity.signature_html ?? '')
  const editor = useMailEditor({ html: identity.signature_html ?? '', onChange: setSig, placeholder: t('settings.accountsTab.signature') })
  const save = useMutation({
    mutationFn: () => {
      const body = { email: v.email?.trim(), name: v.name?.trim() ?? '', reply_to: v.reply_to?.trim() ?? '', signature_html: sig, connector_id: v.connector_id ?? null, receives: v.receives ?? [] }
      return identity.id ? api.patch(`/identities/${identity.id}`, body) : api.post('/identities', body)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.me })
      toast({ message: t('settings.accountsTab.addressSaved') })
      onClose()
    },
    onError: (e) => toastError(e, t('settings.accountsTab.couldNotSave')),
  })
  const mailConnectors = (connectors.data ?? []).filter((c) => c.kind !== 'caldav' && c.kind !== 'speakr')
  return (
    <Dialog
      open
      onClose={onClose}
      title={identity.id ? t('settings.accountsTab.editAddress') : t('settings.accountsTab.addAnotherEmailAddressYou')}
      wide
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!v.email?.includes('@') || save.isPending} onClick={() => save.mutate()}>
            {t('common.save')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="grid-2">
          <div className="field">
            <label htmlFor="id-name">{t('common.name')}</label>
            <input id="id-name" className="input" value={v.name ?? ''} onChange={(e) => setV({ ...v, name: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="id-email">{t('settings.accountsTab.emailAddress')}</label>
            <input id="id-email" className="input" type="email" value={v.email ?? ''} onChange={(e) => setV({ ...v, email: e.target.value })} disabled={!!identity.id} />
          </div>
        </div>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="id-reply">{t('settings.accountsTab.replyToAddressOptional')}</label>
            <input id="id-reply" className="input" type="email" value={v.reply_to ?? ''} onChange={(e) => setV({ ...v, reply_to: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="id-conn">{t('settings.accountsTab.sendThrough')}</label>
            <select id="id-conn" className="select" value={v.connector_id ?? ''} onChange={(e) => setV({ ...v, connector_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">{t('settings.accountsTab.chooseAnAccount')}</option>
              {mailConnectors.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="id-receives">{t('settings.accountsTab.defaultForMailSentTo')}</label>
          <input id="id-receives" className="input" value={(v.receives ?? []).join(', ')} onChange={(e) => setV({ ...v, receives: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })} placeholder={t('settings.accountsTab.exampleCom')} />
        </div>
        <div className="field">
          <span className="field-label">{t('settings.accountsTab.signature')}</span>
          <div className="card editor-card" style={{ minHeight: 160 }}>
            <FormatBar editor={editor} />
            <EditorArea editor={editor} />
          </div>
        </div>
      </div>
    </Dialog>
  )
}

// ---------- connectors ----------

function ConnectorsSection({ onAdd }: { onAdd: () => void }) {
  const qc = useQueryClient()
  const q = useApiList<Connector>(['connectors'], '/connectors', undefined, { refetchInterval: 15_000 })
  const [arming, setArming] = useState<Connector | null>(null)
  // The mail export is off on the server: deletion at the provider cannot be armed (storeMode.ts).
  const features = useMe().data?.features
  const storeOff = storeModeUnavailable(features)
  // MAILR_STORE_BACKUP_CHECK=false: deletion at the provider waits for no backup (storeMode.ts).
  const unchecked = backupUnchecked(features)
  const [confirmText, setConfirmText] = useState('')
  const [armBusy, setArmBusy] = useState(false)
  const [removing, setRemoving] = useState<Connector | null>(null)
  const [editing, setEditing] = useState<Connector | null>(null)
  const done = () => void qc.invalidateQueries({ queryKey: ['connectors'] })
  const act = (c: Connector, path: string, msg: string) =>
    api
      .post(`/connectors/${c.id}/${path}`)
      .then(() => {
        done()
        toast({ message: msg })
      })
      .catch((e) => toastError(e))
  const mail = (q.data ?? []).filter((c) => c.kind !== 'caldav' && c.kind !== 'speakr')
  // The colour of each mail account (its dot on rows and in the navigation), from GET /counts.
  const { byId: mailAccounts, multi } = useMailAccounts()

  return (
    <Section
      title={t('common.mailAccounts')}
      desc={t('settings.accountsTab.connectorsBringMailInAnd')}
      actions={
        <button type="button" className="btn filled" onClick={onAdd}>
          <Plus />{' '}{t('settings.accountsTab.addAMailAccount')}
        </button>
      }
    >
      {q.isLoading ? (
        <Skeleton height={80} />
      ) : failedEmpty(q) ? (
        <LoadFailed what={t('settings.accountsTab.yourMailAccounts')} q={q} />
      ) : mail.length === 0 ? (
        <div className="card">
          <p style={{ margin: 0 }}>{t('settings.accountsTab.noMailAccountsYetAdd')}</p>
        </div>
      ) : (
        <div className="card-list">
          {(() => {
            // ACT settings.account_health @web.settings
            const sum = healthSummary(mail.filter((c) => c.enabled))
            if (!sum) return null
            return (
              <p className={`sub account-health-line${sum.state === 'syncing' ? '' : ' bad'}`} role="status" data-testid="account-health-line" style={{ margin: '0 0 4px' }}>
                {sum.state === 'syncing' ? null : <AlertTriangle size={14} style={{ verticalAlign: '-2px', marginInlineEnd: 4 }} />}
                {sum.text}
              </p>
            )
          })()}
          {mail.map((c) => (
            <div key={c.id} className="card">
              <div className="card-row wrap-narrow">
                <div className="grow">
                  <div className="title row">
                    {multi && mailAccounts.get(c.id) && <AccountColourButton account={mailAccounts.get(c.id)!} />}
                    {c.name} {statusPill(c)}
                    {c.data_class === 'local_only' && (
                      <span className="pill warn" data-tip={t('settings.accountsTab.neverSentToHostedModels')}>
                        <Lock size={11} />{' '}{t('common.localOnly')}
                      </span>
                    )}
                    {c.managed_by_admin && (
                      <span className="pill info" data-testid="account-managed" data-tip={t('settings.accountsTab.youCanChangeOrDisconnect')}>
                        {t('settings.accountsTab.connectedByYourAdministrator')}
                      </span>
                    )}
                  </div>
                  <div className="sub">
                    {c.kind.toUpperCase()} · <span data-tip={modeLine(c.mode)}>{modeName(c.mode)}</span>
                    {c.last_sync_at ? t('settings.accountsTab.synced', { relative: formatRelative(c.last_sync_at) }) : ''}
                  </div>
                  {c.health?.state === 'signin' ? (
                    <div className="sub" style={{ color: 'var(--danger)' }}>
                      {t('settings.accountsTab.theProviderRefusedThePassword')}
                    </div>
                  ) : c.status_detail && (c.status === 'error' ? (
                    <details className="sub" style={{ color: 'var(--danger)' }}>
                      <summary>{t('settings.accountsTab.whatTheServerSaid')}</summary>
                      {c.status_detail}
                    </details>
                  ) : c.status_detail !== pillText(c) && (
                    // The pill already says "Up to date"; a detail that only repeats it is left out.
                    <div className="sub">{c.status_detail}</div>
                  ))}
                  {sentCopiesWarning(c.sent_copies) && (
                    <div className="sub" style={{ marginTop: 4 }} data-testid="sent-copies-failed">
                      {sentCopiesWarning(c.sent_copies)}{' '}·{' '}
                      <SentCopiesRetry c={c} />
                    </div>
                  )}
                  {c.mode === 'store' && (
                    <div className="sub" style={{ marginTop: 4 }} data-testid="provider-deletion">
                      {c.delete_upstream ? (
                        <b style={{ color: 'var(--danger)' }}>{t('settings.accountsTab.deletedFromProviderAfter', { provider: providerName(c), count: c.retention_days })}</b>
                      ) : storeOff ? (
                        t('settings.accountsTab.mailAlsoStaysAtProvider', { provider: providerName(c) })
                      ) : (
                        <>
                          {t('settings.accountsTab.mailAlsoStaysAtProvider', { provider: providerName(c) })} ·{' '}
                          <button type="button" className="btn text sm" style={{ padding: 0, height: 'auto' }} onClick={() => { setConfirmText(''); setArming(c) }}>
                            {t('settings.accountsTab.deleteFromProviderAfter', { provider: providerName(c), count: c.retention_days })}
                          </button>
                        </>
                      )}
                      {unchecked && (
                        <div className="muted" data-testid="backup-unchecked">{t('settings.accountsTab.backupUnchecked')}</div>
                      )}
                    </div>
                  )}
                </div>
                {/* The everyday controls carry their words; the rarer ones keep a tooltip. */}
                <button type="button" className="btn text sm" onClick={() => void act(c, 'sync', t('settings.accountsTab.syncStarted'))}>
                  <RefreshCw size={16} aria-hidden />{' '}{t('common.syncNow')}
                </button>
                {c.enabled && c.status !== 'paused' ? (
                  <button type="button" className="btn text sm" onClick={() => void act(c, 'pause', t('settings.accountsTab.accountPaused'))}>
                    <Pause size={16} aria-hidden />{' '}{t('common.pause')}
                  </button>
                ) : (
                  <button type="button" className="btn text sm" onClick={() => void act(c, 'resume', t('settings.accountsTab.accountResumed'))}>
                    <Play size={16} aria-hidden />{' '}{t('common.resume')}
                  </button>
                )}
                <button type="button" className="icon-btn" aria-label={t('settings.accountsTab.resyncFromScratch')} data-tip={t('settings.accountsTab.resyncFromScratch')} onClick={() => void act(c, 'resync', t('settings.accountsTab.fullResyncStarted'))}>
                  <RotateCcw />
                </button>
                <button type="button" className="btn text sm" onClick={() => setEditing(c)}>
                  {t('common.edit2')}
                </button>
                <button type="button" className="icon-btn" aria-label={t('settings.accountsTab.removeAccount')} data-tip={t('settings.accountsTab.removeAccount')} onClick={() => setRemoving(c)}>
                  <Trash2 />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <Dialog
        open={!!arming}
        onClose={() => !armBusy && setArming(null)}
        title={t('settings.accountsTab.armUpstreamDeletion', { provider: arming ? providerName(arming) : '' })}
        modal
        footer={
          <>
            <button type="button" className="btn text" disabled={armBusy} onClick={() => setArming(null)}>
              {t('common.cancel')}
            </button>
            <button
              type="button"
              className="btn danger"
              disabled={armBusy || confirmText.trim() !== arming?.name}
              onClick={() => {
                const c = arming!
                // The server checks the backup first (a sample read from the backup target), which
                // takes a few seconds: the dialog stays open and says so until the answer arrives.
                setArmBusy(true)
                api
                  .post(`/connectors/${c.id}/arm-deletion`, { confirm: confirmText.trim() })
                  .then(() => qc.invalidateQueries({ queryKey: ['connectors'] }))
                  .then(() => {
                    setArming(null)
                    toast({ message: t('settings.accountsTab.upstreamDeletionArmedFor', { itemName: c.name, provider: providerName(c), count: c.retention_days }) })
                  })
                  .catch((e) => toastError(e, t('settings.accountsTab.notArmed')))
                  .finally(() => setArmBusy(false))
              }}
            >
              {armBusy ? (unchecked ? t('settings.accountsTab.arming') : t('settings.accountsTab.checkingTheBackup')) : t('settings.accountsTab.armDeletion')}
            </button>
          </>
        }
      >
        <div className="stack">
          <div className="banner error">
            <ShieldAlert />
            <span>
              {unchecked
                ? tx('settings.accountsTab.mailFromWillBeDeletedUnchecked', { armingName: arming?.name, provider: arming ? providerName(arming) : '', count: arming?.retention_days }, { b: (c) => <b>{c}</b> })
                : tx('settings.accountsTab.mailFromWillBeDeleted', { armingName: arming?.name, provider: arming ? providerName(arming) : '', count: arming?.retention_days }, { b: (c) => <b>{c}</b> })}
            </span>
          </div>
          <p style={{ margin: 0 }}>
            {unchecked
              ? tx('settings.accountsTab.backupsAreTheAdministrators', { armingName: arming?.name }, { b: (c) => <b>{c}</b> })
              : tx('settings.accountsTab.makeSureYourBackupsAre', { armingName: arming?.name }, { b: (c) => <b>{c}</b> })}
          </p>
          <input className="input" value={confirmText} disabled={armBusy} onChange={(e) => setConfirmText(e.target.value)} aria-label={t('settings.accountsTab.typeTheAccountNameTo')} autoFocus />
          {armBusy && !unchecked && <p className="sub" role="status" style={{ margin: 0 }}>{t('settings.accountsTab.checkingThatTheBackupCan')}</p>}
        </div>
      </Dialog>
      <ConfirmDialog
        open={!!removing}
        title={removing?.managed_by_admin ? t('settings.accountsTab.disconnect', { removingName: removing?.name }) : t('settings.accountsTab.remove', { removingName: removing?.name })}
        body={removing?.managed_by_admin
          ? t('settings.accountsTab.yourAdministratorConnectedThisAccount')
          : t('settings.accountsTab.thisAccountStopsSyncingMail')}
        confirmLabel={removing?.managed_by_admin ? t('settings.accountsTab.disconnect2') : t('common.remove')}
        danger
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          const c = removing!
          setRemoving(null)
          api
            .del(`/connectors/${c.id}`)
            .then(() => {
              done()
              toast({ message: t('settings.accountsTab.accountRemoved') })
            })
            .catch((e) => toastError(e))
        }}
      />
      {editing && <EditConnectorDialog c={editing} onClose={() => setEditing(null)} />}
    </Section>
  )
}

function EditConnectorDialog({ c, onClose }: { c: Connector; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(c.name)
  const [retention, setRetention] = useState(c.retention_days)
  const [localOnly, setLocalOnly] = useState(c.data_class === 'local_only')
  const [password, setPassword] = useState('')
  const [saveSent, setSaveSent] = useState<SaveSent>(c.sent_copies?.setting ?? 'auto')
  const save = useMutation({
    mutationFn: () =>
      api.patch(`/connectors/${c.id}`, {
        name: name.trim(),
        retention_days: retention,
        data_class: localOnly ? 'local_only' : 'normal',
        ...(password ? { secrets: { password } } : {}),
        ...(c.sent_copies && saveSent !== c.sent_copies.setting ? { save_sent: saveSent } : {}),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['connectors'] })
      toast({ message: t('settings.accountsTab.accountUpdated') })
      onClose()
    },
    onError: (e) => toastError(e, t('common.notSaved')),
  })
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('settings.accountsTab.editAccount')}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={save.isPending || !name.trim()} onClick={() => save.mutate()}>
            {t('common.save')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="field">
          <label htmlFor="ec-name">{t('common.name')}</label>
          <input id="ec-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        {c.mode === 'store' && (
          <div className="field">
            <label htmlFor="ec-ret">{t('settings.accountsTab.retentionAtTheProviderDays')}</label>
            <input id="ec-ret" className="input" inputMode="numeric" value={retention} onChange={(e) => setRetention(Math.max(1, Number(e.target.value.replace(/\D/g, '')) || 1))} />
          </div>
        )}
        <div className="field">
          <label htmlFor="ec-pass">{t('settings.accountsTab.newPasswordLeaveEmptyTo')}</label>
          <input id="ec-pass" className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <SentCopiesFields c={c} value={saveSent} onChange={setSaveSent} />
        <Check checked={localOnly} onChange={setLocalOnly} label={t('common.localModelsOnly')} desc={t('settings.accountsTab.neverSendThisAccountsMail')} />
        {c.data_class === 'local_only' && !localOnly && (
          <div className="banner warn">
            <AlertTriangle />
            <span>{t('settings.accountsTab.turningThisOffLetsHosted')}</span>
          </div>
        )}
      </div>
    </Dialog>
  )
}

// ---------- IMAP access for phone apps ----------

export function ImapSection() {
  const info = useApi<{ host: string; port: number; security: string; username: string; smtp_host?: string; smtp_port?: number }>(['me', 'imap'], '/me/imap')
  const [pw, setPw] = useState<string | null>(null)
  const make = useMutation({
    mutationFn: () => api.post<{ password: string }>('/me/imap/app-password', {}),
    onSuccess: (r) => setPw(r.password),
    onError: (e) => toastError(e, t('settings.accountsTab.couldNotCreateAppPassword')),
  })
  return (
    <Section title={t('settings.accountsTab.phoneAndDesktopApps')} desc={t('settings.accountsTab.anyImapAppFairemailThunderbird')}>
      {info.isLoading ? (
        <Skeleton height={60} />
      ) : info.isError ? (
        <p className="muted">{t('settings.accountsTab.imapAccessIsNotAvailable')}</p>
      ) : (
        <div className="card">
          <div className="card-row">
            <Smartphone />
            <div className="grow" style={{ fontSize: 13.5 }}>
              <div>
                {tx('settings.accountsTab.imapPort', { host: info.data?.host, port: info.data?.port, security: info.data?.security }, { code: (c) => <code>{c}</code> })}
              </div>
              <div>
                {tx('settings.accountsTab.username', { username: info.data?.username }, { code: (c) => <code>{c}</code> })}
              </div>
            </div>
            <button type="button" className="btn tonal" onClick={() => make.mutate()} disabled={make.isPending}>
              <KeyRound />{' '}{t('settings.accountsTab.newAppPassword')}
            </button>
          </div>
          {pw && (
            <div className="stack" style={{ marginTop: 12 }}>
              <div className="token-box">
                <span style={{ flex: 1 }}>{pw}</span>
                <button type="button" className="icon-btn sm" aria-label={t('common.copy')} data-tip={t('common.copy')} onClick={() => void navigator.clipboard.writeText(pw).then(() => toast({ message: t('settings.accountsTab.copied') }))}>
                  <Copy size={16} />
                </button>
              </div>
              <span className="muted" style={{ fontSize: 12.5 }}>
                {t('settings.accountsTab.shownOncePasteItInto')}
              </span>
            </div>
          )}
        </div>
      )}
    </Section>
  )
}

// ---------- password + sessions ----------

/** What a recovery step signed out (POST /me/password, POST /me/sign-out-everywhere). */
type SignedOut = {
  ok: boolean
  revoked_tokens?: number
  imap_password_reset?: boolean
  revoked_app_passwords?: number
  mail_apps_failed?: boolean
}

/** The second sentence of a recovery toast: the mail and calendar apps. */
function mailAppsNote(r: SignedOut | undefined): string {
  if (r?.mail_apps_failed) return ' ' + t('settings.accountsTab.mailAppsNotSignedOut')
  if (r?.imap_password_reset || r?.revoked_app_passwords) return ' ' + t('settings.accountsTab.mailAppsSignedOut')
  return ''
}

export function PasswordSection() {
  const qc = useQueryClient()
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [mailApps, setMailApps] = useState(true)
  const change = useMutation({
    // ACT settings.password_change @web.settings
    mutationFn: () => api.post<SignedOut>('/me/password', { current: cur, new: next, sign_out_mail_apps: mailApps }),
    onSuccess: (r) => {
      setCur('')
      setNext('')
      setAgain('')
      void qc.invalidateQueries({ queryKey: qk.me }) // "Review after signing out"
      // A new password also revokes every API token and, unless
      // unticked, signs out the mail and calendar apps.
      const n = r?.revoked_tokens ?? 0
      toast({ message: (n ? t('settings.accountsTab.passwordChangedApiTokensWere', { count: n }) : t('settings.accountsTab.passwordChanged')) + mailAppsNote(r) })
    },
    onError: (e) => toastError(e, t('settings.accountsTab.passwordNotChanged')),
  })
  return (
    <Section title={t('settings.accountsTab.changePassword')} desc={t('settings.accountsTab.forAccountsThatSignIn')}>
      <form
        className="grid-3"
        style={{ maxWidth: 760, alignItems: 'end' }}
        onSubmit={(e) => {
          e.preventDefault()
          change.mutate()
        }}
      >
        <div className="field">
          <label htmlFor="pw-cur">{t('settings.accountsTab.currentPassword')}</label>
          <input id="pw-cur" className="input" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="pw-new">{t('settings.accountsTab.newPassword')}</label>
          <input id="pw-new" className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} minLength={10} />
        </div>
        <div className="field">
          <label htmlFor="pw-again">{t('common.confirm')}</label>
          <input id="pw-again" className="input" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <Check checked={mailApps} onChange={setMailApps} label={t('settings.accountsTab.alsoSignOutMailApps')} desc={t('settings.accountsTab.alsoSignOutMailAppsDesc')} />
        </div>
        <div>
          {next && again && next !== again && <span className="error" style={{ color: 'var(--danger)', fontSize: 12 }}>{t('settings.accountsTab.passwordsDontMatch')}{' '}</span>}
          <button type="submit" className="btn tonal" disabled={!cur || next.length < 10 || next !== again || change.isPending}>
            {t('settings.accountsTab.changePassword')}
          </button>
        </div>
      </form>
    </Section>
  )
}

export function DevicesSection() {
  const qc = useQueryClient()
  const sessions = useApiList<SessionInfo>(['me', 'sessions'], '/me/sessions')
  const [asking, setAsking] = useState(false)
  const revoke = (id: string) =>
    api
      .del(`/me/sessions/${id}`)
      .then(() => void qc.invalidateQueries({ queryKey: ['me', 'sessions'] }))
      .catch((e) => toastError(e))
  // Recovery without a password change (single sign-on has none).
  const everywhere = useMutation({
    // ACT settings.sign_out_everywhere @web.settings
    mutationFn: () => api.post<SignedOut & { ended_sessions?: number }>('/me/sign-out-everywhere', {}),
    onSuccess: (r) => {
      setAsking(false)
      void qc.invalidateQueries({ queryKey: ['me', 'sessions'] })
      void qc.invalidateQueries({ queryKey: qk.me }) // "Review after signing out"
      toast({ message: t('settings.accountsTab.signedOutEverywhere') + mailAppsNote(r) })
    },
    onError: (e) => toastError(e),
  })
  return (
    <Section title={t('settings.accountsTab.signedInDevices')} desc={t('settings.accountsTab.sessionsStayValidUntilThey')}>
      <RecoveryReview />
      <div className="row" style={{ marginBottom: 8 }}>
        <button type="button" className="btn outlined sm" onClick={() => setAsking(true)}>
          {t('settings.accountsTab.signOutEverywhere')}
        </button>
      </div>
      <ConfirmDialog
        open={asking}
        title={t('settings.accountsTab.signOutEverywhereTitle')}
        body={t('settings.accountsTab.signOutEverywhereBody')}
        confirmLabel={t('settings.accountsTab.signOutEverywhere')}
        danger
        busy={everywhere.isPending}
        onConfirm={() => everywhere.mutate()}
        onClose={() => setAsking(false)}
      />
      <div className="table-wrap"><table className="table">
        <thead>
          <tr>
            <th>{t('settings.accountsTab.device')}</th>
            <th>{t('settings.accountsTab.lastActive')}</th>
            <th>{t('settings.accountsTab.ip')}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(sessions.data ?? []).map((s) => (
            <tr key={s.id}>
              <td>
                <div className="row">
                  {s.device === 'android' ? <Smartphone size={16} /> : <Monitor size={16} />}
                  <span className="ellipsis" style={{ maxWidth: 380 }} title={s.user_agent} data-testid="session-device">
                    {deviceName(s.user_agent, s.device)}
                  </span>
                  {s.current && <span className="pill info">{t('settings.accountsTab.thisDevice')}</span>}
                </div>
              </td>
              <td>{formatRelative(s.last_seen_at)}</td>
              <td>{s.ip}</td>
              <td>
                {!s.current && (
                  <button type="button" className="btn text sm" onClick={() => void revoke(s.id)}>
                    {t('common.signOut')}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </Section>
  )
}
