import { assistantNameNow } from '../../rail/assistant/useAssistantName'
// Settings → Ambrose → Standing orders: orders ("when mail like this arrives"), recording orders
// ("when a new recording arrives", with a Speakr account; integrations/speakr/orders.py) and routines
// ("every morning…"), plus the activity log of every run.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Bell, Bot, CalendarClock, Check as CheckIcon, ChevronDown, ChevronRight, FlaskConical, Forward, Inbox, Mic, Minus, Pencil, Play, Plus, ShieldAlert, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { api } from '../../../api/client'
import { useLabelList, useSettings, useUpdateSettings } from '../../../api/hooks'
import { Skeleton, Switch } from '../../../components/bits'
import { ConfirmDialog, Dialog } from '../../../components/Dialog'
import { formatRelative } from '../../../lib/format'
import { mailPath } from '../../../lib/views'
import { toast, toastError } from '../../../stores/ui'
import { Markdown } from '../../rail/assistant/Markdown'
import { useAssistantName } from '../../rail/assistant/useAssistantName'
import { useSpeakrStatus } from '../../speakr/api'
import { useRecordingSheet } from '../../speakr/store'
import { Check, Section } from '../common'
import { orderName, qkA, type AssistantOrder, type AssistantRun, type OrderChoice, type RecordingFilter, type RunRecording } from './types'
import { t as tr, tx, appLocale } from '../../../i18n'
import { clockOpts } from '../../../lib/dates'
import { scheduleWords } from './scheduleWords'

const SCHEDULES: { label: string; cron: string }[] = [
  { label: tr('settings.assistant.ordersSection.everyWeekdayAt700'), cron: '0 7 * * 1-5' },
  { label: tr('settings.assistant.ordersSection.everyDayAt700'), cron: '0 7 * * *' },
  { label: tr('settings.assistant.ordersSection.everyDayAt1700'), cron: '0 17 * * *' },
  { label: tr('settings.assistant.ordersSection.mondaysAt800'), cron: '0 8 * * 1' },
  { label: tr('settings.assistant.ordersSection.fridaysAt1600'), cron: '0 16 * * 5' },
]

/** An order's action in words ("Mark as read"), not its key ("mark_read"). */
export function actionWord(k: string): string {
  const words: Record<string, string> = {
    archive: tr('common.archive'), mark_read: tr('common.markAsRead'), star: tr('common.star'), important: tr('common.markImportant'),
    notify: tr('settings.assistant.ordersSection.notifyMe'), add_to_calendar: tr('settings.assistant.ordersSection.addToCalendar'),
  }
  return words[k] ?? k.replace(/_/g, ' ')
}

/** Where an order's result goes, in words. */
function deliverWord(k: string): string {
  return k === 'brief' ? tr('settings.assistant.ordersSection.aBriefOnTheEmail') : k === 'notify' || k === 'digest' ? tr('settings.assistant.ordersSection.aNotification') : k
}

function describeSchedule(cron: string): string {
  return SCHEDULES.find((s) => s.cron === cron)?.label ?? scheduleWords(cron)
}

type Draft = Omit<AssistantOrder, 'id' | 'position' | 'source' | 'runs' | 'last_run_at' | 'next_run_at'> & { id?: number }

const EMPTY: Draft = {
  name: '', trigger: 'message', schedule: '0 7 * * 1-5', timezone: '', query: '', question: '', choices: [], threshold: 0.7,
  actions: {}, errand: '', deliver: [], enabled: true, recordings: {}, from_trusted: false, from_me: false,
}

/** The search matches on a recipient address (to:, cc:, bcc:, deliveredto:). */
export function matchesRecipient(query: string): boolean {
  return /(^|[\s(])-?(to|cc|bcc|deliveredto):/i.test(query)
}

/** A message order that writes (adds to the calendar, or gives Ambrose work that may save memories,
 * tasks or drafts) with no condition on the sender: anyone who knows the address can trigger it. */
export function needsSenderCondition(d: Pick<AssistantOrder, 'trigger' | 'query' | 'actions' | 'errand' | 'from_me' | 'from_trusted'>): boolean {
  if (d.trigger !== 'message' || !matchesRecipient(d.query)) return false
  const writes = !!d.actions.add_to_calendar || !!d.errand.trim()
  const sender = !!d.from_me || !!d.from_trusted || /(^|[\s(])from:/i.test(d.query)
  return writes && !sender
}

/** "Title or participants contain …, tag …, with …": a recording order's filter in words. */
export function describeFilter(f: RecordingFilter | undefined): string {
  const parts = [
    f?.q && tr('settings.assistant.ordersSection.titleOrParticipantsContain', { q: f.q }),
    f?.tag && tr('settings.assistant.ordersSection.tagged', { tag: f.tag }),
    f?.participant && tr('settings.assistant.ordersSection.with', { participant: f.participant }),
  ].filter(Boolean)
  return parts.length ? parts.join(', ') : tr('settings.assistant.ordersSection.everyNewRecording')
}

interface RecordingRunResult {
  matched: boolean
  already: boolean
  recording: RunRecording
  run: AssistantRun | null
}

export function OrdersSection() {
  const qc = useQueryClient()
  const name = useAssistantName()
  const orders = useQuery<{ items: AssistantOrder[] }>({ queryKey: qkA.orders, queryFn: () => api.get('/assistant/orders') })
  const speakr = useSpeakrStatus()
  const [editing, setEditing] = useState<Draft | null>(null)
  const [deleting, setDeleting] = useState<AssistantOrder | null>(null)
  const [testing, setTesting] = useState<AssistantOrder | null>(null)
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: qkA.orders })
    void qc.invalidateQueries({ queryKey: ['assistant', 'runs'] })
  }
  // ACT order.pause @web.settings (and resume)
  const toggle = useMutation({
    mutationFn: (o: AssistantOrder) => api.patch(`/assistant/orders/${o.id}`, { enabled: !o.enabled }),
    onSuccess: refresh,
    onError: (e) => toastError(e, tr('settings.assistant.ordersSection.couldNotChangeTheOrder')),
  })
  // ACT order.move @web.settings: the top order acts first when several match one email
  const move = useMutation({
    mutationFn: ({ o, direction }: { o: AssistantOrder; direction: 'up' | 'down' }) => api.post(`/assistant/orders/${o.id}/move`, { direction }),
    onSuccess: refresh,
    onError: (e) => toastError(e, tr('settings.assistant.ordersSection.couldNotChangeTheOrder')),
  })
  // ACT order.permission @web.settings: the built-in order's Ask me or Allow
  const permission = useMutation({
    mutationFn: ({ o, value }: { o: AssistantOrder; value: 'ask' | 'allow' }) => api.patch(`/assistant/orders/${o.id}`, { permission: value }),
    onSuccess: () => { refresh(); void qc.invalidateQueries({ queryKey: ['permissions'] }) },
    onError: (e) => toastError(e, tr('settings.assistant.ordersSection.couldNotChangeTheOrder')),
  })
  // ACT order.run @web.settings (a routine, now)
  const runNow = useMutation({
    mutationFn: (o: AssistantOrder) => api.post<{ matched: boolean }>(`/assistant/orders/${o.id}/run`, {}),
    onSuccess: () => {
      toast({ message: tr('settings.assistant.ordersSection.isOnItTheResult', { name }) })
      refresh()
    },
    onError: (e) => toastError(e, tr('settings.assistant.ordersSection.couldNotRunIt')),
  })
  // ACT order.run (a recording order: the newest recording its filter matches, once per recording)
  const runRecording = useMutation({
    mutationFn: (o: AssistantOrder) => api.post<RecordingRunResult>(`/assistant/orders/${o.id}/run`, {}),
    onSuccess: (r) => {
      const title = `“${r.recording.title || tr('common.untitledRecording')}”`
      toast({
        message: r.already ? tr('settings.assistant.ordersSection.itAlreadyRanOnThe', { title })
          : r.run?.status === 'skipped' ? tr('settings.assistant.ordersSection.skipped', { title, error: r.run.error || (r.run.condition.reason ? tr('settings.assistant.ordersSection.theAnswerWasNo', { reason: r.run.condition.reason }) : tr('settings.assistant.ordersSection.theAnswerWasNo2')) })
            : r.run?.status === 'queued' ? tr('settings.assistant.ordersSection.isOnItWithThe', { name, title })
              : tr('settings.assistant.ordersSection.ranOnTheResultIs', { title }),
      })
      refresh()
    },
    onError: (e) => toastError(e, tr('settings.assistant.ordersSection.couldNotRunIt')),
  })
  const settings = useSettings()
  const updateSettings = useUpdateSettings()
  const items = orders.data?.items ?? []
  const byTrigger = (t: AssistantOrder['trigger']) => items.filter((o) => o.trigger === t)
  const recordingOrders = byTrigger('recording')

  return (
    <>
      <Section
        title={tr('settings.assistant.ordersSection.whenMailArrives')}
        desc={<>{tx('settings.assistant.ordersSection.standingOrdersRunOnEach', { name }, { i: (c) => <i>{c}</i> })}</>}
        actions={<button type="button" className="btn tonal sm" onClick={() => setEditing({ ...EMPTY })}><Plus size={16} />{' '}{tr('settings.assistant.ordersSection.newOrder')}</button>}
      >
        {orders.isLoading ? <Skeleton width="60%" /> : <OrderList items={byTrigger('message')} empty={tr('settings.assistant.ordersSection.noStandingOrdersYet')}
          onEdit={(o) => setEditing({ ...o })} onDelete={setDeleting} onToggle={(o) => toggle.mutate(o)} onTest={setTesting}
          onMove={(o, direction) => move.mutate({ o, direction })} onPermission={(o, value) => permission.mutate({ o, value })} />}
        {byTrigger('message').filter((o) => o.enabled).length > 1 && <p className="as-hint" data-testid="orders-duplicates-hint">{tr('settings.assistant.ordersSection.duplicatesHint')}</p>}
      </Section>
      {(speakr.data?.connected || recordingOrders.length > 0) && (
        <Section
          title={tr('settings.assistant.ordersSection.whenARecordingArrives')}
          desc={<>{tr('settings.assistant.ordersSection.recordingOrdersRunOnceOn', { name })}</>}
          actions={<button type="button" className="btn tonal sm" onClick={() => setEditing({ ...EMPTY, trigger: 'recording', deliver: ['notify'] })}><Plus size={16} />{' '}{tr('settings.assistant.ordersSection.newRecordingOrder')}</button>}
        >
          {orders.isLoading ? <Skeleton width="60%" /> : <OrderList items={recordingOrders} empty={tr('settings.assistant.ordersSection.noRecordingOrdersYet')}
            onEdit={(o) => setEditing({ ...o, recordings: o.recordings ?? {} })} onDelete={setDeleting} onToggle={(o) => toggle.mutate(o)}
            onRun={(o) => runRecording.mutate(o)} runLabel={tr('settings.assistant.ordersSection.runNowOnTheNewest')} />}
        </Section>
      )}
      <Section
        title={tr('settings.assistant.ordersSection.routines')}
        desc={<>{tr('settings.assistant.ordersSection.scheduledWorkForAMorning', { name })}</>}
        actions={<button type="button" className="btn tonal sm" onClick={() => setEditing({ ...EMPTY, trigger: 'schedule', deliver: ['digest'], query: 'is:needs_reply newer_than:3d' })}><Plus size={16} />{' '}{tr('settings.assistant.ordersSection.newRoutine')}</button>}
      >
        {orders.isLoading ? <Skeleton width="60%" /> : <OrderList items={byTrigger('schedule')} empty={tr('settings.assistant.ordersSection.noRoutinesYet')}
          onEdit={(o) => setEditing({ ...o })} onDelete={setDeleting} onToggle={(o) => toggle.mutate(o)}
          onRun={(o) => runNow.mutate(o)} />}
        {/* event.prep in digests (the assistant's routines): today's meetings, with links to their meeting prep, at the end of each digest (meeting_prep_digest). */}
        <label className="row" style={{ gap: 10, marginTop: 12, cursor: 'pointer' }} data-testid="meeting-prep-digest">
          <Switch checked={!!settings.meeting_prep_digest} onChange={(on) => updateSettings.mutate({ meeting_prep_digest: on })}
            label={tr('settings.assistant.ordersSection.addTodaysMeetingsToDigests')} />
          <span>{tr('settings.assistant.ordersSection.addTodaysMeetingsToDigests2')}</span>
        </label>
      </Section>
      <ActivitySection orders={items} />
      {testing && (
        <Dialog open onClose={() => setTesting(null)} wide title={tr('settings.assistant.ordersSection.test', { testingName: testing.name })}
          footer={<button type="button" className="btn text" onClick={() => setTesting(null)}>{tr('common.close')}</button>}>
          <OrderTest draft={testing} auto orderId={testing.id} onRan={refresh} />
        </Dialog>
      )}
      {editing && <OrderEditor draft={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh() }} />}
      {/* ACT order.delete @web.settings */}
      <ConfirmDialog
        open={!!deleting}
        title={tr('settings.assistant.ordersSection.deleteThisStandingOrder')}
        body={deleting ? tr('settings.assistant.ordersSection.stopsRunningItsHistoryStays', { deletingName: deleting.name }) : ''}
        confirmLabel={tr('common.delete2')}
        danger
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return
          try {
            await api.del(`/assistant/orders/${deleting.id}`)
            refresh()
          } catch (e) {
            toastError(e, tr('settings.assistant.ordersSection.couldNotDeleteIt'))
          }
          setDeleting(null)
        }}
      />
    </>
  )
}

function OrderList({ items, empty, onEdit, onDelete, onToggle, onRun, onTest, onMove, onPermission, runLabel = tr('settings.assistant.ordersSection.runNow') }: {
  items: AssistantOrder[]; empty: string; onEdit: (o: AssistantOrder) => void; onDelete: (o: AssistantOrder) => void
  onToggle: (o: AssistantOrder) => void; onRun?: (o: AssistantOrder) => void; onTest?: (o: AssistantOrder) => void
  /** Message orders: their order is their priority when several match one email. */
  onMove?: (o: AssistantOrder, direction: 'up' | 'down') => void
  onPermission?: (o: AssistantOrder, value: 'ask' | 'allow') => void
  runLabel?: string
}) {
  const name = useAssistantName()
  const labels = useLabelList()
  const labelName = (id: number) => labels.find((l) => l.id === id)?.name ?? tr('settings.assistant.ordersSection.label', { id })
  if (!items.length) return <p className="muted">{empty}</p>
  return (
    <ul className="as-orders">
      {items.map((o, i) => (
        <li key={o.id} className={`as-order${o.enabled ? '' : ' off'}${o.builtin ? ' builtin' : ''}`} data-testid={o.builtin ? 'order-builtin' : undefined}>
          <div className="as-order-head">
            {o.builtin ? <Forward size={18} /> : o.trigger === 'schedule' ? <CalendarClock size={18} /> : o.trigger === 'recording' ? <Mic size={18} aria-label={tr('settings.assistant.ordersSection.whenARecordingArrives')} /> : <Inbox size={18} />}
            <b>{orderName(o)}</b>
            {o.builtin && <span className="chip-mini">{tr('settings.assistant.ordersSection.builtIn')}</span>}
            {o.source === 'assistant' && <span className="chip-mini">{tr('settings.assistant.ordersSection.by', { name: assistantNameNow() })}</span>}
            <span className="spacer" />
            <Switch checked={o.enabled} onChange={() => onToggle(o)} label={o.enabled ? tr('common.pause') : tr('common.resume')} />
            {onMove && items.length > 1 && <>
              <button type="button" className="icon-btn sm" disabled={i === 0} aria-label={tr('settings.assistant.ordersSection.moveUp', { name: orderName(o) })} data-tip={tr('settings.assistant.ordersSection.moveUp', { name: orderName(o) })} onClick={() => onMove(o, 'up')}><ArrowUp size={16} /></button>
              <button type="button" className="icon-btn sm" disabled={i === items.length - 1} aria-label={tr('settings.assistant.ordersSection.moveDown', { name: orderName(o) })} data-tip={tr('settings.assistant.ordersSection.moveDown', { name: orderName(o) })} onClick={() => onMove(o, 'down')}><ArrowDown size={16} /></button>
            </>}
            {onTest && !o.builtin && <button type="button" className="icon-btn sm" aria-label={tr('settings.assistant.ordersSection.testAndRunOnRecent')} data-tip={tr('settings.assistant.ordersSection.testAndRunOnRecent')} onClick={() => onTest(o)}><FlaskConical size={16} /></button>}
            {onRun && <button type="button" className="icon-btn sm" aria-label={runLabel} data-tip={runLabel} onClick={() => onRun(o)}><Play size={16} /></button>}
            {!o.builtin && <button type="button" className="icon-btn sm" aria-label={tr('common.edit2')} data-tip={tr('common.edit2')} onClick={() => onEdit(o)}><Pencil size={16} /></button>}
            {!o.builtin && <button type="button" className="icon-btn sm" aria-label={tr('common.delete2')} data-tip={tr('common.delete2')} onClick={() => onDelete(o)}><Trash2 size={16} /></button>}
          </div>
          {o.builtin === 'forwarded_notes' && <BuiltinForwards o={o} name={name} onPermission={onPermission} />}
          {!o.builtin && <dl className="as-order-lines">
            {o.from_trusted && <><dt>{tr('common.mail2')}</dt><dd>{tr('settings.assistant.ordersSection.onlyMailIForwardFrom')}</dd></>}
            {o.from_me && <><dt>{tr('common.mail2')}</dt><dd>{tr('settings.assistant.ordersSection.onlyFromMe')}</dd></>}
            {o.trigger === 'schedule' && <><dt>{tr('common.when')}</dt><dd title={o.schedule}>{describeSchedule(o.schedule)} ({o.timezone || 'UTC'}){o.next_run_at ? tr('settings.assistant.ordersSection.next', { relative: formatRelative(o.next_run_at) }) : ''}</dd></>}
            {o.trigger === 'recording' && <><dt>{tr('common.recordings')}</dt><dd>{describeFilter(o.recordings)}</dd></>}
            {o.trigger === 'schedule' && o.recordings?.enabled && <><dt>{tr('common.recordings')}</dt><dd>{o.recordings.days && o.recordings.days > 1 ? tr('settings.assistant.ordersSection.theLastDays', { count: o.recordings.days }) : tr('common.today').toLowerCase()}{o.recordings.q || o.recordings.tag || o.recordings.participant ? `, ${describeFilter(o.recordings)}` : ''}</dd></>}
            {o.query && <><dt>{o.trigger === 'schedule' ? tr('settings.assistant.ordersSection.reads') : tr('common.mail2')}</dt><dd><code>{o.query}</code></dd></>}
            {o.question && !o.choices?.length && <><dt>{tr('settings.assistant.ordersSection.if')}</dt><dd>{o.question} <span className="muted">{tr('settings.assistant.ordersSection.sure', { round: Math.round(o.threshold * 100), name })}</span></dd></>}
            {o.question && !!o.choices?.length && (
              <><dt>{tr('settings.assistant.ordersSection.pick')}</dt><dd>{o.question} <span className="muted">{tr('settings.assistant.ordersSection.sure', { round: Math.round(o.threshold * 100), name })}</span>
                <ul className="as-choice-list">{o.choices.map((c) => (
                  <li key={c.key ?? c.name}><b>{c.name}</b>{Object.keys(c.actions).length ? ' → ' : ''}{Object.entries(c.actions).map(([k, v]) =>
                    k === 'add_labels' ? tr('settings.assistant.ordersSection.label2', { map: (v as number[]).map(labelName).join(', ') }) : k === 'category' ? tr('settings.assistant.ordersSection.moveTo', { v: String(v) }) : actionWord(k)).join(' · ')}</li>
                ))}</ul></dd></>
            )}
            {Object.keys(o.actions).length > 0 && (
              <><dt>{tr('settings.assistant.ordersSection.then')}</dt><dd>{Object.entries(o.actions).map(([k, v]) =>
                k === 'add_labels' ? tr('settings.assistant.ordersSection.label2', { map: (v as number[]).map(labelName).join(', ') }) : k === 'category' ? tr('settings.assistant.ordersSection.moveTo', { v: String(v) }) : actionWord(k)).join(' · ')}</dd></>
            )}
            {o.errand && <><dt>{assistantNameNow()}</dt><dd>{o.errand} <span className="muted">→ {o.deliver.map(deliverWord).join(', ')}</span></dd></>}
            <dt>{tr('settings.assistant.ordersSection.runs')}</dt><dd>{o.runs}{o.last_run_at ? tr('settings.assistant.ordersSection.last', { relative: formatRelative(o.last_run_at) }) : ''}</dd>
          </dl>}
        </li>
      ))}
    </ul>
  )
}

/** The built-in order "Follow my forwarded notes" (backend assistant/forwards.py): what it does, its
 * condition, and its permission. It can be turned off and moved; never deleted or edited. */
function BuiltinForwards({ o, name, onPermission }: { o: AssistantOrder; name: string; onPermission?: (o: AssistantOrder, value: 'ask' | 'allow') => void }) {
  const perm = o.permission ?? 'ask'
  return (
    <>
      <p className="as-hint as-order-desc">{tr('settings.assistant.ordersSection.builtinForwardsDesc', { name })}</p>
      <dl className="as-order-lines">
        <dt>{tr('common.mail2')}</dt>
        <dd>{tr('settings.assistant.ordersSection.builtinForwardsCondition')}{' '}
          <Link to="/settings/ambrose/forwards">{tr('settings.assistant.ordersSection.yourTrustedAddresses')}</Link></dd>
        <dt>{tr('settings.assistant.ordersSection.then')}</dt>
        <dd>{tr('settings.assistant.ordersSection.builtinForwardsThen', { name })}</dd>
        <dt>{tr('settings.assistant.ordersSection.permission')}</dt>
        <dd>
          <select className="input as-order-perm" value={perm} disabled={!o.enabled || !onPermission} data-testid="order-builtin-permission"
            aria-label={tr('settings.assistant.ordersSection.permissionOf', { name: orderName(o) })}
            onChange={(e) => onPermission?.(o, e.target.value as 'ask' | 'allow')}>
            <option value="ask">{tr('settings.assistant.ordersSection.permissionAsk')}</option>
            <option value="allow">{tr('settings.assistant.ordersSection.permissionAllow')}</option>
          </select>
          <span className="muted">{' '}{!o.enabled ? tr('settings.assistant.ordersSection.builtinOff')
            : perm === 'allow' ? tr('settings.assistant.ordersSection.permissionAllowDesc', { name }) : tr('settings.assistant.ordersSection.permissionAskDesc', { name })}</span>
        </dd>
        <dt>{tr('settings.assistant.ordersSection.runs')}</dt><dd>{o.runs}{o.last_run_at ? tr('settings.assistant.ordersSection.last', { relative: formatRelative(o.last_run_at) }) : ''}</dd>
      </dl>
    </>
  )
}

function OrderEditor({ draft, onClose, onSaved }: { draft: Draft; onClose: () => void; onSaved: () => void }) {
  const name = useAssistantName()
  const labels = useLabelList().filter((l) => !l.role)
  const [d, setD] = useState<Draft>(draft)
  const [busy, setBusy] = useState(false)
  const speakr = useSpeakrStatus()
  const routine = d.trigger === 'schedule'
  const recording = d.trigger === 'recording'
  const f = d.recordings ?? {}
  const setF = (k: 'q' | 'tag' | 'participant', v: string) => setD((x) => ({ ...x, recordings: { ...(x.recordings ?? {}), [k]: v } }))
  // A yes/no question, or a choice among options (each with its own actions).
  const [kind, setKind] = useState<'yesno' | 'choice'>((draft.choices ?? []).length ? 'choice' : 'yesno')
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }))
  const setChoice = (i: number, c: OrderChoice) => set('choices', d.choices.map((x, j) => (j === i ? c : x)))
  const pickKind = (k: 'yesno' | 'choice') => {
    setKind(k)
    if (k === 'choice' && d.choices.length < 2) set('choices', [...d.choices, ...Array.from({ length: 2 - d.choices.length }, () => ({ name: '', description: '', actions: {} }))])
  }
  const act = (k: keyof Draft['actions'], v: unknown) => setD((x) => {
    const a = { ...x.actions } as Record<string, unknown>
    if (v === false || v === '' || v === undefined || (Array.isArray(v) && !v.length)) delete a[k]
    else a[k] = v
    return { ...x, actions: a as Draft['actions'] }
  })
  const deliver = (k: 'brief' | 'notify' | 'digest', on: boolean) =>
    set('deliver', on ? [...new Set([...d.deliver, k])] : d.deliver.filter((x) => x !== k))
  const custom = !SCHEDULES.some((s) => s.cron === d.schedule)
  // ACT order.create @web.settings, ACT order.edit @web.settings
  const save = async () => {
    setBusy(true)
    const body = recording ? {
      // A recording order: no mail search, no choices, notify is its only instant action.
      ...d, id: undefined, timezone: undefined, schedule: undefined, query: '', choices: [],
      actions: d.actions.notify ? { notify: true } : {},
      deliver: d.errand ? ['notify'] : [],
      recordings: Object.fromEntries(Object.entries(f).filter(([k, v]) => ['q', 'tag', 'participant'].includes(k) && String(v ?? '').trim()).map(([k, v]) => [k, String(v).trim()])),
    } : { ...d, id: undefined, timezone: d.timezone || undefined,
      choices: kind === 'choice' && !routine ? d.choices.filter((c) => c.name.trim()) : [],
      recordings: routine ? d.recordings : {} }
    try {
      if (d.id) await api.patch(`/assistant/orders/${d.id}`, body)
      else await api.post('/assistant/orders', body)
      toast({ message: d.id ? tr('settings.assistant.ordersSection.orderSaved') : routine ? tr('settings.assistant.ordersSection.routineCreated') : recording ? tr('settings.assistant.ordersSection.recordingOrderCreated') : tr('settings.assistant.ordersSection.standingOrderCreated') })
      onSaved()
    } catch (e) {
      toastError(e, tr('settings.assistant.ordersSection.couldNotSaveIt'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open onClose={onClose} wide title={d.id ? tr('settings.assistant.ordersSection.edit', { value: routine ? 'routine' : recording ? tr('settings.assistant.ordersSection.recordingOrder') : tr('settings.assistant.ordersSection.standingOrder') }) : routine ? tr('settings.assistant.ordersSection.newRoutine') : recording ? tr('settings.assistant.ordersSection.newRecordingOrder') : tr('settings.assistant.ordersSection.newStandingOrder')}
      footer={<>
        <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
        <button type="button" className="btn filled" disabled={busy} onClick={() => void save()}>{tr('common.save')}</button>
      </>}>
      <div className="as-order-form">
        <label>{tr('common.name')}<input className="input" value={d.name} placeholder={routine ? tr('settings.assistant.ordersSection.morningBriefing') : recording ? tr('settings.assistant.ordersSection.clientCallFollowUps') : tr('settings.assistant.ordersSection.billsLabelBillDue')} onChange={(e) => set('name', e.target.value)} /></label>
        {!routine && (speakr.data?.connected || recording) && (
          // The trigger: new mail, or a new Speakr recording (only with a Speakr account).
          <label>{tr('settings.assistant.ordersSection.runs')}
            <select className="input" value={d.trigger} aria-label={tr('settings.assistant.ordersSection.whenItRuns')} onChange={(e) => {
              const t = e.target.value as Draft['trigger']
              setD((x) => ({ ...x, trigger: t, deliver: x.errand ? [t === 'recording' ? 'notify' : 'brief'] : [] }))
            }}>
              <option value="message">{tr('settings.assistant.ordersSection.whenAnEmailArrives')}</option>
              <option value="recording">{tr('settings.assistant.ordersSection.whenANewRecordingArrives')}</option>
            </select>
          </label>
        )}
        {recording && (
          <>
            <div className="as-order-row">
              <label>{tr('settings.assistant.ordersSection.titleOrParticipantsContain2')}
                <input className="input" value={f.q ?? ''} placeholder="client" onChange={(e) => setF('q', e.target.value)} />
              </label>
              <label>{tr('common.tag')}
                <input className="input" value={f.tag ?? ''} placeholder={tr('common.work')} onChange={(e) => setF('tag', e.target.value)} />
              </label>
              <label>{tr('settings.assistant.ordersSection.withAParticipant')}
                <input className="input" value={f.participant ?? ''} placeholder={tr('settings.assistant.ordersSection.dana')} onChange={(e) => setF('participant', e.target.value)} />
              </label>
            </div>
            <label>{tr('settings.assistant.ordersSection.onlyIfSaysYesTo2', { name })}
              <input className="input" value={d.question} placeholder={tr('settings.assistant.ordersSection.isThisAMeetingWith')} onChange={(e) => set('question', e.target.value)} />
            </label>
            <span className="as-hint">{tr('settings.assistant.ordersSection.allOptionalWithoutAFilter', { name })}</span>
          </>
        )}
        {routine && (
          <label>{tr('common.when')}
            <span className="as-order-when">
              <select className="input" value={custom ? 'custom' : d.schedule} onChange={(e) => set('schedule', e.target.value === 'custom' ? '0 9 * * *' : e.target.value)}>
                {SCHEDULES.map((s) => <option key={s.cron} value={s.cron}>{s.label}</option>)}
                <option value="custom">{tr('settings.assistant.ordersSection.customCron')}</option>
              </select>
              {custom && <input className="input" value={d.schedule} onChange={(e) => set('schedule', e.target.value)} placeholder={tr('settings.assistant.ordersSection.minuteHourDayMonthWeekday')} />}
            </span>
          </label>
        )}
        {!recording && <div className="as-order-row">
          <label>{routine ? tr('settings.assistant.ordersSection.mailItReadsSearch') : tr('settings.assistant.ordersSection.mailItAppliesToSearch')}
            <input className="input" value={d.query} placeholder={routine ? tr('settings.assistant.ordersSection.isNeedsReplyNewerThan') : 'from:billing@example.com'} onChange={(e) => set('query', e.target.value)} />
          </label>
          {!routine && (
            <label>{kind === 'choice' ? tr('settings.assistant.ordersSection.picksOneFor', { name }) : tr('settings.assistant.ordersSection.onlyIfSaysYesTo', { name })}
              <input className="input" value={d.question}
                placeholder={kind === 'choice' ? tr('settings.assistant.ordersSection.whichOfMyProjectsIs') : tr('settings.assistant.ordersSection.isThisABillOr')}
                onChange={(e) => set('question', e.target.value)} />
            </label>
          )}
        </div>}
        {!routine && !recording && (
          <div className="as-order-kind" role="radiogroup" aria-label={tr('settings.assistant.ordersSection.kindOfQuestion')}>
            <label className="choice"><input type="radio" name="okind" checked={kind === 'yesno'} onChange={() => pickKind('yesno')} />{' '}{tr('settings.assistant.ordersSection.aYesNoQuestion')}</label>
            <label className="choice"><input type="radio" name="okind" checked={kind === 'choice'} onChange={() => pickKind('choice')} />{' '}{tr('settings.assistant.ordersSection.pickOneOfSeveralEach')}</label>
          </div>
        )}
        {!recording && <span className="as-hint">{tx('settings.assistant.ordersSection.bothOptionalSearchSyntaxPlus', undefined, { code: (c) => <code>{c}</code> })}</span>}
        {!routine && !recording && (
          <div className="as-order-checks">
            {/* A condition: only mail you forward from Trusted addresses (core/trusted.py). */}
            <Check checked={!!d.from_trusted} onChange={(v) => set('from_trusted', v)}
              label={tr('settings.assistant.ordersSection.onlyMailIForwardFrom')} />
            {/* A condition for orders that match on a recipient address: only from you or your trusted addresses. */}
            {(d.from_me || matchesRecipient(d.query)) && (
              <Check checked={!!d.from_me} onChange={(v) => set('from_me', v)} label={tr('settings.assistant.ordersSection.onlyFromMe')} />
            )}
          </div>
        )}
        {needsSenderCondition(d) && (
          <div className="as-order-warn" role="note" data-testid="order-sender-warning">
            <ShieldAlert size={16} aria-hidden />
            <span>{tr('settings.assistant.ordersSection.anyoneCanTrigger')}</span>
            <button type="button" className="btn tonal sm" onClick={() => set('from_me', true)}>{tr('settings.assistant.ordersSection.onlyFromMe')}</button>
          </div>
        )}
        {!routine && !recording && kind === 'choice' && (
          <fieldset>
            <legend>{tr('settings.assistant.ordersSection.choicesPicksOneOrNone', { name })}</legend>
            {d.choices.map((c, i) => (
              <div key={i} className="as-choice">
                <div className="as-choice-row">
                  <input className="input" value={c.name} placeholder={[tr('settings.assistant.ordersSection.clients'), tr('settings.assistant.ordersSection.hiring'), tr('settings.assistant.ordersSection.product')][i] ?? tr('common.name')} aria-label={tr('settings.assistant.ordersSection.choiceName', { value: i + 1 })}
                    onChange={(e) => setChoice(i, { ...c, name: e.target.value })} />
                  <input className="input as-choice-desc" value={c.description} aria-label={tr('settings.assistant.ordersSection.choiceDescription', { value: i + 1 })}
                    placeholder={[tr('settings.assistant.ordersSection.clientWorkProposalsInvoicesMeetings'), tr('settings.assistant.ordersSection.candidatesInterviewsAndOffers'), tr('settings.assistant.ordersSection.theProductIssuesReleasesUsers')][i] ?? tr('settings.assistant.ordersSection.whatFitsThisChoice')}
                    onChange={(e) => setChoice(i, { ...c, description: e.target.value })} />
                  <button type="button" className="icon-btn sm" aria-label={tr('settings.assistant.ordersSection.removeChoice', { value: i + 1 })} disabled={d.choices.length <= 2}
                    onClick={() => set('choices', d.choices.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
                </div>
                <div className="as-order-acts">
                  <select className="input" value="" aria-label={tr('settings.assistant.ordersSection.choiceAddALabel', { value: i + 1 })}
                    onChange={(e) => e.target.value && setChoice(i, { ...c, actions: { ...c.actions, add_labels: [...new Set([...(c.actions.add_labels ?? []), Number(e.target.value)])] } })}>
                    <option value="">{tr('settings.assistant.ordersSection.addALabel')}</option>
                    {labels.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                  </select>
                  {(c.actions.add_labels ?? []).map((id) => (
                    <button key={id} type="button" className="chip" onClick={() => setChoice(i, { ...c, actions: { ...c.actions, add_labels: (c.actions.add_labels ?? []).filter((x) => x !== id) } })}>
                      {labels.find((l) => l.id === id)?.name ?? id} ×
                    </button>
                  ))}
                  <Check checked={!!c.actions.star} onChange={(v) => setChoice(i, { ...c, actions: { ...c.actions, star: v || undefined } })} label={tr('common.star')} />
                  <Check checked={!!c.actions.important} onChange={(v) => setChoice(i, { ...c, actions: { ...c.actions, important: v || undefined } })} label={tr('common.important')} />
                  <Check checked={!!c.actions.archive} onChange={(v) => setChoice(i, { ...c, actions: { ...c.actions, archive: v || undefined } })} label={tr('common.archive')} />
                </div>
              </div>
            ))}
            {d.choices.length < 8 && (
              <button type="button" className="btn text sm" onClick={() => set('choices', [...d.choices, { name: '', description: '', actions: {} }])}><Plus size={16} />{' '}{tr('settings.assistant.ordersSection.addAChoice')}</button>
            )}
            <span className="as-hint">{tr('settings.assistant.ordersSection.theDescriptionsGoToWith', { name })}</span>
          </fieldset>
        )}
        {recording ? (
          <fieldset>
            <legend>{tr('settings.assistant.ordersSection.thenAtOnce')}</legend>
            <div className="as-order-checks">
              <Check checked={!!d.actions.notify} onChange={(v) => act('notify', v)} label={tr('settings.assistant.ordersSection.notifyMe')} />
            </div>
            <span className="as-hint">{tr('settings.assistant.ordersSection.mailActionsDoNotApply')}</span>
          </fieldset>
        ) : <fieldset>
          <legend>{kind === 'choice' && !routine ? tr('settings.assistant.ordersSection.whateverIsPickedAlso') : tr('settings.assistant.ordersSection.thenAtOnce')}</legend>
          <div className="as-order-checks">
            <Check checked={!!d.actions.star} onChange={(v) => act('star', v)} label={tr('common.star')} />
            <Check checked={!!d.actions.important} onChange={(v) => act('important', v)} label={tr('common.markImportant')} />
            <Check checked={!!d.actions.mark_read} onChange={(v) => act('mark_read', v)} label={tr('common.markAsRead')} />
            <Check checked={!!d.actions.archive} onChange={(v) => act('archive', v)} label={tr('common.archive')} />
            {!routine && <Check checked={!!d.actions.notify} onChange={(v) => act('notify', v)} label={tr('settings.assistant.ordersSection.notifyMe')} />}
            {/* ACT invite.add_to_calendar @web.settings */}
            {!routine && <Check checked={!!d.actions.add_to_calendar} onChange={(v) => act('add_to_calendar', v)} label={tr('settings.assistant.ordersSection.addTheInvitationToMy')} />}
          </div>
          <div className="as-order-acts">
            <select className="input" value="" onChange={(e) => e.target.value && act('add_labels', [...new Set([...(d.actions.add_labels ?? []), Number(e.target.value)])])}>
              <option value="">{tr('settings.assistant.ordersSection.addALabel')}</option>
              {labels.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            {(d.actions.add_labels ?? []).map((id) => (
              <button key={id} type="button" className="chip" onClick={() => act('add_labels', (d.actions.add_labels ?? []).filter((x) => x !== id))}>
                {labels.find((l) => l.id === id)?.name ?? id} ×
              </button>
            ))}
          </div>
        </fieldset>}
        <label>{tr('settings.assistant.ordersSection.workForOptional', { name })}
          <textarea className="textarea" rows={4} value={d.errand}
            placeholder={routine ? tr('settings.assistant.ordersSection.listWhatNeedsMyReply')
              : recording ? tr('settings.assistant.ordersSection.linkItToTheMeeting')
                : tr('settings.assistant.ordersSection.noteTheAmountAndDue')}
            onChange={(e) => set('errand', e.target.value)} />
        </label>
        {d.errand && (
          <fieldset>
            <legend>{tr('settings.assistant.ordersSection.deliverTheResultAs')}</legend>
            <div className="as-order-checks">
              {!routine && !recording && <Check checked={d.deliver.includes('brief')} onChange={(v) => deliver('brief', v)} label={tr('settings.assistant.ordersSection.aBriefOnTheEmail')} />}
              <Check checked={d.deliver.includes('notify') || d.deliver.includes('digest') || recording} disabled={recording} onChange={(v) => deliver(routine ? 'digest' : 'notify', v)} label={tr('settings.assistant.ordersSection.aNotification')} />
            </div>
            <span className="as-hint">{routine || recording ? tr('settings.assistant.ordersSection.theNotificationCarriesTheFirst') : tr('settings.assistant.ordersSection.aBriefAppearsAboveThe')}</span>
          </fieldset>
        )}
        {recording ? <RecordingTest draft={d} /> : !routine && <OrderTest draft={d} />}
      </div>
    </Dialog>
  )
}

interface TestItem {
  conversation_id: number
  subject: string
  from: string
  date: string | null
  matched: boolean
  answer?: boolean
  choice?: string | null
  confidence?: number
  reason?: string
}

/** Dry run of a message order on recent mail: which conversations the search picks, and the
 * question's answer for each. Nothing is changed. */
function OrderTest({ draft, auto, orderId, onRan }: {
  draft: Pick<Draft, 'query' | 'question' | 'threshold'> & { choices?: OrderChoice[] }; auto?: boolean
  /** A saved order: pick conversations and run it on them for real. */
  orderId?: number; onRan?: () => void
}) {
  const name = useAssistantName()
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [running, setRunning] = useState(false)
  // After Run: follow the runs this dialog started and show each result under its email.
  const [started, setStarted] = useState<{ since: number; ids: Set<number> } | null>(null)
  const runs = useQuery<{ items: AssistantRun[] }>({
    queryKey: ['assistant', 'runs', 'test', orderId, started?.since],
    queryFn: () => api.get('/assistant/runs', { order_id: orderId, limit: 50 }),
    enabled: !!orderId && !!started,
    refetchInterval: (q) => {
      const mine = (q.state.data?.items ?? []).filter((r) => started && r.created_at && Date.parse(r.created_at) >= started.since - 2000)
      return mine.length < (started?.ids.size ?? 0) || mine.some((r) => r.status === 'queued' || r.status === 'running') ? 2500 : false
    },
  })
  const result = useMemo(() => {
    const m = new Map<number, AssistantRun>()
    for (const r of runs.data?.items ?? []) {
      if (!started || !r.conversation_id || !r.created_at || Date.parse(r.created_at) < started.since - 2000) continue
      if (!m.has(r.conversation_id)) m.set(r.conversation_id, r) // newest first
    }
    return m
  }, [runs.data, started])
  const [where, setWhere] = useState<'inbox' | 'all'>('inbox')
  const [res, setRes] = useState<{ searched: number; matched: number; items: TestItem[]; at: Date } | null>(null)
  const [busy, setBusy] = useState(false)
  const can = !!(draft.query.trim() || draft.question.trim())
  // ACT order.test @web.settings
  const run = async (scope = where) => {
    setBusy(true)
    try {
      setRes(null)
      const r = await api.post<{ searched: number; matched: number; items: TestItem[] }>('/assistant/orders/preview', { query: draft.query, question: draft.question, threshold: draft.threshold, scope, limit: 15,
        choices: (draft.choices ?? []).filter((c) => c.name.trim()) })
      setRes({ ...r, at: new Date() })
      setPicked(new Set(r.items.filter((i) => i.matched).map((i) => i.conversation_id)))
    } catch (e) {
      toastError(e, tr('settings.assistant.ordersSection.couldNotTestIt'))
    } finally {
      setBusy(false)
    }
  }
  // ACT order.run (a message order, on the emails picked from the test)
  const runPicked = async () => {
    if (!orderId || !picked.size) return
    setRunning(true)
    const since = Date.now()
    try {
      const r = await api.post<{ queued: number; done: number; skipped: number; already: number; no_match: number }>(
        `/assistant/orders/${orderId}/run`, { conversation_ids: [...picked] })
      const parts = [
        r.queued && tr('settings.assistant.ordersSection.queuedFor', { queued: r.queued, name }), r.done && tr('settings.assistant.ordersSection.done', { done: r.done }),
        r.skipped && tr('settings.assistant.ordersSection.skippedByTheQuestion', { skipped: r.skipped }), r.already && tr('settings.assistant.ordersSection.hadAlreadyRun', { already: r.already }),
      ].filter(Boolean)
      toast({ message: `${parts.join(', ') || tr('settings.assistant.ordersSection.nothingToRun')}.${r.queued ? tr('settings.assistant.ordersSection.theResultsAppearBelowAs') : ''}` })
      setStarted({ since, ids: new Set(picked) })
      onRan?.()
    } catch (e) {
      toastError(e, tr('settings.assistant.ordersSection.couldNotRunIt'))
    } finally {
      setRunning(false)
    }
  }
  const toggle = (id: number) => setPicked((x) => {
    const n = new Set(x)
    if (n.has(id)) n.delete(id)
    else n.add(id)
    return n
  })
  useEffect(() => {
    if (auto && can) void run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <div className="as-order-test">
      <div className="as-order-test-bar">
        <button type="button" className="btn tonal sm" disabled={!can || busy} onClick={() => void run()}>
          <FlaskConical size={16} /> {busy ? tr('common.checking') : draft.question.trim() ? tr('settings.assistant.ordersSection.checkMatchesAsksTheModel') : tr('settings.assistant.ordersSection.checkMatches')}
        </button>
        <select className="input" value={where} onChange={(e) => { const v = e.target.value as 'inbox' | 'all'; setWhere(v); if (res) void run(v) }}>
          <option value="inbox">{tr('common.inbox')}</option>
          <option value="all">{tr('common.allMail')}</option>
        </select>
        {res && (
          <span className="muted">
            {res.searched ? tr('settings.assistant.ordersSection.ofTheNewestMatchesWould', { matched: res.matched, searched: res.searched }) : tr('settings.assistant.ordersSection.nothingInMatchesTheSearch', { value: where === 'inbox' ? tr('settings.assistant.ordersSection.theInbox') : tr('settings.assistant.ordersSection.yourMail') })}
            {' · '}{tr('settings.assistant.ordersSection.checkedAt', { time: res.at.toLocaleTimeString(appLocale(), { hour: '2-digit', minute: '2-digit', ...clockOpts(), second: '2-digit' }) })}
          </span>
        )}
      </div>
      {res && !draft.question.trim() && res.searched > 0 && (
        <span className="as-hint">{tr('settings.assistant.ordersSection.noOnlyIfQuestionSo')}</span>
      )}
      {res && where === 'inbox' && res.searched < 3 && (
        <span className="as-hint">{tr('settings.assistant.ordersSection.fewMatchesInTheInbox')}</span>
      )}
      {res && res.items.length > 0 && (
        <ul className="as-order-test-list">
          {res.items.map((i) => (
            <li key={i.conversation_id} className={`${i.matched ? 'yes' : 'no'}${orderId ? ' pick' : ''}`}>
              {orderId && <input type="checkbox" className="checkbox" aria-label={tr('settings.assistant.ordersSection.runOnThisOne')} checked={picked.has(i.conversation_id)} onChange={() => toggle(i.conversation_id)} />}
              {i.matched ? <CheckIcon size={16} aria-label={tr('settings.assistant.ordersSection.wouldRun')} /> : <Minus size={16} aria-label={tr('settings.assistant.ordersSection.skipped2')} />}
              <Link to={mailPath('all', i.conversation_id)} className="as-order-test-subj">{i.subject || tr('common.noSubject')}</Link>
              <span className="muted as-order-test-from">{i.from}{i.date ? ` · ${formatRelative(i.date)}` : ''}</span>
              {i.confidence != null && (
                <span className="muted as-order-test-why" title={i.reason}>
                  {'choice' in i ? (i.choice ?? tr('settings.assistant.ordersSection.noneOfThem')): i.answer ? 'yes' : 'no'} {Math.round(i.confidence * 100)}%{i.reason ? ` — ${i.reason}` : ''}
                </span>
              )}
              {result.has(i.conversation_id) && <TestRunResult r={result.get(i.conversation_id)!} />}
            </li>
          ))}
        </ul>
      )}
      {orderId && res && res.items.length > 0 && (
        <div className="as-order-test-bar">
          <button type="button" className="btn text sm" onClick={() => setPicked(new Set(res.items.map((i) => i.conversation_id)))}>{tr('common.selectAll')}</button>
          <button type="button" className="btn text sm" onClick={() => setPicked(new Set())}>{tr('settings.assistant.ordersSection.selectNone')}</button>
          <span className="spacer" />
          <button type="button" className="btn filled sm" disabled={!picked.size || running} onClick={() => void runPicked()}>
            <Play size={16} /> {running ? tr('settings.assistant.ordersSection.starting') : tr('settings.assistant.ordersSection.runOnSelected', { pickedSize: picked.size })}
          </button>
        </div>
      )}
    </div>
  )
}

interface RecordingTestItem extends RunRecording {
  matched: boolean
  local_only: boolean
  answer?: boolean
  confidence?: number
  reason?: string
}

/** Dry run of a recording order on the newest recordings: which ones the filter picks, and the
 * question's answer for those. Nothing is changed. */
function RecordingTest({ draft }: { draft: Pick<Draft, 'question' | 'threshold' | 'recordings'> }) {
  const [res, setRes] = useState<{ searched: number; matched: number; items: RecordingTestItem[] } | null>(null)
  const [busy, setBusy] = useState(false)
  // ACT order.test (a recording order)
  const run = async () => {
    setBusy(true)
    try {
      setRes(null)
      const f = draft.recordings ?? {}
      const recordings = Object.fromEntries((['q', 'tag', 'participant'] as const).filter((k) => String(f[k] ?? '').trim()).map((k) => [k, String(f[k]).trim()]))
      setRes(await api.post('/assistant/orders/preview', { trigger: 'recording', recordings, question: draft.question, threshold: draft.threshold, limit: 10 }))
    } catch (e) {
      toastError(e, tr('settings.assistant.ordersSection.couldNotTestIt'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="as-order-test" data-testid="recording-test">
      <div className="as-order-test-bar">
        <button type="button" className="btn tonal sm" disabled={busy} onClick={() => void run()}>
          <FlaskConical size={16} /> {busy ? tr('common.checking') : draft.question.trim() ? tr('settings.assistant.ordersSection.checkTheNewestRecordingsAsks') : tr('settings.assistant.ordersSection.checkTheNewestRecordings')}
        </button>
        {res && <span className="muted">{res.searched ? tr('settings.assistant.ordersSection.ofTheNewestRecordingsWould', { matched: res.matched, searched: res.searched }) : tr('settings.assistant.ordersSection.noCompletedRecordingsYet')}</span>}
      </div>
      {res && res.items.length > 0 && (
        <ul className="as-order-test-list">
          {res.items.map((i) => (
            <li key={`${i.connector_id}:${i.recording_id}`} className={i.matched ? 'yes' : 'no'}>
              {i.matched ? <CheckIcon size={16} aria-label={tr('settings.assistant.ordersSection.wouldRun')} /> : <Minus size={16} aria-label={tr('settings.assistant.ordersSection.skipped2')} />}
              <button type="button" className="btn text sm as-order-test-subj" onClick={() => useRecordingSheet.getState().show(i.connector_id, i.recording_id)}>{i.title || tr('common.untitledRecording')}</button>
              <span className="muted as-order-test-from">{i.meeting_date ? formatRelative(i.meeting_date) : ''}</span>
              {i.confidence != null && (
                <span className="muted as-order-test-why" title={i.reason}>{i.answer ? 'yes' : 'no'} {Math.round(i.confidence * 100)}%{i.reason ? ` — ${i.reason}` : ''}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function TestRunResult({ r }: { r: AssistantRun }) {
  const acts = r.actions.filter((a) => a.action !== 'errand').map((a) => a.action.replace('_', ' ')).join(', ')
  return (
    <div className="as-order-test-run">
      <span className={`chip-mini run-${r.status}`}>
        {r.status === 'queued' ? tr('settings.assistant.ordersSection.queued') : r.status === 'running' ? tr('settings.assistant.ordersSection.working') : r.status === 'done' ? tr('common.done') : r.status === 'skipped' ? tr('settings.assistant.ordersSection.skipped2') : tr('common.failed')}
      </span>
      {acts && <span className="muted">{' '}{tr('settings.assistant.ordersSection.did', { acts })}</span>}
      {(r.status === 'failed' || r.status === 'skipped') && r.error && <span className="muted"> {r.error}</span>}
      {r.output && <div className="as-run-output"><Markdown text={r.output} citations={r.citations} /></div>}
      {r.delivered.length > 0 && <span className="muted"><Bell size={12} />{' '}{tr('settings.assistant.ordersSection.delivered', { delivered: r.delivered.join(', ') })}</span>}
    </div>
  )
}

function ActivitySection({ orders }: { orders: AssistantOrder[] }) {
  const [params] = useSearchParams()
  const focus = Number(params.get('run')) || null
  const [filter, setFilter] = useState<number | null>(null)
  const runs = useQuery<{ items: AssistantRun[] }>({
    queryKey: qkA.runs(filter),
    queryFn: () => api.get('/assistant/runs', filter ? { order_id: filter, limit: 50 } : { limit: 50 }),
    refetchInterval: (q) => (q.state.data?.items.some((r) => r.status === 'queued' || r.status === 'running') ? 3000 : false),
  })
  return (
    <Section
      id="activity"
      title={tr('common.activity')}
      desc={tr('settings.assistant.ordersSection.everyRunWhatMatchedHow', { name: assistantNameNow() })}
      actions={
        <select className="input" style={{ width: 220 }} value={filter ?? ''} onChange={(e) => setFilter(e.target.value ? Number(e.target.value) : null)}>
          <option value="">{tr('settings.assistant.ordersSection.allOrdersAndRoutines')}</option>
          {orders.map((o) => <option key={o.id} value={o.id}>{orderName(o)}</option>)}
        </select>
      }
    >
      {runs.isLoading ? <Skeleton width="70%" /> : !(runs.data?.items.length) ? <p className="muted">{tr('settings.assistant.ordersSection.nothingHasRunYet')}</p> : (
        <ul className="as-runs">
          {runs.data.items.map((r) => <RunRow key={r.id} r={r} initiallyOpen={r.id === focus} />)}
        </ul>
      )}
    </Section>
  )
}

/** A run's action in words; a duplicate names the order that did it (or the approval that waits). */
export function actionText(a: AssistantRun['actions'][number]): string {
  const what = actionWord(a.action)
  if (a.result !== 'duplicate') return what
  return a.by ? tr('settings.assistant.ordersSection.duplicateOf', { action: what, name: orderName(a.by) })
    : tr('settings.assistant.ordersSection.duplicateWaiting', { action: what })
}

function RunRow({ r, initiallyOpen }: { r: AssistantRun; initiallyOpen: boolean }) {
  const [open, setOpen] = useState(initiallyOpen)
  useEffect(() => {
    if (initiallyOpen) document.getElementById(`run-${r.id}`)?.scrollIntoView({ block: 'center' })
  }, [initiallyOpen, r.id])
  const c = r.condition || {}
  const summary = useMemo(() => {
    if (r.status === 'skipped') return r.error || (c.question ? tr('settings.assistant.ordersSection.notThisTime', { reason: c.reason || tr('settings.assistant.ordersSection.theAnswerWasNo2') }) : tr('settings.assistant.ordersSection.skipped2'))
    if (r.status === 'failed') return r.error || tr('common.failed')
    if (r.status === 'queued' || r.status === 'running') return tr('settings.assistant.ordersSection.workingOnIt')
    const acts = r.actions.filter((a) => a.result !== 'duplicate').map((a) => a.action.replace('_', ' ')).join(', ')
    const dup = r.actions.filter((a) => a.result === 'duplicate').map(actionText).join(', ')
    return [acts && tr('settings.assistant.ordersSection.did', { acts }), dup, r.output && r.output.split('\n').find((l) => l.trim())?.slice(0, 120)].filter(Boolean).join(' · ') || tr('common.done')
  }, [r, c.question, c.reason])
  return (
    <li id={`run-${r.id}`} className={`as-run ${r.status}`}>
      <button type="button" className="as-run-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <span className={`as-run-status ${r.status}`}>{r.status}</span>
        <b>{r.order_name ? orderName({ name: r.order_name, builtin: r.order_builtin }) : tr('settings.assistant.ordersSection.deletedOrder')}</b>
        <span className="muted as-run-sum">{summary}</span>
        <span className="muted">{r.created_at ? formatRelative(r.created_at) : ''}</span>
      </button>
      {open && (
        <div className="as-run-body">
          {c.question && <p><Bot size={14} />{' '}{tx('settings.assistant.ordersSection.sure2', { question: c.question, value: 'choice' in c ? (c.choice ?? tr('settings.assistant.ordersSection.noneOfThem')) : c.answer ? 'yes' : 'no', round: Math.round((c.confidence ?? 0) * 100), value2: c.reason ? ` (${c.reason})` : '' }, { b: (c) => <b>{c}</b> })}</p>}
          {c.recording && (
            <p><Mic size={14} />{' '}{tr('settings.assistant.ordersSection.recording')}{' '}
              <button type="button" className="btn text sm" onClick={() => useRecordingSheet.getState().show(c.recording!.connector_id, c.recording!.recording_id)}>{c.recording.title || tr('common.untitledRecording')}</button>
            </p>
          )}
          {c.conversations !== undefined && <p>{tr('settings.assistant.ordersSection.readConversations', { count: c.conversations, query: c.query })}</p>}
          {r.actions.length > 0 && <p>{tr('settings.assistant.ordersSection.actions', { map: r.actions.map((a) => a.result === 'duplicate' ? actionText(a) : `${a.action}${a.affected !== undefined ? ` (${a.affected})` : ''}${a.result ? ` (${a.result})` : ''}`).join(', ') })}</p>}
          {r.output && <div className="as-run-output"><Markdown text={r.output} citations={r.citations} /></div>}
          {r.delivered.length > 0 && <p className="muted"><Bell size={14} />{' '}{tr('settings.assistant.ordersSection.delivered', { delivered: r.delivered.join(', ') })}</p>}
          {r.conversation_id && <Link to={mailPath('inbox', r.conversation_id)}>{tr('common.openTheEmail')}</Link>}
        </div>
      )}
    </li>
  )
}
