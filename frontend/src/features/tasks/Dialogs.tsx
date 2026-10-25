// The app's dialogs: a small question (ask()), the conflict sheet (changed elsewhere: Keep theirs /
// Use mine / Keep both, or "deleted elsewhere" with Restore as new), and the name-and-colour
// form for a new list or a tag rename.

import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { create } from 'zustand'
import { Dialog } from '../../components/Dialog'
import { toast } from '../../stores/ui'
import { createBody, createTask, patchTask, useAsk } from './actions'
import { upsertCached } from './api'
import { SWATCHES } from './colors'
import type { TaskItem } from './model'
import { useTasksUI } from './store'
import { t as tr } from '../../i18n'

// The Tasks app, the tasks panel and the calendar (task chips) each mount an AskDialog; only the
// first one mounted draws the question.
let hosts: number[] = []
let nextHost = 1
const useHosts = create<{ first: number }>(() => ({ first: 0 }))

export function AskDialog() {
  const [id] = useState(() => nextHost++)
  useEffect(() => {
    hosts.push(id)
    useHosts.setState({ first: hosts[0] })
    return () => {
      hosts = hosts.filter((h) => h !== id)
      useHosts.setState({ first: hosts[0] ?? 0 })
    }
  }, [id])
  const first = useHosts((s) => s.first)
  const a = useAsk((s) => s.ask)
  if (!a || first !== id) return null
  return (
    <Dialog open onClose={() => a.resolve('cancel')} title={a.title} initialFocus={a.altPlain ? '.tk-ask-default' : undefined} footer={
      <>
        <button type="button" className="btn text" onClick={() => a.resolve('cancel')}>{a.cancel ?? tr('common.cancel')}</button>
        {a.alt && <button type="button" className={`btn ${a.altPlain ? 'tonal' : 'danger'}`} onClick={() => a.resolve('alt')}>{a.alt}</button>}
        <button type="button" className={`btn ${a.danger && !a.alt ? 'danger' : 'filled'} tk-ask-default`} onClick={() => a.resolve('confirm')} autoFocus>{a.confirm}</button>
      </>
    }>
      {a.body && <p className="tk-dialog-text">{a.body}</p>}
    </Dialog>
  )
}

const FIELD_NAMES: Record<string, string> = {
  summary: tr('common.title'), notes: tr('common.notes'), due: tr('common.due'), clear_due: tr('common.due'), start: tr('common.start'), clear_start: tr('common.start'), priority: tr('common.priority'), categories: tr('common.tags'),
  list: tr('common.list'), reminders: tr('common.reminders'), recurrence: tr('common.repeat'), repeat_from: tr('common.repeat'), completed: tr('common.done'), parent: tr('tasks.dialogs.parent'), sort_order: tr('common.order'),
}

function show(v: unknown): string {
  if (v == null || v === '') return '—'
  if (Array.isArray(v)) return v.length ? v.join(', ') : '—'
  return String(v)
}

/** The conflict sheet for tasks. */
export function ConflictSheet() {
  const c = useTasksUI((s) => s.conflict)
  const qc = useQueryClient()
  const close = () => useTasksUI.getState().setConflict(null)
  if (!c) return null
  const fields = Object.keys(c.mine).filter((k) => k !== 'etag' && k !== 'tz')
  if (!c.server) {
    return (
      <Dialog open onClose={close} title={tr('tasks.dialogs.thisTaskWasDeletedElsewhere')} footer={
        <>
          <button type="button" className="btn text" onClick={close}>{tr('common.discard')}</button>
          <button type="button" className="btn filled" onClick={async () => {
            close()
            const t = await createTask(qc, createBody({ ...c.local, ...applyMine(c.local, c.mine) }))
            if (t) toast({ message: tr('tasks.dialogs.restoredAsANewTask') })
          }}>{tr('common.restoreAsNew')}</button>
        </>
      }>
        <p className="tk-dialog-text">{tr('tasks.dialogs.isNoLongerOnThe', { title: c.title })}</p>
      </Dialog>
    )
  }
  const server = c.server
  return (
    <Dialog open onClose={close} title={tr('common.changedElsewhere')} wide footer={
      <>
        {/* Keep theirs: the server copy stays. */}
        <button type="button" className="btn text" onClick={() => {
          upsertCached(qc, server)
          close()
        }}>{tr('common.keepTheirs')}</button>
        {/* Keep both: a duplicate with my change. */}
        <button type="button" className="btn tonal" onClick={async () => {
          close()
          upsertCached(qc, server)
          await createTask(qc, createBody({ ...server, ...applyMine(server, c.mine) }, { parent: undefined }))
          toast({ message: tr('tasks.dialogs.keptBothYourVersionIs') })
        }}>{tr('tasks.dialogs.keepBoth')}</button>
        {/* Use mine: the change again, on the server's version. */}
        <button type="button" className="btn filled" onClick={async () => {
          close()
          await patchTask(qc, server, c.mine, { label: tr('tasks.dialogs.yourChangeWasSaved') })
        }}>{tr('common.useMine')}</button>
      </>
    }>
      <p className="tk-dialog-text">{tr('tasks.dialogs.changedOnAnotherDeviceAfter', { title: c.title })}</p>
      <table className="tk-conflict">
        <thead><tr><th /><th>{tr('tasks.dialogs.theirs')}</th><th>{tr('tasks.dialogs.mine')}</th></tr></thead>
        <tbody>
          {fields.map((f) => (
            <tr key={f}>
              <th>{FIELD_NAMES[f] ?? f}</th>
              <td>{show((server as unknown as Record<string, unknown>)[f === 'recurrence' ? 'rrule' : f === 'clear_due' ? 'due' : f === 'clear_start' ? 'start' : f])}</td>
              <td>{f.startsWith('clear_') ? '—' : show(c.mine[f])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Dialog>
  )
}

function applyMine(t: TaskItem, mine: Record<string, unknown>): Partial<TaskItem> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(mine)) {
    if (k === 'etag' || k === 'tz') continue
    if (k === 'clear_due') out.due = null
    else if (k === 'clear_start') out.start = null
    else if (k === 'recurrence') out.rrule = v || null
    else if (k === 'summary') out.summary = v
    else out[k] = v
  }
  void t
  return out as Partial<TaskItem>
}

/** A name (and a colour): "+ New list" (list.create) and the rename of a tag or list. */
export function NameDialog({ open, title, initial, withColor, initialColor, confirm, onClose, onSave }: {
  open: boolean; title: string; initial?: string; withColor?: boolean; initialColor?: string | null; confirm: string
  onClose: () => void; onSave: (name: string, color: string | null) => void
}) {
  const [name, setName] = useState(initial ?? '')
  const [color, setColor] = useState<string | null>(initialColor ?? null)
  useEffect(() => {
    if (open) {
      setName(initial ?? '')
      setColor(initialColor ?? (withColor ? SWATCHES[6].hex : null))
    }
  }, [open, initial, initialColor, withColor])
  const submit = () => {
    if (!name.trim()) return
    onSave(name.trim(), color)
  }
  return (
    <Dialog open={open} onClose={onClose} title={title} initialFocus="input" footer={
      <>
        <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
        <button type="button" className="btn filled" disabled={!name.trim()} onClick={submit}>{confirm}</button>
      </>
    }>
      <label className="tk-dialog-field">
        <span>{tr('common.name')}</span>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()} aria-label={tr('common.name')} />
      </label>
      {withColor && (
        <div className="tk-dialog-field">
          <span>{tr('common.colour')}</span>
          <div className="tk-swatches wide" role="radiogroup" aria-label={tr('common.colour')}>
            {SWATCHES.map((s) => (
              <button key={s.hex} type="button" role="radio" aria-checked={color === s.hex} aria-label={s.name} title={s.name}
                className={`tk-swatch${color === s.hex ? ' on' : ''}`} style={{ background: s.hex }} onClick={() => setColor(s.hex)} />
            ))}
          </div>
          <span className="muted small">{SWATCHES.find((s) => s.hex === color)?.name ?? tr('common.noColour')}</span>
        </div>
      )}
    </Dialog>
  )
}
