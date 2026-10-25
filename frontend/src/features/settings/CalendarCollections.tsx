import { assistantNameNow } from '../rail/assistant/useAssistantName'
// Settings → Calendar & tasks → Calendars and task lists (web.settings): the default calendar and
// task list, and per collection its colour, name, what it holds (events, tasks), deletion with an
// .ics copy first, and a new calendar or list. The Calendar app's left column offers the same
// actions; both write through the collection routes (section 4).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, Pencil, Plus, RefreshCw, Share2, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../api/client'
import { useSettings } from '../../api/hooks'
import type { CalendarDefaults } from '../../api/types'
import { Skeleton } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { Popover } from '../../components/Popover'
import { addDaysISO, resolveDatePrefs, startOfDay, todayISO, utcIsoformat } from '../../lib/dates'
import { toast, toastError } from '../../stores/ui'
import { SWATCHES } from '../calendar/colour'
import { calApi, useAccounts } from '../calendar/data'
import { exportIcs } from '../calendar/ics'
import { ShareDialog } from '../calendar/ShareDialog'
import { sharedLabel, sharingLabel, type Collection } from '../calendar/types'
import { Section } from './common'
import '../calendar/calendar.css'
import { t } from '../../i18n'

const urlOf = (c: Collection) => c.url ?? c.id
const keyOf = (c: Collection) => `${c.connector_id}:${c.id}`

function useZone(): string {
  const s = useSettings() as unknown as { timezone?: string }
  return resolveDatePrefs(s).zone
}

/** Why a member or sharee cannot rename or delete a calendar, or null for their own. */
function notMine(c: Collection, verb: 'renames' | 'deletes'): string | null {
  if (!c.shared) return null
  return c.shared.kind === 'user' ? t('settings.calendarCollections.onlyIt', { owner: c.shared.owner, verb }) : t('settings.calendarCollections.onlyAnAdministratorAGroup', { verb })
}

export function CollectionsSection() {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [renaming, setRenaming] = useState<Collection | null>(null)
  const [deleting, setDeleting] = useState<Collection | null>(null)
  const [sharing, setSharing] = useState<Collection | null>(null)
  const [colouring, setColouring] = useState<{ c: Collection; anchor: HTMLElement } | null>(null)
  const cals = useQuery<Collection[]>({
    queryKey: ['calendar', 'calendars'],
    queryFn: async ({ signal }) => {
      const r = await api.get<Collection[] | { items: Collection[] }>('/calendar/calendars', undefined, signal)
      return Array.isArray(r) ? r : r.items ?? []
    },
  })
  const defaults = useQuery<CalendarDefaults>({
    queryKey: ['calendar', 'defaults'],
    queryFn: ({ signal }) => api.get<CalendarDefaults>('/calendar/defaults', undefined, signal),
  })
  const done = () => {
    void qc.invalidateQueries({ queryKey: ['calendar'] })
    void qc.invalidateQueries({ queryKey: ['tasks'] })
  }
  // ACT calendar.set_default @web.settings, ACT list.set_default @web.settings (the two selects); the kinds are mailr's own.
  const save = useMutation({
    mutationFn: (b: { calendar?: string | null; task_list?: string | null; kinds?: Record<string, { events?: boolean; tasks?: boolean }> }) =>
      api.put<CalendarDefaults>('/calendar/defaults', {
        calendar: b.calendar !== undefined ? b.calendar : defaults.data?.calendar ?? null,
        task_list: b.task_list !== undefined ? b.task_list : defaults.data?.task_list ?? null,
        kinds: b.kinds,
      }),
    onSuccess: () => {
      done()
      toast({ message: t('settings.calendarCollections.calendarSettingsSaved') })
    },
    onError: (e) => toastError(e, t('common.notSaved')),
  })
  const patch = async (c: Collection, body: Record<string, unknown>, ok: string) => {
    try {
      const out = await calApi.patchCollection(c, body)
      if (!out) return toast({ message: t('settings.calendarCollections.thisServerCannotChangeCalendars'), error: true })
      done()
      toast({ message: ok })
    } catch (e) {
      toastError(e, t('settings.calendarCollections.couldNotChangeTheCalendar'))
    }
  }
  const refresh = async () => {
    setRefreshing(true)
    try {
      await api.get('/calendar/calendars', { refresh: 1 })
      await qc.invalidateQueries({ queryKey: ['calendar'] })
    } catch (e) {
      toastError(e, t('common.couldNotRefresh'))
    } finally {
      setRefreshing(false)
    }
  }
  const all = cals.data ?? []
  const good = all.filter((c) => !c.error)
  const broken = all.filter((c) => c.error)
  const events = good.filter((c) => c.events)
  const lists = good.filter((c) => c.tasks)
  const d = defaults.data
  const isDefault = (c: Collection) => d?.calendar === urlOf(c) || d?.task_list === urlOf(c) || d?.calendar === c.id || d?.task_list === c.id
  // The last collection that holds events (or tasks) cannot be deleted.
  const isLast = (c: Collection) => (c.events && events.length <= 1) || (c.tasks && lists.length <= 1)
  return (
    <Section
      title={t('settings.calendarCollections.calendarsAndTaskLists')}
      desc={t('settings.calendarCollections.theDefaultCalendarAndTask', { name: assistantNameNow() })}
      actions={
        <>
          <button type="button" className="btn text sm" disabled={refreshing} onClick={() => void refresh()}>
            <RefreshCw className={refreshing ? 'spin' : undefined} />{' '}{t('common.refresh')}
          </button>
          <button type="button" className="btn tonal sm" onClick={() => setCreating(true)}>
            <Plus />{' '}{t('settings.calendarCollections.newCalendarOrList')}
          </button>
        </>
      }
    >
      {cals.isLoading || defaults.isLoading ? (
        <Skeleton height={60} />
      ) : (
        <>
          <div className="grid-2" style={{ marginBottom: 12 }}>
            <div className="field">
              <label htmlFor="def-cal">{t('settings.calendarCollections.defaultCalendar')}</label>
              <select id="def-cal" className="select" value={d?.calendar ?? ''} disabled={!events.length || save.isPending} onChange={(e) => save.mutate({ calendar: e.target.value || null })}>
                {!events.length && <option value="">{t('settings.calendarCollections.noCalendarAcceptsEvents')}</option>}
                {events.map((c) => (
                  <option key={keyOf(c)} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="def-list">{t('settings.calendarCollections.defaultTaskList')}</label>
              <select id="def-list" className="select" value={d?.task_list ?? ''} disabled={!lists.length || save.isPending} onChange={(e) => save.mutate({ task_list: e.target.value || null })}>
                {!lists.length && <option value="">{t('settings.calendarCollections.noCollectionAcceptsTasks')}</option>}
                {lists.map((c) => (
                  <option key={keyOf(c)} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="table-wrap">
            <table className="table" data-testid="settings-collections">
              <thead>
                <tr>
                  <th aria-label={t('common.colour')} />
                  <th>{t('common.name')}</th>
                  <th>{t('settings.calendarCollections.showsEvents')}</th>
                  <th>{t('settings.calendarCollections.showsTasks')}</th>
                  <th aria-label={t('common.actions')} />
                </tr>
              </thead>
              <tbody>
                {good.map((c) => (
                  <tr key={keyOf(c)} data-testid="settings-collection">
                    <td style={{ width: 32 }}>
                      {/* ACT calendar.color @web.settings */}
                      <button type="button" className="icon-btn sm" aria-label={t('settings.calendarCollections.colourOf', { itemName: c.name })} data-tip={c.read_only && !c.shared ? undefined : t('common.colour')} disabled={c.read_only && !c.shared}
                        onClick={(e) => setColouring({ c, anchor: e.currentTarget })}>
                        <span aria-hidden style={{ display: 'inline-block', width: 14, height: 14, borderRadius: 4, background: c.color || 'var(--primary)' }} />
                      </button>
                    </td>
                    <td>
                      {c.name}
                      {d?.calendar === c.id && <span className="chip sm" style={{ marginInlineStart: 8 }}>{t('settings.calendarCollections.defaultCalendar2')}</span>}
                      {d?.task_list === c.id && <span className="chip sm" style={{ marginInlineStart: 8 }}>{t('settings.calendarCollections.defaultTaskList2')}</span>}
                      {c.read_only && <span className="chip sm" style={{ marginInlineStart: 8 }}>{t('settings.calendarCollections.readOnly')}</span>}
                      {c.shared && <div className="muted" style={{ fontSize: 12 }}>{sharedLabel(c)}</div>}
                      {!c.shared && c.sharing && <div className="muted" style={{ fontSize: 12 }}>{sharingLabel(c)}</div>}
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        className="checkbox"
                        aria-label={t('settings.calendarCollections.holdsEvents', { itemName: c.name })}
                        checked={!!c.events}
                        onChange={(e) => save.mutate({ kinds: { [c.id]: { events: e.target.checked, tasks: !!c.tasks } } })}
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        className="checkbox"
                        aria-label={t('settings.calendarCollections.holdsTasks', { itemName: c.name })}
                        checked={!!c.tasks}
                        onChange={(e) => save.mutate({ kinds: { [c.id]: { events: !!c.events, tasks: e.target.checked } } })}
                      />
                    </td>
                    <td style={{ whiteSpace: 'nowrap', textAlign: 'end' }}>
                      {c.shareable && (
                        // ACT calendar.share @web.settings
                        <button type="button" className="icon-btn sm" aria-label={t('settings.calendarCollections.share', { itemName: c.name })} data-tip={t('common.share')} onClick={() => setSharing(c)}>
                          <Share2 size={16} />
                        </button>
                      )}
                      <button type="button" className="icon-btn sm" aria-label={t('settings.calendarCollections.rename', { itemName: c.name })} data-tip={notMine(c, 'renames') ?? t('common.rename')} disabled={c.read_only || !!c.shared} onClick={() => setRenaming(c)}>
                        <Pencil size={16} />
                      </button>
                      <button type="button" className="icon-btn sm" aria-label={t('settings.calendarCollections.delete', { itemName: c.name })}
                        data-tip={notMine(c, 'deletes') ?? (isDefault(c) ? t('settings.calendarCollections.theDefaultCannotBeDeleted') : isLast(c) ? t('settings.calendarCollections.theLastOneCannotBe') : t('common.delete2'))}
                        disabled={c.read_only || !!c.shared || isDefault(c) || isLast(c)} onClick={() => setDeleting(c)}>
                        <Trash2 size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            {t('settings.calendarCollections.detectedFromWhatEachServer')}
          </p>
          {broken.map((c) => (
            <div key={`err:${c.connector_id}`} className="banner error" style={{ marginTop: 8 }}>
              <AlertTriangle /> <span>{t('settings.calendarCollections.couldNotReadOneCalendar', { error: c.error })}</span>
            </div>
          ))}
        </>
      )}
      {sharing && <ShareDialog c={sharing} onClose={() => setSharing(null)} />}
      {colouring && (
        <Popover anchor={colouring.anchor} open onClose={() => setColouring(null)} label={t('settings.calendarCollections.colourOf', { itemName: colouring.c.name })} className="cal-cal-pop">
          <div className="cal-swatches" role="radiogroup" aria-label={t('common.colour')}>
            {SWATCHES.map((s) => (
              <button key={s.id} type="button" role="radio" aria-checked={colouring.c.color === s.light} aria-label={s.name} data-tip={s.name} className="cal-swatch" style={{ background: s.light }}
                onClick={() => {
                  const c = colouring.c
                  setColouring(null)
                  void patch(c, { color: s.light }, `Colour: ${s.name}`)
                }}>
                {colouring.c.color === s.light && <Check size={14} aria-hidden />}
              </button>
            ))}
          </div>
        </Popover>
      )}
      {renaming && <RenameDialog c={renaming} onClose={() => setRenaming(null)} onSave={(name) => {
        const c = renaming
        setRenaming(null)
        // ACT calendar.rename @web.settings
        void patch(c, { name }, 'Renamed')
      }} />}
      {deleting && <DeleteDialog c={deleting} onClose={() => setDeleting(null)} onDone={done} />}
      {creating && <NewCollectionDialog onClose={() => setCreating(false)} onDone={done} />}
    </Section>
  )
}

function RenameDialog({ c, onClose, onSave }: { c: Collection; onClose: () => void; onSave: (n: string) => void }) {
  const [name, setName] = useState(c.name)
  return (
    <Dialog open onClose={onClose} title={t('settings.calendarCollections.rename2', { itemName: c.name })} footer={<><button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button><button type="button" className="btn filled" disabled={!name.trim() || name.trim() === c.name} onClick={() => onSave(name.trim())}>{t('common.save')}</button></>}>
      <input className="input" value={name} autoFocus aria-label={t('common.name')} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && name.trim() && onSave(name.trim())} />
    </Dialog>
  )
}

/** How many events a calendar holds from a year back to a year ahead (one agenda read). */
function useEventCount(c: Collection, zone: string) {
  return useQuery<number>({
    queryKey: ['calendar', 'count', urlOf(c)],
    queryFn: async ({ signal }) => {
      const today = todayISO(zone)
      const r = await api.get<{ events?: { uid: string; calendar?: string }[] }>('/calendar/agenda', {
        from: utcIsoformat(startOfDay(addDaysISO(today, -365), zone)), to: utcIsoformat(startOfDay(addDaysISO(today, 365), zone)), calendar: urlOf(c),
      }, signal)
      return new Set((r.events ?? []).filter((e) => !e.calendar || e.calendar === urlOf(c)).map((e) => e.uid)).size
    },
    enabled: c.events !== false,
    staleTime: 60_000,
  })
}

/** ACT calendar.delete @web.settings: no undo, so it is confirmed with its event count and an .ics copy first. */
function DeleteDialog({ c, onClose, onDone }: { c: Collection; onClose: () => void; onDone: () => void }) {
  const zone = useZone()
  const count = useEventCount(c, zone)
  const [busy, setBusy] = useState(false)
  const n = count.data
  return (
    <ConfirmDialog
      open
      title={t('settings.calendarCollections.delete2', { itemName: c.name })}
      danger
      busy={busy}
      confirmLabel={c.events === false ? t('settings.calendarCollections.deleteList') : t('settings.calendarCollections.deleteCalendar')}
      onClose={onClose}
      body={
        <>
          <p data-testid="delete-collection-count">
            {t('settings.calendarCollections.everythingInItIsDeleted', { value: c.events !== false && (n === undefined ? t('settings.calendarCollections.countingItsEvents') : t('settings.calendarCollections.itHoldsEventsBetweenA', { count: n })) })}
          </p>
          <button type="button" className="btn tonal sm" onClick={() => void exportIcs(c, zone)}>{t('common.downloadIcsFirst')}</button>
        </>
      }
      onConfirm={() => {
        setBusy(true)
        calApi.deleteCollection(c).then(() => {
          onClose()
          onDone()
          toast({ message: t('settings.calendarCollections.deleted', { itemName: c.name }) })
        }, (e) => {
          setBusy(false)
          toastError(e, t('settings.calendarCollections.couldNotDeleteIt'))
        })
      }}
    />
  )
}

/** ACT calendar.create @web.settings: a calendar for events, a task list, or both, on a chosen account. */
function NewCollectionDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const accounts = useAccounts().data ?? []
  const [name, setName] = useState('')
  const [colour, setColour] = useState(SWATCHES[6].light)
  const [acct, setAcct] = useState<number | undefined>(undefined)
  const [kind, setKind] = useState<'events' | 'tasks' | 'both'>('events')
  const [busy, setBusy] = useState(false)
  const create = () => {
    if (!name.trim() || busy) return
    setBusy(true)
    calApi.createCollection({ connector_id: acct ?? accounts[0]?.id, name: name.trim(), color: colour, kind }).then((c) => {
      onClose()
      onDone()
      toast({
        message: t('settings.calendarCollections.created', { itemName: c?.name ?? name.trim() }), actionLabel: t('common.undo'), duration: 7000,
        onAction: () => void (c?.calendar_id != null && calApi.deleteCollection(c).then(onDone, (e) => toastError(e, t('common.couldNotUndo')))),
      })
    }, (e) => {
      setBusy(false)
      toastError(e, t('settings.calendarCollections.couldNotCreateIt'))
    })
  }
  return (
    <Dialog open onClose={onClose} title={t('settings.calendarCollections.newCalendarOrTaskList')} footer={<><button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button><button type="button" className="btn filled" disabled={!name.trim() || busy} onClick={create}>{t('common.create')}</button></>}>
      <div className="stack">
        <div className="field">
          <label htmlFor="nc-name">{t('common.name')}</label>
          <input id="nc-name" className="input" value={name} autoFocus onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && create()} />
        </div>
        {accounts.length > 1 && (
          <div className="field">
            <label htmlFor="nc-acct">{t('common.account')}</label>
            <select id="nc-acct" className="select" value={acct ?? accounts[0]?.id} onChange={(e) => setAcct(Number(e.target.value))}>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.username || a.name}</option>)}
            </select>
          </div>
        )}
        <div className="field">
          <label htmlFor="nc-kind">{t('settings.calendarCollections.holds')}</label>
          <select id="nc-kind" className="select" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="events">{t('common.events')}</option>
            <option value="tasks">{t('common.tasks')}</option>
            <option value="both">{t('settings.calendarCollections.eventsAndTasks')}</option>
          </select>
        </div>
        <div className="field">
          <span>{t('common.colour')}</span>
          <div className="cal-swatches" role="radiogroup" aria-label={t('common.colour')}>
            {SWATCHES.map((s) => (
              <button key={s.id} type="button" role="radio" aria-checked={colour === s.light} aria-label={s.name} data-tip={s.name} className="cal-swatch" style={{ background: s.light }} onClick={() => setColour(s.light)}>
                {colour === s.light && <Check size={14} aria-hidden />}
              </button>
            ))}
          </div>
        </div>
      </div>
    </Dialog>
  )
}
