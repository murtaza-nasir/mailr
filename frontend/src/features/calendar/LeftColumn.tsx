// The left column (`[` toggles it): Create (Event, Task), the mini-month, the calendars grouped by
// account (swatch, visibility, lock, a menu), "+ New calendar", and the Tasks section.

import { useQueryClient } from '@tanstack/react-query'
import { CalendarPlus, Check, ChevronLeft, ChevronRight, ListTodo, Lock, MoreVertical, Plus, Users, X } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import { useModalFocus } from '../../hooks/useModalFocus'
import { Dialog, ConfirmDialog } from '../../components/Dialog'
import { MenuItem, Popover } from '../../components/Popover'
import { RemoveSubscription, SubscriptionMenuItems } from './Subscriptions'
import { addMonthsISO, formatDate, monthGrid, parseISODate, startOfDay, startOfMonthISO, weekdayOf, type ISODate } from '../../lib/dates'
import { OFFSETS, offsetLabel } from '../../lib/quickChoices'
import { toast, toastError } from '../../stores/ui'
import { TaskEditor } from '../rail/QuickSheet'
import { displayColour, SWATCHES } from './colour'
import { useCal } from './context'
import { calApi, defaultReminders, SHARE_NOTICES_KEY, useAccounts, useDefaults, useNormalized, useRange, useShareNotices } from './data'
import { exportIcs } from './ics'
import { isoWeek } from './geometry'
import { useSavedView } from './prefs'
import { saveVisibility } from './visibility'
import { useCalUI } from './store'
import { ShareDialog } from './ShareDialog'
import { ownerNote, sharedLabel, sharingLabel, type Collection } from './types'
import { t } from '../../i18n'

const urlOf = (c: Collection) => c.url ?? c.id
const noop = () => {}

export function LeftColumn({ shownDays, onCloseDrawer }: { shownDays: ISODate[]; onCloseDrawer?: () => void }) {
  const cal = useCal()
  // On a narrow screen the column is a modal drawer: focus in, Tab kept inside, Escape closes.
  const ref = useRef<HTMLElement>(null)
  useModalFocus(ref, !!onCloseDrawer, onCloseDrawer ?? noop)
  const [menu, setMenu] = useState<HTMLElement | null>(null)
  const [newTask, setNewTask] = useState(false)
  // A new task is due on the day in view: today when it is shown, else the first day shown.
  const taskDay = shownDays.includes(cal.today) ? cal.today : shownDays[0] ?? cal.today
  return (
    <nav ref={ref} className="cal-left" aria-label={t('calendar.leftColumn.calendarNavigation')} data-testid="cal-left"
      {...(onCloseDrawer ? { role: 'dialog', 'aria-modal': true, 'data-modal-drawer': '' } : {})}>
      <button type="button" className="cal-create" ref={undefined} onClick={(e) => setMenu(e.currentTarget)} aria-haspopup="menu" disabled={cal.readOnly}>
        <Plus aria-hidden />
        <span>{t('common.create')}</span>
      </button>
      <Popover anchor={menu} open={!!menu} onClose={() => setMenu(null)} label={t('common.create')}>
        <MenuItem icon={<CalendarPlus size={18} />} onSelect={() => { setMenu(null); useCalUI.getState().set({ quick: { start: null, end: null, allDay: false, anchor: null } }) }} shortcut="c">{t('common.event')}</MenuItem>
        {/* ACT task.create @web.calendar: the task editor over the calendar, due on the day in view. */}
        <MenuItem icon={<ListTodo size={18} />} onSelect={() => { setMenu(null); setNewTask(true) }}>{t('common.task')}</MenuItem>
      </Popover>
      {newTask && <TaskEditor task={null} seed={{ due: taskDay }} heading={t('common.newTask')} saveLabel={t('common.addTask')} onClose={() => setNewTask(false)} />}
      <MiniMonth shownDays={shownDays} />
      <CalendarList />
      <TaskSection />
    </nav>
  )
}

// ---------- mini-month ----------

function MiniMonth({ shownDays }: { shownDays: ISODate[] }) {
  const cal = useCal()
  const [month, setMonth] = useState<ISODate>(() => startOfMonthISO(cal.date))
  const [followed, setFollowed] = useState(cal.date)
  if (followed !== cal.date) {
    // Follow the main view when it moves to another month.
    setFollowed(cal.date)
    if (startOfMonthISO(cal.date) !== month) setMonth(startOfMonthISO(cal.date))
  }
  const weeks = useMemo(() => monthGrid(month, cal.prefs.weekStart), [month, cal.prefs.weekStart])
  const from = useMemo(() => startOfDay(weeks[0][0], cal.prefs.zone), [weeks, cal.prefs.zone])
  const to = useMemo(() => startOfDay(weeks[weeks.length - 1][6], cal.prefs.zone), [weeks, cal.prefs.zone])
  const q = useRange(from, to)
  const evs = useNormalized(q.data?.events, cal.prefs.zone, cal.collections, cal.hidden)
  const busyDays = useMemo(() => {
    const s = new Set<string>()
    for (const e of evs) for (let d = e.firstDay; d <= e.lastDay && s.size < 400; d = nextDay(d)) s.add(d)
    return s
  }, [evs])
  const shown = new Set(cal.view === 'month' ? [] : shownDays)
  const m = parseISODate(month).month
  return (
    <div className="cal-mini" data-testid="cal-mini">
      <div className="cal-mini-head">
        <span className="cal-mini-title">{formatDate(month, 'monthYear')}</span>
        <button type="button" className="icon-btn xs" aria-label={t('common.previousMonth')} onClick={() => setMonth(addMonthsISO(month, -1))}><ChevronLeft size={18} /></button>
        <button type="button" className="icon-btn xs" aria-label={t('common.nextMonth')} onClick={() => setMonth(addMonthsISO(month, 1))}><ChevronRight size={18} /></button>
      </div>
      <div className={`cal-mini-grid${cal.prefs.weekNumbers ? ' wn' : ''}`} role="group" aria-label={formatDate(month, 'monthYear')}>
        {cal.prefs.weekNumbers && <span />}
        {weeks[0].map((d) => <span key={d} className="cal-mini-wd" aria-hidden>{formatDate(d, 'weekday').slice(0, 1)}</span>)}
        {weeks.map((w) => (
          <MiniWeek key={w[0]} w={w} m={m} shown={shown} busy={busyDays} />
        ))}
      </div>
    </div>
  )
}

function nextDay(d: ISODate): ISODate {
  const { year, month, day } = parseISODate(d)
  return new Date(Date.UTC(year, month - 1, day + 1)).toISOString().slice(0, 10)
}

function MiniWeek({ w, m, shown, busy }: { w: ISODate[]; m: number; shown: Set<string>; busy: Set<string> }) {
  const cal = useCal()
  return (
    <>
      {cal.prefs.weekNumbers && (
        <button type="button" className="cal-mini-wn" onClick={() => cal.go('week', w[0])} aria-label={t('calendar.leftColumn.week', { isoWeek: isoWeek(w[0]) })}>{isoWeek(w[0])}</button>
      )}
      {w.map((d) => {
        const wd = weekdayOf(d)
        const inShown = shown.has(d)
        return (
          <button
            key={d}
            type="button"
            className={`cal-mini-day${parseISODate(d).month === m ? '' : ' out'}${d === cal.today ? ' today' : ''}${d === cal.date ? ' current' : ''}${inShown ? ' shown' : ''}${inShown && !shown.has(prevDayOf(d)) ? ' shown-start' : ''}${inShown && !shown.has(nextDay(d)) ? ' shown-end' : ''}${wd === 0 || wd === 6 ? ' weekend' : ''}`}
            onClick={() => cal.go(cal.view === 'schedule' ? 'schedule' : cal.view, d)}
            aria-label={`${formatDate(d, 'long')}${busy.has(d) ? t('calendar.leftColumn.hasEvents') : ''}`}
            aria-current={d === cal.today ? 'date' : undefined}
          >
            {Number(d.slice(8))}
            {busy.has(d) && <span className="cal-mini-dot" aria-hidden />}
          </button>
        )
      })}
    </>
  )
}

function prevDayOf(d: ISODate): ISODate {
  const { year, month, day } = parseISODate(d)
  return new Date(Date.UTC(year, month - 1, day - 1)).toISOString().slice(0, 10)
}

// ---------- calendars ----------

/** ACT calendar.toggle_visibility @web.calendar, ACT calendar.show_only @web.calendar */
export function useVisibility() {
  const cal = useCal()
  const qc = useQueryClient()
  // The checkbox follows the last click at once; the saves follow, one calendar at a time (visibility.ts).
  const setHidden = (targets: Collection[], hide: boolean, after?: Promise<unknown>) => saveVisibility(qc, targets, hide, after)
  const eventCals = cal.collections.filter((c) => c.events !== false && !c.error)
  return {
    toggle: (c: Collection) => setHidden([c], !cal.hidden.has(urlOf(c))),
    setRange: (cs: Collection[], hide: boolean) => setHidden(cs, hide),
    showOnly: (c: Collection) => {
      useSavedView.getState().set({ beforeShowOnly: [...cal.hidden] })
      // Both on screen at once; the saves hide the others first and end by showing this one.
      void setHidden([c], false, setHidden(eventCals.filter((x) => urlOf(x) !== urlOf(c)), true))
    },
    showAll: () => {
      const before = useSavedView.getState().beforeShowOnly
      useSavedView.getState().set({ beforeShowOnly: null })
      const restoreHidden = new Set(before ?? [])
      void setHidden(eventCals.filter((x) => !restoreHidden.has(urlOf(x))), false)
    },
  }
}

const SHARED = -1

function CalendarList() {
  const cal = useCal()
  const accounts = useAccounts().data ?? []
  const defaults = useDefaults().data
  const vis = useVisibility()
  const lastClicked = useRef<number>(-1)
  const [menu, setMenu] = useState<{ c: Collection; anchor: HTMLElement } | null>(null)
  const [creating, setCreating] = useState(false)
  const list = cal.collections.filter((c) => c.events !== false)
  // Calendars by account; group calendars and calendars people share (shared) last, under "Shared" (key -1).
  // ACT calendar.group_shared @web.calendar, ACT calendar.user_shared @web.calendar
  const groups = useMemo(() => {
    const by = new Map<number, Collection[]>()
    for (const c of list) {
      const k = c.shared ? SHARED : c.connector_id
      by.set(k, [...(by.get(k) ?? []), c])
    }
    return [...by.entries()].sort(([a], [b]) => (a === SHARED ? 1 : 0) - (b === SHARED ? 1 : 0))
  }, [list])
  const flat = groups.flatMap(([, cs]) => cs)
  return (
    <section className="cal-cals" aria-label={t('common.calendars')}>
      <div className="cal-section-head">
        <h2>{t('common.calendars')}</h2>
        <button type="button" className="icon-btn xs" aria-label={t('calendar.leftColumn.newCalendar')} data-tip={t('calendar.leftColumn.newCalendar')} onClick={() => setCreating(true)} disabled={cal.readOnly}>
          <Plus size={18} />
        </button>
      </div>
      <ShareNotices />
      {groups.map(([cid, cs]) => {
        const acct = accounts.find((a) => a.id === cid)
        return (
          <div key={cid} className="cal-acct" data-testid={cid === SHARED ? 'cal-shared' : undefined}>
            {cid === SHARED ? (
              <div className="cal-acct-name">{t('calendar.leftColumn.shared')}</div>
            ) : (
              // An account's heading only tells two accounts apart: by the name it was given, never
              // its sign-in name (a login such as alex.rivera-… read as an error).
              groups.length > 1 && <div className="cal-acct-name" title={acct?.url}>{acct?.name || t('common.account')}</div>
            )}
            {cs.map((c) => {
              const url = urlOf(c)
              const on = !cal.hidden.has(url)
              const colour = displayColour(cal.colourOf(c), cal.dark)
              const idx = flat.indexOf(c)
              return (
                <div key={url} className={`cal-cal${on ? '' : ' off'}`} data-testid="cal-calendar">
                  <label className="cal-cal-check" title="Alt+click: show only this calendar">
                    <input
                      type="checkbox"
                      className="cal-swatch-box"
                      style={{ '--sw': colour } as React.CSSProperties}
                      checked={on}
                      aria-label={t('calendar.leftColumn.show', { itemName: c.name })}
                      onClick={(e) => {
                        if (e.altKey) {
                          e.preventDefault()
                          vis.showOnly(c)
                        } else if (e.shiftKey && lastClicked.current >= 0) {
                          e.preventDefault()
                          const [a, b] = [Math.min(lastClicked.current, idx), Math.max(lastClicked.current, idx)]
                          void vis.setRange(flat.slice(a, b + 1), on)
                        }
                        lastClicked.current = idx
                      }}
                      onChange={(e) => {
                        if ((e.nativeEvent as MouseEvent).altKey || (e.nativeEvent as MouseEvent).shiftKey) return
                        void vis.toggle(c)
                      }}
                    />
                    <span className="cal-cal-name">
                      {c.name}
                      {c.shared && <span className="cal-cal-sub">{sharedLabel(c)}</span>}
                      {!c.shared && c.sharing && <span className="cal-cal-sub">{sharingLabel(c)}</span>}
                    </span>
                  </label>
                  {c.read_only && <Lock className="cal-lock" aria-label="Read-only" size={14} />}
                  {defaults?.calendar === url && <span className="cal-default" title={t('calendar.leftColumn.defaultCalendar')}>{t('common.default')}</span>}
                  <button type="button" className="icon-btn xs cal-cal-menu" aria-label={t('calendar.leftColumn.optionsFor', { itemName: c.name })} onClick={(e) => setMenu({ c, anchor: e.currentTarget })}>
                    <MoreVertical size={16} />
                  </button>
                </div>
              )
            })}
          </div>
        )
      })}
      {cal.collections.filter((c) => c.error).map((c) => (
        <div key={urlOf(c)} className="cal-cal error" title={c.error}>{c.name || t('common.calendar')}: {c.error}</div>
      ))}
      <button type="button" className="cal-newcal" onClick={() => setCreating(true)} disabled={cal.readOnly}>
        <Plus size={16} aria-hidden />{' '}{t('calendar.leftColumn.newCalendar')}
      </button>
      {menu && <CalendarMenu c={menu.c} anchor={menu.anchor} onClose={() => setMenu(null)} isDefault={defaults?.calendar === urlOf(menu.c)} only={list.length <= 1} />}
      {creating && <NewCalendar onClose={() => setCreating(false)} />}
    </section>
  )
}

function CalendarMenu({ c, anchor, onClose, isDefault, only }: { c: Collection; anchor: HTMLElement; onClose: () => void; isDefault: boolean; only: boolean }) {
  const cal = useCal()
  const qc = useQueryClient()
  const vis = useVisibility()
  const [mode, setMode] = useState<'menu' | 'rename' | 'reminders' | 'delete' | 'share' | 'unsubscribe'>('menu')
  const done = () => void qc.invalidateQueries({ queryKey: ['calendar'] })
  const unsupported = () => toast({ message: t('calendar.leftColumn.thisServerCannotChangeCalendars'), error: true })
  const patch = async (body: Record<string, unknown>, ok: string) => {
    try {
      const out = await calApi.patchCollection(c, body)
      if (!out) return unsupported()
      done()
      toast({ message: ok })
    } catch (e) {
      toastError(e, t('calendar.leftColumn.couldNotChangeTheCalendar'))
    }
  }
  if (mode === 'rename') return <RenameCalendar c={c} onClose={onClose} onSave={(name) => { onClose(); void patch({ name }, 'Calendar renamed') /* ACT calendar.rename @web.calendar */ }} />
  if (mode === 'reminders') return <DefaultReminders c={c} onClose={onClose} onSave={(r) => { onClose(); void patch({ default_reminders: r }, 'Default reminders saved') /* ACT calendar.default_reminders @web.calendar */ }} />
  if (mode === 'delete') return <DeleteCalendar c={c} onClose={onClose} />
  if (mode === 'share') return <ShareDialog c={c} onClose={onClose} />
  if (mode === 'unsubscribe') return <RemoveSubscription c={c} onClose={onClose} />
  const current = cal.colourOf(c)
  return (
    <Popover anchor={anchor} open onClose={onClose} label={t('calendar.leftColumn.optionsFor', { itemName: c.name })} className="cal-cal-pop">
      <MenuItem onSelect={() => { onClose(); vis.showOnly(c) }}>{t('calendar.leftColumn.showOnlyThis')}</MenuItem>
      {cal.hidden.size > 0 && <MenuItem onSelect={() => { onClose(); vis.showAll() }}>{t('common.showAll')}</MenuItem>}
      <div className="menu-sep" />
      <div className="cal-swatches" role="radiogroup" aria-label={t('common.colour')}>
        {SWATCHES.map((s) => (
          <button
            key={s.id}
            type="button"
            role="radio"
            aria-checked={current === s.light}
            aria-label={s.name}
            data-tip={s.name}
            className="cal-swatch"
            style={{ background: cal.dark ? s.dark : s.light }}
            onClick={() => { onClose(); void patch({ color: s.light }, `Colour: ${s.name}`) /* ACT calendar.color @web.calendar */ }}
          >
            {current === s.light && <Check size={14} aria-hidden />}
          </button>
        ))}
      </div>
      <div className="menu-sep" />
      {c.subscription ? (
        <SubscriptionMenuItems c={c} onClose={onClose} onRemove={() => setMode('unsubscribe')} />
      ) : c.shared ? (
        <div className="cal-menu-note">{sharedLabel(c)}. {ownerNote(c)}</div>
      ) : (
        <MenuItem onSelect={() => setMode('rename')} disabled={c.read_only}>{t('calendar.leftColumn.rename')}</MenuItem>
      )}
      {!c.subscription && <>
      {/* ACT calendar.share @web.calendar: own calendars of the mailr calendar, or shared with "Edit and share" */}
      {c.shareable && <MenuItem onSelect={() => setMode('share')}>{t('common.share2')}</MenuItem>}
      <MenuItem onSelect={() => setMode('reminders')}>{t('calendar.leftColumn.defaultReminders2')}</MenuItem>
      <MenuItem
        disabled={isDefault || c.read_only}
        onSelect={() => {
          onClose()
          // ACT calendar.set_default @web.calendar
          calApi.setDefault(urlOf(c)).then(() => { done(); toast({ message: t('calendar.leftColumn.isTheDefaultCalendar', { itemName: c.name }) }) }, (e) => toastError(e, t('calendar.leftColumn.couldNotSetTheDefault')))
        }}
      >
        {t('calendar.leftColumn.setAsDefault')}
      </MenuItem>
      <MenuItem onSelect={() => { onClose(); void calApi.sync().then(done, (e) => toastError(e, t('common.couldNotRefresh'))) /* ACT calendar.sync @web.calendar */ }}>{t('common.refresh')}</MenuItem>
      {!c.shared && <div className="menu-sep" />}
      {!c.shared && <MenuItem danger disabled={isDefault || only || c.read_only} onSelect={() => setMode('delete')}>{t('calendar.leftColumn.delete2')}</MenuItem>}
      </>}
    </Popover>
  )
}

/** "Rosa shared “Work” with you." until dismissed (calendar/sharing.py notices).
 *  ACT calendar.share_notices @web.calendar */
function ShareNotices() {
  const qc = useQueryClient()
  const notes = useShareNotices().data ?? []
  if (!notes.length) return null
  const dismiss = () =>
    api.del('/calendar/share-notices').then(() => void qc.invalidateQueries({ queryKey: SHARE_NOTICES_KEY }), (e) => toastError(e))
  return (
    <div className="cal-share-notes" role="status" data-testid="share-notices">
      <Users size={15} aria-hidden />
      <div className="cal-share-notes-text">
        {notes.slice(-3).map((n) => (
          <div key={n.path}>{t('calendar.leftColumn.sharedWithYou', { by: n.by, itemName: n.name })}</div>
        ))}
        {notes.length > 3 && <div className="muted">{t('calendar.leftColumn.andMore', { value: notes.length - 3 })}</div>}
      </div>
      <button type="button" className="icon-btn xs" aria-label={t('common.dismiss')} data-tip={t('common.dismiss')} onClick={() => void dismiss()}>
        <X size={14} />
      </button>
    </div>
  )
}

function RenameCalendar({ c, onClose, onSave }: { c: Collection; onClose: () => void; onSave: (n: string) => void }) {
  const [name, setName] = useState(c.name)
  return (
    <Dialog open onClose={onClose} title={t('calendar.leftColumn.renameCalendar')} footer={<><button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button><button type="button" className="btn filled" disabled={!name.trim()} onClick={() => onSave(name.trim())}>{t('common.save')}</button></>}>
      <input className="input" value={name} onChange={(e) => setName(e.target.value)} aria-label={t('common.name')} onKeyDown={(e) => e.key === 'Enter' && name.trim() && onSave(name.trim())} />
    </Dialog>
  )
}


function DefaultReminders({ c, onClose, onSave }: { c: Collection; onClose: () => void; onSave: (r: number[]) => void }) {
  const [sel, setSel] = useState<number[]>(defaultReminders(c, false) ?? [])
  return (
    <Dialog open onClose={onClose} title={t('calendar.leftColumn.defaultReminders', { itemName: c.name })} footer={<><button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button><button type="button" className="btn filled" onClick={() => onSave(sel)}>{t('common.save')}</button></>}>
      <p className="cal-note">{t('calendar.leftColumn.newEventsInThisCalendar')}</p>
      <div className="cal-chips">
        {OFFSETS.map((m) => (
          <button key={m} type="button" className={`chip cal-chip${sel.includes(m) ? ' on' : ''}`} aria-pressed={sel.includes(m)} onClick={() => setSel(sel.includes(m) ? sel.filter((x) => x !== m) : [...sel, m].sort((a, b) => a - b))}>
            {offsetLabel(m)}
          </button>
        ))}
      </div>
    </Dialog>
  )
}

function DeleteCalendar({ c, onClose }: { c: Collection; onClose: () => void }) {
  const cal = useCal()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const count = cal.events.filter((e) => e.calendar === urlOf(c)).length
  return (
    <ConfirmDialog
      open
      title={t('calendar.leftColumn.delete', { itemName: c.name })}
      danger
      busy={busy}
      confirmLabel={t('calendar.leftColumn.deleteCalendar')}
      onClose={onClose}
      body={
        <>
          <p>{t('calendar.leftColumn.theCalendarAndEveryEvent', { value: count ? t('calendar.leftColumn.eventsInViewNow', { count }) : '' })}</p>
          <button type="button" className="btn tonal sm" onClick={() => void exportIcs(c, cal.prefs.zone)}>{t('common.downloadIcsFirst')}</button>
        </>
      }
      onConfirm={() => {
        setBusy(true)
        // ACT calendar.delete @web.calendar (no undo: confirmed, with an .ics copy offered first)
        calApi.deleteCollection(c).then(() => {
          onClose()
          void qc.invalidateQueries({ queryKey: ['calendar'] })
          toast({ message: t('calendar.leftColumn.deleted', { itemName: c.name }) })
        }, (e) => {
          setBusy(false)
          toastError(e, t('calendar.leftColumn.couldNotDeleteTheCalendar'))
        })
      }}
    />
  )
}

function NewCalendar({ onClose }: { onClose: () => void }) {
  const accounts = useAccounts().data ?? []
  const qc = useQueryClient()
  const cal = useCal()
  const [name, setName] = useState('')
  const [colour, setColour] = useState(SWATCHES[6].light)
  const [acct, setAcct] = useState<number | undefined>(accounts[0]?.id)
  const [kind, setKind] = useState<'events' | 'tasks' | 'both'>('events')
  const [busy, setBusy] = useState(false)
  const save = () => {
    if (!name.trim()) return
    setBusy(true)
    // ACT calendar.create @web.calendar
    calApi.createCollection({ connector_id: acct, name: name.trim(), color: colour, kind }).then((c) => {
      onClose()
      void qc.invalidateQueries({ queryKey: ['calendar'] })
      toast({
        message: t('calendar.leftColumn.created', { itemName: c?.name ?? name }), actionLabel: t('common.undo'), duration: 7000,
        onAction: () => void (c?.calendar_id != null && calApi.deleteCollection(c).then(() => qc.invalidateQueries({ queryKey: ['calendar'] }))),
      })
    }, (e) => {
      setBusy(false)
      toastError(e, (e as { status?: number }).status === 404 ? t('calendar.leftColumn.thisServerCannotCreateCalendars') : t('calendar.leftColumn.couldNotCreateTheCalendar'))
    })
  }
  return (
    <Dialog open onClose={onClose} title={t('calendar.leftColumn.newCalendar')} footer={<><button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button><button type="button" className="btn filled" disabled={!name.trim() || busy} onClick={save}>{t('common.create')}</button></>}>
      <div className="cal-form">
        <label className="cal-field"><span>{t('common.name')}</span><input className="input" value={name} autoFocus onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && save()} /></label>
        {accounts.length > 1 && (
          <label className="cal-field"><span>{t('common.account')}</span>
            <select className="input" value={acct} onChange={(e) => setAcct(Number(e.target.value))}>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.username || a.name}</option>)}
            </select>
          </label>
        )}
        <label className="cal-field"><span>{t('calendar.leftColumn.holds')}</span>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="events">{t('common.events')}</option>
            <option value="tasks">{t('common.tasks')}</option>
            <option value="both">{t('calendar.leftColumn.eventsAndTasks')}</option>
          </select>
        </label>
        <div className="cal-field"><span>{t('common.colour')}</span>
          <div className="cal-swatches" role="radiogroup" aria-label={t('common.colour')}>
            {SWATCHES.map((s) => (
              <button key={s.id} type="button" role="radio" aria-checked={colour === s.light} aria-label={s.name} data-tip={s.name} className="cal-swatch" style={{ background: cal.dark ? s.dark : s.light }} onClick={() => setColour(s.light)}>
                {colour === s.light && <Check size={14} aria-hidden />}
              </button>
            ))}
          </div>
        </div>
      </div>
    </Dialog>
  )
}

// ---------- tasks ----------

function TaskSection() {
  const cal = useCal()
  const saved = useSavedView()
  const lists = cal.collections.filter((c) => c.tasks)
  if (!lists.length) return null
  return (
    <section className="cal-cals cal-tasks-sec" aria-label={t('common.tasks')}>
      <div className="cal-section-head"><h2>{t('common.tasks')}</h2></div>
      <label className="cal-cal">
        <input type="checkbox" className="checkbox" checked={saved.tasksOnCalendar} onChange={(e) => saved.set({ tasksOnCalendar: e.target.checked })} />
        <span className="cal-cal-name">{t('calendar.leftColumn.showTasksOnCalendar')}</span>
      </label>
      {saved.tasksOnCalendar && lists.map((l) => {
        const url = urlOf(l)
        const on = !saved.hiddenLists.includes(url)
        return (
          <label key={url} className="cal-cal sub">
            <input type="checkbox" className="cal-swatch-box" style={{ '--sw': displayColour(cal.colourOf(l), cal.dark) } as React.CSSProperties} checked={on} onChange={() => saved.set({ hiddenLists: on ? [...saved.hiddenLists, url] : saved.hiddenLists.filter((x) => x !== url) })} />
            <span className="cal-cal-name">{l.name}</span>
          </label>
        )
      })}
    </section>
  )
}
