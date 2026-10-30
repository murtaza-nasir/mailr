import { CalendarDays, CheckCircle2, AlertTriangle, Copy, Lock, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useApiList } from '../../api/hooks'
import type { Connector, TestResult } from '../../api/types'
import { Skeleton, LoadFailed, Switch, failedEmpty } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { Section } from './common'
import { CollectionsSection } from './CalendarCollections'
import { MigrateDialog } from './CalendarMigration'
import { PhoneAppsSection } from './PhoneApps'
import { PhoneCalendarsSection } from './PhoneCalendars'
import { t } from '../../i18n'

/** Settings → Calendar & tasks → the calendar and task accounts, their calendars, and phone access. */
export function CalendarAccountsSection() {
  const qc = useQueryClient()
  const conns = useApiList<Connector>(['connectors'], '/connectors')
  const [params, setParams] = useSearchParams()
  // ?add=1 (the empty Calendar and Tasks apps' "Connect a calendar") opens the connect form.
  const [adding, setAdding] = useState(params.get('add') === '1')
  useEffect(() => {
    if (params.get('add') === '1') {
      params.delete('add')
      setParams(params, { replace: true })
    }
  }, [params, setParams])
  const [removing, setRemoving] = useState<Connector | null>(null)
  const [copying, setCopying] = useState<Connector | null>(null)
  const caldav = (conns.data ?? []).filter((c) => c.kind === 'caldav')
  // The copy into the mailr calendar (calendar/migrate.py) needs the built-in calendar, shown.
  const canCopy = caldav.some((c) => c.builtin && c.enabled)
  return (
    <>
      <Section
        title={t('settings.calendarTab.calendarAndTaskAccounts')}
        desc={t('settings.calendarTab.connectACaldavServerDavis')}
        actions={
          <button type="button" className="btn filled" onClick={() => setAdding(true)}>
            <Plus />{' '}{t('settings.calendarTab.connectCaldav')}
          </button>
        }
      >
        {conns.isLoading ? (
          <Skeleton height={60} />
        ) : failedEmpty(conns) ? (
          <LoadFailed what={t('settings.calendarTab.calendarAccounts')} q={conns} />
        ) : caldav.length === 0 ? (
          <div className="card row">
            <CalendarDays size={18} /> <span className="muted">{t('settings.calendarTab.noCalendarConnected')}</span>
          </div>
        ) : (
          <div className="card-list">
            {caldav.map((c) =>
              c.builtin ? (
                <BuiltinCard key={c.id} c={c} />
              ) : (
              <div key={c.id} className="card">
                <div className="card-row">
                  <CalendarDays />
                  <div className="grow">
                    <div className="title">{c.name}</div>
                    <div className="sub">
                      {hostOf(String((c.config as { url?: string }).url ?? ''))}
                      {c.last_sync_at ? t('settings.calendarTab.synced', { relative: formatRelative(c.last_sync_at) }) : ''}
                    </div>
                    {c.status === 'error' && <div className="sub" style={{ color: 'var(--danger)' }}>{c.status_detail}</div>}
                  </div>
                  {canCopy && (
                    <button type="button" className="btn outlined sm" onClick={() => setCopying(c)} data-testid="migrate-open">
                      <Copy size={16} />{' '}{t('settings.calendarTab.copyIntoMyMailrCalendar')}
                    </button>
                  )}
                  <button type="button" className="icon-btn" aria-label={t('common.remove')} data-tip={t('common.remove')} onClick={() => setRemoving(c)}>
                    <Trash2 />
                  </button>
                </div>
              </div>
              ),
            )}
          </div>
        )}
      </Section>
      {caldav.length > 0 && <CollectionsSection />}
      <PhoneCalendarsSection />
      {caldav.some((c) => c.builtin) && <PhoneAppsSection />}
      {adding && <CaldavDialog onClose={() => setAdding(false)} />}
      {copying && <MigrateDialog conn={copying} onClose={() => setCopying(null)} />}
      <ConfirmDialog
        open={!!removing}
        title={t('settings.calendarTab.disconnect', { removingName: removing?.name })}
        body={t('settings.calendarTab.eventsAndTasksStayOn')}
        confirmLabel={t('settings.calendarTab.disconnect2')}
        danger
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          const c = removing!
          setRemoving(null)
          api
            .del(`/connectors/${c.id}`)
            .then(() => {
              void qc.invalidateQueries({ queryKey: ['connectors'] })
              void qc.invalidateQueries({ queryKey: ['calendar'] })
            })
            .catch((e) => toastError(e))
        }}
      />
    </>
  )
}

/** The built-in "mailr calendar" (calendar/builtin.py): no address, no Remove; a switch hides it.
 *  ACT calendar.builtin_hide @web.settings */
function BuiltinCard({ c }: { c: Connector }) {
  const qc = useQueryClient()
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.patch(`/connectors/${c.id}`, { enabled }),
    onSuccess: (_d, enabled) => {
      void qc.invalidateQueries({ queryKey: ['connectors'] })
      void qc.invalidateQueries({ queryKey: ['calendar'] })
      void qc.invalidateQueries({ queryKey: ['tasks'] })
      toast({ message: enabled ? t('settings.calendarTab.mailrCalendarShown') : t('settings.calendarTab.mailrCalendarHidden') })
    },
    onError: (e) => toastError(e),
  })
  return (
    <div className="card" data-testid="builtin-calendar">
      <div className="card-row">
        <CalendarDays />
        <div className="grow">
          <div className="title">
            {t('settings.calendarTab.mailrCalendar')}{' '}
            <span className="pill" style={{ marginInlineStart: 6, gap: 4 }}>
              <Lock size={12} aria-hidden />{' '}{t('settings.calendarTab.onThisServer')}
            </span>
          </div>
          <div className="sub">
            {t('settings.calendarTab.yourCalendarAndTaskList', { value: c.enabled ? (c.last_sync_at ? t('settings.calendarTab.synced2', { relative: formatRelative(c.last_sync_at) }) : '') : t('settings.calendarTab.hiddenItsEventsAndTasks') })}
          </div>
          {c.enabled && c.status === 'error' && <div className="sub" style={{ color: 'var(--danger)' }}>{c.status_detail}</div>}
        </div>
        <Switch checked={c.enabled} disabled={toggle.isPending} onChange={(v) => toggle.mutate(v)} label={t('settings.calendarTab.showTheMailrCalendar')} />
      </div>
    </div>
  )
}

/** A calendar server's address as its host (caldav.fastmail.com), for the account's card. */
export function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** The name a calendar account gets when none is typed: its server's host without the usual
 *  calendar prefix (caldav.fastmail.com → fastmail.com). */
function serverName(url: string): string {
  try {
    return new URL(url.trim()).hostname.replace(/^(caldav|carddav|dav|cal|calendar|www)\./, '')
  } catch {
    return ''
  }
}

/** Connect a CalDAV account (Settings → Calendar & tasks, and the setup guide's calendar step,
 *  which may fill in the calendar server the admin prepared). */
export function CaldavDialog({ onClose, initial, onConnected }: { onClose: () => void; initial?: { name?: string; url?: string; username?: string }; onConnected?: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(initial?.name ?? '')
  const [url, setUrl] = useState(initial?.url ?? '')
  // An empty name takes the server's name (fastmail.com), so the account is known by more than "Calendar".
  const named = name.trim() || serverName(url) || t('common.calendar')
  const [username, setUsername] = useState(initial?.username ?? '')
  const [password, setPassword] = useState('')
  const [test, setTest] = useState<TestResult | null>(null)
  const draft = () => ({ kind: 'caldav', name: named, mode: 'mirror', data_class: 'normal', config: { url: url.trim(), username: username.trim() }, secrets: { password } })
  const testM = useMutation({
    mutationFn: () => api.post<TestResult>('/connectors/test', draft() as unknown as Record<string, unknown>),
    onSuccess: setTest,
    onError: (e) => setTest({ ok: false, detail: (e as Error).message, folders: [], identities: [], extensions: [] }),
  })
  const create = useMutation({
    mutationFn: () => api.post('/connectors', draft() as unknown as Record<string, unknown>),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['connectors'] })
      void qc.invalidateQueries({ queryKey: ['calendar'] })
      void qc.invalidateQueries({ queryKey: ['tasks'] })
      toast({ message: t('settings.calendarTab.calendarConnected') })
      onConnected?.()
      onClose()
    },
    onError: (e) => toastError(e, t('common.notConnected')),
  })
  const ready = url.trim() && username.trim() && password
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('settings.calendarTab.connectACaldavAccount')}
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="btn outlined" disabled={!ready || testM.isPending} onClick={() => testM.mutate()}>
            {t('common.test')}
          </button>
          <button type="button" className="btn filled" disabled={!ready || !test?.ok || create.isPending} onClick={() => create.mutate()}>
            {t('common.connect')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="field">
          <label htmlFor="cd-name">{t('common.name')}</label>
          <input id="cd-name" className="input" value={name} placeholder={named} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cd-url">{t('settings.calendarTab.caldavUrl')}</label>
          <input id="cd-url" className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://cal.example.com/dav/" />
          <span className="hint">{t('settings.calendarTab.theServerRootOrYour')}</span>
        </div>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="cd-user">{t('common.username')}</label>
            <input id="cd-user" className="input" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="cd-pass">{t('common.password')}</label>
            <input id="cd-pass" className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
        </div>
        {test && (
          <div className={`banner ${test.ok ? '' : 'error'}`}>
            {test.ok ? <CheckCircle2 /> : <AlertTriangle />}
            <span>{test.ok ? t('settings.calendarTab.connectedFoundCalendars', { count: test.folders.length, value: test.folders.length ? `: ${test.folders.map((f) => f.name).join(', ')}` : '' }) : test.detail}</span>
          </div>
        )}
      </div>
    </Dialog>
  )
}
