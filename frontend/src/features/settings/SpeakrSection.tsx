import { assistantNameNow } from '../rail/assistant/useAssistantName'
// Settings → Mail → Mail accounts → Speakr (speakr.connect, speakr.sync).
// In the wizard, you enter the Speakr address and an API token, test them, and connect with the
// data class chosen beside its reason (local-only by default). The card has the sync status and
// counts, Sync now, Pause or Resume, Keep away from hosted models, the switches for edits,
// public share links and recordings shared with you (all off until you turn them on), the
// token's access with warnings about scopes it lacks or does not need (a newer Speakr), webhooks
// (Set up in Speakr creates the webhook through Speakr's API, Send test checks that Speakr
// reaches mailr, Turn off deletes it; a secret pasted from a webhook made by hand also works), a
// new token and Remove. The token and the webhook secret are never shown again after they are
// saved.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, KeyRound, Lock, Mic, Pause, Play, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useLocation } from 'react-router'
import { api } from '../../api/client'
import { useApiList } from '../../api/hooks'
import type { Connector } from '../../api/types'
import { Skeleton, Spinner, LoadFailed, failedEmpty } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { Check, Section } from './common'
import { useVisibility } from './useVisibility'
import { pollNote, scopeLabel, webhookOffMessage, webhookTestMessage, type WebhookOff, type WebhookOn, type WebhookTest } from './speakrWebhook'
import { t, tx } from '../../i18n'

interface SpeakrInfo {
  count?: number | null
  processing?: number | null
  auth_failed?: boolean
  breaker_until?: string | null
  webhook_path?: string | null
  /** A newer Speakr: the token's scopes ("full" for full access) and what is missing or not needed. */
  scopes?: string[] | null
  scope_notes?: string[]
  changes_feed?: boolean
  internal_sharing?: boolean
  webhook_v2?: boolean
}
type SpeakrConnector = Connector & { speakr?: SpeakrInfo }

interface TestOut {
  ok: boolean
  detail: string
  warnings?: string[]
}

const STATUS_TEXT: Record<string, string> = { idle: t('common.upToDate'), syncing: t('settings.speakrSection.syncing'), error: t('settings.speakrSection.error'), paused: t('common.paused'), new: t('settings.speakrSection.new') }

// ACT speakr.connect @web.settings, ACT speakr.sync @web.settings
export function SpeakrSection() {
  const qc = useQueryClient()
  const q = useApiList<SpeakrConnector>(['connectors'], '/connectors', undefined, { refetchInterval: 15_000 })
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<SpeakrConnector | null>(null)
  const [tokenFor, setTokenFor] = useState<SpeakrConnector | null>(null)
  const [hookFor, setHookFor] = useState<SpeakrConnector | null>(null)
  const [testing, setTesting] = useState<number | null>(null)
  const rows = (q.data ?? []).filter((c) => c.kind === 'speakr')
  const vis = useVisibility()
  const location = useLocation()
  const done = () => {
    void qc.invalidateQueries({ queryKey: ['connectors'] })
    void qc.invalidateQueries({ queryKey: ['speakr'] })
  }
  const act = (c: Connector, path: string, msg: string) =>
    api.post(`/connectors/${c.id}/${path}`).then(() => { done(); toast({ message: msg }) }).catch((e) => toastError(e))
  const setLocal = (c: Connector, local: boolean) =>
    api.patch(`/connectors/${c.id}`, { data_class: local ? 'local_only' : 'normal' })
      .then(() => { done(); toast({ message: local ? t('settings.speakrSection.recordingsStayAwayFromHosted') : t('settings.speakrSection.hostedModelsMayNowRead') }) })
      .catch((e) => toastError(e, t('common.notSaved')))
  const setSwitch = (c: Connector, key: 'write_back' | 'share_links' | 'include_shared', on: boolean, msg: string) =>
    api.patch(`/connectors/${c.id}`, { config: { [key]: on } })
      .then(() => { done(); toast({ message: msg }) })
      .catch((e) => toastError(e, t('common.notSaved')))
  // ACT speakr.connect (Turn off: mailr's webhook is deleted in Speakr too)
  const hookOff = (c: Connector) =>
    api.del<WebhookOff>(`/speakr/connectors/${c.id}/webhook`)
      .then((out) => { done(); toast({ message: webhookOffMessage(out) }) })
      .catch((e) => toastError(e, t('common.notSaved')))
  // ACT speakr.connect (Send test)
  const hookTest = (c: Connector) => {
    setTesting(c.id)
    api.post<WebhookTest>(`/speakr/connectors/${c.id}/webhook/test`)
      .then((out) => toast({ message: webhookTestMessage(out) }))
      .catch((e) => toastError(e, t('settings.speakrSection.testNotSent')))
      .finally(() => setTesting(null))
  }
  // In simple settings, a member with no Speakr server connected is not offered a third-party
  // product they may not use; advanced settings, a connection or a link to #speakr (the Recordings
  // app's "Connect Speakr") shows it.
  if (!vis.advanced && !q.isLoading && rows.length === 0 && location.hash !== '#speakr') return null
  return (
    <Section
      id="speakr"
      title={t('settings.speakrSection.recordingsSpeakr')}
      desc={t('settings.speakrSection.connectYourSpeakrAccountTo')}
      actions={
        <button type="button" className="btn tonal" onClick={() => setAdding(true)}>
          <Plus />{' '}{t('settings.speakrSection.addSpeakr')}
        </button>
      }
    >
      {q.isLoading ? <Skeleton height={60} /> : failedEmpty(q) ? <LoadFailed what={t('settings.speakrSection.speakrAccounts')} q={q} /> : rows.length === 0 ? (
        <div className="card"><p style={{ margin: 0 }} className="muted"><Mic size={16} />{' '}{t('settings.speakrSection.noSpeakrAccountConnected')}</p></div>
      ) : (
        <div className="card-list">
          {rows.map((c) => {
            const info = c.speakr ?? {}
            const local = c.data_class === 'local_only'
            return (
              <div key={c.id} className="card" data-testid="speakr-card">
                <div className="card-row">
                  <Mic />
                  <div className="grow">
                    <div className="title row">
                      {c.name}
                      <span className={`pill ${c.status === 'error' ? 'err' : c.status === 'paused' ? 'warn' : c.status === 'syncing' ? 'info' : 'ok'}`}>{STATUS_TEXT[c.status] ?? c.status}</span>
                      {local && <span className="pill warn" data-tip={t('settings.speakrSection.neverSentToHostedModels')}><Lock size={11} />{' '}{t('common.localOnly')}</span>}
                    </div>
                    <div className="sub">
                      {String(c.config.base_url ?? '')}
                      {c.config.speakr_username ? ` · ${String(c.config.speakr_username)}` : ''}
                      {c.last_sync_at ? t('settings.speakrSection.synced', { relative: formatRelative(c.last_sync_at) }) : ''}
                    </div>
                    {c.status_detail && <div className="sub" style={{ color: c.status === 'error' ? 'var(--danger)' : undefined }}>{c.status_detail}</div>}
                    {scopeLabel(info.scopes) && (
                      <div className="sub" data-testid="speakr-scopes">
                        {scopeLabel(info.scopes)}
                        {(info.scope_notes ?? []).map((n) => <div key={n} className="muted"><AlertTriangle size={12} aria-hidden /> {n}</div>)}
                      </div>
                    )}
                    {info.auth_failed && (
                      <div className="sub" style={{ marginTop: 4 }}>
                        <button type="button" className="btn text sm" style={{ padding: 0, height: 'auto' }} onClick={() => setTokenFor(c)}>{t('settings.speakrSection.pasteANewToken2')}</button>
                      </div>
                    )}
                    <div style={{ marginTop: 8 }}>
                      <Check checked={local} onChange={(v) => void setLocal(c, v)} label={t('settings.speakrSection.keepRecordingsAwayFromHosted')}
                        desc={t('settings.speakrSection.recordingsHoldOtherPeoplesVoices')} />
                      {/* ACT recording.edit @web.recording, ACT recording.share_link @web.recording: off until turned on here. */}
                      <Check checked={!!c.config.write_back} onChange={(v) => void setSwitch(c, 'write_back', v, v ? t('settings.speakrSection.editsToSpeakrAreOn') : t('settings.speakrSection.editsToSpeakrAreOff'))}
                        label={t('settings.speakrSection.allowEditsInSpeakr')}
                        desc={t('settings.speakrSection.fromTheRecordingChangeThe', { name: assistantNameNow() })} />
                      <Check checked={!!c.config.share_links} onChange={(v) => void setSwitch(c, 'share_links', v, v ? t('settings.speakrSection.shareLinksAreOn') : t('settings.speakrSection.shareLinksAreOff'))}
                        label={t('settings.speakrSection.allowPublicShareLinks')}
                        desc={t('settings.speakrSection.anyoneWithSuchALink', { name: assistantNameNow() })} />
                      {info.internal_sharing && (
                        <Check checked={!!c.config.include_shared} onChange={(v) => void setSwitch(c, 'include_shared', v, v ? t('settings.speakrSection.recordingsSharedWithYouAppear') : t('settings.speakrSection.recordingsSharedWithYouGo'))}
                          label={t('settings.speakrSection.includeRecordingsSharedWithMe')}
                          desc={t('settings.speakrSection.recordingsOtherSpeakrUsersShared')} />
                      )}
                    </div>
                    <div className="sub" style={{ marginTop: 6 }} data-testid="speakr-webhook">
                      {info.webhook_path ? (
                        <>
                          {c.config.speakr_webhook_id ? t('settings.speakrSection.instantUpdatesOn') : <>{tx('settings.speakrSection.instantUpdatesOn2', { value: `${window.location.origin}${info.webhook_path}` }, { code: (c) => <code>{c}</code> })}</>}{' '}
                          {!!c.config.speakr_webhook_id && (
                            <button type="button" className="btn text sm" disabled={testing === c.id} onClick={() => hookTest(c)}>
                              {testing === c.id ? <Spinner small /> : null}{' '}{t('settings.speakrSection.sendTest')}
                            </button>
                          )}
                          <button type="button" className="btn text sm" onClick={() => void hookOff(c)}>{t('settings.speakrSection.turnOff')}</button>
                          <div className="muted">{pollNote(info.webhook_v2)}</div>
                        </>
                      ) : (
                        <>
                          {info.changes_feed ? t('settings.speakrSection.speakrIsAskedForChanges') : t('settings.speakrSection.speakrIsCheckedEveryTen')}{' '}
                          <button type="button" className="btn text sm" onClick={() => setHookFor(c)}>{t('settings.speakrSection.getUpdatesInstantly')}</button>
                        </>
                      )}
                    </div>
                  </div>
                  <button type="button" className="icon-btn" aria-label={t('common.syncNow')} data-tip={t('common.syncNow')} onClick={() => void act(c, 'sync', t('settings.speakrSection.syncStarted'))}><RefreshCw /></button>
                  {c.enabled && c.status !== 'paused' ? (
                    <button type="button" className="icon-btn" aria-label={t('common.pause')} data-tip={t('common.pause')} onClick={() => void act(c, 'pause', t('settings.speakrSection.speakrPaused'))}><Pause /></button>
                  ) : (
                    <button type="button" className="icon-btn" aria-label={t('common.resume')} data-tip={t('common.resume')} onClick={() => void act(c, 'resume', t('settings.speakrSection.speakrResumed'))}><Play /></button>
                  )}
                  <button type="button" className="icon-btn" aria-label={t('settings.speakrSection.newToken')} data-tip={t('settings.speakrSection.pasteANewToken')} onClick={() => setTokenFor(c)}><KeyRound /></button>
                  <button type="button" className="icon-btn" aria-label={t('settings.speakrSection.removeSpeakr')} data-tip={t('common.remove')} onClick={() => setRemoving(c)}><Trash2 /></button>
                </div>
              </div>
            )
          })}
        </div>
      )}
      {adding && <SpeakrWizard onClose={() => setAdding(false)} onDone={done} />}
      {tokenFor && <TokenDialog c={tokenFor} onClose={() => setTokenFor(null)} onDone={done} />}
      {hookFor && <WebhookDialog c={hookFor} onClose={() => setHookFor(null)} onDone={done} />}
      <ConfirmDialog
        open={!!removing}
        title={t('settings.speakrSection.remove', { removingName: removing?.name })}
        body={t('settings.speakrSection.mailrForgetsTheseRecordingsAnd')}
        confirmLabel={t('common.remove')}
        danger
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          const c = removing!
          setRemoving(null)
          api.del(`/connectors/${c.id}`).then(() => { done(); toast({ message: t('settings.speakrSection.speakrRemoved') }) }).catch((e) => toastError(e))
        }}
      />
    </Section>
  )
}

function TokenHelp() {
  return (
    <p className="muted" style={{ margin: 0, fontSize: 13 }}>
      {tx('settings.speakrSection.inSpeakrOpenAccountSettings', undefined, { b: (c) => <b>{c}</b> })}
    </p>
  )
}

function SpeakrWizard({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [local, setLocal] = useState(true)
  const [test, setTest] = useState<TestOut | null>(null)
  const [busy, setBusy] = useState(false)
  const draft = () => ({ kind: 'speakr', name: '', config: { base_url: url.trim() }, secrets: { token: token.trim() }, data_class: local ? 'local_only' : 'normal' })
  const runTest = async () => {
    setBusy(true)
    try {
      setTest(await api.post<TestOut>('/connectors/test', draft()))
    } catch (e) {
      toastError(e, t('settings.speakrSection.testFailed'))
    } finally {
      setBusy(false)
    }
  }
  const connect = useMutation({
    mutationFn: () => api.post<Connector>('/connectors', draft()),
    onSuccess: () => {
      onDone()
      toast({ message: t('settings.speakrSection.speakrConnectedRecordingsAppearAfter') })
      onClose()
    },
    onError: (e) => toastError(e, t('common.notConnected')),
  })
  const ready = url.trim().length > 8 && token.trim().length > 8
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('settings.speakrSection.connectSpeakr')}
      wide
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn outlined" disabled={!ready || busy} onClick={() => void runTest()}>
            {busy ? <Spinner small /> : null}{' '}{t('common.test')}
          </button>
          <button type="button" className="btn filled" disabled={!ready || !test?.ok || connect.isPending} onClick={() => connect.mutate()}>
            {t('common.connect')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="field">
          <label htmlFor="spk-url">{t('settings.speakrSection.speakrAddress')}</label>
          <input id="spk-url" className="input" placeholder="https://speakr.example.org" value={url}
            onChange={(e) => { setUrl(e.target.value); setTest(null) }} autoComplete="url" />
        </div>
        <div className="field">
          <label htmlFor="spk-token">{t('settings.speakrSection.apiToken')}</label>
          <input id="spk-token" className="input" type="password" autoComplete="off" value={token}
            onChange={(e) => { setToken(e.target.value); setTest(null) }} />
        </div>
        <TokenHelp />
        {test && (
          <div className={`banner ${test.ok ? '' : 'error'}`} role="status">
            {test.ok ? <CheckCircle2 /> : <AlertTriangle />}
            <span>{test.detail}</span>
          </div>
        )}
        {!!test?.warnings?.length && (
          <ul className="muted" style={{ margin: 0, paddingInlineStart: 18, fontSize: 13 }} data-testid="speakr-scope-warnings">
            {test.warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        )}
        <Check checked={local} onChange={setLocal} label={t('settings.speakrSection.keepRecordingsAwayFromHosted')}
          desc={t('settings.speakrSection.onByDefaultRecordingsHold')} />
      </div>
    </Dialog>
  )
}

function TokenDialog({ c, onClose, onDone }: { c: Connector; onClose: () => void; onDone: () => void }) {
  const [token, setToken] = useState('')
  const save = useMutation({
    mutationFn: () => api.patch(`/connectors/${c.id}`, { secrets: { token: token.trim() } }),
    onSuccess: () => {
      onDone()
      toast({ message: t('settings.speakrSection.tokenSavedSyncingAgain') })
      onClose()
    },
    onError: (e) => toastError(e, t('settings.speakrSection.tokenNotSaved')),
  })
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('settings.speakrSection.newSpeakrToken')}
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn filled" disabled={token.trim().length < 8 || save.isPending} onClick={() => save.mutate()}>{t('common.save')}</button>
        </>
      }
    >
      <div className="stack">
        <div className="field">
          <label htmlFor="spk-new-token">{t('settings.speakrSection.apiToken')}</label>
          <input id="spk-new-token" className="input" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
        </div>
        <TokenHelp />
      </div>
    </Dialog>
  )
}

// ACT speakr.sync (a Speakr webhook queues the same sync), ACT speakr.connect (Get updates instantly)
function WebhookDialog({ c, onClose, onDone }: { c: Connector; onClose: () => void; onDone: () => void }) {
  const [secret, setSecret] = useState('')
  const [manual, setManual] = useState(false)
  const setUp = useMutation({
    mutationFn: () => api.post<WebhookOn>(`/speakr/connectors/${c.id}/webhook`),
    onSuccess: () => {
      onDone()
      toast({ message: t('settings.speakrSection.instantUpdatesOnUseSend') })
      onClose()
    },
    onError: (e) => toastError(e, t('settings.speakrSection.notSetUp')),
  })
  const save = useMutation({
    mutationFn: () => api.patch<SpeakrConnector>(`/connectors/${c.id}`, { secrets: { webhook_secret: secret.trim() } }),
    onSuccess: (out) => {
      onDone()
      const url = out.speakr?.webhook_path ? `${window.location.origin}${out.speakr.webhook_path}` : ''
      toast({ message: url ? t('settings.speakrSection.webhookOnInSpeakrSet', { url }) : t('settings.speakrSection.webhookOn') })
      onClose()
    },
    onError: (e) => toastError(e, t('common.notSaved')),
  })
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('settings.speakrSection.instantUpdatesFromSpeakr')}
      wide
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
          {manual ? (
            <button type="button" className="btn filled" disabled={secret.trim().length < 8 || save.isPending} onClick={() => save.mutate()}>{t('settings.speakrSection.turnOn')}</button>
          ) : (
            <button type="button" className="btn filled" disabled={setUp.isPending} onClick={() => setUp.mutate()}>
              {setUp.isPending ? <Spinner small /> : null}{' '}{t('settings.speakrSection.setUpInSpeakr')}
            </button>
          )}
        </>
      }
    >
      <div className="stack">
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          {t('settings.speakrSection.withoutThisSpeakrIsChecked', { pollNote: pollNote((c as SpeakrConnector).speakr?.webhook_v2) })}
        </p>
        {manual ? (
          <>
            <ol style={{ margin: 0, paddingInlineStart: 18, fontSize: 13 }}>
              <li>{tx('settings.speakrSection.inSpeakrOpenAccountSettings2', undefined, { b: (c) => <b>{c}</b> })}</li>
              <li>{t('settings.speakrSection.pasteTheSecretSpeakrShows')}</li>
            </ol>
            <div className="field">
              <label htmlFor="spk-hook-secret">{t('settings.speakrSection.webhookSecret')}</label>
              <input id="spk-hook-secret" className="input" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} />
            </div>
          </>
        ) : (
          <p style={{ margin: 0, fontSize: 13 }}>
            {t('settings.speakrSection.setUpInSpeakrCreates')}{' '}
            <button type="button" className="btn text sm" style={{ padding: 0, height: 'auto' }} onClick={() => setManual(true)}>{t('settings.speakrSection.iMadeTheWebhookIn')}</button>
          </p>
        )}
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          {t('settings.speakrSection.speakrMustReachMailrWithout')}
        </p>
      </div>
    </Dialog>
  )
}
