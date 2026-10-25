// Admin → Users: mail accounts an administrator connects for a member (backend core/managed.py).
// Off by default: the switch at the top of Users. On, each person's row has "mail accounts", and an
// email preset (Prepare a new user) can carry one for the first sign-in. The member sees
// "Connected by your administrator" on it and may disconnect it.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { AdminUser, DiscoverResult, ServerSettings, TestResult } from '../../api/types'
import { Switch } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { useMe } from '../../api/hooks'
import { accountDraft } from './accountDraft'
import { Check, Radio } from './common'
import { savedToast } from './save'
import { storeModeUnavailable } from './storeMode'
import { modeLine, modeName } from './syncModes'
import { t } from '../../i18n'

export interface ManagedAccount {
  id: number
  user_id: number
  owner: string | null
  name: string
  address: string
  mode: 'mirror' | 'store'
  data_class: 'normal' | 'local_only'
  status: string
  enabled: boolean
  created_at: string | null
}

/** A preset's prepared account (never the password). */
export interface PresetMailAccount {
  name: string
  mode: 'mirror' | 'store'
  data_class: 'normal' | 'local_only'
  address: string
  config: { email?: string; imap?: ServerSettings; smtp?: ServerSettings | null; username?: string }
  has_password: boolean
  connected_at: string | null
  skipped: string | null
}

const KEY = ['admin', 'managed-accounts']

export function useManagedSetting() {
  return useQuery<{ enabled: boolean; items: ManagedAccount[] }>({ queryKey: KEY, queryFn: () => api.get('/admin/managed-accounts') })
}

/** The instance switch, at the top of Admin → Users. ACT admin.managed_accounts_setting @web.settings */
export function ManagedSwitch() {
  const qc = useQueryClient()
  const q = useManagedSetting()
  const on = !!q.data?.enabled
  const put = (v: boolean, undo: boolean) =>
    api
      .put('/admin/managed-accounts', { enabled: v })
      .then(() => {
        void qc.invalidateQueries({ queryKey: KEY })
        void qc.invalidateQueries({ queryKey: ['admin', 'users'] })
        savedToast(undo ? (v ? t('settings.managedAccounts.administratorsMayConnectMailAccounts') : t('settings.managedAccounts.membersConnectTheirOwnMail')) : t('common.changeUndone'),
          undo ? () => void put(!v, false) : undefined)
      })
      .catch((e) => toastError(e))
  return (
    <div className="row managed-switch" style={{ gap: 12, alignItems: 'flex-start', margin: '4px 0 12px' }} data-testid="managed-switch">
      <Switch checked={on} disabled={!q.data} onChange={(v) => void put(v, true)} label={t('settings.managedAccounts.administratorsMayConnectMailAccounts2')} />
      <div>
        <b>{t('settings.managedAccounts.administratorsMayConnectMailAccounts2')}</b>
        <div className="muted" style={{ fontSize: 13 }}>
          {t('settings.managedAccounts.forAnOrganisationThatHands')}
        </div>
      </div>
    </div>
  )
}

export interface AccountFieldsValue {
  email: string
  username: string
  password: string
  imap: ServerSettings
  smtp: ServerSettings
  mode: 'mirror' | 'store'
  localOnly: boolean
  preset: string
}

export const EMPTY_ACCOUNT: AccountFieldsValue = {
  email: '', username: '', password: '', imap: { host: '', port: 993, tls: 'ssl' }, smtp: { host: '', port: 465, tls: 'ssl' },
  mode: 'mirror', localOnly: false, preset: '',
}

/** The request body for an account the admin connects (POST /admin/users/{uid}/accounts, or a preset's mail_account). */
export function managedBody(v: AccountFieldsValue): Record<string, unknown> {
  const d = accountDraft({ kind: 'imap', email: v.email, displayName: '', username: v.username || v.email, password: v.password,
    preset: v.preset, imap: v.imap, smtp: v.smtp, mode: v.mode, localOnly: v.localOnly, retention: 14, since: '' })
  const { retention_days: _r, kind: _k, ...rest } = d as Record<string, unknown>
  return { ...rest, secrets: v.password ? { password: v.password } : {} }
}

function Server({ label, id, value, onChange }: { label: string; id: string; value: ServerSettings; onChange: (v: ServerSettings) => void }) {
  return (
    <div className="grid-3 managed-server">
      <div className="field">
        <label htmlFor={`${id}-host`}>{t('settings.managedAccounts.server', { label })}</label>
        <input id={`${id}-host`} className="input" value={value.host} onChange={(e) => onChange({ ...value, host: e.target.value.trim() })} placeholder={id === 'imap' ? 'imap.example.com' : 'smtp.example.com'} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-port`}>{t('settings.managedAccounts.port')}</label>
        <input id={`${id}-port`} className="input" inputMode="numeric" value={value.port || ''} onChange={(e) => onChange({ ...value, port: Number(e.target.value.replace(/\D/g, '')) || 0 })} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-tls`}>{t('settings.managedAccounts.security')}</label>
        <select id={`${id}-tls`} className="select" value={value.tls} onChange={(e) => onChange({ ...value, tls: e.target.value as ServerSettings['tls'] })}>
          <option value="ssl">{t('settings.managedAccounts.sslTls')}</option>
          <option value="starttls">{t('settings.managedAccounts.starttls')}</option>
          <option value="none">{t('common.none')}</option>
        </select>
      </div>
    </div>
  )
}

function sameServer(a: ServerSettings, b: ServerSettings): boolean {
  return a.host === b.host && a.port === b.port && a.tls === b.tls
}

/** The address, the servers (found from the address when possible), the password and the mode. */
export function AccountFields({ v, set, passwordHint }: { v: AccountFieldsValue; set: (v: AccountFieldsValue) => void; passwordHint?: string }) {
  const [finding, setFinding] = useState(false)
  const storeOff = storeModeUnavailable(useMe().data?.features)
  // The answer can come back long after the request (the server asks DNS and autoconfig servers;
  // 30 s was seen). By then the servers may have been typed and tested, or the address changed.
  // The answer fills only the server fields left untouched since the request, and changes nothing
  // when it adds nothing: in the admin's dialog every change clears the test result and the
  // "Connect it again anyway" tick.
  const latest = useRef(v)
  latest.current = v
  const find = async () => {
    if (!v.email.includes('@')) return
    const asked = v
    setFinding(true)
    try {
      const r = await api.post<DiscoverResult>('/connectors/discover', { email: asked.email.trim() })
      const now = latest.current
      if (now.email.trim() !== asked.email.trim()) return
      const imap = r.imap && now.imap === asked.imap && !sameServer(r.imap, now.imap) ? r.imap : null
      const smtp = r.smtp && now.smtp === asked.smtp && !sameServer(r.smtp, now.smtp) ? r.smtp : null
      if (!imap && !smtp) return
      set({ ...now, preset: r.preset ?? '', imap: imap ?? now.imap, smtp: smtp ?? now.smtp, username: now.username || now.email.trim() })
    } catch {
      // Best effort: the server fields stay for typing.
    } finally {
      setFinding(false)
    }
  }
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="field">
        <label htmlFor="ma-email">{t('settings.managedAccounts.emailAddressOfTheAccount')}</label>
        <div className="row" style={{ gap: 8 }}>
          <input id="ma-email" className="input" type="email" value={v.email} onChange={(e) => set({ ...v, email: e.target.value })} onBlur={() => { if (!v.imap.host) void find() }} placeholder={t('settings.managedAccounts.nameExampleCom')} />
          <button type="button" className="btn text sm" disabled={!v.email.includes('@') || finding} onClick={() => void find()}>
            {finding ? t('settings.managedAccounts.finding') : t('settings.managedAccounts.findServers')}
          </button>
        </div>
      </div>
      <Server label={t('common.incomingImap')} id="imap" value={v.imap} onChange={(imap) => set({ ...v, imap })} />
      <Server label={t('common.outgoingSmtp')} id="smtp" value={v.smtp} onChange={(smtp) => set({ ...v, smtp })} />
      <div className="grid-2">
        <div className="field">
          <label htmlFor="ma-user">{t('common.username')}</label>
          <input id="ma-user" className="input" value={v.username} placeholder={v.email || t('settings.managedAccounts.usuallyTheAddress')} onChange={(e) => set({ ...v, username: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="ma-pass">{t('common.password')}</label>
          <input id="ma-pass" className="input" type="password" autoComplete="new-password" value={v.password} onChange={(e) => set({ ...v, password: e.target.value })} />
          {passwordHint && <span className="hint">{passwordHint}</span>}
        </div>
      </div>
      <div>
        <Radio name="ma-mode" value="mirror" current={v.mode} onChange={(mode) => set({ ...v, mode })} label={modeName('mirror')}
          desc={modeLine('mirror')} />
        <Radio name="ma-mode" value="store" current={v.mode} onChange={(mode) => set({ ...v, mode })} label={modeName('store')}
          desc={storeOff ?? t('settings.managedAccounts.mailrKeepsTheMailMember')} disabled={!!storeOff} />
      </div>
      <Check checked={v.localOnly} onChange={(localOnly) => set({ ...v, localOnly })} label="Local-only"
        desc={t('settings.managedAccounts.itsMailIsNeverSent')} />
    </div>
  )
}

function serverText(e: unknown): string {
  return e instanceof ApiError ? e.message : (e as Error).message
}

/** One member's accounts that an administrator connected, and the form to connect another.
 *  ACT admin.managed_account_list @web.settings
 *  ACT admin.managed_account_test @web.settings
 *  ACT admin.managed_account_connect @web.settings
 *  ACT admin.managed_account_remove @web.settings */
export function ManagedAccountsDialog({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const qc = useQueryClient()
  const who = user.name || user.email
  const key = ['admin', 'users', user.id, 'accounts']
  const q = useQuery<{ enabled: boolean; items: ManagedAccount[]; disconnected: { address: string; at: string }[] }>({
    queryKey: key, queryFn: () => api.get(`/admin/users/${user.id}/accounts`),
  })
  const [v, setV] = useState<AccountFieldsValue>({ ...EMPTY_ACCOUNT, email: '' })
  const [test, setTest] = useState<TestResult | null>(null)
  const [conflict, setConflict] = useState<string | null>(null)
  const [reconnect, setReconnect] = useState(false)
  const [removing, setRemoving] = useState<ManagedAccount | null>(null)
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: key })
    void qc.invalidateQueries({ queryKey: KEY })
  }
  const runTest = useMutation({
    mutationFn: () => api.post<TestResult>(`/admin/users/${user.id}/accounts/test`, managedBody(v)),
    onSuccess: (r) => setTest(r),
    onError: (e) => setTest({ ok: false, detail: serverText(e), folders: [], identities: [], extensions: [] }),
  })
  const connect = useMutation({
    mutationFn: () => api.post<ManagedAccount>(`/admin/users/${user.id}/accounts`, { ...managedBody(v), reconnect, skip_test: !!test?.ok }),
    onSuccess: (r) => {
      refresh()
      setV({ ...EMPTY_ACCOUNT })
      setTest(null)
      setConflict(null)
      setReconnect(false)
      toast({ message: t('settings.managedAccounts.connectedForTheyAreTold', { address: r.address, who }) })
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) setConflict(e.message)
      else toastError(e, t('common.notConnected'))
    },
  })
  const items = q.data?.items ?? []
  const gone = q.data?.disconnected ?? []
  const ready = v.email.includes('@') && !!v.imap.host && !!v.password
  return (
    <Dialog
      open
      onClose={onClose}
      modal
      wide
      title={t('settings.managedAccounts.mailAccountsFor', { who })}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {t('common.close')}
          </button>
          <button type="button" className="btn tonal" disabled={!ready || runTest.isPending} onClick={() => runTest.mutate()}>
            {runTest.isPending ? t('common.testing') : t('common.test')}
          </button>
          <button type="button" className="btn filled" disabled={!ready || connect.isPending || (!!conflict && !reconnect)} onClick={() => connect.mutate()}>
            {connect.isPending ? t('settings.managedAccounts.connecting') : t('settings.managedAccounts.connectForThem')}
          </button>
        </>
      }
    >
      <div className="stack" data-testid="managed-accounts">
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          {t('settings.managedAccounts.onlyTheAccountsAnAdministrator', { who })}
        </p>
        {items.length > 0 && (
          <ul className="managed-list" aria-label={t('settings.managedAccounts.accountsConnectedByAnAdministrator')}>
            {items.map((a) => (
              <li key={a.id} className="row" style={{ gap: 8, alignItems: 'center' }}>
                <span className="grow">
                  <b>{a.address}</b>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {' '}· {modeName(a.mode)}{a.data_class === 'local_only' ? t('settings.managedAccounts.localOnly') : ''} · {a.status}
                    {a.created_at ? t('settings.managedAccounts.connected', { relative: formatRelative(a.created_at) }) : ''}
                  </span>
                </span>
                <button type="button" className="icon-btn" aria-label={t('settings.managedAccounts.remove', { address: a.address })} data-tip={t('common.remove')} onClick={() => setRemoving(a)}>
                  <Trash2 />
                </button>
              </li>
            ))}
          </ul>
        )}
        {gone.length > 0 && (
          <p className="muted" style={{ fontSize: 13, margin: 0 }} data-testid="managed-disconnected">
            {t('settings.managedAccounts.disconnected', { who, map: gone.map((d) => `${d.address} (${formatRelative(d.at)})`).join(', ') })}
          </p>
        )}
        <h3 style={{ margin: '8px 0 0', fontSize: 15 }}>{t('settings.managedAccounts.connectAnAccount')}</h3>
        <AccountFields v={v} set={(x) => { setV(x); setTest(null); setConflict(null); setReconnect(false) }} />
        {test && (
          <div className={test.ok ? 'banner info' : 'banner error'} role="status" data-testid="managed-test">
            {test.ok ? t('settings.managedAccounts.signedInFoldersFound', { count: test.folders.length }) : test.detail}
          </div>
        )}
        {conflict && (
          <div className="banner warn" role="alert">
            {conflict}
            <Check checked={reconnect} onChange={setReconnect} label={t('settings.managedAccounts.connectItAgainAnyway')} desc={t('settings.managedAccounts.isToldAgainAndCan', { who })} />
          </div>
        )}
      </div>
      <ConfirmDialog
        open={!!removing}
        title={t('settings.managedAccounts.remove2', { address: removing?.address })}
        body={t('settings.managedAccounts.itStopsSyncingForThe', { who })}
        confirmLabel={t('common.remove')}
        danger
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          const a = removing!
          setRemoving(null)
          api
            .del(`/admin/users/${user.id}/accounts/${a.id}`)
            .then(() => {
              refresh()
              toast({ message: t('settings.managedAccounts.removed', { address: a.address }) })
            })
            .catch((e) => toastError(e))
        }}
      />
    </Dialog>
  )
}
