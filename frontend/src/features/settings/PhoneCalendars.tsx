// Settings → Calendar → Phone calendars: the calendars on the user's phones that send busy times
// (backend calendar/phonecal.py). Each shows what the phone sends (busy times only, or with titles
// and places), its device, how fresh it is and how many busy times mailr holds; Remove deletes them
// from mailr (the phone sends them again unless its setting changes).
// ACT calendar.phone_list @web.settings, ACT calendar.phone_remove @web.settings

import { Smartphone, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { qk, useApiList } from '../../api/hooks'
import { LoadFailed, Skeleton, failedEmpty } from '../../components/bits'
import { ConfirmDialog } from '../../components/Dialog'
import { phoneFreshness } from '../calendar/phone'
import { toast, toastError } from '../../stores/ui'
import { Section } from './common'
import { t } from '../../i18n'

export interface PhoneCalendar {
  key: string
  name: string
  color: string | null
  device: string
  synced_at: string | null
  count: number
  stale?: boolean
  /** The phone sends each event's title and place too ("Show and send details"). */
  details?: boolean
}

export function PhoneCalendarsSection() {
  const qc = useQueryClient()
  const list = useApiList<PhoneCalendar>(qk.calendar.phoneCalendars, '/calendar/phone-calendars')
  const [removing, setRemoving] = useState<PhoneCalendar | null>(null)
  const items = list.data ?? []
  return (
    <Section title={t('settings.phoneCalendars.title')} desc={t('settings.phoneCalendars.desc')} id="phone-calendars">
      {list.isLoading ? (
        <Skeleton height={60} />
      ) : failedEmpty(list) ? (
        <LoadFailed what={t('settings.phoneCalendars.title')} q={list} />
      ) : items.length === 0 ? (
        <div className="card row" data-testid="phone-calendars-none">
          <Smartphone size={18} /> <span className="muted">{t('settings.phoneCalendars.none')}</span>
        </div>
      ) : (
        <div className="card-list" data-testid="phone-calendars">
          {items.map((c) => {
            const f = phoneFreshness({ phone: { key: c.key, device: c.device, synced_at: c.synced_at, stale: !!c.stale } })
            return (
              <div key={c.key} className="card" data-testid="phone-calendar">
                <div className="card-row">
                  <span className="cal-dot" style={{ background: c.color || 'var(--text-3)', width: 12, height: 12, borderRadius: 6, flex: 'none' }} aria-hidden />
                  <div className="grow">
                    <div className="title">{c.name}</div>
                    <div className="sub" data-testid="phone-calendar-sends">
                      {c.details ? t('settings.phoneCalendars.withDetails') : t('settings.phoneCalendars.busyOnly')}
                    </div>
                    <div className={`sub cal-phone-fresh${f?.stale ? ' stale' : ''}`}>
                      {f?.text}{f?.stale && <>{' · '}{t('calendar.phone.stale')}</>}
                      {' · '}{t('settings.phoneCalendars.busyTimes', { count: c.count })}
                    </div>
                  </div>
                  <button type="button" className="icon-btn" aria-label={t('settings.phoneCalendars.removeName', { name: c.name })} data-tip={t('common.remove')} onClick={() => setRemoving(c)}>
                    <Trash2 />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
      <ConfirmDialog
        open={!!removing}
        title={t('settings.phoneCalendars.removeTitle', { name: removing?.name })}
        body={t('settings.phoneCalendars.removeBody')}
        confirmLabel={t('common.remove')}
        danger
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          const c = removing!
          setRemoving(null)
          api
            .del(`/calendar/phone-calendars/${c.key}`)
            .then(() => {
              void qc.invalidateQueries({ queryKey: qk.calendar.all })
              toast({ message: t('settings.phoneCalendars.removed', { name: c.name }) })
            })
            .catch((e) => toastError(e, t('settings.phoneCalendars.notRemoved')))
        }}
      />
    </Section>
  )
}
