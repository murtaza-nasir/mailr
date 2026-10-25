import { AlertTriangle, Copy, Download, KeyRound, Smartphone, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useApi } from '../../api/hooks'
import { Skeleton } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { formatFullDate } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { Section } from './common'
import { t, tx } from '../../i18n'

/** GET /me/dav (calendar/phones.py, api/phone_routes.py). */
export interface DavInfo {
  available: boolean
  reason: string
  instance: boolean
  url: string
  server: string
  username: string
  passwords: { id: string; name: string; created_at: string | null; paused: boolean }[]
  scheduling_note: string
  max: number
}

interface Made {
  id: string
  name: string
  password: string
  username: string
  server: string
  url: string
  profile: string
  profile_name: string
}

function copy(text: string, what: string) {
  void navigator.clipboard.writeText(text).then(() => toast({ message: t('settings.phoneApps.copied', { what }) }))
}

function downloadProfile(m: Made) {
  const bytes = Uint8Array.from(atob(m.profile), (c) => c.charCodeAt(0))
  const blob = new Blob([bytes], { type: 'application/x-apple-aspen-config' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = m.profile_name
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000)
}

function Field({ label, value, testid }: { label: string; value: string; testid?: string }) {
  return (
    <div className="phone-field">
      <span className="muted">{label}</span>
      <code data-testid={testid}>{value}</code>
      <button type="button" className="icon-btn sm" aria-label={t('settings.phoneApps.copy', { label: label.toLowerCase() })} data-tip={t('common.copy')} onClick={() => copy(value, label)}>
        <Copy size={16} />
      </button>
    </div>
  )
}

/** Settings → Calendar & tasks → "Phones and other apps": DAV-only app passwords for the built-in
 *  calendars and contacts (one per device, shown once, with an iPhone and Mac profile).
 *  ACT settings.phone_passwords @web.settings */
export function PhoneAppsSection() {
  const qc = useQueryClient()
  const q = useApi<DavInfo>(['me', 'dav'], '/me/dav')
  const [naming, setNaming] = useState(false)
  const [made, setMade] = useState<Made | null>(null)
  const [deleting, setDeleting] = useState<DavInfo['passwords'][number] | null>(null)
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/me/dav/app-passwords/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['me', 'dav'] })
      toast({ message: t('settings.phoneApps.appPasswordDeletedTheDevice') })
    },
    onError: (e) => toastError(e, t('settings.phoneApps.notDeleted')),
  })
  const d = q.data
  // Nothing to say to someone whose server does not offer it and who has no passwords left.
  if (q.isSuccess && d && !d.instance && d.passwords.length === 0) return null
  return (
    <Section
      title={t('settings.phoneApps.phonesAndOtherApps')}
      desc={t('settings.phoneApps.useYourMailrCalendarsTask')}
      actions={
        d?.available ? (
          <button type="button" className="btn filled" onClick={() => setNaming(true)} data-testid="phone-new">
            <KeyRound />{' '}{t('settings.phoneApps.newAppPassword')}
          </button>
        ) : undefined
      }
    >
      {q.isLoading ? (
        <Skeleton height={80} />
      ) : q.isError || !d ? (
        <p className="muted">{t('settings.phoneApps.phoneAccessCouldNotBe')}</p>
      ) : (
        <div className="stack" data-testid="phone-apps">
          {!d.available && <p className="muted">{d.reason}</p>}
          {d.available && (
            <div className="card">
              <div className="card-row">
                <Smartphone />
                <div className="grow phone-fields">
                  <Field label={t('settings.phoneApps.server')} value={d.server} testid="phone-server" />
                  <Field label={t('common.username')} value={d.username} testid="phone-username" />
                  <span className="muted" style={{ fontSize: 12.5 }}>
                    {t('settings.phoneApps.passwordAnAppPasswordFrom')}
                  </span>
                </div>
              </div>
            </div>
          )}
          {d.available && (
            <div className="banner" role="note">
              <AlertTriangle />
              <span>{d.scheduling_note}</span>
            </div>
          )}
          {d.passwords.length > 0 && (
            <div className="card-list" data-testid="phone-passwords">
              {d.passwords.map((p) => (
                <div key={p.id} className="card">
                  <div className="card-row">
                    <KeyRound />
                    <div className="grow">
                      <div className="title">
                        {p.name}
                        {p.paused && (
                          <span className="pill warn" style={{ marginInlineStart: 8 }} data-tip={t('settings.phoneApps.itWorksAgainWhenPhone')}>
                            {t('common.paused')}
                          </span>
                        )}
                      </div>
                      <div className="sub">{t('settings.phoneApps.made', { fullDate: formatFullDate(p.created_at) })}</div>
                    </div>
                    <button type="button" className="icon-btn" aria-label={t('settings.phoneApps.deleteTheAppPassword', { itemName: p.name })} data-tip={t('common.delete2')} onClick={() => setDeleting(p)}>
                      <Trash2 />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {naming && (
        <NewPasswordDialog
          onClose={() => setNaming(false)}
          onMade={(m) => {
            setNaming(false)
            setMade(m)
            void qc.invalidateQueries({ queryKey: ['me', 'dav'] })
          }}
        />
      )}
      {made && <MadeDialog made={made} note={d?.scheduling_note ?? ''} onClose={() => setMade(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={t('settings.phoneApps.delete', { deletingName: deleting?.name })}
        body={t('settings.phoneApps.theDeviceThatUsesThis')}
        confirmLabel={t('common.delete2')}
        danger
        busy={del.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          const p = deleting!
          setDeleting(null)
          del.mutate(p.id)
        }}
      />
    </Section>
  )
}

function NewPasswordDialog({ onClose, onMade }: { onClose: () => void; onMade: (m: Made) => void }) {
  const [name, setName] = useState('')
  const make = useMutation({
    mutationFn: () => api.post<Made>('/me/dav/app-passwords', { name: name.trim() }),
    onSuccess: onMade,
    onError: (e) => toastError(e, t('settings.phoneApps.noAppPasswordMade')),
  })
  return (
    <Dialog
      open
      modal
      onClose={onClose}
      title={t('settings.phoneApps.newAppPassword')}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!name.trim() || make.isPending} onClick={() => make.mutate()} data-testid="phone-make">
            {t('settings.phoneApps.makePassword')}
          </button>
        </>
      }
    >
      <form
        className="field"
        onSubmit={(e) => {
          e.preventDefault()
          if (name.trim()) make.mutate()
        }}
      >
        <label htmlFor="phone-name">{t('settings.phoneApps.deviceName')}</label>
        <input id="phone-name" className="input" autoFocus maxLength={60} value={name} onChange={(e) => setName(e.target.value)} placeholder={t('settings.phoneApps.samsIphone')} />
        <span className="hint">{t('settings.phoneApps.soYouCanTellThe')}</span>
      </form>
    </Dialog>
  )
}

function MadeDialog({ made, note, onClose }: { made: Made; note: string; onClose: () => void }) {
  return (
    <Dialog
      open
      modal
      wide
      onClose={onClose}
      title={t('settings.phoneApps.appPasswordFor', { madeName: made.name })}
      footer={
        <button type="button" className="btn filled" onClick={onClose}>
          {t('common.done')}
        </button>
      }
    >
      <div className="stack" data-testid="phone-made">
        <p style={{ margin: 0 }}>{t('settings.phoneApps.enterTheseInTheApp')}</p>
        <div className="phone-fields">
          <Field label={t('settings.phoneApps.server')} value={made.server} />
          <Field label={t('common.username')} value={made.username} />
          <Field label={t('common.password')} value={made.password} testid="phone-secret" />
        </div>
        <div className="phone-howto">
          <h3>{t('settings.phoneApps.iphoneIpadOrMac')}</h3>
          <p>
            {tx('settings.phoneApps.downloadTheProfileOnThe', undefined, { b: (c) => <b>{c}</b> })}
          </p>
          <button type="button" className="btn tonal" onClick={() => downloadProfile(made)} data-testid="phone-profile">
            <Download />{' '}{t('settings.phoneApps.downloadProfileForIphoneAnd')}
          </button>
          <h3>{t('common.android')}</h3>
          <p>
            {tx('settings.phoneApps.installDavx5ChooseLoginWith', { url: made.url }, { b: (c) => <b>{c}</b>, code: (c) => <code>{c}</code> })}
          </p>
          <h3>{t('settings.phoneApps.thunderbird')}</h3>
          <p>
            {tx('settings.phoneApps.calendarNewCalendarOnThe', { url: made.url }, { b: (c) => <b>{c}</b>, code: (c) => <code>{c}</code> })}
          </p>
        </div>
        <div className="banner" role="note">
          <AlertTriangle />
          <span>{note}</span>
        </div>
      </div>
    </Dialog>
  )
}
