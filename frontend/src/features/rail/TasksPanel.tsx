// The tasks side panel (web.panel): a quick glance from mail with the Tasks app's quick
// add (the grammar and its chips) and rows. Today (overdue, due today, started) comes first,
// then the next few upcoming; Completed opens on demand. A row opens the quick-action sheet; the
// checkbox completes; "Open Tasks" is in the panel header.
// ACT task.create @web.panel, ACT task.complete @web.panel, ACT task.reopen @web.panel, ACT task.open_email @web.panel

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleCheck } from 'lucide-react'
import { useCallback, useMemo, useRef, useState, type MouseEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { api } from '../../api/client'
import { qk } from '../../api/hooks'
import type { Task } from '../../api/types'
import { EmptyState, Skeleton } from '../../components/bits'
import { useAppRoute } from '../../hooks/useAppRoute'
import { taskPath, tasksPath } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import { createTask, useLinger, useTaskActions } from '../tasks/actions'
import { useDatePrefs, useDefaults, useServerTags, useTaskLists } from '../tasks/api'
import { AskDialog, ConflictSheet } from '../tasks/Dialogs'
import { isDone, type TaskItem } from '../tasks/model'
import { QuickAdd, type QuickAddHandle } from '../tasks/QuickAdd'
import { TaskRow, type RowHandlers } from '../tasks/TaskRow'
import '../tasks/tasks.css'
import { QuickSheet, TaskEditor } from './QuickSheet'
import './panels.css'
import { t as tr } from '../../i18n'

interface Page { items: TaskItem[]; connected?: boolean }

function useSmart(smart: 'today' | 'upcoming' | 'completed', enabled = true) {
  return useQuery<Page>({
    queryKey: qk.tasks.list({ smart, status: smart === 'completed' ? 'completed' : undefined }),
    queryFn: async ({ signal }) => {
      const r = await api.get<Page | TaskItem[]>('/tasks', { smart, limit: smart === 'today' ? 100 : 20, ...(smart === 'completed' ? { status: 'completed' } : {}) }, signal)
      return Array.isArray(r) ? { items: r } : { items: r.items ?? [], connected: r.connected }
    },
    enabled,
    staleTime: 10_000,
  })
}

const noop = () => {}

export function TasksPanel() {
  const qc = useQueryClient()
  const nav = useNavigate()
  const app = useAppRoute().app
  const prefs = useDatePrefs()
  const listsQ = useTaskLists().data
  const lists = useMemo(() => listsQ ?? [], [listsQ])
  const tags = useServerTags(true).data ?? []
  const defaults = useDefaults().data
  const actions = useTaskActions()
  const lingering = useLinger((s) => s.uids)
  const [showDone, setShowDone] = useState(false)
  const today = useSmart('today')
  const upcoming = useSmart('upcoming')
  const done = useSmart('completed', showDone)
  const quick = useRef<QuickAddHandle>(null)
  // web.sheet / web.editor: a row opens the quick-action popover; Edit opens the editor.
  const [sheet, setSheet] = useState<{ task: TaskItem; anchor: HTMLElement } | null>(null)
  const [editing, setEditing] = useState<TaskItem | null>(null)
  // A new clock with each load, so the due chips stay current.
  const loadedAt = today.dataUpdatedAt
  const now = useMemo(() => new Date(loadedAt || Date.now()), [loadedAt])
  const byList = useMemo(() => new Map(lists.map((l) => [l.id, l])), [lists])

  const h: RowHandlers = useMemo(() => ({
    onSelect: (t: TaskItem, e: MouseEvent) => setSheet({ task: t, anchor: e.currentTarget as HTMLElement }),
    onOpen: (t: TaskItem) => nav(taskPath(t.uid)),
    onToggle: (t: TaskItem) => void actions.toggleComplete(t),
    onEdit: (t: TaskItem) => setEditing(t),
    onTitle: noop,
    onCancelEdit: noop,
    onDue: (t: TaskItem, anchor: HTMLElement) => setSheet({ task: t, anchor }),
    onPriority: (t: TaskItem, anchor: HTMLElement) => setSheet({ task: t, anchor }),
    onMenu: (t: TaskItem, anchor: HTMLElement) => setSheet({ task: t, anchor }),
    onTag: (tag: string) => nav(tasksPath({ kind: 'tag', id: tag })),
    onEmail: (t: TaskItem) => t.conversation_id != null && nav(mailPath('all', Number(t.conversation_id))),
    onDragStart: noop, onDragOver: noop, onDrop: noop, onDragEnd: noop,
  }), [actions, nav])

  const onCreate = useCallback(async (body: Record<string, unknown>, o: { open: boolean }) => {
    const t = await createTask(qc, body)
    if (t && o.open) nav(taskPath(t.uid))
    return t
  }, [qc, nav])

  const row = (t: TaskItem) => (
    <TaskRow key={t.uid} entry={{ task: t, depth: 0 }} list={t.list ? byList.get(t.list) : undefined} showList={lists.length > 1}
      focused={false} selected={false} selectMode={false} editing={false} expanded={false} lingering={lingering.has(t.uid)}
      readOnly={!!(t.list && byList.get(t.list)?.read_only)} draggable={false} dropHint={null} table={false} now={now} prefs={prefs} lists={lists} h={h} />
  )

  if (today.isError && upcoming.isError)
    return (
      <EmptyState icon={<CircleCheck />} title={tr('rail.tasksPanel.noTaskListConnected')}>
        <p>{tr('rail.tasksPanel.connectACaldavAccountWith')}</p>
        <Link className="btn tonal" to="/settings/calendar?add=1">{tr('rail.calendarPanel.connectCalendar')}</Link>
      </EmptyState>
    )

  const todayItems = (today.data?.items ?? []).filter((t) => !isDone(t) || lingering.has(t.uid))
  const seen = new Set(todayItems.map((t) => t.uid))
  const nextItems = (upcoming.data?.items ?? []).filter((t) => !seen.has(t.uid) && (!isDone(t) || lingering.has(t.uid))).slice(0, 8)
  const doneItems = (done.data?.items ?? []).slice(0, 30)
  const loading = today.isLoading && upcoming.isLoading

  return (
    <div className="tasks tp-panel" data-testid="tasks-panel">
      <div className="tp-quick">
        <QuickAdd ref={quick} prefill={{}} lists={lists} tags={tags} prefs={prefs} now={now}
          defaultList={defaults?.task_list ?? null} onCreate={onCreate} />
      </div>
      {loading ? (
        <div style={{ display: 'grid', gap: 8, padding: 8 }}><Skeleton height={36} /><Skeleton height={36} /></div>
      ) : (
        <div role="grid" aria-label={tr('common.tasks')} className="tp-grid">
          <div className="tp-section" role="presentation">{tr('common.today')}</div>
          {todayItems.length ? todayItems.map(row) : <p className="muted tp-empty">{tr('rail.tasksPanel.nothingDueToday')}</p>}
          {nextItems.length > 0 && <div className="tp-section" role="presentation">{tr('common.next')}</div>}
          {nextItems.map(row)}
          <button type="button" className="btn text sm tp-done-toggle" aria-expanded={showDone} onClick={() => setShowDone(!showDone)}>
            {tr('rail.tasksPanel.completed', { value: showDone && done.data ? ` (${done.data.items.length})` : '' })}
          </button>
          {showDone && done.isSuccess && doneItems.length === 0 && <p className="muted tp-empty">{tr('rail.tasksPanel.noCompletedTasks')}</p>}
          {showDone && doneItems.map(row)}
        </div>
      )}
      {sheet && <QuickSheet target={{ kind: 'task', task: sheet.task as unknown as Task }} anchor={sheet.anchor} onClose={() => setSheet(null)} onEdit={() => setEditing(sheet.task)} />}
      {editing && <TaskEditor task={editing as unknown as Task} onClose={() => setEditing(null)} />}
      {app !== 'tasks' && (
        <>
          <AskDialog />
          <ConflictSheet />
        </>
      )}
    </div>
  )
}
