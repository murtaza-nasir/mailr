// Task commands as data (the useMailCommands pattern): Office's ribbon, Desk's toolbar and the
// palette draw these. Each command runs the app's key handler for the same action
// (useAppShortcuts in TasksApp), so a button and its key always do the same thing, on the
// focused task or the selection.

import {
  CalendarClock, CalendarDays, CalendarPlus, CircleCheck, Copy, Flag, FolderInput, ListChecks, ListTodo, Plus, Search, Sun, Tag, Trash2, Undo2,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { commandSet, type AppCommand, type AppCommands } from '../layout/appCommands'
import { useAppRoute } from '../../hooks/useAppRoute'
import { tasksPath, type SmartList } from '../../lib/routes'
import { shortcutHandler, type TaskShortcut } from '../../lib/shortcuts'
import { t } from '../../i18n'

const LISTS: { id: SmartList; label: string; key: string; icon: ReactNode }[] = [
  { id: 'today', label: t('common.today'), key: 'g t', icon: <Sun /> },
  { id: 'upcoming', label: t('common.upcoming'), key: 'g u', icon: <CalendarDays /> },
  { id: 'all', label: t('common.all'), key: 'g a', icon: <ListTodo /> },
]

function viaKey(action: TaskShortcut, arg?: string) {
  return () => void shortcutHandler('tasks', action)?.(arg)
}

export function useTaskCommands(): AppCommands {
  const nav = useNavigate()
  const { tasks } = useAppRoute()
  const list = tasks?.list
  const cmd = (id: string, label: string, icon: ReactNode, group: string, shortcut: string, action: TaskShortcut, arg?: string, short?: string): AppCommand => ({
    id, label, short, icon, group, shortcut, enabled: true, run: viaKey(action, arg),
  })
  const commands: AppCommand[] = [
    // ACT task.create @web.tasks
    { id: 'create', label: t('common.newTask'), icon: <Plus />, group: 'new', shortcut: 'n', enabled: true, run: viaKey('tasks.quickAdd') },
    ...LISTS.map((l): AppCommand => ({
      id: `list-${l.id}`, label: l.label, icon: l.icon, group: 'go', shortcut: l.key, enabled: true,
      on: list?.kind === 'smart' && list.id === l.id, run: () => void nav(tasksPath(l.id)),
    })),
    // ACT task.complete @web.tasks
    cmd('complete', t('common.complete'), <CircleCheck />, 'task', 'space', 'tasks.complete'),
    // ACT task.reschedule @web.tasks
    cmd('reschedule', t('common.reschedule'), <CalendarClock />, 'task', 'r', 'tasks.reschedule'),
    // ACT task.set_start @web.tasks
    cmd('defer', t('tasks.commands.defer'), <CalendarPlus />, 'task', 's', 'tasks.start'),
    // ACT task.set_priority @web.tasks
    cmd('priority', t('common.highPriority'), <Flag />, 'task', '3', 'tasks.priority', '3', t('common.priority')),
    // ACT task.move_list @web.tasks
    cmd('move', t('common.moveToList'), <FolderInput />, 'task', 'v', 'tasks.move', undefined, t('common.move')),
    // ACT task.tag @web.tasks
    cmd('tags', t('common.tags'), <Tag />, 'task', 'l', 'tasks.tags'),
    // ACT task.duplicate @web.tasks
    cmd('duplicate', t('common.duplicate'), <Copy />, 'task', 'ctrl+d', 'tasks.duplicate'),
    // ACT task.delete @web.tasks
    cmd('delete', t('common.delete2'), <Trash2 />, 'task', '#', 'tasks.delete'),
    cmd('select', t('common.select'), <ListChecks />, 'tools', 'x', 'tasks.select'),
    cmd('undo', t('common.undo'), <Undo2 />, 'tools', 'z', 'tasks.undo'),
    // ACT task.search @web.tasks
    cmd('search', t('tasks.commands.searchTasks'), <Search />, 'tools', '/', 'tasks.search', undefined, t('common.search')),
  ]
  return commandSet(commands)
}
