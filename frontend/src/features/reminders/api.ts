// Reminder data and actions on the web: the tray list (GET /reminders/active), the settings
// (GET/PUT /reminders/settings), and the actions of the sheet, the tray and the triage queue,
// each with an undo toast. A handled reminder's browser notification closes at once
// (notify.ts) and every other tab and device hears it through the `reminder` change.

import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { create } from 'zustand'
import { api } from '../../api/client'
import { qk } from '../../api/hooks'
import { toast, toastError } from '../../stores/ui'
import type { ReminderItem } from './logic'
import { closeTag } from './notify'
import { t, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'

export interface ReminderSettings {
  events: boolean
  tasks: boolean
  default_timed_event: number | null
  default_all_day_event: number | null
  default_task: boolean
  morning_hour: number
  web: { desktop: boolean; push: boolean }
  /** A late reminder is shown as "Missed: …" (false: marked fired, no notification). */
  missed?: boolean
  /** Clients play a sound with each reminder. */
  sound?: boolean
  /** Remind again: the server alerts the web again; the phone repeats on its own. */
  repeat?: { enabled: boolean; every_min: number; max: number }
}

export function useActiveReminders(enabled = true) {
  return useQuery<ReminderItem[]>({
    queryKey: qk.reminders.active,
    queryFn: async ({ signal }) => {
      // This page's clock decides which snoozes are due and which events are over, as the tray shows them.
      const r = await api.get<{ items: ReminderItem[] }>('/reminders/active', { now: new Date().toISOString() }, signal)
      return r.items ?? []
    },
    enabled,
    staleTime: 15_000,
    // Snoozes come due without a change event; a slow poll brings them into the tray.
    refetchInterval: 60_000,
    retry: false,
  })
}

export function useReminderSettings() {
  return useQuery<ReminderSettings>({
    queryKey: qk.reminders.settings,
    queryFn: ({ signal }) => api.get<ReminderSettings>('/reminders/settings', undefined, signal),
    staleTime: 5 * 60_000,
  })
}

/** ACT notification.subscribe @web.settings (the per-kind switches and the web toggles): a partial PUT. */
export function useSaveReminderSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (patch: Partial<Omit<ReminderSettings, 'web' | 'repeat'>> & { web?: Partial<ReminderSettings['web']>; repeat?: Partial<NonNullable<ReminderSettings['repeat']>> }) =>
      api.put<ReminderSettings>('/reminders/settings', patch as Record<string, unknown>),
    onMutate: async (patch) => {
      const prev = qc.getQueryData<ReminderSettings>(qk.reminders.settings)
      if (prev) qc.setQueryData<ReminderSettings>(qk.reminders.settings, {
        ...prev, ...patch, web: { ...prev.web, ...(patch.web ?? {}) },
        repeat: { enabled: false, every_min: 10, max: 3, ...prev.repeat, ...(patch.repeat ?? {}) },
      })
      return { prev }
    },
    onError: (e, _p, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.reminders.settings, ctx.prev)
      toastError(e, t('reminders.api.reminderSettingsNotSaved'))
    },
    onSuccess: (s) => qc.setQueryData(qk.reminders.settings, s),
  })
}

// ---------- what is open: the sheet for one key, or the triage queue ----------

interface ReminderUI {
  /** The key whose sheet is open from a notification (a dialog). The tray opens its own in place. */
  sheet: string | null
  /** The sheet opens on the Remind before offsets ("Remind before…" on a notification). */
  sheetMode?: 'offset' | null
  /** The triage queue: the keys, in stack order, when open. */
  triage: string[] | null
  trayOpen: boolean
  set: (p: Partial<Omit<ReminderUI, 'set'>>) => void
}

export const useReminderUI = create<ReminderUI>((set) => ({
  sheet: null,
  sheetMode: null,
  triage: null,
  trayOpen: false,
  set: (p) => set(p),
}))

// ---------- actions ----------

const enc = encodeURIComponent

function refresh(qc: QueryClient) {
  void qc.invalidateQueries({ queryKey: qk.reminders.all })
  void qc.invalidateQueries({ queryKey: qk.tasks.all })
  void qc.invalidateQueries({ queryKey: qk.calendar.all })
}

/** Takes the reminder out of the tray at once; the server answer settles it. */
function drop(qc: QueryClient, key: string) {
  qc.setQueryData<ReminderItem[]>(qk.reminders.active, (old) => old?.filter((r) => r.key !== key))
  void closeTag(key)
}

const clock = (d: Date) => d.toLocaleTimeString(appLocale(), { hour: 'numeric', minute: '2-digit', ...clockOpts() })
const moment = (d: Date) => {
  const today = new Date()
  const same = d.toDateString() === today.toDateString()
  return same ? clock(d) : `${d.toLocaleDateString(appLocale(), { weekday: 'short', day: 'numeric', month: 'short' })} ${clock(d)}`
}

export function useReminderActions() {
  const qc = useQueryClient()
  const run = async (r: ReminderItem, message: string, fn: () => Promise<unknown>, undo?: () => Promise<unknown>, quiet = false) => {
    drop(qc, r.key)
    try {
      await fn()
      refresh(qc)
      if (!quiet)
        toast({
          message,
          ...(undo ? { actionLabel: t('common.undo'), onAction: () => void undo().then(() => refresh(qc)).catch((e) => toastError(e, t('common.couldNotUndo'))) } : {}),
        })
      return true
    } catch (e) {
      refresh(qc)
      toastError(e, t('reminders.api.couldNotDoThat'))
      return false
    }
  }
  const unhandle = (r: ReminderItem) => api.post('/reminders/handle', { keys: [r.key], undo: true })
  return {
    /** ACT task.snooze @web.sheet, ACT event.snooze @web.sheet */
    snooze: (r: ReminderItem, until: Date, quiet = false) =>
      run(r, `Snoozed until ${moment(until)}`, () => api.post('/reminders/snooze', { key: r.key, until: until.toISOString() }), () => unhandle(r), quiet),
    /** ACT reminder.dismiss @web.sheet */
    dismiss: (r: ReminderItem, quiet = false) =>
      run(r, 'Reminder dismissed', () => api.post('/reminders/handle', { keys: [r.key] }), () => unhandle(r), quiet),
    /** ACT task.complete @web.sheet (on an event reminder Done is a dismiss) */
    done: (r: ReminderItem, quiet = false) =>
      r.kind === 'task'
        ? run(r, `Done: ${r.title}`,
            async () => {
              await api.patch(`/tasks/${enc(r.uid)}`, { completed: true, ...(r.occurrence ? { occurrence: r.occurrence } : {}) })
              await api.post('/reminders/handle', { keys: [r.key] })
            },
            async () => {
              await api.patch(`/tasks/${enc(r.uid)}`, { completed: false })
              await unhandle(r)
            }, quiet)
        : run(r, 'Reminder dismissed', () => api.post('/reminders/handle', { keys: [r.key] }), () => unhandle(r), quiet),
    /** ACT event.remind_before @web.sheet: this reminder is handled and a one-off fires `minutes` before the start. */
    remindBefore: (r: ReminderItem, minutes: number, label: string) =>
      run(r, `You will be reminded ${label.toLowerCase()}`,
        () => api.post('/reminders/add', { kind: 'e', uid: r.uid, occurrence: r.occurrence, minutes, handle: [r.key] }),
        () => unhandle(r)),
    /** ACT task.reschedule @web.sheet: a new due; the old reminder is handled and new ones are planned from it. */
    rescheduleTask: (r: ReminderItem, due: string, label: string) =>
      run(r, `Due ${label}`,
        async () => {
          await api.patch(`/tasks/${enc(r.uid)}`, { due })
          await api.post('/reminders/handle', { keys: [r.key] })
        },
        async () => {
          await api.patch(`/tasks/${enc(r.uid)}`, r.start ? { due: r.start } : { clear_due: true })
          await unhandle(r)
        }),
  }
}

export type ReminderActions = ReturnType<typeof useReminderActions>

/** The most keys one /reminders/handle call takes (HandleIn in calendar/reminders.py). */
const HANDLE_MAX = 500

async function handleKeys(keys: string[], undo = false) {
  for (let i = 0; i < keys.length; i += HANDLE_MAX)
    await api.post('/reminders/handle', { keys: keys.slice(i, i + HANDLE_MAX), ...(undo ? { undo: true } : {}) })
}

export const clearAllText = (n: number) => t('reminders.api.remindersCleared', { count: n })

/** ACT reminder.dismiss_all @web.notification: the tray's Clear all. Every reminder the tray lists
 * is dismissed in one request (as a dismiss on any device, it is gone everywhere), and one Undo
 * puts them all back. Snoozes still in the future are not in the tray, so they stay. */
export function useClearAllReminders() {
  const qc = useQueryClient()
  return async (items: ReminderItem[]) => {
    const keys = items.map((r) => r.key)
    if (!keys.length) return false
    qc.setQueryData<ReminderItem[]>(qk.reminders.active, (old) => old?.filter((r) => !keys.includes(r.key)))
    for (const k of keys) void closeTag(k)
    try {
      await handleKeys(keys)
      refresh(qc)
      toast({
        message: clearAllText(keys.length),
        actionLabel: t('common.undo'),
        onAction: () => void handleKeys(keys, true).then(() => refresh(qc)).catch((e) => toastError(e, t('common.couldNotUndo'))),
      })
      return true
    } catch (e) {
      refresh(qc)
      toastError(e, t('reminders.api.couldNotClearTheReminders'))
      return false
    }
  }
}
