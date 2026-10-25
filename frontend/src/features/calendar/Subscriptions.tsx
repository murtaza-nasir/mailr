// Calendar ⋮ (the header's More menu) and calendar links: import an .ics file into
// a calendar (ImportDialog), and subscribe to a calendar link such as public holidays or a school's
// term dates (backend calendar/webcal.py). A subscribed calendar is read-only; its own menu in the
// calendar list offers Refresh now, how often it refreshes, and Remove.
// ACT calendar.subscribe @web.calendar, ACT calendar.unsubscribe @web.calendar

import { useQueryClient } from '@tanstack/react-query'
import { FileUp, Link2, MoreVertical, Settings } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { MenuItem, Popover } from '../../components/Popover'
import { toast, toastError } from '../../stores/ui'
import { ImportDialog } from '../imports/ImportDialog'
import { useCollections } from './data'
import type { Collection } from './types'
import { t } from '../../i18n'

export const REFRESH_CHOICES = [6, 12, 24, 72, 168]

/** "every 12 hours", "every day", "every week". */
export function refreshText(h: number): string {
  if (h === 24) return t('calendar.subscriptions.everyDay')
  if (h === 168) return t('calendar.subscriptions.everyWeek')
  if (h % 24 === 0) return t('calendar.subscriptions.everyDays', { count: h / 24 })
  return t('calendar.subscriptions.everyHours', { count: h })
}

/** The link's server, for the menu note ("calendarlabs.com"). */
export function linkHost(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

export function CalendarMoreMenu() {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const [open, setOpen] = useState<'import' | 'subscribe' | null>(null)
  const nav = useNavigate()
  const cols = useCollections().data ?? []
  const writable = cols.filter((c) => c.events !== false && !c.read_only && c.calendar_id != null)
  return (
    <>
      <button type="button" className="icon-btn sm cal-menu-btn" aria-label={t('calendar.subscriptions.moreCalendarActions')} data-tip={t('common.more')} aria-haspopup="menu"
        onClick={(e) => setAnchor(e.currentTarget)} data-testid="cal-more">
        <MoreVertical size={18} />
      </button>
      <Popover anchor={anchor} open={!!anchor} onClose={() => setAnchor(null)} placement="bottom-end" label={t('calendar.subscriptions.moreCalendarActions')}>
        <MenuItem icon={<FileUp size={18} />} onSelect={() => { setAnchor(null); setOpen('import') }} disabled={!writable.length}>{t('calendar.subscriptions.importAnIcsFile2')}</MenuItem>
        <MenuItem icon={<Link2 size={18} />} onSelect={() => { setAnchor(null); setOpen('subscribe') }}>{t('calendar.subscriptions.subscribeToACalendarLink2')}</MenuItem>
        <div className="menu-sep" />
        <MenuItem icon={<Settings size={18} />} onSelect={() => { setAnchor(null); nav('/settings/calendar') }}>{t('calendar.subscriptions.calendarSettings')}</MenuItem>
      </Popover>
      {open === 'import' && (
        <ImportDialog kind="ics" title={t('calendar.subscriptions.importAnIcsFile')} onClose={() => setOpen(null)}
          intro={t('calendar.subscriptions.eventsFromACalendarFile')}
          calendars={writable.map((c) => ({ id: c.calendar_id!, name: c.name }))} refresh={[['calendar']]} />
      )}
      {open === 'subscribe' && <SubscribeDialog onClose={() => setOpen(null)} />}
    </>
  )
}

/** "Subscribe to a calendar link…" outside the ⋮ menu: the calendar's empty state. */
export function SubscribeButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className="btn text" onClick={() => setOpen(true)} data-testid="cal-empty-subscribe">
        <Link2 size={18} aria-hidden />{' '}{t('calendar.subscriptions.subscribeToACalendarLink2')}
      </button>
      {open && <SubscribeDialog onClose={() => setOpen(false)} />}
    </>
  )
}

export function SubscribeDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [hours, setHours] = useState(12)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const go = async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await api.post<{ events: number; calendar: Collection }>('/calendar/subscriptions', { url: url.trim(), name: name.trim(), refresh_hours: hours })
      void qc.invalidateQueries({ queryKey: ['calendar'] })
      toast({ message: t('calendar.subscriptions.subscribedToEvents', { count: r.events, calendarName: r.calendar.name }) })
      onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open onClose={onClose} title={t('calendar.subscriptions.subscribeToACalendarLink')} modal
      footer={<><button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
        <button type="button" className="btn filled" disabled={!url.trim() || busy} onClick={() => void go()} data-testid="subscribe-go">{t('calendar.subscriptions.subscribe')}</button></>}>
      <div className="stack" data-testid="subscribe-dialog">
        <p className="muted" style={{ margin: 0 }}>
          {t('calendar.subscriptions.aLinkToACalendar')}
        </p>
        <div className="field">
          <label htmlFor="sub-url">{t('calendar.subscriptions.calendarLinkHttpsOrWebcal')}</label>
          <input id="sub-url" className="input" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/holidays.ics" autoFocus />
        </div>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="sub-name">{t('calendar.subscriptions.nameOptional')}</label>
            <input id="sub-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('calendar.subscriptions.theCalendarsOwnName')} />
          </div>
          <div className="field">
            <label htmlFor="sub-hours">{t('calendar.subscriptions.checkForChanges')}</label>
            <select id="sub-hours" className="select" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              {REFRESH_CHOICES.map((h) => <option key={h} value={h}>{refreshText(h)}</option>)}
            </select>
          </div>
        </div>
        {error && <div className="banner error" role="alert">{error}</div>}
      </div>
    </Dialog>
  )
}

/** The part of a subscribed calendar's menu (LeftColumn CalendarMenu): where it comes from, Refresh now and Remove. */
export function SubscriptionMenuItems({ c, onClose, onRemove }: { c: Collection; onClose: () => void; onRemove: () => void }) {
  const qc = useQueryClient()
  const sub = c.subscription!
  return (
    <>
      <div className="cal-menu-note" data-testid="subscription-note">
        {t('calendar.subscriptions.aCalendarLinkFromRead', { linkHost: linkHost(sub.url), refreshText: refreshText(sub.refresh_hours) })}
      </div>
      <MenuItem onSelect={() => {
        onClose()
        api.post<{ error: string | null }>(`/calendar/subscriptions/${c.connector_id}/refresh`)
          .then((r) => {
            void qc.invalidateQueries({ queryKey: ['calendar'] })
            toast(r?.error ? { message: r.error, error: true } : { message: t('calendar.subscriptions.isUpToDate', { itemName: c.name }) })
          }, (e) => toastError(e, t('common.couldNotRefresh')))
      }}>{t('calendar.subscriptions.refreshNow')}</MenuItem>
      <MenuItem danger onSelect={onRemove}>{t('calendar.subscriptions.remove2')}</MenuItem>
    </>
  )
}

/** "Remove “Holidays”?" for a calendar link. */
export function RemoveSubscription({ c, onClose }: { c: Collection; onClose: () => void }) {
  const qc = useQueryClient()
  return (
    <ConfirmDialog open title={t('calendar.subscriptions.remove', { itemName: c.name })} confirmLabel={t('common.remove')} danger
      body={t('calendar.subscriptions.itsEventsLeaveYourCalendar')}
      onClose={onClose}
      onConfirm={() => {
        onClose()
        api.del(`/calendar/subscriptions/${c.connector_id}`).then(() => {
          void qc.invalidateQueries({ queryKey: ['calendar'] })
          toast({ message: t('calendar.subscriptions.removed', { itemName: c.name }) })
        }, (e) => toastError(e, t('calendar.subscriptions.notRemoved')))
      }} />
  )
}
