// The task detail pane (web.pane): every field saves as it changes (pickers at once,
// text after 600 ms or on blur) with a small "Saved" mark, and every change has its own undo.
// Esc closes it; there is never anything unsaved to ask about. Placement per theme is the app's.

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import { Bot, CalendarClock, CalendarPlus, Check, Copy, Mail, Reply, SkipForward, Trash2, Undo2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { Popover, useAnchor } from '../../components/Popover'
import { formatDate, formatTime, parseISO, todayISO, type DatePrefs } from '../../lib/dates'
import { convSlug } from '../../lib/pubid'
import { useCompose } from '../../stores/compose'
import { toast, toastError, useUI } from '../../stores/ui'
import type { ComposePayload } from '../../api/types'
import { offersFor } from './InlineTitle'
import { buildTitle } from '../../lib/quickadd'
import { createBody, dueScope, undoBody, useTaskActions } from './actions'
import { icalPriority, placeOf, isDone, PRIORITY_NAMES, priorityLevel, dueDay, type PriorityLevel, type TagInfo, type TaskItem, type TaskList } from './model'
import { ListPicker, Notes, ParentPicker, ReminderField, RepeatField, Subtasks, TagField } from './PaneFields'
import { WhenPicker } from './WhenPicker'
import { openAsk, useAskTarget } from '../ask/askStore'
import { TaskRecording } from '../speakr/TaskRecording'
import { LinkedPanel } from '../notes/LinkedPanel'
import { describe } from './when'
import { registerEditor } from '../../lib/live/editors'
import { LiveDoc, type ConflictState } from '../../lib/live/machine'
import { same } from '../../lib/live/merge3'
import { byName } from '../../lib/live/store'
import { fetchLive, reportTyping, useLiveItem } from '../../lib/live/useLiveItem'
import { ConflictBar, EditingBanner } from '../live/LiveBars'
import { t as tr } from '../../i18n'
import { ItemBoundary } from '../../components/bits'

/** The pane's text fields as a live item (backend calendar/live_items.py: summary and notes). */
type TaskText = { summary: string; notes: string }
const TEXT_MAX = 2000 // the task list cuts notes here (backend mirror.LIST_DESCRIPTION_MAX)

interface Props {
  task: TaskItem | null
  /** The last copy seen, for "Restore as new" when the task is gone. */
  lastSeen: TaskItem | null
  /** The tasks are still loading: the pane waits instead of saying the task is gone. */
  loading?: boolean
  subtasks: TaskItem[]
  lists: TaskList[]
  tags: TagInfo[]
  now: Date
  prefs: DatePrefs
  readOnly: boolean
  onClose: () => void
  onOpen: (uid: string) => void
  placement: 'pane' | 'below' | 'card' | 'sheet'
}

interface Undoable {
  field: string
  before: TaskItem
  changes: Record<string, unknown>
  /** The task as the server returned it after this change: the undo writes against it. */
  after: TaskItem
}

/** The task's pane. A failure stays inside it (components/bits ItemBoundary). */
export function TaskPane(props: Props) {
  return (
    <ItemBoundary key={props.task?.uid ?? ''} what="task" onClose={props.onClose}>
      <TaskPaneBody {...props} />
    </ItemBoundary>
  )
}

function TaskPaneBody({ task, lastSeen, loading, subtasks, lists, tags, now, prefs, readOnly, onClose, onOpen, placement }: Props) {
  const acts = useTaskActions()
  const nav = useNavigate()
  const [saved, setSaved] = useState<'idle' | 'saving' | 'saved'>('idle')
  const [history, setHistory] = useState<Undoable[]>([])
  const dueA = useAnchor<HTMLButtonElement>()
  const startA = useAnchor<HTMLButtonElement>()
  const titleTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const notesTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [title, setTitle] = useState(task?.summary ?? '')
  const [titleExtra, setTitleExtra] = useState<Record<string, unknown>>({})
  const uid = task?.uid
  // Another task resets the pane. A task that leaves the list behind the pane (its new due date
  // moves it out of Today) is still this task: its undo history stays, so "Undo due" can bring
  // it back. Before, the brief gap reset the history and the Undo button vanished.
  const shownUid = useRef<string | undefined>(uid)

  useEffect(() => {
    if (!uid || uid === shownUid.current) return
    shownUid.current = uid
    setTitle(task?.summary ?? '')
    setHistory([])
    setSaved('idle')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uid])

  // A change from elsewhere refreshes the title unless it is being typed.
  useEffect(() => {
    if (task && !titleTimer.current && document.activeElement?.getAttribute('aria-label') !== tr('tasks.taskPane.taskTitle')) setTitle(task.summary)
  }, [task, task?.summary])

  // ----- live editing (lib/live) -----
  const [notes, setNotes] = useState(task?.notes ?? '')
  // Which task `notes` belongs to: until the sync below has run for this task, the pane shows the
  // task's own notes (a pane opened while the task was loading must not start in edit mode).
  const [notesUid, setNotesUid] = useState(task?.uid)
  const titleRef = useRef(title)
  titleRef.current = title
  const notesRef = useRef(notes)
  notesRef.current = notes
  const docRef = useRef(new LiveDoc<TaskText>({ notes: 'lines' }))
  const [conflict, setConflict] = useState<ConflictState<TaskText> | null>(null)
  const flushRef = useRef<() => Promise<void>>(async () => {})
  /** The pane's save in flight: a change from elsewhere is applied after it lands. */
  const savingRef = useRef<Promise<unknown>>(Promise.resolve())
  const applyRef = useRef<(c: TaskText, before: TaskText | null, by: string | null | undefined, merged: boolean) => void>(() => {})
  useEffect(() => {
    if (task && !notesTimer.current && document.activeElement?.getAttribute('aria-label') !== tr('common.notes')) {
      setNotes(task.notes ?? '')
      setNotesUid(task.uid)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task?.uid, task?.notes])
  useEffect(() => {
    docRef.current = new LiveDoc<TaskText>({ notes: 'lines' })
    setConflict(null)
    if (!uid) return
    let alive = true
    fetchLive('task', uid).then((got) => {
      if (alive && !docRef.current.base) docRef.current.setBase(got.version, { summary: String(got.content.summary ?? ''), notes: String(got.content.notes ?? '') })
    }).catch(() => {})
    return () => {
      alive = false
    }
  }, [uid])
  const editing = useLiveItem({
    kind: 'task', id: uid, version: () => docRef.current.version, settled: () => savingRef.current,
    onRemote: (theirs) => {
      const doc = docRef.current
      const content = { summary: String(theirs.content.summary ?? ''), notes: String(theirs.content.notes ?? '') }
      const mine = { summary: titleRef.current, notes: notesRef.current }
      // Typing not saved yet: the pane saves 600 ms after the last key, so a pending timer is it.
      const dirty = !!titleTimer.current || !!notesTimer.current
      const d = doc.onRemote({ version: theirs.version, content, by: theirs.by }, mine, dirty)
      if (d.type === 'replace') applyRef.current(d.content, d.before, theirs.by, false)
      else if (d.type === 'merge') applyRef.current(d.content, d.before, theirs.by, true)
      else if (d.type === 'conflict') setConflict(d.conflict)
    },
  })
  useEffect(() => {
    if (!uid) return
    return registerEditor('task-pane', {
      kind: 'task', id: () => uid, flush: () => flushRef.current(),
      snapshot: () => ({
        kind: 'task', id: uid, base_version: docRef.current.version, focused: true,
        dirty: !!titleTimer.current || !!notesTimer.current || !!conflict,
        fields: { summary: titleRef.current, notes: notesRef.current, due: task?.due ?? null, start: task?.start ?? null, priority: task?.priority ?? null },
      }),
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uid, conflict])

  const offers = useMemo(() => (title ? offersFor(title, now, prefs, lists) : []), [title, now, prefs, lists])
  // Shift+A and the palette's "Ask about this…" mean the open task.
  useAskTarget(task ? { kind: 'task', uid: task.uid, title: task.summary } : null)

  if (!task && loading) return <aside className={`tk-pane tk-pane-${placement}`} aria-label={tr('tasks.taskPane.taskDetails')} aria-busy="true" data-testid="task-pane" />
  if (!task) {
    // Its own key: React must not reuse the full pane's buttons here, or a click aimed at "Undo
    // due" a moment before would land on Close in the same place.
    return (
      <aside key="gone" className={`tk-pane tk-pane-${placement} gone`} aria-label={tr('tasks.taskPane.taskDetails')} data-testid="task-pane">
        <div className="tk-pane-head">
          <span />
          <button type="button" className="icon-btn" aria-label={tr('common.close')} onClick={onClose}><X size={18} /></button>
        </div>
        <div className="tk-pane-gone">
          <p>{lastSeen ? tr('tasks.taskPane.thisTaskWasDeletedElsewhere') : tr('tasks.taskPane.thisTaskIsNotHere')}</p>
          {lastSeen && <button type="button" className="btn tonal" onClick={async () => {
            const t = await acts.create(createBody(lastSeen))
            if (t) onOpen(t.uid)
          }}>{tr('common.restoreAsNew')}</button>}
        </div>
      </aside>
    )
  }

  const t = task
  const doc = docRef.current
  const save = async (field: string, changes: Record<string, unknown>, label?: string) => {
    if (doc.conflict && (field === 'title' || field === 'notes')) return // the bar decides first
    setSaved('saving')
    const before = t
    // Made from the version the pane is based on: the server merges what others saved since.
    const base = doc.base?.content ?? { summary: t.summary, notes: t.notes ?? '' }
    const sent: TaskText = { summary: typeof changes.summary === 'string' ? changes.summary : base.summary, notes: typeof changes.notes === 'string' ? changes.notes : base.notes }
    const onConflict = (e: { body?: Record<string, unknown> }) => {
      // ACT task.resolve_conflict @web.pane: a live-editing 409 opens the bar (both changed the same line).
      const b = e.body as { conflict?: boolean; base?: { version?: number | null; content?: Record<string, unknown> | null }; theirs?: { version?: number | null; content?: Record<string, unknown>; by?: string | null }; fields?: string[] } | undefined
      if (!b?.conflict) return false
      const text = (c?: Record<string, unknown> | null): TaskText | null => (c ? { summary: String(c.summary ?? ''), notes: String(c.notes ?? '') } : null)
      const mine = { summary: titleRef.current, notes: notesRef.current }
      setConflict(doc.onRejected({ base: { version: b.base?.version ?? null, content: text(b.base?.content) }, theirs: { version: b.theirs?.version ?? null, content: text(b.theirs?.content) ?? mine, by: b.theirs?.by ?? null }, fields: b.fields }, mine))
      return true
    }
    const job = acts.update(t, { ...changes, ...(doc.version != null ? { base_version: doc.version } : {}) }, { ...(label ? { label, undo: true } : {}), onConflict })
    savingRef.current = job
    const r = await job
    if (r) {
      let stored: TaskText = { summary: r.summary, notes: r.notes ?? '' }
      if (stored.notes.length >= TEXT_MAX) {
        try {
          const got = await fetchLive('task', t.uid)
          stored = { summary: String(got.content.summary ?? ''), notes: String(got.content.notes ?? '') }
        } catch {
          /* keep the list's copy */
        }
      }
      const fix = doc.onSaved(sent, r.version ?? null, same(stored, sent) ? null : stored, { summary: titleRef.current, notes: notesRef.current })
      if (fix?.conflict) setConflict(doc.conflict)
      else if (fix) applyRef.current(fix.content, null, null, true)
      setSaved('saved')
      setHistory((h) => [...h.slice(-19), { field, before, changes, after: r }])
      // z undoes the last field change too.
      useUI.getState().setUndo(() => void acts.update(r, undoBody(before, changes)))
    } else setSaved('idle')
  }

  /** Cancels the title and notes autosaves still waiting for a pause in the typing. The conflict bar
   *  decides what is saved: a timer left running would write the typing over "Use theirs" a moment
   *  later (it waited out the bar, then found no conflict and saved). */
  const dropPendingSaves = () => {
    for (const r of [titleTimer, notesTimer]) {
      if (r.current) clearTimeout(r.current)
      r.current = null
    }
  }

  const undoLast = async () => {
    const last = history[history.length - 1]
    if (!last) return
    setHistory((h) => h.slice(0, -1))
    // Against the copy the last save returned, not the shown one: right after a change the list may
    // not have refetched yet, and an undo sent with the older copy was refused as stale, so the
    // change stayed (tasks-app e2e, 2 of 16 under load, 8 Oct 2026). The z undo already did this.
    await acts.update(last.after, undoBody(last.before, last.changes))
    if (last.field === 'title') setTitle(last.before.summary)
    toast({ message: tr('tasks.taskPane.restored', { field: last.field[0].toUpperCase(), field2: last.field.slice(1) }) })
  }

  const onTitle = (v: string) => {
    setTitle(v)
    reportTyping('task', t.uid)
    if (titleTimer.current) clearTimeout(titleTimer.current)
    titleTimer.current = setTimeout(() => {
      titleTimer.current = null
      if (v.trim() && v.trim() !== t.summary) void save('title', { summary: v.trim(), ...titleExtra })
      setTitleExtra({})
    }, 600)
  }
  const flushTitle = () => {
    if (!titleTimer.current) return
    clearTimeout(titleTimer.current)
    titleTimer.current = null
    if (title.trim() && title.trim() !== t.summary) void save('title', { summary: title.trim(), ...titleExtra })
    setTitleExtra({})
  }

  const onNotes = (v: string) => {
    setNotes(v)
    reportTyping('task', t.uid)
    if (notesTimer.current) clearTimeout(notesTimer.current)
    notesTimer.current = setTimeout(() => {
      notesTimer.current = null
      if (v !== (t.notes ?? '')) void save('notes', { notes: v })
    }, 600)
  }
  const flushNotes = () => {
    if (!notesTimer.current) return
    clearTimeout(notesTimer.current)
    notesTimer.current = null
    if (notesRef.current !== (t.notes ?? '')) void save('notes', { notes: notesRef.current })
  }
  // The assistant panel saves the pane before it asks (flush-before-ask).
  flushRef.current = async () => {
    flushTitle()
    flushNotes()
    await savingRef.current.catch(() => {})
  }
  // A change saved elsewhere: the pane shows it (with Undo), or the merge of it and what is typed here.
  applyRef.current = (c, before, by, merged) => {
    setTitle(c.summary)
    setNotes(c.notes)
    if (merged) {
      if (c.summary.trim() && c.summary !== t.summary) onTitle(c.summary)
      if (c.notes !== (t.notes ?? '')) onNotes(c.notes)
    }
    if (by === undefined || !before) return
    const who = byName(by)
    // ACT task.live_update @web.pane: Undo writes the text back as a new version.
    toast({
      message: merged ? tr('tasks.taskPane.changedThisTaskTooBoth', { charAt: who.charAt(0).toUpperCase(), who: who.slice(1) }) : tr('tasks.taskPane.updatedBy', { who }),
      actionLabel: tr('common.undo'), duration: 10_000,
      onAction: () => {
        setTitle(before.summary)
        setNotes(before.notes)
        const back: Record<string, unknown> = {}
        if (before.summary !== c.summary && before.summary.trim()) back.summary = before.summary
        if (before.notes !== c.notes) back.notes = before.notes
        if (Object.keys(back).length) void save('notes' in back ? 'notes' : 'title', back)
      },
    })
  }

  const lvl = priorityLevel(t.priority)
  const anchorDay = dueDay(t, prefs.zone) ?? todayISO(prefs.zone, now)
  const createdAt = t.created ? parseISO(t.created, prefs.zone) : null
  const modifiedAt = t.last_modified ? parseISO(t.last_modified, prefs.zone) : null
  const stamp = (d: Date) => `${formatDate(d, 'day', prefs.zone)}, ${formatTime(d, prefs)}`

  const reply = async () => {
    // ACT task.reply_email @web.pane: a reply to the source email.
    try {
      const conv = await api.get<{ messages?: { id: number; from_me?: boolean }[] }>(`/conversations/${t.conversation_id}`)
      const last = conv.messages?.[conv.messages.length - 1]
      if (!last) throw new Error(tr('tasks.taskPane.theEmailHasNoMessages'))
      const p = await api.get<{ payload: ComposePayload } | ComposePayload>('/compose/reply', { message_id: last.id, mode: 'reply' })
      useCompose.getState().open({ payload: 'payload' in p ? p.payload : p, conversationId: Number(t.conversation_id) || null })
    } catch (e) {
      toastError(e, tr('tasks.taskPane.couldNotStartAReply'))
    }
  }

  return (
    <aside className={`tk-pane tk-pane-${placement}`} aria-label={tr('tasks.taskPane.taskDetails')} data-testid="task-pane" onKeyDown={(e) => {
      if (e.key === 'Escape' && !(e.target as HTMLElement).closest('.popover')) {
        e.stopPropagation()
        flushTitle()
        onClose()
      }
    }}>
      <div className="tk-pane-head">
        <span className={`tk-saved ${saved}`} aria-live="polite" data-testid="pane-saved">
          {saved === 'saving' ? tr('common.saving') : saved === 'saved' ? <><Check size={14} aria-hidden />{' '}{tr('common.saved')}</> : readOnly ? tr('tasks.taskPane.readOnlyList') : ''}
        </span>
        {history.length > 0 && (
          <button type="button" className="btn text sm" onClick={() => void undoLast()} aria-label={tr('tasks.taskPane.undo', { field: history[history.length - 1].field })}>
            <Undo2 size={14} aria-hidden />{' '}{tr('tasks.taskPane.undo', { field: history[history.length - 1].field })}
          </button>
        )}
        <button type="button" className="icon-btn" aria-label={tr('common.close')} title={tr('common.closeEsc')} onClick={() => {
          flushTitle()
          onClose()
        }}><X size={18} /></button>
      </div>

      <EditingBanner kind="task" editing={editing} />
      {conflict && (
        <ConflictBar kind="task" conflict={conflict}
          onMine={() => {
            // "Use mine": saved over theirs as a new version.
            dropPendingSaves()
            const m = doc.useMine()
            setConflict(null)
            if (m) void save('notes', { summary: m.summary.trim() || t.summary, notes: m.notes })
          }}
          onTheirs={() => {
            // The typing that met the conflict is decided here: its autosave must not run after.
            dropPendingSaves()
            const th = doc.useTheirs()
            setConflict(null)
            if (th) {
              setTitle(th.summary)
              setNotes(th.notes)
            }
          }} />
      )}
      <div className="tk-pane-body">
        <div className="tk-pane-title">
          {/* ACT task.complete @web.pane, ACT task.reopen @web.pane */}
          <button type="button" className={`tk-check-ring lg pri-${['none', 'low', 'med', 'high'][lvl]}${isDone(t) ? ' on' : ''}`} role="checkbox" aria-checked={isDone(t)}
            aria-label={isDone(t) ? tr('tasks.taskPane.reopen') : tr('common.complete')} disabled={readOnly} onClick={() => void acts.toggleComplete(t)}>
            {isDone(t) && <Check size={15} strokeWidth={3} aria-hidden />}
          </button>
          {/* ACT task.edit @web.pane: the title saves as it changes */}
          <textarea className={`tk-title-input${isDone(t) ? ' done' : ''}`} dir="auto" value={title} rows={1} aria-label={tr('tasks.taskPane.taskTitle')} disabled={readOnly}
            onChange={(e) => onTitle(e.target.value.replace(/\n/g, ' '))} onBlur={flushTitle}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') {
                e.preventDefault()
                flushTitle()
              }
            }} />
        </div>
        {offers.length > 0 && !readOnly && (
          <div className="tk-inline-offers pane">
            {offers.map((o) => (
              <button key={o.label} type="button" className="tk-chip tk-chip-offer" onClick={() => {
                const next = buildTitle(title, o.tokens)
                const { addTags, ...rest } = o.apply as { addTags?: string[] } & Record<string, unknown>
                const extra = addTags ? { ...rest, categories: [...(t.categories ?? []), ...addTags] } : rest
                if (titleTimer.current) clearTimeout(titleTimer.current)
                titleTimer.current = null
                setTitle(next)
                void save('title', { summary: next, ...extra })
              }}>{tr('tasks.taskPane.apply', { label: o.label })}</button>
            ))}
          </div>
        )}

        <dl className="tk-fields">
          <dt>{tr('common.priority')}</dt>
          <dd>
            {/* ACT task.set_priority @web.pane */}
            <div className="tk-seg" role="radiogroup" aria-label={tr('common.priority')}>
              {([0, 1, 2, 3] as PriorityLevel[]).map((l) => (
                <button key={l} type="button" role="radio" aria-checked={lvl === l} className={`${lvl === l ? 'on' : ''} pri-${['none', 'low', 'med', 'high'][l]}`} disabled={readOnly}
                  onClick={() => lvl !== l && void save('priority', { priority: icalPriority(l) }, tr('tasks.taskPane.priority', { priorityNames: PRIORITY_NAMES[l] }))}>
                  {PRIORITY_NAMES[l]}
                </button>
              ))}
            </div>
          </dd>

          <dt>{tr('common.due')}</dt>
          <dd className="tk-field-row">
            {/* ACT task.reschedule @web.pane */}
            <button type="button" className="tk-field-btn" disabled={readOnly} onClick={(e) => dueA.toggle(e.currentTarget)} data-testid="pane-due">
              <CalendarClock size={15} aria-hidden /><span className="ellipsis">{t.due ? describe(t.due, now, prefs) : tr('common.noDueDate')}</span>
            </button>
            {t.due && !readOnly && <button type="button" className="icon-btn sm" aria-label={tr('tasks.taskPane.clearTheDueDate')} onClick={() => void save('due', { clear_due: true }, tr('tasks.taskPane.dueDateCleared'))}><X size={14} /></button>}
          </dd>

          <dt>{tr('common.starts')}</dt>
          <dd className="tk-field-row">
            {/* ACT task.set_start @web.pane */}
            <button type="button" className="tk-field-btn" disabled={readOnly} onClick={(e) => startA.toggle(e.currentTarget)} data-testid="pane-start">
              <CalendarPlus size={15} aria-hidden /><span className="ellipsis">{t.start ? describe(t.start, now, prefs) : tr('tasks.taskPane.noStartDate')}</span>
            </button>
            {t.start && !readOnly && <button type="button" className="icon-btn sm" aria-label={tr('tasks.taskPane.clearTheStartDate')} onClick={() => void save('start', { clear_start: true }, tr('tasks.taskPane.startCleared'))}><X size={14} /></button>}
          </dd>

          <dt>{tr('common.repeat')}</dt>
          <dd>
            <RepeatField rrule={t.rrule ?? null} repeatFrom={t.repeat_from ?? 'due'} anchor={anchorDay} disabled={readOnly}
              onChange={(rrule, from) => {
                const changes: Record<string, unknown> = { recurrence: rrule ?? '' }
                if (from) changes.repeat_from = from
                if (rrule && !t.due) changes.due = anchorDay
                void save('repeat', changes, rrule ? tr('tasks.taskPane.repeatSet') : tr('tasks.taskPane.repeatRemoved'))
              }} />
            {/* ACT task.skip @web.pane: this occurrence only; the task moves to its next date, with undo. */}
            {t.recurring && !isDone(t) && !readOnly && (
              <button type="button" className="btn text sm" onClick={() => void acts.skip(t)}><SkipForward size={14} aria-hidden />{' '}{tr('tasks.taskPane.skipThisOccurrence')}</button>
            )}
          </dd>

          <dt>{tr('common.reminders')}</dt>
          <dd>
            <ReminderField task={t} now={now} prefs={prefs} disabled={readOnly} onChange={(patch, label) => void save('reminders', patch, label)} />
          </dd>

          <dt>{tr('common.list')}</dt>
          <dd>
            {/* ACT task.move_list @web.pane */}
            <ListPicker value={t.list} lists={lists} disabled={readOnly} onPick={(l) => void acts.move(t, { list: l.id }, tr('tasks.taskPane.movedTo', { itemName: l.name }))} />
          </dd>

          <dt>{tr('tasks.taskPane.subtaskOf')}</dt>
          <dd>
            {/* ACT task.reparent @web.pane: a parent picker, one level */}
            <ParentPicker task={t} disabled={readOnly} onPick={(p) => void acts.move(t, { parent: p?.uid ?? '' }, p ? tr('tasks.taskPane.nowASubtaskOf', { summary: p.summary }) : tr('tasks.taskPane.nowATaskOfIts'))} />
          </dd>

          <dt>{tr('common.tags')}</dt>
          <dd>
            <TagField value={t.categories ?? []} all={tags} disabled={readOnly} onChange={(cats, label) => void save('tags', { categories: cats }, label)} />
          </dd>

          <dt>{tr('tasks.taskPane.subtasks')}</dt>
          <dd>
            <Subtasks subtasks={subtasks} disabled={readOnly}
              // ACT task.subtask_add @web.pane
              onAdd={(title2) => void acts.create({ summary: title2, parent: t.uid, list: t.list, tz: prefs.zone })}
              onToggle={(s) => void acts.toggleComplete(s)}
              onOpen={(s) => onOpen(s.uid)}
              onReorder={(s, before) => {
                const order = subtasks.filter((x) => x.uid !== s.uid)
                const i = before ? order.findIndex((x) => x.uid === before.uid) : order.length
                const prev = order[i - 1]?.sort_order ?? null
                const next = order[i]?.sort_order ?? null
                // ACT task.reorder @web.pane: dropped at the end it goes after the last one; one Undo puts it back.
                const at = before ? { before: before.uid } : { after: order[order.length - 1]?.uid }
                if (!at.before && !at.after) return
                void acts.move(s, { ...at, sort_order: prev == null && next == null ? i * 1024 : Math.floor(((prev ?? (next! - 2048)) + (next ?? (prev! + 2048))) / 2) },
                  tr('tasks.tasksApp.taskMoved'), placeOf(s, subtasks))
              }} />
          </dd>

          <dt>{tr('common.notes')}</dt>
          <dd>
            <Notes key={t.uid} value={notesUid === t.uid ? notes : t.notes ?? ''} disabled={readOnly} onChange={onNotes} />
          </dd>

          {/* ACT recording.view @web.pane: "From: <recording>" for a task made from a recording */}
          <TaskRecording uid={t.uid} />
          {/* ACT note.link @web.pane, ACT note.unlink @web.pane, ACT table.link @web.pane, ACT table.unlink @web.pane,
              ACT checkitem.to_task @web.pane: "From the list …" opens the list on the item the task came from */}
          <dt>{tr('common.links')}</dt>
          <dd><LinkedPanel kind="task" target={{ uid: t.uid }} title={t.summary || ''} /></dd>

          {t.conversation_id != null && t.conversation_id !== '' && (
            <>
              <dt>{tr('common.email')}</dt>
              <dd className="tk-field-row">
                {/* ACT task.open_email @web.pane */}
                <button type="button" className="tk-chip tk-chip-link" onClick={() => nav(`/mail/all/${convSlug(t.conversation_id as number)}`)}>
                  <Mail size={13} aria-hidden />{' '}{tr('tasks.taskPane.openEmail')}
                </button>
                {/* ACT task.reply_email */}
                <button type="button" className="btn text sm" onClick={() => void reply()}><Reply size={14} aria-hidden />{' '}{tr('common.reply')}</button>
              </dd>
            </>
          )}
        </dl>
      </div>

      <div className="tk-pane-foot">
        <span className="tk-stamps">
          {createdAt && <>{tr('tasks.taskPane.created', { stamp: stamp(createdAt) })}</>}
          {modifiedAt && <>{' '}{tr('tasks.taskPane.modified', { stamp: stamp(modifiedAt) })}</>}
        </span>
        <span className="tk-pane-actions">
          {/* ACT agent.ask_about @web.pane */}
          <button type="button" className="icon-btn" aria-label={tr('tasks.taskPane.ask', { name: assistantNameNow() })} title={tr('tasks.taskPane.askShiftA', { name: assistantNameNow() })} onClick={(e) => openAsk({ kind: 'task', uid: t.uid, title: t.summary }, e.currentTarget)}><Bot size={17} /></button>
          {/* ACT task.duplicate @web.pane */}
          <button type="button" className="icon-btn" aria-label={tr('common.duplicate')} title={tr('tasks.taskPane.duplicateCtrlD')} disabled={readOnly} onClick={() => void acts.duplicate(t)}><Copy size={17} /></button>
          {/* ACT task.delete @web.pane */}
          <button type="button" className="icon-btn danger" aria-label={tr('common.delete2')} title={tr('tasks.taskPane.delete')} disabled={readOnly} onClick={() => {
            acts.remove([t])
            onClose()
          }}><Trash2 size={17} /></button>
        </span>
      </div>

      <Popover anchor={dueA.anchor} open={dueA.open} onClose={dueA.close} role="dialog" className="tk-when-pop" label={tr('common.due')} noFocus>
        <WhenPicker value={t.due} now={now} prefs={prefs} label={tr('common.due')} onPick={(v) => {
          dueA.close()
          // The series question first on a repeating task.
          void dueScope(t, v, prefs.zone).then((extra) => extra && save('due', v ? { due: v, tz: prefs.zone, ...extra } : { clear_due: true }, v ? tr('tasks.taskPane.due', { split: describe(v, now, prefs).split(' · ')[0] }) : tr('tasks.taskPane.dueDateCleared')))
        }} />
      </Popover>
      <Popover anchor={startA.anchor} open={startA.open} onClose={startA.close} role="dialog" className="tk-when-pop" label={tr('common.starts')} noFocus>
        <WhenPicker value={t.start ?? null} now={now} prefs={prefs} label={tr('tasks.taskPane.startHiddenUntilThen')} laterToday={false} onPick={(v) => {
          startA.close()
          void save('start', v ? { start: v, tz: prefs.zone } : { clear_start: true }, v ? tr('tasks.taskPane.starts', { split: describe(v, now, prefs).split(' · ')[0] }) : tr('tasks.taskPane.startCleared'))
        }} />
      </Popover>
    </aside>
  )
}
