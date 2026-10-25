// ACT mail.follow_up: making a waiting conversation a task cancels its follow-up.
// The Tasks app (web.tasks): the left column of smart lists, lists
// and tags; the header; the quick-add bar; the grouped list (a sortable table in Desk); the
// detail pane placed per theme (useThemeDef().apps.taskDetail); the selection bar; the keys of
// the `tasks` scope; and drag and drop. A props-free component, rendered by router.tsx in each
// theme's main region for every /tasks/* URL.

import { ModalDrawer } from '../../components/ModalDrawer'
import './tasks.css'
import { CalendarCheck, CircleCheck, CloudOff, Inbox, Sun, Tag as TagIcon } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router'
import { NoCalendarConnected } from '../calendar/BuiltinCalendarHint'
import { useQueryClient } from '@tanstack/react-query'
import { Popover } from '../../components/Popover'
import { Skeleton } from '../../components/bits'
import { useActions } from '../../api/hooks'
import type { ConversationSummary } from '../../api/types'
import { useAppRoute } from '../../hooks/useAppRoute'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { usePhoneChrome } from '../pwa/PhoneChrome'
import { addDaysISO, todayISO } from '../../lib/dates'
import { mailPath } from '../../lib/views'
import { taskListSegment, taskPath, tasksPath, type SmartList, type TaskListRef } from '../../lib/routes'
import { toast, useUI } from '../../stores/ui'
import { useShallow } from 'zustand/react/shallow'
import { useThemeDef } from '../../themes/useUITheme'
import { useAppShortcuts } from '../shortcuts/useAppShortcuts'
import { ask, dueScope, localTagColors, useLinger, useListActions, useTaskActions, type MoveStep } from './actions'
import {
  enc, findCached, serverParams, taskRequest, useAllTasks, useChildren, useTomorrowCount, useDatePrefs, useDefaults, useServerCounts, useServerView, useSidebarData, useTaskLists,
} from './api'
import { autoColor } from './colors'
import { BulkBar } from './BulkBar'
import { AskDialog, ConflictSheet, NameDialog } from './Dialogs'
import { Header, type HeaderHandle } from './Header'
import { MoveMenu, PriorityMenu, ReschedulePanel, RowMenu, TagsMenu, type RowVerb } from './Menus'
import {
  buildSections, defaultSort, filterForView, icalPriority, isDone, isEmptyQuery, listKey, matchesQuery, midpoint, nestRows, parseTaskQuery, placeOf,
  type BulkOp, type PriorityLevel, type RowEntry, type Section, type TagInfo, type TaskItem, type TaskList, type TaskPlace,
} from './model'
import { QuickAdd, type Prefill, type QuickAddHandle } from './QuickAdd'
import { SMART_META, Sidebar } from './Sidebar'
import { lastList, prefsFor, rememberList, useTasksUI, type ViewPrefs } from './store'
import { TaskListView, flatten, type DropTarget, type Item, type ListHandle } from './TaskList'
import { TaskPane } from './TaskPane'
import { followUpTaskBody, FollowUpRow, toastFollowUp, useFollowUps, WaitingList } from './Waiting'
import { join, split } from './when'
import { useOnline } from '../../hooks/useOnline'
import { useNow } from '../../hooks/useNow'
import { useDocumentTitle } from '../../hooks/useDocumentTitle'
import { t as tr, tx } from '../../i18n'



const COMPLETED_PAGE = 50

interface Pop {
  kind: 'due' | 'start' | 'priority' | 'move' | 'tags' | 'menu'
  uids: string[]
  anchor: HTMLElement
}

export function TasksApp() {
  const { tasks: route } = useAppRoute()
  if (route?.search != null) return <SearchLink q={route.search} />
  if (route && !route.list && !route.taskUid) return <Navigate to={`/tasks/${lastList() ?? 'today'}`} replace />
  return <TasksAppBody />
}

/** /tasks/search?q= (ACT task.search @web.tasks from mail search): All, with the query in the search box. */
function SearchLink({ q }: { q: string }) {
  const nav = useNavigate()
  useEffect(() => {
    useTasksUI.getState().setSearch(q)
    nav('/tasks/all', { replace: true })
  }, [q, nav])
  return null
}

function TasksAppBody() {
  const { tasks: route } = useAppRoute()
  const routeList = route?.list ?? null
  const ref: TaskListRef = useMemo(() => routeList ?? { kind: 'smart', id: 'today' }, [routeList])
  const viewKey = taskListSegment(ref)
  const taskUid = route?.taskUid ?? null
  const nav = useNavigate()
  const qc = useQueryClient()
  const theme = useThemeDef()
  const narrow = useMediaQuery('(max-width: 760px)')
  const phone = usePhoneChrome()
  const placement = narrow ? 'sheet' : theme.apps.taskDetail
  const table = theme.apps.taskList === 'table' && !narrow
  const now = useNow(60_000)
  const online = useOnline()
  const prefs = useDatePrefs()
  const zone = prefs.zone
  // The fields this page draws, not the whole store (anchor, conflict, lastCreated, subtaskOf).
  const ui = useTasksUI(useShallow((st) => ({ editing: st.editing, focused: st.focused, hidden: st.hidden, prefs: st.prefs, search: st.search, selected: st.selected, toggled: st.toggled })))
  const lingering = useLinger((s) => s.uids)
  const vp: ViewPrefs = prefsFor(ui.prefs, viewKey)
  const acts = useTaskActions()
  const listActs = useListActions()
  const mailActs = useActions()
  const quickRef = useRef<QuickAddHandle>(null)
  const headRef = useRef<HeaderHandle>(null)
  const listRef = useRef<ListHandle>(null)
  const rootRef = useRef<HTMLElement>(null)
  const [drawer, setDrawer] = useState(false)
  const [pop, setPop] = useState<Pop | null>(null)
  const [newList, setNewList] = useState(false)
  const [renameTag, setRenameTag] = useState<TagInfo | null>(null)
  const [renameList, setRenameList] = useState<TaskList | null>(null)
  const [page, setPage] = useState(1)
  const lastSeen = useRef<Map<string, TaskItem>>(new Map())
  /** When the open task went missing from the view (see the pane below). */
  const missedAt = useRef<number | null>(null)

  useEffect(() => {
    if (route?.list) rememberList(viewKey)
    setPage(1)
    useTasksUI.getState().clearSelection()
    setDrawer(false)
  }, [viewKey, route?.list])

  // ---- data
  const listsQ = useTaskLists()
  const lists = useMemo(() => listsQ.data ?? [], [listsQ.data])
  const defaults = useDefaults().data
  const countsQ = useServerCounts()
  const serverMode = countsQ.data != null
  const isWaiting = ref.kind === 'smart' && ref.id === 'waiting'
  const params = serverMode && !isWaiting ? serverParams(ref, lists, vp) : null
  const serverQ = useServerView(params)
  const viewItems = useMemo(() => serverQ.data?.pages.flatMap((p) => p.items) ?? [], [serverQ.data])
  // Server mode holds only the view: subtasks of expanded rows and of the open task come on their
  // own, and a deep-linked task outside the view comes from the first page of every task.
  const expandedParents = useMemo(() => (serverMode
    ? viewItems.filter((t) => (t.subtask_count ?? 0) > 0 && (ref.kind === 'list' ? !ui.toggled.has(t.uid) : ui.toggled.has(t.uid))).map((t) => t.uid).slice(0, 30)
    : []), [serverMode, viewItems, ref.kind, ui.toggled])
  const childParents = useMemo(() => (taskUid && serverMode && !expandedParents.includes(taskUid) ? [...expandedParents, taskUid] : expandedParents), [taskUid, serverMode, expandedParents])
  const kids = useChildren(childParents, serverMode)
  const paneMissing = serverMode && !!taskUid && !serverQ.isPending && !viewItems.some((t) => t.uid === taskUid) && !kids.some((t) => t.uid === taskUid)
  const allQ = useAllTasks(countsQ.isFetched && (!serverMode || paneMissing))
  const all = useMemo(() => allQ.data?.items ?? [], [allQ.data])
  const source: TaskItem[] = serverMode ? viewItems : all
  const followUps = useFollowUps()
  const { counts, tags } = useSidebarData(serverMode ? undefined : allQ.data?.items, countsQ.data, now, zone)
  const tagColors = useMemo(() => (tags ? localTagColors() : {}), [tags])
  const tagColor = useCallback((n: string) => tags.find((t) => t.name.toLowerCase() === n.toLowerCase())?.color ?? tagColors[n.toLowerCase()] ?? null, [tags, tagColors])
  const connected = serverMode ? serverQ.data?.pages[0]?.connected !== false : allQ.data?.connected !== false
  const loading = !countsQ.isFetched || (serverMode ? !!params && serverQ.isPending : allQ.isPending)
  const byUid = useMemo(() => new Map([...all, ...kids, ...source].map((t) => [t.uid, t])), [source, all, kids])
  const listById = useMemo(() => new Map(lists.map((l) => [l.id, l])), [lists])
  const currentList = ref.kind === 'list' ? lists.find((l) => listKey(l) === ref.id || l.id === ref.id) : undefined
  const isReadOnly = useCallback((t: TaskItem) => !!(t.list && listById.get(t.list)?.read_only), [listById])

  const tomorrowISO = addDaysISO(todayISO(zone, now), 1)
  const tomorrowCount = useTomorrowCount(serverMode && ref.kind === 'smart' && ref.id === 'today' && !loading && source.length === 0, tomorrowISO)

  // ---- the view's tasks
  const q = ui.search.trim()
  const query = useMemo(() => parseTaskQuery(q, now, zone), [q, now, zone])
  const searching = !!q && !isEmptyQuery(query)
  const viewTasks = useMemo(() => {
    const o = { now, zone, showDeferred: vp.showDeferred, showCompleted: vp.showCompleted }
    let base: TaskItem[]
    if (searching) base = source.filter((t) => (query.done ? true : !isDone(t)) && matchesQuery(t, query, { ...o, lists }))
    else if (serverMode) base = ref.kind === 'smart' && (ref.id === 'high' || ref.id === 'email') ? filterForView(source, ref, lists, o) : source
    else base = filterForView(source, ref, lists, o)
    // A row that was just completed stays a moment, struck through.
    if (lingering.size && !searching && !(ref.kind === 'smart' && ref.id === 'completed')) {
      const have = new Set(base.map((t) => t.uid))
      for (const uid of lingering) {
        const t = byUid.get(uid)
        if (t && !have.has(uid)) base = [...base, t]
      }
    }
    return base.filter((t) => !ui.hidden.has(t.uid))
  }, [source, ref, lists, now, zone, vp.showDeferred, vp.showCompleted, searching, query, serverMode, lingering, byUid, ui.hidden])

  const isCompletedView = ref.kind === 'smart' && ref.id === 'completed'
  const pagedTasks = useMemo(() => (isCompletedView && !serverMode
    ? [...viewTasks].sort((a, b) => ((a.completed_at ?? '') < (b.completed_at ?? '') ? 1 : -1)).slice(0, page * COMPLETED_PAGE)
    : viewTasks), [isCompletedView, serverMode, viewTasks, page])
  const moreCompleted = isCompletedView && (serverMode ? !!serverQ.hasNextPage : viewTasks.length > pagedTasks.length)

  const sortKey = vp.sort === 'auto' ? defaultSort(ref) : vp.sort
  const manual = sortKey === 'manual' && !searching && (vp.group === 'auto' || vp.group === 'none') && (ref.kind === 'list' || (ref.kind === 'smart' && ref.id === 'nodate') || vp.group === 'none')
  const sections: Section[] = useMemo(() => {
    if (searching) return [{ id: 'results', title: tr('tasks.tasksApp.resultsFor', { q }), tasks: pagedTasks }]
    return buildSections(pagedTasks, ref, { now, zone, lists, sort: vp.sort, group: vp.group, weekStart: prefs.weekStart, showDeferred: vp.showDeferred })
  }, [pagedTasks, ref, now, zone, lists, vp.sort, vp.group, prefs.weekStart, vp.showDeferred, searching, q])

  const expandAll = ref.kind === 'list'
  const expanded = useCallback((uid: string) => (expandAll ? !ui.toggled.has(uid) : ui.toggled.has(uid)), [expandAll, ui.toggled])
  const childrenOf = useMemo(() => {
    const m: Record<string, TaskItem[]> = {}
    for (const t of byUid.values()) if (t.parent && !ui.hidden.has(t.uid)) (m[t.parent] ??= []).push(t)
    for (const k of Object.keys(m)) m[k].sort((a, b) => (a.sort_order ?? 1e15) - (b.sort_order ?? 1e15) || (a.created ?? '').localeCompare(b.created ?? ''))
    return m
  }, [byUid, ui.hidden])

  const withRows = useMemo(() => sections.map((s) => {
    const exp = new Set(s.tasks.filter((t) => expanded(t.uid)).map((t) => t.uid))
    const extra: Record<string, TaskItem[]> = {}
    // An expanded parent shows its completed subtasks too (struck through), unless the list hides them.
    for (const uid of exp) if (childrenOf[uid]) extra[uid] = childrenOf[uid].filter((c) => !expandAll || vp.showCompleted || !isDone(c) || lingering.has(c.uid))
    return { ...s, rows: nestRows(s.tasks, exp, extra) }
  }), [sections, expanded, childrenOf, vp.showCompleted, lingering, expandAll])

  // Today's Follow-ups: the mail follow-ups due by the end of today.
  const todayEnd = useMemo(() => new Date(`${addDaysISO(todayISO(zone, now), 1)}T00:00:00`), [zone, now])
  const todayFollowUps = ref.kind === 'smart' && ref.id === 'today' && !searching
    ? followUps.items.filter((c) => c.follow_up_at && new Date(c.follow_up_at) < todayEnd)
    : []

  const items: Item[] = useMemo(() => flatten(withRows, { addRows: ref.kind === 'smart' && ref.id === 'upcoming' && !searching, more: moreCompleted }), [withRows, ref, searching, moreCompleted])
  const rows: RowEntry[] = useMemo(() => items.flatMap((it) => (it.kind === 'row' ? [it.entry] : [])), [items])
  const rowUids = useMemo(() => [...new Set(rows.map((r) => r.task.uid))], [rows])

  // ---- the pane
  const found = taskUid ? byUid.get(taskUid) ?? null : null
  // The open task can leave the view behind the pane (a new due moves it out of Today). The pane
  // then looks for it among all tasks, and until that answer comes it keeps the copy it last
  // showed. Before, it said "This task was deleted elsewhere" for that moment, and a click meant
  // for "Undo due" landed on the Close button that took its place (tasks-app e2e, 2 of 3 runs
  // under load, 9 Oct 2026). Only an answer from after the miss can say the task is gone.
  if (found || !paneMissing) missedAt.current = null
  else missedAt.current ??= Date.now()
  const lookingUp = !found && paneMissing && missedAt.current != null && !allQ.isError && allQ.dataUpdatedAt < missedAt.current
  const paneTask = found ?? (lookingUp && taskUid ? lastSeen.current.get(taskUid) ?? null : null)
  if (found) lastSeen.current.set(found.uid, found)
  // A recent copy of every task (staleTime) may not be fetched again on its own: ask for one.
  useEffect(() => {
    if (lookingUp) void allQ.refetch({ cancelRefetch: false })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lookingUp])
  const paneSubtasks = paneTask ? childrenOf[paneTask.uid] ?? [] : []
  const openPane = useCallback((uid: string) => nav(taskPath(uid, ref)), [nav, ref])
  const closePane = useCallback(() => nav(tasksPath(ref)), [nav, ref])
  useEffect(() => {
    if (taskUid) useTasksUI.getState().setFocused(taskUid)
  }, [taskUid])

  // ---- helpers
  const focused = ui.focused && rowUids.includes(ui.focused) ? ui.focused : null
  // Keys read the store, not this render: a fast "j x j x" must see each move.
  const curFocused = () => {
    const f = useTasksUI.getState().focused
    return f && (rowUids.includes(f) || byUid.has(f)) ? f : null
  }
  const targets = (): TaskItem[] => {
    const sel = [...useTasksUI.getState().selected].map((u) => byUid.get(u)).filter((t): t is TaskItem => !!t)
    if (sel.length) return sel
    const f = curFocused() ?? taskUid
    const t = f ? byUid.get(f) : undefined
    return t ? [t] : []
  }
  const rowEl = (uid: string): HTMLElement | null => rootRef.current?.querySelector<HTMLElement>(`.tk-row[data-uid="${CSS.escape(uid)}"]`) ?? null
  const anchorFor = (uid: string, sel = '.tk-due'): HTMLElement | null => {
    const el = rowEl(uid)
    return el?.querySelector<HTMLElement>(sel) ?? el?.querySelector<HTMLElement>('.tk-title') ?? el
  }
  const moveFocus = (d: 1 | -1) => {
    if (!rowUids.length) return
    const cur = curFocused()
    const i = cur ? rowUids.indexOf(cur) : -1
    const next = rowUids[Math.max(0, Math.min(rowUids.length - 1, i < 0 ? 0 : i + d))]
    useTasksUI.getState().setFocused(next)
    listRef.current?.scrollTo(next)
    // With the pane beside the list, the pane follows the cursor.
    if (taskUid && (placement === 'pane' || placement === 'below')) openPane(next)
  }

  const dayLabel = (v: string) => {
    const { day } = split(v, zone)
    const t0 = todayISO(zone, now)
    return day === t0 ? 'today' : day === addDaysISO(t0, 1) ? 'tomorrow' : day ?? ''
  }

  const setDue = (ts: TaskItem[], v: string | null, start = false) => {
    if (!ts.length) return
    if (ts.length === 1) {
      const field = start ? 'start' : 'due'
      const body = v ? { [field]: v, tz: zone } : { [`clear_${field}`]: true }
      const label = v ? tr(start ? 'tasks.tasksApp.startsOn' : 'tasks.tasksApp.dueOn', { day: dayLabel(v) }) : start ? tr('tasks.tasksApp.startCleared') : tr('tasks.tasksApp.dueDateCleared')
      // ACT task.reschedule @web.tasks (the series question on a repeating task), ACT task.set_start @web.tasks
      if (start) void acts.update(ts[0], body, { label })
      else void dueScope(ts[0], v, zone).then((extra) => extra && acts.update(ts[0], { ...body, ...extra }, { label }))
      return
    }
    if (start) {
      // ACT task.set_start @web.tasks: several at once, with one Undo that puts each start back.
      const before = ts.map((t) => ({ uid: t.uid, start: t.start ?? null }))
      void Promise.all(ts.map((t) => acts.update(t, v ? { start: v } : { clear_start: true }))).then(() => toast({
        message: tr('tasks.tasksApp.deferredTasks', { count: ts.length }), actionLabel: tr('common.undo'), duration: 7000,
        onAction: () => {
          for (const b of before) {
            const cur = findCached(qc, b.uid)
            if (cur) void acts.update(cur, b.start ? { start: b.start, tz: zone } : { clear_start: true })
          }
        },
      }))
      return
    }
    // ACT task.bulk_reschedule @web.tasks
    void acts.bulk('reschedule', ts, { due: v })
  }

  const bulk = (op: BulkOp, ts: TaskItem[], args: Record<string, unknown> = {}) => {
    if (ts.length) void acts.bulk(op, ts, args)
  }
  const setPriority = (ts: TaskItem[], l: PriorityLevel) => {
    // ACT task.set_priority @web.tasks
    if (ts.length === 1) void acts.update(ts[0], { priority: icalPriority(l) }, { label: tr('tasks.tasksApp.priority', { value: [tr('common.none'), tr('common.low'), tr('common.medium'), tr('common.high')][l] }) })
    else bulk('priority', ts, { priority: icalPriority(l) })
  }
  const moveTo = (ts: TaskItem[], l: TaskList) => {
    // ACT task.move_list @web.tasks
    if (ts.length === 1) void acts.move(ts[0], { list: l.id }, tr('tasks.tasksApp.movedTo', { itemName: l.name }))
    else bulk('move', ts, { list: l.id })
  }
  const addTag = (ts: TaskItem[], tag: string) => {
    // ACT task.tag @web.tasks
    if (ts.length === 1) {
      const t = ts[0]
      if ((t.categories ?? []).some((c) => c.toLowerCase() === tag.toLowerCase())) return
      void acts.update(t, { categories: [...(t.categories ?? []), tag] }, { label: tr('tasks.tasksApp.tagged', { tag }) })
    } else bulk('tag', ts, { tag })
  }
  const removeTag = (ts: TaskItem[], tag: string) => {
    if (ts.length === 1) void acts.update(ts[0], { categories: (ts[0].categories ?? []).filter((c) => c.toLowerCase() !== tag.toLowerCase()) }, { label: tr('tasks.tasksApp.removed', { tag }) })
    else bulk('untag', ts, { tag })
  }

  /** Siblings of a row in display order (same parent), for reorder and indent. */
  const siblingsOf = (t: TaskItem): TaskItem[] => rows.filter((r) => (r.task.parent ?? null) === (t.parent ?? null)).map((r) => r.task)

  /** Where a row sits now (its parent and neighbours), for the Undo of a move. */
  const placeNow = (t: TaskItem) => placeOf(t, siblingsOf(t))

  // ACT task.reorder @web.tasks: between two neighbours (manual sort only). One Undo puts it back
  // ([label] null: the caller offers one Undo for several moves).
  const reorder = async (t: TaskItem, target: TaskItem, pos: 'before' | 'after', label: string | null = tr('tasks.tasksApp.taskMoved'), from: TaskPlace = placeNow(t)): Promise<MoveStep> => {
    let sibs = siblingsOf(target).filter((x) => x.uid !== t.uid)
    // A list without manual orders yet gets them first, in the order it shows.
    if (sibs.some((x) => x.sort_order == null) || new Set(sibs.map((x) => x.sort_order)).size < sibs.length) {
      await Promise.all(sibs.map((x, i) => (x.sort_order === (i + 1) * 1024 ? null : taskRequest('PATCH', `/tasks/${enc(x.uid)}`, { sort_order: (i + 1) * 1024 }).catch(() => null))))
      sibs = sibs.map((x, i) => ({ ...x, sort_order: (i + 1) * 1024 }))
    }
    const i = sibs.findIndex((x) => x.uid === target.uid)
    const at = pos === 'before' ? i : i + 1
    const prev = sibs[at - 1]
    const next = sibs[at]
    const order = midpoint(prev?.sort_order, next?.sort_order)
    const parentChange = (target.parent ?? null) !== (t.parent ?? null) ? { parent: target.parent ?? '' } : {}
    const fresh = byUid.get(t.uid) ?? t
    const m = { before: next?.uid, after: prev?.uid, sort_order: order, ...parentChange }
    const saved = await acts.move(fresh, m, label ?? undefined, from)
    return { task: fresh, m, from, saved }
  }

  // ACT task.reparent @web.tasks
  const indent = (t: TaskItem) => {
    if (t.parent) return
    const sibs = siblingsOf(t)
    const above = sibs[sibs.findIndex((x) => x.uid === t.uid) - 1]
    if (!above) return toast({ message: tr('tasks.tasksApp.thereIsNoTaskAbove') })
    if (above.list !== t.list) return toast({ message: tr('tasks.tasksApp.aSubtaskStaysInIts') })
    void acts.move(t, { parent: above.uid }, tr('tasks.tasksApp.nowASubtaskOf', { summary: above.summary }), placeNow(t))
    // The new parent opens, so the task stays in sight.
    if (!expanded(above.uid)) useTasksUI.getState().toggle(above.uid)
  }
  const outdent = (t: TaskItem) => {
    if (t.parent) void acts.move(t, { parent: '' }, tr('tasks.tasksApp.nowATaskOfIts'), placeNow(t))
  }
  const nudge = (t: TaskItem, d: 1 | -1) => {
    if (!manual) return toast({ message: tr('tasks.tasksApp.switchToManualOrderTo') })
    const sibs = siblingsOf(t)
    const target = sibs[sibs.findIndex((x) => x.uid === t.uid) + d]
    if (target) void reorder(t, target, d === 1 ? 'after' : 'before')
  }

  const onRowSelect = (t: TaskItem, e: MouseEvent) => {
    const s = useTasksUI.getState()
    if (e.shiftKey && s.anchor) {
      const a = rowUids.indexOf(s.anchor)
      const b = rowUids.indexOf(t.uid)
      if (a >= 0 && b >= 0) {
        s.select(rowUids.slice(Math.min(a, b), Math.max(a, b) + 1), true)
        return
      }
    }
    if (e.ctrlKey || e.metaKey || s.selected.size > 0) {
      s.select([t.uid], !s.selected.has(t.uid), t.uid)
      return
    }
    s.setFocused(t.uid)
    useTasksUI.setState({ anchor: t.uid })
    openPane(t.uid)
  }

  const verb = (v: RowVerb, t: TaskItem, anchor: HTMLElement) => {
    switch (v) {
      case 'reschedule':
        return setPop({ kind: 'due', uids: [t.uid], anchor })
      case 'start':
        return setPop({ kind: 'start', uids: [t.uid], anchor })
      case 'remind':
        // ACT task.remind_at @web.pane: the pane holds the reminder list.
        openPane(t.uid)
        return toast({ message: tr('tasks.tasksApp.addTheReminderUnderReminders') })
      case 'priority':
      case 'move':
      case 'tags':
        return setPop({ kind: v, uids: [t.uid], anchor })
      case 'duplicate':
        return void acts.duplicate(t)
      case 'delete':
        return acts.remove([t])
      case 'email':
        return nav(mailPath('all', Number(t.conversation_id)))
      case 'indent':
        return indent(t)
      case 'outdent':
        return outdent(t)
      case 'skip':
        // ACT task.skip @web.tasks (with undo)
        return void acts.skip(t)
    }
  }

  // ---- keys (the tasks scope, lib/shortcuts.ts TASK_BINDINGS)
  // Tab, Shift+Tab and Space change a task only from the list or the page itself; with the focus in
  // the task pane or the sidebar they move focus and press buttons as usual (false: the browser's).
  // A focused control keeps those keys anyway (useGlobalShortcuts, keyBelongsToTarget).
  const focusOnList = () => {
    const a = document.activeElement
    return !a || a === document.body || a === document.documentElement || !!a.closest('.tk-list-wrap')
  }
  const popAt = (kind: Pop['kind'], sel?: string) => {
    const ts = targets()
    const a = ts[0] && anchorFor(ts[0].uid, sel)
    if (a) setPop({ kind, uids: ts.map((t) => t.uid), anchor: a })
  }
  useAppShortcuts('tasks', {
    'tasks.quickAdd': () => quickRef.current?.focus(),
    'tasks.next': () => moveFocus(1),
    'tasks.prev': () => moveFocus(-1),
    'tasks.open': () => {
      const f = curFocused() ?? rowUids[0]
      if (f) openPane(f)
    },
    'tasks.edit': () => {
      const t = targets()[0]
      if (t && !isReadOnly(t)) useTasksUI.getState().setEditing(t.uid)
    },
    'tasks.complete': () => {
      if (!focusOnList()) return false
      const ts = targets()
      // ACT task.bulk_complete @web.tasks
      if (ts.length > 1) bulk(ts.every(isDone) ? 'reopen' : 'complete', ts)
      else if (ts[0]) void acts.toggleComplete(ts[0])
    },
    'tasks.reschedule': () => popAt('due'),
    'tasks.start': () => popAt('start'),
    // No task.snooze here: only a fired reminder snoozes (the bell tray); the app says where.
    'tasks.snooze': () => void toast({ message: tr('tasks.tasksApp.snoozeWorksOnAFired') }),
    'tasks.priority': (arg) => {
      const l = Number(arg)
      if (l >= 0 && l <= 3) setPriority(targets().filter((t) => !isReadOnly(t)), l as PriorityLevel)
    },
    'tasks.move': () => popAt('move', '.tk-title'),
    'tasks.tags': () => popAt('tags', '.tk-title'),
    'tasks.delete': () => {
      const ts = targets().filter((t) => !isReadOnly(t))
      if (!ts.length) return
      const f = curFocused()
      // ACT task.delete @web.tasks, ACT task.bulk_delete @web.tasks
      acts.remove(ts)
      if (f && ts.some((t) => t.uid === f)) {
        const i = rowUids.indexOf(f)
        useTasksUI.getState().setFocused(rowUids[i + 1] ?? rowUids[i - 1] ?? null)
      }
      if (taskUid && ts.some((t) => t.uid === taskUid)) closePane()
    },
    'tasks.select': () => {
      const f = curFocused()
      if (f) useTasksUI.getState().select([f], !useTasksUI.getState().selected.has(f), f)
    },
    'tasks.selectAll': () => useTasksUI.getState().setSelection(rowUids),
    'tasks.selectNone': () => useTasksUI.getState().clearSelection(),
    'tasks.indent': () => {
      if (!focusOnList()) return false
      const t = targets()[0]
      if (!t) return false
      indent(t)
    },
    'tasks.outdent': () => {
      if (!focusOnList()) return false
      const t = targets()[0]
      if (!t) return false
      outdent(t)
    },
    'tasks.moveUp': () => {
      const t = targets()[0]
      if (t) nudge(t, -1)
    },
    'tasks.moveDown': () => {
      const t = targets()[0]
      if (t) nudge(t, 1)
    },
    'tasks.duplicate': () => {
      const t = targets()[0]
      // ACT task.duplicate @web.tasks
      if (t) void acts.duplicate(t)
    },
    'tasks.search': () => headRef.current?.focusSearch(),
    'tasks.goToday': () => void nav(tasksPath('today')),
    'tasks.goUpcoming': () => void nav(tasksPath('upcoming')),
    'tasks.goAll': () => void nav(tasksPath('all')),
    'tasks.undo': () => useUI.getState().lastUndo?.(),
    'tasks.escape': () => {
      if (taskUid) return void closePane()
      if (useTasksUI.getState().selected.size) return void useTasksUI.getState().clearSelection()
      if (useTasksUI.getState().editing) return void useTasksUI.getState().setEditing(null)
      return false
    },
  })

  // ---- drag and drop
  const tasksOf = (uids: string[]) => uids.map((u) => byUid.get(u)).filter((t): t is TaskItem => !!t && !isReadOnly(t))
  const dropDay = (ts: TaskItem[], day: string) => {
    if (ts.length === 1) return setDue(ts, join(day, split(ts[0].due, zone).hm, zone))
    void acts.bulk('reschedule', ts, { duesFor: (t) => join(day, split(t.due, zone).hm, zone) })
  }
  const onDropRow = (uids: string[], target: DropTarget) => {
    const tgt = byUid.get(target.uid)
    const ts = tasksOf(uids)
    if (!tgt || !ts.length) return
    // Where every dragged row sat, before any of them moves: one Undo puts them all back.
    const from = new Map(ts.map((t) => [t.uid, placeNow(t)]))
    if (target.pos === 'inside') {
      if (tgt.parent) return toast({ message: tr('tasks.tasksApp.subtasksAreOneLevelDeep') })
      const under = ts.filter((t) => t.uid !== tgt.uid && t.list === tgt.list)
      if (under.length === 1) return void acts.move(under[0], { parent: tgt.uid }, tr('tasks.tasksApp.nowASubtaskOf', { summary: tgt.summary }), from.get(under[0].uid))
      void (async () => {
        const steps: MoveStep[] = []
        for (const t of under) {
          const m = { parent: tgt.uid }
          steps.push({ task: t, m, from: from.get(t.uid), saved: await acts.move(t, m) })
        }
        acts.offerMovesUndo(steps, tr('tasks.tasksApp.nowASubtaskOf', { summary: tgt.summary }))
      })()
      return
    }
    void (async () => {
      const order = target.pos === 'after' ? [...ts].reverse() : ts
      if (order.length === 1) return void await reorder(order[0], tgt, target.pos as 'before' | 'after')
      const steps: MoveStep[] = []
      for (const t of order) steps.push(await reorder(t, tgt, target.pos as 'before' | 'after', null, from.get(t.uid)))
      acts.offerMovesUndo(steps, tr('tasks.tasksApp.tasksMoved', { count: steps.length }))
    })()
  }
  const dragUids = (t: TaskItem) => (ui.selected.has(t.uid) ? [...ui.selected] : [t.uid])

  // ---- quick add
  const prefill: Prefill = (() => {
    if (ref.kind === 'list') return { list: currentList?.id, label: currentList?.name }
    if (ref.kind === 'tag') return { tag: ref.id, label: `@${ref.id}` }
    if (ref.id === 'today') return { due: todayISO(zone, now), label: tr('common.today') }
    if (ref.id === 'high') return { priority: 1, label: tr('common.highPriority') }
    return {}
  })()
  const defaultList = defaults?.task_list ?? lists.find((l) => !l.read_only)?.id ?? null
  const onCreate = async (body: Record<string, unknown>, o: { open: boolean; newList?: string | null }) => {
    if (o.newList) {
      const choice = await ask({ title: tr('tasks.tasksApp.createTheList', { newList: o.newList }), confirm: tr('tasks.tasksApp.createList'), cancel: tr('tasks.tasksApp.addTo', { itemName: listById.get(String(body.list))?.name ?? tr('tasks.tasksApp.theDefaultList') }) })
      if (choice === 'confirm') {
        const l = await listActs.create(o.newList, autoColor(o.newList))
        if (l) body.list = l.id
      }
    }
    // ACT task.create @web.tasks, ACT task.subtask_add @web.tasks
    const t = await acts.create(body)
    if (t) {
      useTasksUI.getState().setFocused(t.uid)
      if (o.open) openPane(t.uid)
      const shown = filterForView([t], ref, lists, { now, zone, showDeferred: vp.showDeferred, showCompleted: true }).length > 0
      toast({ message: shown ? tr('tasks.tasksApp.added', { summary: t.summary }) : tr('tasks.tasksApp.addedToNotInThis', { summary: t.summary, itemName: listById.get(t.list ?? '')?.name ?? tr('common.tasks') }) })
    }
    return t
  }
  const addToSection = (s: Section) => quickRef.current?.focus(s.dropDay ? `${s.title === tr('common.tomorrow') ? 'tom' : s.dropDay} ` : '')

  // ---- left column drops
  const sideDrops = {
    onDropList: (uids: string[], l: TaskList) => {
      const ts = tasksOf(uids).filter((t) => t.list !== l.id)
      if (ts.length) moveTo(ts, l)
    },
    onDropTag: (uids: string[], tag: string) => addTag(tasksOf(uids), tag),
    onDropSmart: (uids: string[], id: SmartList) => {
      const ts = tasksOf(uids)
      const today = todayISO(zone, now)
      if (id === 'today') dropDay(ts, today)
      else if (id === 'upcoming') dropDay(ts, addDaysISO(today, 1))
      else if (id === 'nodate') setDue(ts, null)
      else if (id === 'high') setPriority(ts, 3)
    },
  }

  // ---- the title of the view
  const title = ref.kind === 'smart' ? SMART_META[ref.id].label : ref.kind === 'list' ? currentList?.name ?? tr('common.list') : `@${ref.id}`
  useDocumentTitle(title, tr('common.tasks'))
  const icon = ref.kind === 'smart' ? SMART_META[ref.id].icon : ref.kind === 'list' ? <i className="tk-dot lg" style={{ background: currentList?.color || autoColor(currentList?.name ?? '') }} /> : <TagIcon />
  const readOnlyView = !!currentList?.read_only
  const openCount = isWaiting ? followUps.items.length : viewTasks.filter((t) => !isDone(t) && !lingering.has(t.uid)).length

  // ---- empty states
  const empty = (() => {
    if (searching) return <div className="tk-empty"><h2>{tr('tasks.tasksApp.noTasksMatch')}</h2><p>{tx('tasks.tasksApp.tryFewerWordsOrAn', undefined, { code: (c) => <code>{c}</code> })}</p></div>
    if (ref.kind === 'smart' && ref.id === 'today') {
      const tomorrow = addDaysISO(todayISO(zone, now), 1)
      const n = serverMode ? tomorrowCount ?? 0 : source.filter((t) => !isDone(t) && t.due && split(t.due, zone).day === tomorrow).length
      return (
        <div className="tk-empty" data-testid="tasks-empty">
          <Sun size={40} aria-hidden />
          <h2>{tr('tasks.tasksApp.nothingDueToday')}</h2>
          {n > 0 ? <Link to={tasksPath('upcoming')} className="tk-empty-next">{tr('tasks.tasksApp.tasksTomorrow', { count: n })}</Link> : <p>{tx('tasks.tasksApp.addOneAboveOrLook', undefined, { link: (c) => <Link to={tasksPath('upcoming')}>{c}</Link> })}</p>}
        </div>
      )
    }
    if (isCompletedView) return <div className="tk-empty" data-testid="tasks-empty"><CalendarCheck size={40} aria-hidden /><h2>{tr('tasks.tasksApp.nothingCompletedYet')}</h2></div>
    return (
      <div className="tk-empty" data-testid="tasks-empty">
        <CircleCheck size={40} aria-hidden />
        <h2>{tr('tasks.tasksApp.noTasks')}</h2>
        {!readOnlyView && <p>{tx('tasks.tasksApp.typeInTheBarAbove', undefined, { code: (c) => <code>{c}</code> })}</p>}
      </div>
    )
  })()

  const selectedTasks = [...ui.selected].map((u) => byUid.get(u)).filter((t): t is TaskItem => !!t)
  const popTasks = pop ? pop.uids.map((u) => byUid.get(u)).filter((t): t is TaskItem => !!t) : []

  const makeTaskFromFollowUp = async (c: ConversationSummary) => {
    const t = await acts.create(followUpTaskBody(c, zone))
    if (t) {
      await mailActs.run({ ids: [c.id], action: 'cancel_follow_up' }, { silent: true })
      toastFollowUp()
    }
  }

  const clearCompleted = async () => {
    const scoped = ref.kind === 'list' ? source.filter((t) => isDone(t) && t.list === currentList?.id) : source.filter(isDone)
    if (!scoped.length) return toast({ message: tr('tasks.tasksApp.noCompletedTasksHere') })
    const where = ref.kind === 'list' ? tr('tasks.tasksApp.in', { currentListName: currentList?.name }) : ''
    const choice = await ask({ title: tr('tasks.tasksApp.deleteCompletedTasks', { count: scoped.length, where }), body: tr('tasks.tasksApp.thisCannotBeUndone'), confirm: tr('common.delete2'), danger: true })
    if (choice !== 'confirm') return
    // ACT task.clear_completed @web.tasks: confirmed, no undo.
    let failed = 0
    await Promise.all(scoped.map((t) => taskRequest('DELETE', `/tasks/${enc(t.uid)}`, undefined, { etag: t.etag ?? undefined }).catch(() => failed++)))
    void qc.invalidateQueries({ queryKey: ['tasks'] })
    toast({ message: failed ? tr('tasks.tasksApp.ofDeleted', { value: scoped.length - failed, scopedLength: scoped.length }) : tr('tasks.tasksApp.deletedCompletedTasks', { count: scoped.length }) })
  }

  // ---- layout
  const sidebar = (
    <Sidebar
      current={ref}
      counts={counts}
      waitingCount={followUps.items.length}
      lists={lists}
      tags={tags}
      tagColor={tagColor}
      defaultList={defaults?.task_list ?? null}
      onNewList={() => setNewList(true)}
      onListMenu={(l, a, color) => {
        if (a === 'rename') setRenameList(l)
        else if (a === 'color') void listActs.patch(l, { color: color ?? null }, 'List colour changed')
        else if (a === 'default') void listActs.setDefault(l, defaults?.task_list ?? null)
        else void listActs.remove(l, lists.filter((x) => x.id !== l.id && !x.read_only), counts?.lists[l.id] ?? 0).then((ok) => {
          if (ok && currentList?.id === l.id) nav(tasksPath('today'))
        })
      }}
      onReorderList={(l, before) => {
        const order = lists.filter((x) => x.id !== l.id)
        const i = before ? order.findIndex((x) => x.id === before.id) : order.length
        order.splice(i, 0, l)
        void listActs.reorder(order.map((x) => x.calendar_id).filter((x): x is number => x != null))
      }}
      onTagMenu={(t, a, color) => {
        if (a === 'rename') setRenameTag(t)
        else if (a === 'color') void listActs.colorTag(t.name, color ?? null)
        else void ask({ title: tr('tasks.tasksApp.deleteTheTag', { itemName: t.name }), body: tr('tasks.tasksApp.itIsRemovedFromTasks', { count: t.count }), confirm: tr('tasks.tasksApp.deleteTag'), danger: true }).then((c) => {
          if (c !== 'confirm') return
          void listActs.deleteTag(t.name)
          if (ref.kind === 'tag' && ref.id.toLowerCase() === t.name.toLowerCase()) nav(tasksPath('all'))
        })
      }}
      onNavigate={() => setDrawer(false)}
      {...sideDrops}
    />
  )

  const listBody = isWaiting ? (
    <WaitingList items={followUps.items} now={now} prefs={prefs} onMakeTask={(c) => void makeTaskFromFollowUp(c)} />
  ) : loading ? (
    <div className="tk-loading" aria-busy="true">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} height={22} style={{ margin: '14px 24px', width: `${70 - i * 8}%` }} />)}</div>
  ) : (
    <>
      {todayFollowUps.length > 0 && (
        <div className="tk-followups" aria-label="Follow-ups">
          <div className="tk-section"><span className="tk-section-title">{tr('tasks.tasksApp.followUps')}</span><span className="tk-section-count">{todayFollowUps.length}</span></div>
          {todayFollowUps.map((c) => <FollowUpRow key={c.id} c={c} now={now} prefs={prefs} onMakeTask={(x) => void makeTaskFromFollowUp(x)} />)}
        </div>
      )}
      <TaskListView
        ref={listRef}
        items={items}
        lists={lists}
        listOf={(t) => (t.list ? listById.get(t.list) : undefined)}
        showList={ref.kind !== 'list'}
        focused={focused}
        selected={ui.selected}
        editing={ui.editing}
        expanded={expanded}
        lingering={lingering}
        readOnly={isReadOnly}
        manual={manual}
        table={table}
        sort={sortKey}
        onSort={(s) => useTasksUI.getState().setPrefs(viewKey, { sort: s })}
        parentTitle={(uid) => byUid.get(uid)?.summary}
        now={now}
        prefs={prefs}
        h={{
          onSelect: onRowSelect,
          onOpen: (t) => openPane(t.uid),
          // ACT task.complete @web.tasks, ACT task.reopen @web.tasks
          onToggle: (t) => void acts.toggleComplete(t),
          onEdit: (t) => useTasksUI.getState().setEditing(t.uid),
          onTitle: (t, c) => {
            useTasksUI.getState().setEditing(null)
            const { addTags, ...rest } = c.extra as { addTags?: string[] } & Record<string, unknown>
            const changes: Record<string, unknown> = { ...rest }
            if (c.summary !== t.summary) changes.summary = c.summary
            if (addTags) changes.categories = [...(t.categories ?? []), ...addTags.filter((x) => !(t.categories ?? []).includes(x))]
            // ACT task.edit @web.tasks
            if (Object.keys(changes).length) void acts.update(t, changes, { label: tr('tasks.tasksApp.taskUpdated') })
          },
          onCancelEdit: () => useTasksUI.getState().setEditing(null),
          onDue: (t, a) => setPop({ kind: 'due', uids: ui.selected.has(t.uid) ? [...ui.selected] : [t.uid], anchor: a }),
          onPriority: (t, a) => setPop({ kind: 'priority', uids: [t.uid], anchor: a }),
          onMenu: (t, a) => setPop({ kind: 'menu', uids: [t.uid], anchor: a }),
          onExpand: (t) => useTasksUI.getState().toggle(t.uid),
          onTag: (tag) => nav(tasksPath({ kind: 'tag', id: tag })),
          // ACT task.open_email @web.tasks
          onEmail: (t) => nav(mailPath('all', Number(t.conversation_id))),
        }}
        onAddTo={addToSection}
        onMore={() => (serverMode ? void serverQ.fetchNextPage() : setPage((p) => p + 1))}
        onDropRow={onDropRow}
        onDropDay={(uids, day) => dropDay(tasksOf(uids), day)}
        dragUids={dragUids}
        empty={todayFollowUps.length ? null : empty}
        ariaLabel={title}
      />
    </>
  )

  const paneEl = taskUid ? (
    <TaskPane
      key={taskUid}
      task={paneTask}
      lastSeen={lastSeen.current.get(taskUid) ?? null}
      loading={loading}
      subtasks={paneSubtasks}
      lists={lists}
      tags={tags}
      now={now}
      prefs={prefs}
      readOnly={paneTask ? isReadOnly(paneTask) : false}
      onClose={closePane}
      onOpen={openPane}
      placement={placement}
    />
  ) : null

  if (!loading && !connected) {
    return (
      <section className="app-frame tasks-app tk-app" data-testid="tasks-app" aria-label={tr('common.tasks')}>
        <div className="tk-empty tk-connect" data-testid="tasks-no-account">
          <NoCalendarConnected app="tasks" />
        </div>
      </section>
    )
  }

  const quickAdd = !isWaiting && !isCompletedView && !readOnlyView && (
    <QuickAdd
      ref={quickRef}
      prefill={prefill}
      lists={lists.filter((l) => !l.read_only)}
      tags={tags}
      prefs={prefs}
      now={now}
      defaultList={defaultList}
      onCreate={onCreate}
      onLeaveDown={() => {
        if (!rowUids[0]) return
        useTasksUI.getState().setFocused(rowUids[0])
        ;(document.activeElement as HTMLElement | null)?.blur()
      }}
      parentTitle={(uid) => byUid.get(uid)?.summary}
      atBottom={phone}
    />
  )

  return (
    <section
      ref={rootRef}
      className={`app-frame tasks-app tk-app tk-theme-${theme.id} tk-detail-${placement}${table ? ' tk-mode-table' : ''}${narrow ? ' tk-narrow' : ''}${taskUid ? ' tk-has-pane' : ''}`}
      data-testid="tasks-app"
      aria-label={tr('common.tasks')}
    >
      {!narrow && sidebar}
      {narrow && drawer && <ModalDrawer className="tk-drawer" scrimClass="tk-drawer-scrim" label={tr('tasks.tasksApp.taskLists')} onClose={() => setDrawer(false)}>{sidebar}</ModalDrawer>}
      <div className="tk-center">
        <div className="tk-main-col">
          {ui.selected.size > 0 ? (
            <BulkBar tasks={selectedTasks} total={rowUids.length} lists={lists} tags={tags} now={now} prefs={prefs}
              onBulk={(op, args) => void acts.bulk(op, selectedTasks, args)}
              onSelectAll={() => useTasksUI.getState().setSelection(rowUids)}
              onClear={() => useTasksUI.getState().clearSelection()} />
          ) : (
            <Header
              ref={headRef}
              title={title}
              icon={icon}
              color={currentList?.color}
              count={loading ? null : isCompletedView ? viewTasks.length : openCount}
              overdue={ref.kind === 'smart' && ref.id === 'today' && !narrow ? counts?.smart.overdue : undefined}
              renamable={ref.kind === 'list' && !!currentList && !readOnlyView}
              readOnly={readOnlyView}
              prefs={vp}
              defaultSortLabel={{ manual: tr('common.manual'), due: tr('tasks.tasksApp.dueDate'), priority: tr('common.priority'), title: tr('common.title'), created: isCompletedView ? tr('common.completed') : tr('common.created') }[defaultSort(ref)]}
              canDeferred={ref.kind === 'smart' && (ref.id === 'today' || ref.id === 'all')}
              canCompleted={ref.kind !== 'smart'}
              canGroup={!isWaiting && !isCompletedView}
              narrow={narrow}
              onMenu={() => setDrawer(true)}
              // ACT list.rename @web.tasks
              onRename={(name) => currentList && void listActs.patch(currentList, { name }, `Renamed to ${name}`)}
              onPrefs={(p) => useTasksUI.getState().setPrefs(viewKey, p)}
              onSelectMode={() => {
                const f = focused ?? rowUids[0]
                if (f) useTasksUI.getState().select([f], true, f)
              }}
              onClearCompleted={ref.kind === 'list' || isCompletedView ? () => void clearCompleted() : undefined}
            />
          )}
          {!online && <div className="tk-banner" role="status"><CloudOff size={15} aria-hidden />{' '}{tr('tasks.tasksApp.offlineChangesAreSentWhen')}</div>}
          {allQ.isError && <div className="tk-banner error" role="alert">{tr('tasks.tasksApp.tasksCouldNotBeLoaded', { message: (allQ.error as Error).message })}</div>}
          {!phone && quickAdd}
          {readOnlyView && <div className="tk-banner"><Inbox size={15} aria-hidden />{' '}{tr('tasks.tasksApp.thisListIsReadOnly')}</div>}
          <div className="tk-list-wrap">{listBody}</div>
          {/* On the phone layout quick add sits at the bottom, above the tab bar, as Ambrose's message box.
              It is a sibling of the list, so it never covers the last rows. */}
          {phone && quickAdd}
        </div>
        {paneEl && (placement === 'pane' || placement === 'below') && paneEl}
      </div>
      {paneEl && (placement === 'card' || placement === 'sheet') && (
        <div className={`tk-overlay tk-overlay-${placement}`} onMouseDown={(e) => e.target === e.currentTarget && closePane()}>
          {paneEl}
        </div>
      )}

      {/* The popovers a row, a key or the selection bar opens. */}
      <Popover anchor={pop?.anchor ?? null} open={!!pop && (pop.kind === 'due' || pop.kind === 'start')} onClose={() => setPop(null)} role="dialog" className="tk-when-pop" label={pop?.kind === 'start' ? tr('tasks.tasksApp.defer') : tr('common.reschedule')} noFocus>
        {pop && (pop.kind === 'due' || pop.kind === 'start') && (
          <ReschedulePanel value={popTasks.length === 1 ? (pop.kind === 'due' ? popTasks[0].due : popTasks[0].start ?? null) : null} now={now} prefs={prefs} start={pop.kind === 'start'}
            label={pop.kind === 'start' ? tr('tasks.tasksApp.deferUntilHiddenUntilThen') : popTasks.length > 1 ? tr('tasks.tasksApp.rescheduleTasks', { count: popTasks.length }) : tr('common.reschedule')}
            onPick={(v) => {
              setPop(null)
              setDue(popTasks, v, pop.kind === 'start')
            }} />
        )}
      </Popover>
      <Popover anchor={pop?.anchor ?? null} open={pop?.kind === 'priority'} onClose={() => setPop(null)} label={tr('common.priority')}>
        <PriorityMenu tasks={popTasks} onPick={(l) => {
          setPop(null)
          setPriority(popTasks, l)
        }} />
      </Popover>
      <Popover anchor={pop?.anchor ?? null} open={pop?.kind === 'move'} onClose={() => setPop(null)} label={tr('common.moveToList')}>
        <MoveMenu lists={lists} current={popTasks.length === 1 ? popTasks[0].list : undefined} onPick={(l) => {
          setPop(null)
          moveTo(popTasks.filter((t) => t.list !== l.id), l)
        }} />
      </Popover>
      <Popover anchor={pop?.anchor ?? null} open={pop?.kind === 'tags'} onClose={() => setPop(null)} label={tr('common.tags')}>
        <TagsMenu tasks={popTasks} tags={tags} onAdd={(t) => {
          setPop(null)
          addTag(popTasks, t)
        }} onRemove={(t) => {
          setPop(null)
          removeTag(popTasks, t)
        }} />
      </Popover>
      <Popover anchor={pop?.anchor ?? null} open={pop?.kind === 'menu'} onClose={() => setPop(null)} placement="bottom-end" label={tr('tasks.tasksApp.taskActions')}>
        {pop?.kind === 'menu' && popTasks[0] && (
          <RowMenu task={popTasks[0]} canIndent={manual} onVerb={(v) => {
            const a = pop.anchor
            const t = popTasks[0]
            setPop(null)
            setTimeout(() => verb(v, t, a), 0)
          }} />
        )}
      </Popover>

      <NameDialog open={newList} title={tr('common.newList')} confirm={tr('common.create')} withColor onClose={() => setNewList(false)} onSave={async (name, color) => {
        setNewList(false)
        // ACT list.create @web.tasks
        const l = await listActs.create(name, color)
        if (l) nav(tasksPath({ kind: 'list', id: listKey(l) }))
      }} />
      <NameDialog open={!!renameList} title={tr('tasks.tasksApp.renameList')} confirm={tr('common.rename')} initial={renameList?.name} onClose={() => setRenameList(null)} onSave={(name) => {
        const l = renameList
        setRenameList(null)
        if (l && name !== l.name) void listActs.patch(l, { name }, `Renamed to ${name}`)
      }} />
      <NameDialog open={!!renameTag} title={tr('tasks.tasksApp.renameTag')} confirm={tr('common.rename')} initial={renameTag?.name} onClose={() => setRenameTag(null)} onSave={(name) => {
        const t = renameTag
        setRenameTag(null)
        if (!t || name === t.name) return
        // ACT tag.rename @web.tasks
        void listActs.renameTag(t.name, name)
        if (ref.kind === 'tag' && ref.id.toLowerCase() === t.name.toLowerCase()) nav(tasksPath({ kind: 'tag', id: name }))
      }} />
      <AskDialog />
      <ConflictSheet />
    </section>
  )
}

export default TasksApp
