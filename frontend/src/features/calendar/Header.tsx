// The calendar header: Today, ‹ ›, the period title (a click opens a month picker), the view
// switcher, search (`/`), the zone chip (display zone and a second zone), Find a time, settings,
// and the sync status ("Synced", or "Synced 4 min ago ⟳").

import { CalendarSearch, ChevronDown, ChevronLeft, ChevronRight, Globe, Menu, RefreshCw } from 'lucide-react'
import { useMemo, useState } from 'react'
import { MenuItem, Popover } from '../../components/Popover'
import { formatDate, isoDate, MONTH_SHORT, parseISODate, zoneAbbr, type ISODate } from '../../lib/dates'
import type { CalendarView } from '../../lib/routes'
import { useCal } from './context'
import { isoWeek } from './geometry'
import { useSavedView } from './prefs'
import { useCalUI } from './store'
import { CalendarMoreMenu } from './Subscriptions'
import { t } from '../../i18n'

export const VIEW_LABELS: { view: CalendarView; label: string; short: string; key: string }[] = [
  { view: 'today', label: t('common.today'), short: t('common.today'), key: 'h' },
  { view: 'day', label: t('common.day'), short: t('common.day'), key: 'd' },
  { view: 'ndays', label: t('common.n3Days'), short: '3d', key: 'x' },
  { view: 'workweek', label: t('common.workWeek'), short: t('common.work'), key: 'o' },
  { view: 'week', label: t('common.week'), short: t('common.week'), key: 'w' },
  { view: 'month', label: t('common.month'), short: t('common.month'), key: 'm' },
  { view: 'schedule', label: t('common.schedule'), short: t('common.list'), key: 'a' },
]

const ZONES = ['UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Lisbon', 'Europe/Paris', 'Europe/Berlin', 'Asia/Karachi', 'Asia/Kolkata', 'Asia/Tokyo', 'Australia/Sydney']

export interface HeaderProps {
  title: string
  days: ISODate[]
  onPrev: () => void
  onNext: () => void
  onToday: () => void
  syncText: string | null
  syncBehind: boolean
  onSync: () => void
  syncing: boolean
}

export function Header(p: HeaderProps) {
  const cal = useCal()
  const saved = useSavedView()
  const [picker, setPicker] = useState<HTMLElement | null>(null)
  const [zoneMenu, setZoneMenu] = useState<HTMLElement | null>(null)
  const [viewMenu, setViewMenu] = useState<HTMLElement | null>(null)
  const nLabel = t('calendar.header.days', { count: saved.ndays })
  const views = VIEW_LABELS.map((v) => (v.view === 'ndays' ? { ...v, label: nLabel } : v))
  const current = views.find((v) => v.view === cal.view)
  return (
    <header className="cal-head" data-testid="cal-header">
      <button
        type="button"
        className="icon-btn cal-left-toggle"
        aria-label={cal.narrow ? t('calendar.header.calendarsAndMiniMonth') : t('calendar.header.showOrHideTheLeft')}
        data-tip={t('calendar.header.leftColumn')}
        onClick={() => (cal.narrow ? useCalUI.getState().set({ drawer: !useCalUI.getState().drawer }) : saved.set({ leftOpen: !saved.leftOpen }))}
      >
        <Menu size={20} />
      </button>
      <button type="button" className="btn outlined cal-today" onClick={p.onToday} title={t('calendar.header.todayT')}>{t('common.today')}</button>
      <div className="cal-arrows">
        <button type="button" className="icon-btn sm" aria-label={t('calendar.header.previousPeriodK')} data-tip={t('calendar.header.previousK')} onClick={p.onPrev}><ChevronLeft size={20} /></button>
        <button type="button" className="icon-btn sm" aria-label={t('calendar.header.nextPeriodJ')} data-tip={t('calendar.header.nextJ')} onClick={p.onNext}><ChevronRight size={20} /></button>
      </div>
      <button type="button" className="cal-title" onClick={(e) => setPicker(e.currentTarget)} aria-haspopup="dialog" data-testid="cal-title">
        {/* On a phone, Today shows its date large on the page: the header keeps only the month picker. */}
        <h1 className={cal.narrow && cal.view === 'today' ? 'sr-only' : undefined}>{p.title}</h1>
        {cal.prefs.weekNumbers && cal.view !== 'month' && cal.view !== 'schedule' && cal.view !== 'today' && <span className="cal-title-wn">W{isoWeek(p.days[0] ?? cal.date)}</span>}
        <ChevronDown size={16} aria-hidden />
      </button>
      {picker && <MonthPicker anchor={picker} onClose={() => setPicker(null)} />}
      <span className="cal-head-spacer" />
      <button type="button" className="chip cal-zone" onClick={(e) => setZoneMenu(e.currentTarget)} aria-haspopup="menu" title={cal.prefs.zone} data-testid="cal-zone">
        <Globe size={14} aria-hidden />
        {zoneAbbr(cal.now, cal.prefs.zone)}
        {cal.prefs.secondaryZone && <span className="muted"> · {zoneAbbr(cal.now, cal.prefs.secondaryZone)}</span>}
      </button>
      {zoneMenu && <ZoneMenu anchor={zoneMenu} onClose={() => setZoneMenu(null)} />}
      <button type="button" className="btn tonal sm cal-findtime" onClick={() => useCalUI.getState().set({ findTime: true })} title={t('calendar.header.findATimeF')}>
        <CalendarSearch size={16} aria-hidden /> <span>{t('common.findATime')}</span>
      </button>
      <div className="cal-views" role="tablist" aria-label={t('common.view')} data-testid="cal-views">
        {views.map((v) => (
          <button key={v.view} type="button" role="tab" aria-selected={v.view === cal.view} className={v.view === cal.view ? 'on' : ''} title={`${v.label} (${v.key})`} onClick={() => cal.go(v.view, cal.date)}>
            {v.label}
          </button>
        ))}
      </div>
      <button type="button" className="chip cal-viewchip" onClick={(e) => setViewMenu(e.currentTarget)} aria-haspopup="menu" aria-label={t('calendar.header.view', { currentLabel: current?.label })}>
        {cal.narrow ? current?.short : current?.label}<ChevronDown size={14} aria-hidden />
      </button>
      <Popover anchor={viewMenu} open={!!viewMenu} onClose={() => setViewMenu(null)} label={t('common.view')}>
        {views.map((v) => (
          <MenuItem key={v.view} checked={v.view === cal.view} shortcut={v.key.toUpperCase()} onSelect={() => { setViewMenu(null); cal.go(v.view, cal.date) }}>{v.label}</MenuItem>
        ))}
        {cal.view === 'week' && (
          <>
            <div className="menu-sep" />
            <MenuItem checked={saved.hideWeekends} onSelect={() => { setViewMenu(null); saved.set({ hideWeekends: !saved.hideWeekends }) }}>{t('calendar.header.hideWeekends')}</MenuItem>
          </>
        )}
        {cal.view === 'ndays' && !cal.narrow && (
          <>
            <div className="menu-sep" />
            {[2, 3, 4, 5, 6, 7].map((n) => (
              <MenuItem key={n} checked={saved.ndays === n} onSelect={() => { setViewMenu(null); saved.set({ ndays: n }) }}>{t('calendar.header.days2', { count: n })}</MenuItem>
            ))}
          </>
        )}
      </Popover>
      <button
        type="button"
        className={`cal-sync${p.syncBehind ? ' behind' : ''}${p.syncing ? ' spinning' : ''}`}
        onClick={p.onSync}
        title={t('calendar.header.syncNowR')}
        data-testid="cal-sync"
        aria-label={t('calendar.header.syncNow', { syncText: p.syncText ?? t('calendar.header.sync') })}
      >
        {p.syncText && <span className="cal-sync-text">{p.syncText}</span>}
        <RefreshCw size={16} aria-hidden />
      </button>
      {/* Import, Subscribe to a calendar link and Calendar settings (Subscriptions.tsx). */}
      <CalendarMoreMenu />
    </header>
  )
}

function MonthPicker({ anchor, onClose }: { anchor: HTMLElement; onClose: () => void }) {
  const cal = useCal()
  const [year, setYear] = useState(parseISODate(cal.date).year)
  const cur = parseISODate(cal.date)
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label={t('calendar.header.goToMonth')} className="cal-pop cal-monthpick">
      <div className="cal-monthpick-head">
        <button type="button" className="icon-btn xs" aria-label={t('calendar.header.previousYear')} onClick={() => setYear(year - 1)}><ChevronLeft size={16} /></button>
        <b>{year}</b>
        <button type="button" className="icon-btn xs" aria-label={t('calendar.header.nextYear')} onClick={() => setYear(year + 1)}><ChevronRight size={16} /></button>
      </div>
      <div className="cal-monthpick-grid">
        {MONTH_SHORT.map((m, i) => (
          <button
            key={m}
            type="button"
            className={`${year === cur.year && i + 1 === cur.month ? 'on' : ''}${isoDate(year, i + 1, 1).slice(0, 7) === cal.today.slice(0, 7) ? ' today' : ''}`}
            onClick={() => {
              onClose()
              const d = isoDate(year, i + 1, Math.min(cur.day, 28))
              cal.go(cal.view, cal.view === 'month' ? isoDate(year, i + 1, 1) : d)
            }}
          >
            {m}
          </button>
        ))}
      </div>
      <button type="button" className="btn text sm cal-monthpick-today" onClick={() => { onClose(); cal.go(cal.view, null) }}>
        {t('calendar.header.today', { date: formatDate(cal.today, 'day') })}
      </button>
    </Popover>
  )
}

function ZoneMenu({ anchor, onClose }: { anchor: HTMLElement; onClose: () => void }) {
  const cal = useCal()
  const saved = useSavedView()
  const [mode, setMode] = useState<'display' | 'secondary'>('display')
  const list = useMemo(() => [...new Set([cal.prefs.homeZone, ...ZONES])], [cal.prefs.homeZone])
  const pick = (z: string | null) => {
    if (mode === 'display') saved.set({ displayZone: z && z !== cal.prefs.homeZone ? z : null })
    else saved.set({ secondaryZone: z })
    onClose()
  }
  const chosen = mode === 'display' ? cal.prefs.zone : cal.prefs.secondaryZone
  return (
    <Popover anchor={anchor} open onClose={onClose} label={t('common.timeZone')} className="cal-zone-pop">
      <div className="cal-seg small" role="tablist">
        <button type="button" role="tab" aria-selected={mode === 'display'} className={mode === 'display' ? 'on' : ''} onClick={() => setMode('display')}>{t('calendar.header.showTimesIn')}</button>
        <button type="button" role="tab" aria-selected={mode === 'secondary'} className={mode === 'secondary' ? 'on' : ''} onClick={() => setMode('secondary')}>{t('calendar.header.secondZone')}</button>
      </div>
      {mode === 'secondary' && <MenuItem checked={!cal.prefs.secondaryZone} onSelect={() => pick(null)}>{t('common.none')}</MenuItem>}
      {list.map((z) => (
        <MenuItem key={z} checked={chosen === z} onSelect={() => pick(z)}>
          {z.replace(/_/g, ' ')}{z === cal.prefs.homeZone ? ' (home)' : ''} <span className="muted">{zoneAbbr(cal.now, z)}</span>
        </MenuItem>
      ))}
    </Popover>
  )
}
