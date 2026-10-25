// The task verbs (task.*, list.*, tag.*) for the Tasks app. Every
// write carries the task's ETag; a 409 opens the conflict sheet. Changes show at
// once (optimistic) and come back from the server; each one has its undo: a toast,
// `z`, and one undo for a whole batch.

import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { create } from 'zustand'
import { ApiError } from '../../api/client'
import { qk } from '../../api/hooks'
import { toast, toastError, useUI } from '../../stores/ui'
import {
  ConflictError, editCaches, enc, findCached, invalidateTasks, isMissingRoute, lastWritten, restoreCaches, snapshotCaches, taskRequest, upsertCached,
} from './api'
import { bulkPatch, bulkReport, inverseOf, isDone, moveBack, type BulkOp, type MoveChange, type TaskItem, type TaskList, type TaskPlace } from './model'
import { useTasksUI } from './store'
import { needsDueScope, ruleAfterMove } from './when'
import { t as tr } from '../../i18n'

const UNDO_MS = 7000

// ---------------------------------------------------------------- confirmations

export interface Ask {
  title: string
  body?: string
  confirm: string
  cancel?: string
  danger?: boolean
  /** A third choice ("Delete them too"). */
  alt?: string
  /** The third choice is not destructive (the series question's "All"). */
  altPlain?: boolean
  resolve: (choice: 'confirm' | 'cancel' | 'alt') => void
}

export const useAsk = create<{ ask: Ask | null; set: (a: Ask | null) => void }>((set) => ({ ask: null, set: (ask) => set({ ask }) }))

/** A small question (AskDialog draws it); resolves to the choice. */
export function ask(a: Omit<Ask, 'resolve'>): Promise<'confirm' | 'cancel' | 'alt'> {
  return new Promise((resolve) => useAsk.getState().set({ ...a, resolve: (c) => {
    useAsk.getState().set(null)
    resolve(c)
  } }))
}

/**
 * The series question for a new due on a repeating task (Android TasksViewModel.requestReschedule):
 * "This occurrence" (the default, Enter) or "All". Resolves to the extra PATCH fields (the moved
 * rule), {} when nothing needs asking, or null when the user cancelled.
 */
export async function dueScope(t: TaskItem, due: string | null, zone: string): Promise<Record<string, unknown> | null> {
  if (!needsDueScope(t, due)) return {}
  const c = await ask({
    title: tr('tasks.actions.changeARepeatingTask'), body: tr('tasks.actions.repeatsMoveOnlyThisOccurrence', { summary: t.summary || tr('common.untitled') }),
    confirm: tr('tasks.actions.thisOccurrence'), alt: tr('common.all'), altPlain: true,
  })
  if (c === 'cancel') return null
  const rule = ruleAfterMove(t.rrule, t.due, due, c === 'alt' ? 'all' : 'this', zone)
  return rule ? { recurrence: rule } : {}
}

// ---------------------------------------------------------------- local copies

const FIELD_OF: Record<string, keyof TaskItem> = { recurrence: 'rrule' }

/** A task with a PATCH body applied, for the optimistic copy. */
export function applyLocal(t: TaskItem, changes: Record<string, unknown>): TaskItem {
  const out: TaskItem = { ...t }
  for (const [k, v] of Object.entries(changes)) {
    if (k === 'etag' || k === 'tz') continue
    if (k === 'completed') {
      out.status = v ? 'completed' : 'needs-action'
      out.completed = !!v
      out.completed_at = v ? new Date().toISOString() : null
    } else if (k === 'clear_due') {
      if (v) out.due = null
    } else if (k === 'clear_start') {
      if (v) out.start = null
    } else if (k === 'recurrence') {
      out.rrule = (v as string) || null
      out.recurring = !!v
    } else if (k === 'due') {
      out.due = v as string
      out.due_all_day = typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)
    } else if (k === 'start') {
      out.start = v as string
      out.start_all_day = typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)
    } else if (k === 'parent') {
      out.parent = (v as string) || null
    } else {
      ;(out as unknown as Record<string, unknown>)[FIELD_OF[k] ?? k] = v
    }
  }
  return out
}

/** The PATCH body that puts back what `changes` changed (per-field undo). */
export function undoBody(before: TaskItem, changes: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const k of Object.keys(changes)) {
    switch (k) {
      case 'completed':
        out.completed = isDone(before)
        break
      case 'due':
      case 'clear_due':
        if (before.due) out.due = before.due
        else out.clear_due = true
        break
      case 'start':
      case 'clear_start':
        if (before.start) out.start = before.start
        else out.clear_start = true
        break
      case 'recurrence':
        out.recurrence = before.rrule ?? ''
        break
      case 'parent':
        out.parent = before.parent ?? ''
        break
      case 'etag':
      case 'tz':
        break
      default:
        out[k] = (before as unknown as Record<string, unknown>)[k] ?? (k === 'categories' || k === 'reminders' || k === 'reminders_at' ? [] : k === 'priority' ? 0 : k === 'notes' ? '' : null)
    }
  }
  return out
}

/** The POST body that recreates a task (undo of a delete, Duplicate, "Restore as new", "Keep both"). */
export function createBody(t: TaskItem, extra: Record<string, unknown> = {}): Record<string, unknown> {
  const b: Record<string, unknown> = {
    summary: t.summary || tr('tasks.actions.untitledTask'), notes: t.notes ?? '', list: t.list, priority: t.priority || undefined,
    due: t.due ?? undefined, start: t.start ?? undefined, categories: t.categories?.length ? t.categories : undefined,
    reminders: t.reminders ?? [], recurrence: t.rrule ?? undefined, repeat_from: t.rrule ? t.repeat_from ?? 'due' : undefined,
    parent: t.parent ?? undefined, sort_order: t.sort_order ?? undefined, tz: t.tz ?? undefined,
    conversation_id: typeof t.conversation_id === 'number' ? t.conversation_id : undefined,
    ...extra,
  }
  for (const k of Object.keys(b)) if (b[k] === undefined) delete b[k]
  return b
}

// ---------------------------------------------------------------- undo

/** Set the app's undo (z, and the mail-wide undo key) and show the toast. */
function offerUndo(message: string, undo: (() => void) | null, opts: { secondary?: { label: string; run: () => void }; duration?: number } = {}) {
  const ui = useUI.getState()
  const fn = undo
    ? () => {
        useUI.getState().setUndo(null)
        undo()
      }
    : null
  ui.setUndo(fn)
  toast({ message, actionLabel: fn ? tr('common.undo') : undefined, onAction: fn ?? undefined, secondaryLabel: opts.secondary?.label, onSecondary: opts.secondary?.run, duration: opts.duration ?? UNDO_MS })
}

// ---------------------------------------------------------------- writes

export interface UpdateOpts {
  /** Toast text ("Due changed"); no toast when absent. */
  label?: string
  /** Offer undo (default true when a label is given). */
  undo?: boolean
  /** Called with the saved task. */
  onSaved?: (t: TaskItem) => void
  /** A 409: return true to handle it here (the task pane's live-editing bar) instead of the sheet. */
  onConflict?: (e: ConflictError) => boolean
}

function conflictFor(t: TaskItem, mine: Record<string, unknown>, e: ConflictError) {
  useTasksUI.getState().setConflict({ uid: t.uid, title: t.summary, mine, server: e.server, local: t })
}

/** PATCH with the ETag; optimistic; 409 → conflict sheet; 404 → "deleted elsewhere". */
// Writes to one task go one after another, each with the version the previous one returned,
// so quick edits in the pane never race each other into a conflict.
// A batch (and a batch's undo) joins the queue of every task in it, so the next action on those
// tasks starts only after the last write has come back with its new version.
const queues = new Map<string, Promise<unknown>>()
/** The live version this tab's last write to a task returned. A pane edit made from an older version
 *  than that is sent from it: the write in between was the pane's own, already merged on the server
 *  (a reminder removed, then one added before the removal's answer came back, 11 Oct 2026). */
const ownVersions = new Map<string, number>()

export function serialMany<T>(uids: string[], fn: () => Promise<T>): Promise<T> {
  const keys = [...new Set(uids)]
  const before = keys.map((u) => queues.get(u)).filter((q): q is Promise<unknown> => !!q)
  const run = Promise.all(before.map((q) => q.catch(() => undefined))).then(fn)
  for (const u of keys) queues.set(u, run)
  void run.finally(() => {
    for (const u of keys) if (queues.get(u) === run) queues.delete(u)
  }).catch(() => undefined)
  return run
}

function serial<T>(uid: string, fn: () => Promise<T>): Promise<T> {
  return serialMany([uid], fn)
}

/** The newest version the app holds of a task (its ETag moves with every write). */
function freshEtag(qc: QueryClient, t: TaskItem): string | null | undefined {
  return findCached(qc, t.uid)?.etag ?? t.etag
}

export async function patchTask(qc: QueryClient, t: TaskItem, all: Record<string, unknown>, opts: UpdateOpts = {}): Promise<TaskItem | null> {
  // `base_version` (live editing) goes to the server only: it is no field of the task.
  const { base_version: baseVersion, ...changes } = all
  // A list read in flight would land over the change shown here.
  await qc.cancelQueries({ queryKey: ['tasks', 'list'] })
  const snap = snapshotCaches(qc)
  editCaches(qc, (items) => items.map((x) => (x.uid === t.uid ? applyLocal(x, changes) : x)))
  try {
    const saved = await serial(t.uid, async () => {
      const own = ownVersions.get(t.uid)
      const base = baseVersion != null && own != null && own > Number(baseVersion) ? own : baseVersion
      const send = async (etag: string | null | undefined) => {
        const r = await taskRequest<TaskItem>('PATCH', `/tasks/${enc(t.uid)}`, { ...changes, ...(etag ? { etag } : {}), ...(base != null ? { base_version: base } : {}) })
        if (r?.version != null) ownVersions.set(t.uid, r.version)
        return r
      }
      try {
        return await send(freshEtag(qc, t))
      } catch (e) {
        // A 409 whose server copy is this tab's own last write: the copy held here was older than
        // that write (a read from before it came back after it). Nobody else changed the task, so
        // the change goes again on that version (an Undo right after a complete, 10 Oct 2026).
        const own = lastWritten(t.uid)
        if (e instanceof ConflictError && baseVersion == null && own?.etag && e.server?.etag === own.etag) return await send(own.etag)
        throw e
      }
    })
    if (saved && typeof saved === 'object' && 'uid' in saved) upsertCached(qc, saved)
    if (opts.label) {
      const undo = opts.undo === false ? null : () => void patchTask(qc, findCached(qc, t.uid) ?? saved ?? t, undoBody(t, changes), {})
      offerUndo(opts.label, undo)
    }
    opts.onSaved?.(saved)
    return saved
  } catch (e) {
    restoreCaches(qc, snap, [t.uid])
    if (e instanceof ConflictError && opts.onConflict?.(e)) return null
    if (e instanceof ConflictError) conflictFor(t, changes, e)
    else if (e instanceof ApiError && e.status === 404) useTasksUI.getState().setConflict({ uid: t.uid, title: t.summary, mine: changes, server: null, local: t })
    else toastError(e, tr('tasks.actions.taskNotSaved'))
    return null
  } finally {
    invalidateTasks(qc)
  }
}

export async function createTask(qc: QueryClient, body: Record<string, unknown>): Promise<TaskItem | null> {
  try {
    const t = await taskRequest<TaskItem>('POST', '/tasks', body)
    if (t && 'uid' in t) upsertCached(qc, t)
    return t
  } catch (e) {
    toastError(e, tr('tasks.actions.taskNotAdded'))
    return null
  } finally {
    invalidateTasks(qc)
  }
}

/** ACT task.move_list @web.tasks, ACT task.reorder @web.tasks, ACT task.reparent @web.tasks: POST /move, else PATCH. */
/** A move made by a gesture: the task before it, the change, where it sat, and the server's answer. */
export interface MoveStep {
  task: TaskItem
  m: MoveChange
  from?: TaskPlace
  saved: TaskItem | null
}

async function moveTask(qc: QueryClient, t: TaskItem, m: MoveChange): Promise<TaskItem | null> {
  const local: Record<string, unknown> = {}
  if (m.list) local.list = m.list
  if (m.parent !== undefined) local.parent = m.parent
  if (m.sort_order !== undefined) local.sort_order = m.sort_order
  await qc.cancelQueries({ queryKey: ['tasks', 'list'] })
  const snap = snapshotCaches(qc)
  editCaches(qc, (items) => items.map((x) => (x.uid === t.uid ? applyLocal(x, local) : x)))
  try {
    try {
      const etag = freshEtag(qc, t)
      const body = { ...m, sort_order: undefined, ...(etag ? { etag } : {}) }
      const saved = await serial(t.uid, () => taskRequest<TaskItem>('POST', `/tasks/${enc(t.uid)}/move`, body))
      upsertCached(qc, saved)
      return saved
    } catch (e) {
      if (!isMissingRoute(e)) throw e
      const saved = await serial(t.uid, () => {
        const etag = freshEtag(qc, t)
        return taskRequest<TaskItem>('PATCH', `/tasks/${enc(t.uid)}`, { ...local, ...(etag ? { etag } : {}) })
      })
      upsertCached(qc, saved)
      return saved
    }
  } catch (e) {
    restoreCaches(qc, snap, [t.uid])
    if (e instanceof ConflictError) conflictFor(t, local, e)
    else toastError(e, tr('tasks.actions.taskNotMoved'))
    return null
  } finally {
    invalidateTasks(qc)
  }
}

export interface BulkArgs {
  due?: string | null
  list?: string
  tag?: string
  priority?: number
  /** A new due per task. */
  dues?: Record<string, string | null>
  /** A new due per task, worked out from the newest copy of each task when the batch is sent
   *  ("Shift by +1 week" keeps each task's own date and time). */
  duesFor?: (t: TaskItem) => string | null
}

type BulkResult = { ok: string[]; failed: { uid: string; detail?: string; conflict?: boolean }[]; items: TaskItem[] }


const VERB: Record<BulkOp, string> = {
  complete: tr('common.completed'), reopen: tr('tasks.actions.reopened'), reschedule: tr('tasks.actions.rescheduled'), move: tr('tasks.actions.moved'), tag: tr('tasks.actions.tagged'), untag: tr('tasks.actions.untagged'),
  priority: tr('tasks.actions.changedThePriorityOf'), delete: tr('common.deleted'),
}

async function pool<T>(items: T[], n: number, fn: (x: T) => Promise<void>) {
  const queue = [...items]
  await Promise.all(Array.from({ length: Math.min(n, queue.length) }, async () => {
    while (queue.length) await fn(queue.shift()!)
  }))
}

/**
 * One op on many tasks (task.bulk_*): POST /tasks/bulk, else one write per task. One toast and
 * one undo for the batch; a partial failure is reported with Review (selects the failed rows).
 */
async function runBulk(op: BulkOp, tasks: TaskItem[], args: BulkArgs): Promise<BulkResult> {
  const ok: string[] = []
  const failed: { uid: string; detail?: string; conflict?: boolean }[] = []
  const items: TaskItem[] = []
  const perTaskDue = !!args.dues
  if (perTaskDue) {
    try {
      const etags = Object.fromEntries(tasks.filter((t) => t.etag).map((t) => [t.uid, t.etag]))
      const r = await taskRequest<{ ok: string[]; failed: { uid: string; detail?: string; error?: string }[]; items?: TaskItem[] }>('POST', '/tasks/bulk', { uids: tasks.map((t) => t.uid), op, args: { dues: args.dues, etags } })
      return { ok: r.ok ?? [], failed: (r.failed ?? []).map((f) => ({ ...f, conflict: f.error === 'conflict' })), items: r.items ?? [] }
    } catch (e) {
      if (!isMissingRoute(e)) throw e
    }
  } else {
    try {
      const etags = Object.fromEntries(tasks.filter((t) => t.etag).map((t) => [t.uid, t.etag]))
      const a: Record<string, unknown> = { etags }
      if (op === 'reschedule') a.due = args.due ?? null
      if (args.list) a.list = args.list
      if (args.tag) a.tag = args.tag
      if (args.priority != null) a.priority = args.priority
      const r = await taskRequest<{ ok: string[]; failed: { uid: string; detail?: string; error?: string }[]; items?: TaskItem[] }>('POST', '/tasks/bulk', { uids: tasks.map((t) => t.uid), op, args: a })
      return { ok: r.ok ?? [], failed: (r.failed ?? []).map((f) => ({ ...f, conflict: f.error === 'conflict' || /changed|conflict/i.test(f.detail ?? '') })), items: r.items ?? [] }
    } catch (e) {
      if (!isMissingRoute(e)) throw e
    }
  }
  await pool(tasks, 6, async (t) => {
    try {
      if (op === 'delete') {
        await taskRequest('DELETE', `/tasks/${enc(t.uid)}`, undefined, { cascade: 'true', etag: t.etag ?? undefined })
      } else {
        const body = perTaskDue ? (args.dues![t.uid] ? { due: args.dues![t.uid] } : { clear_due: true }) : bulkPatch(op, t, args)
        if (body) {
          const saved = await taskRequest<TaskItem>('PATCH', `/tasks/${enc(t.uid)}`, { ...body, ...(t.etag ? { etag: t.etag } : {}) })
          if (saved && typeof saved === 'object' && 'uid' in saved) items.push(saved)
        }
      }
      ok.push(t.uid)
    } catch (e) {
      failed.push({ uid: t.uid, detail: e instanceof Error ? e.message : String(e), conflict: e instanceof ConflictError })
    }
  })
  return { ok, failed, items }
}

/**
 * Send a batch after every earlier write to its tasks has come back (an undo still running, a
 * pane edit), built from the newest copy of each task: its ETag, and its due for `duesFor`. The
 * optimistic copies go in once the batch is next in line; the server's copies replace them.
 */
export function sendBulk(qc: QueryClient, op: BulkOp, tasks: TaskItem[], args: BulkArgs): Promise<{ res: BulkResult; sent: TaskItem[] }> {
  return serialMany(tasks.map((t) => t.uid), async () => {
    const sent = tasks.map((t) => findCached(qc, t.uid) ?? t)
    const { duesFor, ...rest } = args
    const a: BulkArgs = duesFor ? { ...rest, dues: Object.fromEntries(sent.map((t) => [t.uid, duesFor(t)])) } : rest
    await qc.cancelQueries({ queryKey: ['tasks', 'list'] })
    const snap = snapshotCaches(qc)
    const byUid = new Set(sent.map((t) => t.uid))
    editCaches(qc, (items) => items.map((x) => {
      if (!byUid.has(x.uid)) return x
      const body = a.dues ? (a.dues[x.uid] ? { due: a.dues[x.uid] } : { clear_due: true }) : bulkPatch(op, x, a)
      return body ? applyLocal(x, body) : x
    }))
    try {
      const res = await runBulk(op, sent, a)
      for (const it of res.items) upsertCached(qc, it)
      return { res, sent }
    } catch (e) {
      restoreCaches(qc, snap, byUid)
      throw e
    }
  })
}

/** The one undo of a batch: each task back to its copy from before, in the tasks' write queues,
 *  with the server's new copies kept so the next action carries the right versions. */
export async function undoBulk(qc: QueryClient, op: BulkOp, done: TaskItem[]): Promise<Map<string, TaskItem>> {
  const back = new Map<string, TaskItem>()
  await serialMany(done.map((t) => t.uid), () => pool(done, 6, async (t) => {
    const inv = inverseOf(op, t)
    const cur = findCached(qc, t.uid) ?? t
    let saved: TaskItem | null = null
    if (inv.patch) saved = await taskRequest<TaskItem>('PATCH', `/tasks/${enc(t.uid)}`, inv.patch).catch(() => null)
    else if (inv.recreate) saved = await taskRequest<TaskItem>('POST', '/tasks', createBody(cur)).catch(() => null)
    if (saved && typeof saved === 'object' && 'uid' in saved) {
      upsertCached(qc, saved)
      back.set(t.uid, saved)
    }
  }))
  return back
}

/**
 * The deletes of task.delete (one or many): in the tasks' write queues, each with the newest
 * version the app holds. Subtasks go with their parent (cascade). Resolves to the uids that failed.
 */
export function sendDelete(qc: QueryClient, tasks: TaskItem[], kids: TaskItem[] = []): Promise<string[]> {
  const uids = tasks.map((t) => t.uid)
  return serialMany([...uids, ...kids.map((k) => k.uid)], async () => {
    const failed: string[] = []
    await pool(tasks, 6, async (t) => {
      try {
        const etag = freshEtag(qc, t)
        await taskRequest('DELETE', `/tasks/${enc(t.uid)}`, undefined, { cascade: 'true', etag: etag ?? undefined })
      } catch {
        failed.push(t.uid)
      }
    })
    const ok = new Set(uids.filter((u) => !failed.includes(u)))
    const gone = new Set([...ok, ...kids.filter((k) => ok.has(k.parent ?? '')).map((k) => k.uid)])
    editCaches(qc, (items) => items.filter((x) => !gone.has(x.uid)))
    return failed
  }).finally(() => invalidateTasks(qc))
}

/**
 * The undo of task.delete once the deletes were sent: each task recreated from its copy from
 * before, in the write queues, then its subtasks under the new copy.
 */
export async function restoreDeleted(qc: QueryClient, tasks: TaskItem[], kids: TaskItem[] = []): Promise<void> {
  const back = await undoBulk(qc, 'delete', tasks)
  await serialMany(kids.map((k) => k.uid), () => pool(kids, 6, async (k) => {
    const parent = back.get(k.parent ?? '')?.uid ?? k.parent ?? undefined
    const saved = await taskRequest<TaskItem>('POST', '/tasks', createBody(k, { parent })).catch(() => null)
    if (saved && typeof saved === 'object' && 'uid' in saved) upsertCached(qc, saved)
  }))
  invalidateTasks(qc)
}

// ---------------------------------------------------------------- the hook

export function useTaskActions() {
  const qc = useQueryClient()
  const ui = useTasksUI

  async function update(t: TaskItem, changes: Record<string, unknown>, opts: UpdateOpts = {}) {
    return patchTask(qc, t, changes, opts)
  }

  /** ACT task.complete @web.tasks, ACT task.reopen @web.tasks */
  async function toggleComplete(t: TaskItem) {
    if (isDone(t)) return update(t, { completed: false }, { label: tr('tasks.actions.taskReopened') })
    const open = t.open_subtask_count ?? 0
    let alsoKids: TaskItem[] = []
    if (open > 0) {
      const choice = await ask({ title: tr('tasks.actions.completeSubtasksToo', { count: open }), confirm: tr('tasks.actions.completeAll'), cancel: tr('tasks.actions.onlyThisTask') })
      if (choice === 'confirm') alsoKids = allCached(qc).filter((x) => x.parent === t.uid && !isDone(x))
    }
    if (t.recurring) {
      // A repeating task advances: the server returns it with its next due.
      const before = t
      const saved = await taskRequest<TaskItem & { next?: string; history_uid?: string }>('PATCH', `/tasks/${enc(t.uid)}`, { completed: true, ...(t.etag ? { etag: t.etag } : {}) })
        .catch((e) => {
          if (e instanceof ConflictError) conflictFor(t, { completed: true }, e)
          else toastError(e, tr('tasks.actions.taskNotCompleted'))
          return null
        })
      invalidateTasks(qc)
      if (!saved) return null
      upsertCached(qc, saved)
      // The undo is a reopen: the server puts the due, COUNT and reminders back and deletes the
      // history copy; an older server gets the due back by hand.
      offerUndo(saved.next || saved.due ? tr('tasks.actions.completedMovedToTheNext') : tr('common.taskCompleted'), () => void (async () => {
        const back = await patchTask(qc, findCached(qc, t.uid) ?? saved, { completed: false })
        if (back && before.due && back.due !== before.due) await patchTask(qc, back, { due: before.due })
        invalidateTasks(qc)
      })())
      return saved
    }
    linger([t.uid, ...alsoKids.map((k) => k.uid)])
    const saved = await update(t, { completed: true })
    if (!saved) return null
    await Promise.all(alsoKids.map((k) => patchTask(qc, k, { completed: true })))
    offerUndo(alsoKids.length ? tr('tasks.actions.completedWithSubtasks', { count: alsoKids.length }) : tr('common.taskCompleted'), () => {
      void patchTask(qc, findCached(qc, t.uid) ?? saved, { completed: false })
      for (const k of alsoKids) void patchTask(qc, findCached(qc, k.uid) ?? k, { completed: false })
    })
    return saved
  }

  /**
   * ACT task.delete @web.tasks: hidden at once, sent after the 7 s undo window, subtasks with it.
   * The deletes and their undo go through the tasks' write queues, as the other batches do: the
   * deletes wait for any write still running on those tasks and send its new version, and an undo
   * that comes after they were sent waits for them and then recreates the tasks.
   */
  function remove(tasks: TaskItem[]) {
    const uids = tasks.map((t) => t.uid)
    const kids = allCached(qc).filter((x) => x.parent && uids.includes(x.parent) && !uids.includes(x.uid))
    const hidden = [...uids, ...kids.map((k) => k.uid)]
    ui.getState().hide(hidden)
    ui.getState().select(hidden, false)
    let state: 'waiting' | 'sending' | 'undone' = 'waiting'
    let sending: Promise<string[]> | null = null
    const timer = setTimeout(() => {
      if (state !== 'waiting') return
      state = 'sending'
      sending = sendDelete(qc, tasks, kids)
      void sending.then((failed) => {
        ui.getState().unhide(hidden)
        if (failed.length && tasks.length > 1) toast({ message: tr('tasks.actions.ofDeletedFailed', { value: tasks.length - failed.length, tasksLength: tasks.length, failedLength: failed.length }), error: true })
        if (failed.length && tasks.length === 1) toast({ message: tr('tasks.actions.taskNotDeleted'), error: true })
      })
    }, UNDO_MS)
    const what = tr('tasks.actions.deletedTasks', { count: tasks.length })
    offerUndo(kids.length ? tr('tasks.actions.andSubtasks', { count: kids.length, what }) : what, () => {
      if (state === 'undone') return
      if (state === 'waiting') {
        state = 'undone'
        clearTimeout(timer)
        ui.getState().unhide(hidden)
        return
      }
      state = 'undone'
      const sent = sending!
      void (async () => {
        const failed = await sent
        const gone = new Set(uids.filter((u) => !failed.includes(u)))
        await restoreDeleted(qc, tasks.filter((t) => gone.has(t.uid)), kids.filter((k) => gone.has(k.parent!)))
        toast({ message: tr('tasks.actions.undoneForTasks', { count: gone.size }) })
      })()
    })
  }

  /** ACT task.duplicate @web.tasks: a copy, with its subtasks. */
  async function duplicate(t: TaskItem) {
    const copy = await createTask(qc, createBody(t, { completed: undefined }))
    if (!copy) return null
    const kids = allCached(qc).filter((x) => x.parent === t.uid)
    for (const k of kids) await createTask(qc, createBody(k, { parent: copy.uid }))
    offerUndo(tr('tasks.actions.taskDuplicated'), () => remove([copy]))
    return copy
  }

  /**
   * One move, with one Undo when [label] is given. [from] is where the task sat (its parent and its
   * neighbours): the Undo moves it back there through the same POST /move (task.reorder, task.reparent).
   */
  async function move(t: TaskItem, m: MoveChange, label?: string, from?: TaskPlace) {
    const saved = await moveTask(qc, t, m)
    if (saved && label) offerMovesUndo([{ task: t, m, from, saved }], label)
    return saved
  }

  /**
   * Several moves made by one gesture (a drag of selected rows, a drop inside a row) and their one
   * Undo: each task goes back to its old parent and neighbours.
   */
  function offerMovesUndo(steps: MoveStep[], label: string) {
    const done = steps.filter((s) => s.saved)
    if (!done.length) return
    const moved = new Set(done.map((s) => s.task.uid))
    // From the top of the old order down, so each one has its old neighbour above back in place.
    const ordered = [...done].sort((a, b) => (a.from?.above.length ?? 0) - (b.from?.above.length ?? 0))
    offerUndo(label, () => void (async () => {
      for (const s of ordered) {
        const back = moveBack(s.task, s.m, s.from, moved)
        moved.delete(s.task.uid)
        if (back) await moveTask(qc, findCached(qc, s.task.uid) ?? s.saved!, back)
      }
    })())
  }

  /**
   * ACT task.skip @web.pane: this occurrence of a repeating task is skipped (it advances without a completed
   * copy). Undo is a reopen, which puts the due back (section 6).
   */
  async function skip(t: TaskItem) {
    try {
      const r = await taskRequest<{ task: TaskItem; next?: string | null }>('POST', `/tasks/${enc(t.uid)}/skip`,
        { ...(t.etag ? { etag: t.etag } : {}), ...(t.due ? { occurrence: t.due } : {}) })
      if (r.task) upsertCached(qc, r.task)
      invalidateTasks(qc)
      offerUndo(tr('tasks.actions.skippedToTheNextOccurrence'), () => void (async () => {
        await patchTask(qc, findCached(qc, t.uid) ?? r.task ?? t, { completed: false })
        invalidateTasks(qc)
      })())
      return r.task ?? null
    } catch (e) {
      if (e instanceof ConflictError) conflictFor(t, {}, e)
      else toast({ message: isMissingRoute(e) ? tr('tasks.actions.skippingNeedsANewerMailr') : tr('tasks.actions.notSkipped', { message: (e as Error).message }), error: true })
      return null
    }
  }

  /** The task.bulk_* actions: one toast, one undo, a partial-failure report. */
  async function bulk(op: BulkOp, tasks: TaskItem[], args: BulkArgs = {}) {
    if (!tasks.length) return
    if (op === 'delete') return remove(tasks)
    if (op === 'complete') linger(tasks.map((t) => t.uid))
    let res: BulkResult
    let sent: TaskItem[]
    try {
      ;({ res, sent } = await sendBulk(qc, op, tasks, args))
    } catch (e) {
      toastError(e, tr('tasks.actions.notDone'))
      return
    } finally {
      invalidateTasks(qc)
    }
    const done = sent.filter((t) => res.ok.includes(t.uid))
    const undo = done.length
      ? () => void (async () => {
          await undoBulk(qc, op, done)
          invalidateTasks(qc)
          toast({ message: tr('tasks.actions.undoneForTasks', { count: done.length }) })
        })()
      : null
    const failedUids = res.failed.map((f) => f.uid)
    offerUndo(bulkReport(VERB[op], res.ok.length, res.failed), undo, failedUids.length ? {
      secondary: { label: tr('common.review'), run: () => ui.getState().setSelection(failedUids) }, duration: 12_000,
    } : {})
    if (!failedUids.length) ui.getState().clearSelection()
    else ui.getState().setSelection(failedUids)
  }

  return { update, toggleComplete, remove, duplicate, move, offerMovesUndo, skip, bulk, create: (b: Record<string, unknown>) => createTask(qc, b) }
}

/** Every cached task (the "all" list when loaded, else whatever views hold). */
export function allCached(qc: QueryClient): TaskItem[] {
  const seen = new Map<string, TaskItem>()
  for (const [, d] of snapshotCaches(qc)) {
    const items = !d ? [] : Array.isArray(d) ? d : 'pages' in d ? d.pages.flatMap((p) => p.items) : 'items' in d ? d.items : []
    for (const t of items) if (!seen.has(t.uid)) seen.set(t.uid, t)
  }
  return [...seen.values()]
}

// A completed row stays a moment, struck through, before it leaves the list.
export const useLinger = create<{ uids: Set<string> }>(() => ({ uids: new Set() }))

function linger(uids: string[]) {
  useLinger.setState({ uids: new Set([...useLinger.getState().uids, ...uids]) })
  setTimeout(() => {
    const next = new Set(useLinger.getState().uids)
    for (const u of uids) next.delete(u)
    useLinger.setState({ uids: next })
  }, 1400)
}

// ---------------------------------------------------------------- lists and tags

const TAG_COLORS = 'mailr.tasks.tagColors'

/** Tag colours kept on this device while the server has no tag routes (tag.color falls back here). */
export function localTagColors(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(TAG_COLORS) ?? '{}') as Record<string, string>
  } catch {
    return {}
  }
}

function notYet(e: unknown, what: string) {
  if (isMissingRoute(e)) toast({ message: tr('tasks.actions.needsANewerMailrServer', { what }), error: true })
  else toastError(e, tr('tasks.actions.failed', { what }))
}

export function useListActions() {
  const qc = useQueryClient()
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: qk.tasks.all })
    void qc.invalidateQueries({ queryKey: qk.calendar.all })
  }
  return {
    /** ACT list.create @web.tasks */
    async create(name: string, color?: string | null, connectorId?: number): Promise<TaskList | null> {
      try {
        const l = await taskRequest<TaskList>('POST', '/calendar/collections', { name, color: color ?? undefined, kind: 'tasks', connector_id: connectorId })
        refresh()
        toast({ message: tr('tasks.actions.listCreated', { itemName: name }) })
        return l
      } catch (e) {
        notYet(e, tr('tasks.actions.creatingAList'))
        return null
      }
    },
    /** ACT list.rename @web.tasks, ACT list.color @web.tasks, ACT list.reorder @web.tasks */
    /** ACT list.reorder: the whole order at once (POST /calendar/collections/reorder). */
    async reorder(ids: number[]) {
      try {
        await taskRequest('POST', '/calendar/collections/reorder', { ids })
        refresh()
        offerUndo(tr('tasks.actions.listsReordered'), null)
      } catch (e) {
        notYet(e, tr('tasks.actions.reorderingLists'))
      }
    },
    async patch(l: TaskList, body: { name?: string; color?: string | null; sort_order?: number }, label?: string) {
      if (l.calendar_id == null) return notYet(new ApiError(404, tr('tasks.actions.notFound')), tr('tasks.actions.changingAList'))
      const before = { name: l.name, color: l.color, sort_order: l.sort_order }
      const snap = qc.getQueryData<TaskList[]>(qk.tasks.lists)
      if (snap) qc.setQueryData<TaskList[]>(qk.tasks.lists, snap.map((x) => (x.id === l.id ? { ...x, ...body } : x)))
      try {
        await taskRequest('PATCH', `/calendar/collections/${l.calendar_id}`, body)
        if (label) offerUndo(label, () => void taskRequest('PATCH', `/calendar/collections/${l.calendar_id}`, Object.fromEntries(Object.keys(body).map((k) => [k, before[k as keyof typeof before]]))).then(refresh, () => undefined))
      } catch (e) {
        if (snap) qc.setQueryData(qk.tasks.lists, snap)
        notYet(e, tr('tasks.actions.changingAList'))
      } finally {
        refresh()
      }
    },
    /** ACT list.set_default @web.tasks */
    async setDefault(l: TaskList, previous: string | null) {
      try {
        await taskRequest('PUT', '/calendar/defaults', { task_list: l.id })
        void qc.invalidateQueries({ queryKey: qk.calendar.defaults })
        offerUndo(tr('tasks.actions.isTheDefaultList', { itemName: l.name }), previous ? () => void taskRequest('PUT', '/calendar/defaults', { task_list: previous }).then(() => qc.invalidateQueries({ queryKey: qk.calendar.defaults })) : null)
      } catch (e) {
        toastError(e, tr('tasks.actions.defaultNotChanged'))
      }
    },
    /** ACT list.delete @web.tasks: "Move n tasks to …" (default) or "Delete them too"; confirmed, no undo. */
    async remove(l: TaskList, others: TaskList[], openCount: number) {
      const target = others[0]
      const choice = await ask({
        title: tr('tasks.actions.delete', { itemName: l.name }),
        body: openCount ? tr('tasks.actions.itHoldsOpenTasks', { count: openCount }) : tr('tasks.actions.theListIsEmpty'),
        confirm: openCount && target ? tr('tasks.actions.moveThemTo', { targetName: target.name }) : tr('tasks.actions.deleteList'), alt: openCount ? tr('tasks.actions.deleteThemToo') : undefined, danger: true,
      })
      if (choice === 'cancel') return false
      try {
        const move = choice === 'confirm' && openCount && target ? target.id : undefined
        try {
          await taskRequest('DELETE', `/calendar/collections/${l.calendar_id}`, undefined, { confirm: l.name, move_to: move })
        } catch (e) {
          if (!isMissingRoute(e) || !move) throw e
          await runBulk('move', allCached(qc).filter((t) => t.list === l.id), { list: move })
          await taskRequest('DELETE', `/calendar/collections/${l.calendar_id}`, undefined, { confirm: l.name })
        }
        toast({ message: tr('tasks.actions.listDeleted', { itemName: l.name }) })
        return true
      } catch (e) {
        notYet(e, tr('tasks.actions.deletingAList'))
        return false
      } finally {
        refresh()
      }
    },
    /** ACT tag.rename @web.tasks: PATCH /tasks/tags/{name}, else each task's tags. */
    async renameTag(from: string, to: string) {
      try {
        await taskRequest('PATCH', `/tasks/tags/${enc(from)}`, { name: to })
      } catch (e) {
        if (!isMissingRoute(e)) return toastError(e, tr('tasks.actions.tagNotRenamed'))
        const tasks = allCached(qc).filter((t) => (t.categories ?? []).some((c) => c.toLowerCase() === from.toLowerCase()))
        await pool(tasks, 6, async (t) => {
          const cats = (t.categories ?? []).map((c) => (c.toLowerCase() === from.toLowerCase() ? to : c))
          await taskRequest('PATCH', `/tasks/${enc(t.uid)}`, { categories: cats, ...(t.etag ? { etag: t.etag } : {}) }).catch(() => undefined)
        })
      }
      refresh()
      offerUndo(tr('tasks.actions.tagRenamedTo', { to }), () => void tagOps(qc).renameTag(to, from))
    },
    /** ACT tag.color @web.tasks */
    async colorTag(name: string, color: string | null) {
      try {
        await taskRequest('PATCH', `/tasks/tags/${enc(name)}`, { color })
      } catch (e) {
        if (!isMissingRoute(e)) return toastError(e, tr('tasks.actions.colourNotChanged'))
        const all = localTagColors()
        if (color) all[name.toLowerCase()] = color
        else delete all[name.toLowerCase()]
        try {
          localStorage.setItem(TAG_COLORS, JSON.stringify(all))
        } catch {
          /* ignore */
        }
      }
      refresh()
    },
    /** ACT tag.delete @web.tasks: removes the tag from every task; undo within 7 s. */
    async deleteTag(name: string) {
      const tasks = allCached(qc).filter((t) => (t.categories ?? []).some((c) => c.toLowerCase() === name.toLowerCase()))
      try {
        await taskRequest('DELETE', `/tasks/tags/${enc(name)}`)
      } catch (e) {
        if (!isMissingRoute(e)) return toastError(e, tr('tasks.actions.tagNotDeleted'))
        await runBulk('untag', tasks, { tag: name })
      }
      refresh()
      offerUndo(tr('tasks.actions.tagRemovedFromTasks', { count: tasks.length, itemName: name }), () => void runBulk('tag', tasks.map((t) => ({ ...t, categories: (t.categories ?? []).filter((c) => c.toLowerCase() !== name.toLowerCase()) })), { tag: name }).then(refresh))
    },
  }
}

function tagOps(qc: QueryClient) {
  return {
    renameTag: async (from: string, to: string) => {
      const tasks = allCached(qc).filter((t) => (t.categories ?? []).some((c) => c.toLowerCase() === from.toLowerCase()))
      await taskRequest('PATCH', `/tasks/tags/${enc(from)}`, { name: to }).catch(async (e) => {
        if (!isMissingRoute(e)) return
        await pool(tasks, 6, async (t) => {
          await taskRequest('PATCH', `/tasks/${enc(t.uid)}`, { categories: (t.categories ?? []).map((c) => (c.toLowerCase() === from.toLowerCase() ? to : c)) }).catch(() => undefined)
        })
      })
      void qc.invalidateQueries({ queryKey: qk.tasks.all })
    },
  }
}
