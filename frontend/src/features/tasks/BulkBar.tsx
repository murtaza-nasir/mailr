// The selection bar: Complete, Reschedule (the when picker plus "Shift by +1 day" and
// "+1 week", which keep times), Move to list, Tags (tri-state), Priority, Delete, "n selected"
// and Ask assistant. Each action is one batch with one undo.

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import { Bot, CalendarClock, CircleCheck, Flag, FolderInput, Tag, Trash2, X } from 'lucide-react'
import { Popover, useAnchor } from '../../components/Popover'
import type { DatePrefs } from '../../lib/dates'
import { openAsk } from '../ask/askStore'
import type { BulkArgs } from './actions'
import { MoveMenu, PriorityMenu, ReschedulePanel, TagsMenu } from './Menus'
import { icalPriority, shiftDue, type BulkOp, type TagInfo, type TaskItem, type TaskList } from './model'
import { t as tr } from '../../i18n'

interface Props {
  tasks: TaskItem[]
  total: number
  lists: TaskList[]
  tags: TagInfo[]
  now: Date
  prefs: DatePrefs
  onBulk: (op: BulkOp, args?: BulkArgs) => void
  onSelectAll: () => void
  onClear: () => void
}

export function BulkBar({ tasks, total, lists, tags, now, prefs, onBulk, onSelectAll, onClear }: Props) {
  const due = useAnchor<HTMLButtonElement>()
  const move = useAnchor<HTMLButtonElement>()
  const tag = useAnchor<HTMLButtonElement>()
  const pri = useAnchor<HTMLButtonElement>()
  const n = tasks.length
  const shift = (days: number) => {
    due.close()
    // Shifted from the newest copy of each task when the batch goes out (an undo may still be saving).
    onBulk('reschedule', { duesFor: (t) => (t.due ? shiftDue(t, days, prefs.zone) : null) })
  }
  return (
    <div className="tk-bulk" role="toolbar" aria-label={tr('tasks.bulkBar.selectedTasks')} data-testid="tasks-bulk">
      <button type="button" className="icon-btn" aria-label={tr('tasks.bulkBar.clearSelection')} title={tr('tasks.bulkBar.clearSelectionEsc')} onClick={onClear}><X size={18} /></button>
      <span className="tk-bulk-count" aria-live="polite">{tr('tasks.bulkBar.selected', { n })}</span>
      {n < total && <button type="button" className="btn text sm" onClick={onSelectAll}>{tr('tasks.bulkBar.selectAll', { total })}</button>}
      <span className="tk-bulk-sep" />
      {/* ACT task.bulk_complete @web.tasks */}
      <button type="button" className="tk-tool" onClick={() => onBulk('complete')}><CircleCheck size={16} aria-hidden /><span className="tk-tool-text">{tr('common.complete')}</span></button>
      {/* ACT task.bulk_reschedule @web.tasks */}
      <button type="button" className="tk-tool" onClick={(e) => due.toggle(e.currentTarget)}><CalendarClock size={16} aria-hidden /><span className="tk-tool-text">{tr('common.reschedule')}</span></button>
      {/* ACT task.bulk_move @web.tasks */}
      <button type="button" className="tk-tool" onClick={(e) => move.toggle(e.currentTarget)}><FolderInput size={16} aria-hidden /><span className="tk-tool-text">{tr('common.move')}</span></button>
      {/* ACT task.bulk_tag @web.tasks */}
      <button type="button" className="tk-tool" onClick={(e) => tag.toggle(e.currentTarget)}><Tag size={16} aria-hidden /><span className="tk-tool-text">{tr('common.tags')}</span></button>
      {/* ACT task.bulk_priority @web.tasks */}
      <button type="button" className="tk-tool" onClick={(e) => pri.toggle(e.currentTarget)}><Flag size={16} aria-hidden /><span className="tk-tool-text">{tr('common.priority')}</span></button>
      {/* ACT task.bulk_delete @web.tasks */}
      <button type="button" className="tk-tool danger" onClick={() => onBulk('delete')}><Trash2 size={16} aria-hidden /><span className="tk-tool-text">{tr('common.delete2')}</span></button>
      {/* ACT agent.ask_about @web.tasks: the ask capture about the selected tasks. */}
      <button type="button" className="tk-tool" onClick={(e) => openAsk({ kind: 'tasks', uids: tasks.map((t) => t.uid) }, e.currentTarget)}><Bot size={16} aria-hidden /><span className="tk-tool-text">{tr('tasks.bulkBar.ask', { name: assistantNameNow() })}</span></button>

      <Popover anchor={due.anchor} open={due.open} onClose={due.close} role="dialog" className="tk-when-pop" label={tr('common.reschedule')} noFocus>
        <ReschedulePanel value={null} now={now} prefs={prefs} label={tr('tasks.bulkBar.rescheduleTasks', { count: n })} onPick={(v) => {
          due.close()
          onBulk('reschedule', { due: v })
        }} extra={
          <div className="tk-when-shift">
            <button type="button" className="tk-preset" onClick={() => shift(1)}><span className="tk-preset-label">{tr('tasks.bulkBar.shiftBy1Day')}</span><span className="tk-preset-hint">{tr('tasks.bulkBar.keepsTimes')}</span></button>
            <button type="button" className="tk-preset" onClick={() => shift(7)}><span className="tk-preset-label">{tr('tasks.bulkBar.shiftBy1Week')}</span><span className="tk-preset-hint">{tr('tasks.bulkBar.keepsTimes')}</span></button>
          </div>
        } />
      </Popover>
      <Popover anchor={move.anchor} open={move.open} onClose={move.close} label={tr('common.moveToList')}>
        <MoveMenu lists={lists} onPick={(l) => {
          move.close()
          onBulk('move', { list: l.id })
        }} />
      </Popover>
      <Popover anchor={tag.anchor} open={tag.open} onClose={tag.close} label={tr('common.tags')}>
        <TagsMenu tasks={tasks} tags={tags} onAdd={(t) => {
          tag.close()
          onBulk('tag', { tag: t })
        }} onRemove={(t) => {
          tag.close()
          onBulk('untag', { tag: t })
        }} />
      </Popover>
      <Popover anchor={pri.anchor} open={pri.open} onClose={pri.close} label={tr('common.priority')}>
        <PriorityMenu tasks={tasks} onPick={(l) => {
          pri.close()
          onBulk('priority', { priority: icalPriority(l) })
        }} />
      </Popover>
    </div>
  )
}
