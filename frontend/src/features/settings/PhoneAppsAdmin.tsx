// Settings → Admin → Phone apps (backend api/phone_app_routes.py, core/phone_apps.py,
// site/docs/admin/phone-apps.md): the two Android apps this server hands out (mailr and the Notes
// app), the version phones download now, the one kept before it, and a release APK to publish.
// The server reads the version from the APK and refuses an APK of another app. Each app also has an
// oldest version the server answers: the mailr app's from MAILR_ANDROID_MIN_VERSION (shown here), the
// Notes app's set here (core/appversion.py); an older app keeps its changes and asks for the update.
// ACT settings.phone_apps @web.settings, ACT settings.notes_min_version @web.settings

import { Smartphone, Undo2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useApi } from '../../api/hooks'
import { Skeleton } from '../../components/bits'
import { ConfirmDialog } from '../../components/Dialog'
import { formatFullDate, formatSize } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import type { PhoneAppRelease } from '../onboarding/steps'
import { Section } from './common'
import { savedToast } from './save'
import { t, tx } from '../../i18n'

interface Meta {
  version_name?: string | null
  version_code?: number | null
  size?: number | null
  sha256?: string | null
  published_at?: string | null
}

interface Floor {
  min_version: string
  /** "env": MAILR_ANDROID_MIN_VERSION (the mailr app); "setting": set here (the Notes app). */
  source: 'env' | 'setting'
}

interface AdminApp extends PhoneAppRelease {
  previous: Meta | null
  min_version?: Floor
}

interface Overview {
  apps: AdminApp[]
  max_bytes: number
}

const KEY = ['admin', 'phone-apps'] as const

/** "", "1.2" or "1.2.3", as the server takes it (core/appversion.py valid()). */
export function validFloor(v: string): boolean {
  return /^\s*(\d+\.\d+(\.\d+)?)?\s*$/.test(v)
}

/** The oldest version phones may run: read-only for mailr (an environment setting), a field for Notes. */
function FloorRow({ app }: { app: AdminApp }) {
  const qc = useQueryClient()
  const floor = app.min_version
  const saved = floor?.min_version ?? ''
  const [draft, setDraft] = useState<string | null>(null)
  const value = draft ?? saved
  const save = useMutation({
    mutationFn: (v: string) => api.put<Overview>('/admin/phone-apps/notes/min-version', { min_version: v }),
    onSuccess: (d, v) => {
      const was = saved
      qc.setQueryData(KEY, d)
      setDraft(null)
      savedToast(v ? t('settings.phoneAppsAdmin.notesAppsOlderThanNow', { v }) : t('settings.phoneAppsAdmin.everyNotesAppVersionIs'),
        () => save.mutate(was))
    },
    onError: (e) => toastError(e, t('common.notSaved')),
  })
  if (!floor) return null
  if (floor.source === 'env') {
    return (
      <div className="desc muted" data-testid={`phone-app-${app.app}-floor`}>
        {floor.min_version ? <>{tx('settings.phoneAppsAdmin.oldestVersionTheServerAnswers', { minVersion: floor.min_version }, { b: (c) => <b>{c}</b>, code: (c) => <code>{c}</code> })}</> : <>{tx('settings.phoneAppsAdmin.everyVersionIsAnsweredMailr', undefined, { code: (c) => <code>{c}</code> })}</>}
      </div>
    )
  }
  const bad = !validFloor(value)
  const dirty = value.trim() !== saved && !bad && !save.isPending
  const commit = () => dirty && save.mutate(value.trim())
  const id = `floor-${app.app}`
  return (
    <div className="field" style={{ marginTop: 8 }}>
      <label htmlFor={id}>{t('settings.phoneAppsAdmin.oldestVersionTheServerAnswers2')}</label>
      <input
        id={id}
        className="input"
        style={{ maxWidth: 160 }}
        value={value}
        inputMode="decimal"
        placeholder={t('settings.phoneAppsAdmin.noLimit')}
        aria-invalid={bad || undefined}
        aria-describedby={`${id}-hint`}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') commit() }}
        data-testid={id}
      />
      <span className="hint" id={`${id}-hint`}>
        {bad
          ? t('settings.phoneAppsAdmin.writeTheVersionAsNumbers')
          : t('settings.phoneAppsAdmin.anOlderNotesAppKeeps')}
      </span>
    </div>
  )
}

function version(m: Meta | null | undefined): string {
  if (!m?.version_name) return ''
  return `${m.version_name}${m.version_code != null ? ` (${m.version_code})` : ''}`
}

function AppRow({ app }: { app: AdminApp }) {
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [back, setBack] = useState(false)
  const done = (d: Overview) => qc.setQueryData(KEY, d)
  const publish = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData()
      fd.append('file', file)
      return api.post<Overview & { unchanged: boolean }>(`/admin/phone-apps/${app.app}`, fd)
    },
    onSuccess: (d) => {
      done(d)
      const now = d.apps.find((a) => a.app === app.app)
      toast({ message: d.unchanged ? t('settings.phoneAppsAdmin.isAlreadyPublished', { appName: app.name, version: version(now) }) : t('settings.phoneAppsAdmin.published', { appName: app.name, version: version(now) }) })
    },
    onError: (e) => toastError(e, t('settings.phoneAppsAdmin.notPublished')),
  })
  const rollback = useMutation({
    mutationFn: () => api.post<Overview>(`/admin/phone-apps/${app.app}/rollback`),
    onSuccess: (d) => {
      done(d)
      setBack(false)
      toast({ message: t('settings.phoneAppsAdmin.isBackTo', { appName: app.name, version: version(d.apps.find((a) => a.app === app.app)) }) })
    },
    onError: (e) => toastError(e, t('common.notChanged')),
  })
  return (
    <div className="card phone-app-row" data-testid={`phone-app-${app.app}`}>
      <div className="row" style={{ alignItems: 'flex-start', gap: 12 }}>
        <Smartphone size={20} aria-hidden style={{ marginTop: 2 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <b>{app.app === 'mailr' ? t('settings.phoneAppsAdmin.mailrApp') : t('settings.phoneAppsAdmin.notesApp')}</b>
          {app.on_server && app.version_name ? (
            <div className="desc" data-testid={`phone-app-${app.app}-current`}>
              {tx('settings.phoneAppsAdmin.phonesDownloadVersion', { version: version(app), size: formatSize(app.size), value: app.published_at ? t('settings.phoneAppsAdmin.published2', { fullDate: formatFullDate(app.published_at) }) : '' }, { b: (c) => <b>{c}</b> })}
              {app.sha256 && (
                <div className="muted" style={{ wordBreak: 'break-all', fontSize: 12 }}>
                  {tx('settings.phoneAppsAdmin.sha256', { sha256: app.sha256 }, { code: (c) => <code>{c}</code> })}
                </div>
              )}
            </div>
          ) : (
            <div className="desc">
              {app.on_server ? t('settings.phoneAppsAdmin.thisServerHoldsAnApk') : t('settings.phoneAppsAdmin.thisServerHoldsNoApk')}
              {!app.on_server && <a href={app.url} target="_blank" rel="noreferrer">{/github\.com\/.+\/releases/.test(app.url) ? t('settings.phoneAppsAdmin.theLatestReleasePage') : app.url.replace(/^https?:\/\//, '')}</a>}
              {!app.on_server && '.'}
            </div>
          )}
          {app.previous?.version_name && (
            <div className="desc muted" data-testid={`phone-app-${app.app}-previous`}>
              {t('settings.phoneAppsAdmin.keptAsTheFallback', { version: version(app.previous) })}
            </div>
          )}
          <FloorRow app={app} />
        </div>
      </div>
      <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <input
          ref={input}
          type="file"
          accept=".apk,application/vnd.android.package-archive"
          hidden
          aria-label={t('settings.phoneAppsAdmin.releaseApkOfTheApp', { value: app.app === 'mailr' ? 'Mailr' : t('common.notes') })}
          onChange={(e) => {
            const f = e.target.files?.[0]
            e.target.value = ''
            if (f) publish.mutate(f)
          }}
        />
        <button type="button" className="btn tonal" disabled={publish.isPending} onClick={() => input.current?.click()}>
          <Upload size={16} /> {publish.isPending ? t('settings.phoneAppsAdmin.publishing') : t('settings.phoneAppsAdmin.publishAnApk')}
        </button>
        {app.previous?.version_name && (
          <button type="button" className="btn text" onClick={() => setBack(true)}>
            <Undo2 size={16} />{' '}{t('settings.phoneAppsAdmin.goBackTo2', { versionName: app.previous.version_name })}
          </button>
        )}
      </div>
      <ConfirmDialog
        open={back}
        title={t('settings.phoneAppsAdmin.goBackTo', { appName: app.name, version: version(app.previous) })}
        body={t('settings.phoneAppsAdmin.phonesDownloadThatVersionAgain')}
        confirmLabel={t('settings.phoneAppsAdmin.goBack')}
        busy={rollback.isPending}
        onConfirm={() => rollback.mutate()}
        onClose={() => setBack(false)}
      />
    </div>
  )
}

export function PhoneAppsSection() {
  const q = useApi<Overview>([...KEY], '/admin/phone-apps')
  return (
    <Section
      id="phone-apps"
      title={t('settings.phoneAppsAdmin.phoneApps')}
      desc={
        <>
          {tx('settings.phoneAppsAdmin.theAndroidAppsPhonesDownload', undefined, { a: (c) => <a href="/get-app">{c}</a>, code: (c) => <code>{c}</code> })}
        </>
      }
    >
      {q.isLoading ? <Skeleton height={60} /> : (q.data?.apps ?? []).map((a) => <AppRow key={a.app} app={a} />)}
      <PushSetupPanel />
    </Section>
  )
}

interface PushSetup {
  server: { url: string; host: string } | null
  people: { phones: number; push: number; no_push: number }
  servers: { host: string; people: number; own: boolean }[]
}

function people(n: number): string {
  return t('settings.phoneAppsAdmin.people', { count: n })
}

/** What a member does to get instant notifications on Android, and where phones push through now
 *  (backend GET /admin/push-setup). ACT admin.push_setup @web.settings */
export function PushSetupPanel() {
  const q = useApi<PushSetup>(['admin', 'push-setup'], '/admin/push-setup')
  const d = q.data
  return (
    <div className="push-setup" data-testid="push-setup">
      <h3 style={{ margin: '20px 0 4px', fontSize: 15 }}>{t('settings.phoneAppsAdmin.instantNotificationsOnPhones')}</h3>
      {!d ? <Skeleton height={96} /> : (
        <>
          <p className="muted" style={{ margin: '0 0 8px', fontSize: 13 }}>{t('settings.phoneAppsAdmin.whatANewMemberDoes')}</p>
          <ol className="push-steps" data-testid="push-steps">
            <li>
              {tx('settings.phoneAppsAdmin.installNtfyAFreeNotification', { value: d.server ? t('settings.phoneAppsAdmin.pushThenWorksAtOnce') : t('settings.phoneAppsAdmin.nothingToTypePushWorks') }, { b: (c) => <b>{c}</b> })}
            </li>
            {d.server && (
              <li>
                {tx('settings.phoneAppsAdmin.pointNtfyAtYourServer', { url: d.server.url }, { b: (c) => <b>{c}</b>, code: (c) => <code>{c}</code> })}
              </li>
            )}
            <li>
              {tx('settings.phoneAppsAdmin.withoutNtfyTheAppChecks', undefined, { b: (c) => <b>{c}</b> })}
            </li>
          </ol>
          <p className="muted" style={{ margin: '8px 0', fontSize: 13 }}>
            {t('settings.phoneAppsAdmin.notificationsAreEncryptedForEach', { value: d.server ? t('settings.phoneAppsAdmin.setByMailrNtfyUrl') : t('settings.phoneAppsAdmin.toRunYourOwnNtfy') })}
          </p>
          <div className="table-wrap">
            <table className="table" aria-label={t('settings.phoneAppsAdmin.wherePhonesPushThrough')}>
              <tbody>
                <tr><td>{t('settings.phoneAppsAdmin.signedInOnAnAndroid')}</td><td data-testid="push-phones">{people(d.people.phones)}</td></tr>
                {d.servers.map((s) => (
                  <tr key={s.host}>
                    <td>{t('settings.phoneAppsAdmin.pushThrough', { host: s.host, value: s.own ? t('settings.phoneAppsAdmin.yourServer') : '' })}</td>
                    <td>{people(s.people)}</td>
                  </tr>
                ))}
                <tr><td>{t('settings.phoneAppsAdmin.noPushAppCheckedEvery')}</td><td data-testid="push-none">{people(d.people.no_push)}</td></tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
