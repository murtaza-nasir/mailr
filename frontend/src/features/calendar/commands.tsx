// Calendar commands as data (the useMailCommands pattern): Office's ribbon, Desk's toolbar and
// the palette draw these. Commands without their own logic run the app's key handler for the same
// action (useAppShortcuts), so a button and its key always do the same thing. The event commands
// (Edit, Delete, Duplicate, RSVP) act on the focused event and are off without one.

import {
  CalendarCheck, CalendarDays, CalendarRange, CalendarSearch, CalendarX, Columns3, CopyPlus, List, Pencil, Plus, RefreshCw,
  Rows3, Search, Square, Sun, Trash2, Undo2,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { commandSet, type AppCommand, type AppCommands } from '../layout/appCommands'
import { useAppRoute } from '../../hooks/useAppRoute'
import { calendarPath, type CalendarView } from '../../lib/routes'
import { shortcutHandler, type CalendarShortcut } from '../../lib/shortcuts'
import { startViewOf, useRememberView, useSavedView } from './prefs'
import { useSettings } from '../../api/hooks'
import { useCalUI } from './store'
import { t } from '../../i18n'

const VIEWS: { view: CalendarView; label: string; key: string; icon: ReactNode }[] = [
  { view: 'today', label: t('calendar.commands.todayView'), key: 'h', icon: <Sun /> },
  { view: 'day', label: t('common.day'), key: 'd', icon: <Square /> },
  { view: 'ndays', label: t('common.n3Days'), key: 'x', icon: <Columns3 /> },
  { view: 'workweek', label: t('common.workWeek'), key: 'o', icon: <Columns3 /> },
  { view: 'week', label: t('common.week'), key: 'w', icon: <CalendarRange /> },
  { view: 'month', label: t('common.month'), key: 'm', icon: <CalendarDays /> },
  { view: 'schedule', label: t('common.schedule'), key: 'a', icon: <List /> },
]

function viaKey(action: CalendarShortcut) {
  return () => void shortcutHandler('calendar', action)?.()
}

export function useCalendarCommands(): AppCommands {
  const nav = useNavigate()
  const { calendar } = useAppRoute()
  const focus = useCalUI((s) => s.focus)
  const ndays = useSavedView((s) => s.ndays)
  const hideWeekends = useSavedView((s) => s.hideWeekends)
  const savedView = useSavedView((s) => s.view)
  const settings = useSettings()
  const remember = useRememberView()
  const view = calendar?.view ?? startViewOf(settings, savedView)
  const commands: AppCommand[] = [
    { id: 'create', label: t('common.newEvent'), icon: <Plus />, group: 'new', shortcut: 'c', enabled: true, run: viaKey('calendar.create') },
    { id: 'today', label: t('common.today'), icon: <Rows3 />, group: 'go', shortcut: 't', enabled: true, run: () => nav(calendarPath(view)) },
    ...VIEWS.map((v): AppCommand => ({
      id: `view-${v.view}`, label: v.view === 'ndays' ? t('calendar.commands.days', { count: ndays }) : v.label, icon: v.icon, group: 'view', shortcut: v.key, enabled: true,
      on: view === v.view, run: () => { remember(v.view); nav(calendarPath(v.view, calendar?.date)) },
    })),
    {
      id: 'hide-weekends', label: t('calendar.commands.hideWeekends'), short: t('calendar.commands.weekends'), icon: <CalendarX />, group: 'view', enabled: view === 'week', on: hideWeekends,
      run: () => useSavedView.getState().set({ hideWeekends: !hideWeekends }),
    },
    { id: 'edit', label: t('common.editEvent'), short: t('common.edit2'), icon: <Pencil />, group: 'event', shortcut: 'e', enabled: !!focus, run: viaKey('calendar.open') },
    { id: 'duplicate', label: t('common.duplicate'), icon: <CopyPlus />, group: 'event', shortcut: 'ctrl+d', enabled: !!focus, run: viaKey('calendar.duplicate') },
    { id: 'delete', label: t('calendar.commands.deleteEvent'), short: t('common.delete2'), icon: <Trash2 />, group: 'event', shortcut: '#', enabled: !!focus, run: viaKey('calendar.delete') },
    { id: 'rsvp-yes', label: t('calendar.commands.replyYes'), short: t('common.yes'), icon: <CalendarCheck />, group: 'event', shortcut: 'y', enabled: !!focus, run: viaKey('calendar.rsvpYes') },
    { id: 'undo', label: t('common.undo'), icon: <Undo2 />, group: 'event', shortcut: 'z', enabled: true, run: viaKey('calendar.undo') },
    { id: 'find-time', label: t('common.findATime'), short: t('calendar.commands.findTime'), icon: <CalendarSearch />, group: 'tools', shortcut: 'f', enabled: true, run: viaKey('calendar.findTime') },
    { id: 'search', label: t('calendar.commands.searchTheCalendar'), short: t('common.search'), icon: <Search />, group: 'tools', shortcut: '/', enabled: true, run: viaKey('calendar.search') },
    { id: 'sync', label: t('common.syncNow'), short: t('calendar.commands.sync'), icon: <RefreshCw />, group: 'tools', shortcut: 'r', enabled: true, run: viaKey('calendar.sync') },
  ]
  return commandSet(commands)
}
