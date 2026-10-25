// Admin → Calendars → Group calendars (calendar/groups.py): calendars an admin attaches to a group.
// Members see them under "Shared" in Calendar. The admin sees only these calendars' names,
// colours, access, editors and item counts; never a user's own calendars or events.
import { Check as Tick, Lock, Plus, Users } from 'lucide-react'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useApi } from '../../api/hooks'
import { Skeleton } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { SWATCHES } from '../calendar/colour'
import { downloadIcs } from '../calendar/ics'
import { toast, toastError } from '../../stores/ui'
import { Check, Radio } from './common'
import { t } from '../../i18n'

export interface GroupPerson {
  id: number
  name: string
  email: string
}

export interface GroupCalendar {
  id: number
  group: string
  name: string
  color: string | null
  access: 'read' | 'write'
  editors: GroupPerson[]
  ready: boolean
  items?: number | null
}

export interface CalendarGroup {
  name: string
  members: GroupPerson[]
  calendars: GroupCalendar[]
}

const KEY = ['admin', 'calendars', 'groups']

export function accessLabel(c: Pick<GroupCalendar, 'access' | 'editors'>): string {
  if (c.access === 'write') return t('settings.groupCalendars.membersCanEdit')
  const n = c.editors.length
  return n ? t('settings.groupCalendars.membersCanViewEditors', { count: n }) : t('settings.groupCalendars.membersCanView')
}

/** ACT settings.group_calendars @web.settings */
export function GroupCalendarsPanel({ enabled }: { enabled: boolean }) {
  const q = useApi<{ enabled: boolean; groups: CalendarGroup[] }>(KEY, '/admin/calendars/groups')
  const [editing, setEditing] = useState<{ group: CalendarGroup; cal: GroupCalendar | null } | null>(null)
  const [deleting, setDeleting] = useState<GroupCalendar | null>(null)
  const groups = q.data?.groups ?? []
  const withCals = groups.filter((g) => g.calendars.length)
  const [pick, setPick] = useState('')
  const pickGroup = groups.find((g) => g.name === pick)
  return (
    <div className="gcal" data-testid="group-calendars">
      <h3 className="gcal-head">{t('settings.groupCalendars.groupCalendars')}</h3>
      <p className="muted gcal-desc">
        {t('settings.groupCalendars.aGroupCalendarBelongsTo')}
      </p>
      {!enabled ? (
        <p className="muted" data-testid="group-calendars-off">{t('settings.groupCalendars.turnOnBuiltInCalendars')}</p>
      ) : q.isLoading ? (
        <Skeleton height={60} />
      ) : groups.length === 0 ? (
        <p className="muted" data-testid="group-calendars-empty">
          {t('settings.groupCalendars.noGroupsYetGroupsAppear')}
        </p>
      ) : (
        <>
          {withCals.length === 0 && <p className="muted" data-testid="group-calendars-none">{t('settings.groupCalendars.noGroupCalendarsYet')}</p>}
          {withCals.map((g) => (
            <div key={g.name} className="gcal-group">
              <div className="gcal-group-name">
                <Users size={15} aria-hidden /> <b>{g.name}</b>
                <span className="muted">{' '}{t('settings.groupCalendars.members', { count: g.members.length })}</span>
                <button type="button" className="btn text sm" onClick={() => setEditing({ group: g, cal: null })}>
                  <Plus size={14} />{' '}{t('settings.groupCalendars.addCalendar')}
                </button>
              </div>
              {g.calendars.map((c) => (
                <div key={c.id} className="gcal-row" data-testid="group-calendar">
                  <span className="gcal-swatch" style={{ background: c.color ?? 'var(--text-3)' }} aria-hidden />
                  <span className="gcal-name">{c.name}</span>
                  <span className="pill">{c.access === 'read' && <Lock size={11} aria-hidden />} {accessLabel(c)}</span>
                  {c.editors.length > 0 && <span className="muted gcal-editors">{t('settings.groupCalendars.editors', { map: c.editors.map((e) => e.name).join(', ') })}</span>}
                  <span className="muted gcal-count">{c.items == null ? '' : t('settings.groupCalendars.items', { count: c.items })}</span>
                  <button type="button" className="btn text sm" onClick={() => setEditing({ group: g, cal: c })}>{t('common.edit')}</button>
                  <button type="button" className="btn text sm" onClick={() => setDeleting(c)}>{t('common.delete')}</button>
                </div>
              ))}
            </div>
          ))}
          <div className="row gcal-new">
            <select className="input" aria-label={t('common.group')} value={pick} onChange={(e) => setPick(e.target.value)} data-testid="group-calendar-group">
              <option value="">{t('settings.groupCalendars.chooseAGroup')}</option>
              {groups.map((g) => (
                <option key={g.name} value={g.name}>{g.name} ({g.members.length})</option>
              ))}
            </select>
            <button type="button" className="btn tonal" disabled={!pickGroup} onClick={() => pickGroup && setEditing({ group: pickGroup, cal: null })}>
              <Plus size={16} />{' '}{t('settings.groupCalendars.newGroupCalendar')}
            </button>
          </div>
        </>
      )}
      {editing && <GroupCalendarDialog group={editing.group} cal={editing.cal} onClose={() => setEditing(null)} />}
      {deleting && <DeleteGroupCalendar cal={deleting} onClose={() => setDeleting(null)} />}
    </div>
  )
}

function GroupCalendarDialog({ group, cal, onClose }: { group: CalendarGroup; cal: GroupCalendar | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(cal?.name ?? '')
  const [color, setColor] = useState(cal?.color ?? SWATCHES[0].light.toUpperCase())
  const [access, setAccess] = useState<'read' | 'write'>(cal?.access ?? 'write')
  const [editors, setEditors] = useState<number[]>(cal?.editors.map((e) => e.id) ?? [])
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { name: name.trim(), color: color.toUpperCase() }
      if (access === 'read') body.editors = editors
      return cal ? api.patch(`/admin/calendars/groups/${cal.id}`, body) : api.post('/admin/calendars/groups', { ...body, group: group.name, access })
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY })
      toast({ message: cal ? t('settings.groupCalendars.groupCalendarSaved') : t('settings.groupCalendars.addedMembersSeeItWithin', { itemName: name.trim() }) })
      onClose()
    },
    onError: (e) => toastError(e, t('settings.groupCalendars.couldNotSaveTheGroup')),
  })
  return (
    <Dialog
      open
      onClose={onClose}
      title={cal ? t('settings.groupCalendars.edit', { calName: cal.name }) : t('settings.groupCalendars.newCalendarFor', { groupName: group.name })}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn filled" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()} data-testid="group-calendar-save">
            {cal ? t('common.save') : t('settings.groupCalendars.addCalendar')}
          </button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="gc-name">{t('common.name')}</label>
        <input id="gc-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('settings.groupCalendars.team')} />
      </div>
      <div className="cal-swatches" role="radiogroup" aria-label={t('common.colour')}>
        {SWATCHES.map((s) => (
          <button
            key={s.id}
            type="button"
            role="radio"
            aria-checked={color.toUpperCase() === s.light.toUpperCase()}
            aria-label={s.name}
            data-tip={s.name}
            className="cal-swatch"
            style={{ background: s.light }}
            onClick={() => setColor(s.light.toUpperCase())}
          >
            {color.toUpperCase() === s.light.toUpperCase() && <Tick size={14} aria-hidden />}
          </button>
        ))}
      </div>
      <fieldset className="gcal-access" disabled={!!cal}>
        <legend>{t('settings.groupCalendars.membersOf', { groupName: group.name })}</legend>
        <Radio name="gc-access" value="write" current={access} onChange={setAccess} label={t('settings.groupCalendars.canEdit')} desc={t('settings.groupCalendars.everyMemberAddsAndChanges')} />
        <Radio name="gc-access" value="read" current={access} onChange={setAccess} label={t('settings.groupCalendars.canView')} desc={t('settings.groupCalendars.membersSeeTheEventsOnly')} />
        {cal && <p className="muted" style={{ fontSize: 12, margin: '4px 0 0' }}>{t('settings.groupCalendars.accessIsSetWhenThe')}</p>}
      </fieldset>
      {access === 'read' && (
        <fieldset className="gcal-editors-pick">
          <legend>{t('settings.groupCalendars.editors2')}</legend>
          {group.members.length === 0 ? (
            <p className="muted">{t('settings.groupCalendars.thisGroupHasNoMembers')}</p>
          ) : (
            group.members.map((m) => (
              <Check
                key={m.id}
                checked={editors.includes(m.id)}
                onChange={(v) => setEditors(v ? [...editors, m.id] : editors.filter((x) => x !== m.id))}
                label={m.name}
                desc={m.email}
              />
            ))
          )}
        </fieldset>
      )}
    </Dialog>
  )
}

function DeleteGroupCalendar({ cal, onClose }: { cal: GroupCalendar; onClose: () => void }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  return (
    <ConfirmDialog
      open
      danger
      busy={busy}
      title={t('settings.groupCalendars.delete', { calName: cal.name })}
      confirmLabel={t('settings.groupCalendars.deleteForEveryone')}
      onClose={onClose}
      body={
        <>
          <p>
            {t('settings.groupCalendars.theCalendarAndItsItems', { count: cal.items, items: cal.items ?? t('settings.groupCalendars.unknownNumberOf'), group: cal.group })}
          </p>
          <button type="button" className="btn tonal sm" onClick={() => void downloadIcs(`/admin/calendars/groups/${cal.id}/export`, cal.name).catch((e) => toastError(e))}>
            {t('common.downloadIcsFirst')}
          </button>
        </>
      }
      onConfirm={() => {
        setBusy(true)
        api
          .del(`/admin/calendars/groups/${cal.id}?confirm=${encodeURIComponent(cal.name)}`)
          .then(() => {
            void qc.invalidateQueries({ queryKey: KEY })
            toast({ message: t('settings.groupCalendars.deleted', { calName: cal.name }) })
            onClose()
          }, (e) => {
            setBusy(false)
            toastError(e, t('settings.groupCalendars.couldNotDeleteTheGroup'))
          })
      }}
    />
  )
}
