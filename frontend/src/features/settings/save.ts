// Settings save at once, with Undo. No settings page has a Save button: a choice saves when
// you make it, and a text field saves when you leave it or stop typing for a moment
// (`useTextDraft`). An edit still waiting when the page goes away is saved then, so switching
// pages never drops it. Each save shows one toast with Undo; a newer save replaces the toast.
//
// The saves run outside React (`saveSettings`), so a save that finishes after its page has gone
// still updates the cache and still offers Undo.

import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import { qk, useSettings } from '../../api/hooks'
import type { Me, Settings } from '../../api/types'
import type { ReminderSettings } from '../reminders/api'
import { toast, toastError, useUI } from '../../stores/ui'
import { t } from '../../i18n'

let lastToast: number | null = null

/** One "Saved" toast at a time, with Undo when `undo` is given. */
export function savedToast(message: string, undo?: () => void): void {
  if (lastToast !== null) useUI.getState().dismissToast(lastToast)
  lastToast = toast(undo ? { message, actionLabel: t('common.undo'), onAction: undo } : { message })
}

/** The values `keys` have now, null for a key that is not set (null clears it again). */
export function before<T extends object>(current: Partial<T> | undefined, patch: Partial<T>): Partial<T> {
  const out: Record<string, unknown> = {}
  const cur = (current ?? {}) as Record<string, unknown>
  for (const k of Object.keys(patch)) out[k] = k in cur && cur[k] !== undefined ? cur[k] : null
  return out as Partial<T>
}

export interface SaveOpts {
  /** The toast's words (default "Saved."). */
  message?: string
  /** No toast at all (the caller shows its own). */
  quiet?: boolean
}

/** PATCH /me/settings with the change shown at once, and a toast that can undo it.
 *  ACT settings.undo_change @web.settings */
export async function saveSettings(qc: QueryClient, patch: Partial<Settings>, opts: SaveOpts = {}, isUndo = false): Promise<void> {
  const was = before<Settings>(qc.getQueryData<Me>(qk.me)?.settings, patch)
  const show = () => {
    const prev = qc.getQueryData<Me>(qk.me)
    if (prev) qc.setQueryData<Me>(qk.me, { ...prev, settings: { ...prev.settings, ...patch } })
  }
  // The change shows at once. A /me answer already on its way holds the old values: drop it, and
  // show the change again in case dropping it put the old ones back.
  show()
  await qc.cancelQueries({ queryKey: qk.me })
  show()
  return api
    .patch<unknown>('/me/settings', patch as Record<string, unknown>)
    .then(() => {
      if (opts.quiet) return
      if (isUndo) savedToast(t('common.changeUndone'))
      else savedToast(opts.message ?? t('common.saved2'), () => void saveSettings(qc, was, {}, true))
    })
    .catch((e) => {
      const now = qc.getQueryData<Me>(qk.me)
      if (now) qc.setQueryData<Me>(qk.me, { ...now, settings: { ...now.settings, ...was } })
      toastError(e, t('common.notSaved'))
    })
    .finally(() => void qc.invalidateQueries({ queryKey: qk.me }))
}

/** `save(patch)` for a settings page: at once, with Undo. */
export function useSaveSettings(): (patch: Partial<Settings>, opts?: SaveOpts) => Promise<void> {
  const qc = useQueryClient()
  return useCallback((patch, opts) => saveSettings(qc, patch, opts), [qc])
}

type ReminderPatch = Partial<Omit<ReminderSettings, 'web' | 'repeat'>> & { web?: Partial<ReminderSettings['web']>; repeat?: Partial<NonNullable<ReminderSettings['repeat']>> }

/** PUT /reminders/settings the same way (reminder defaults, which reminders). */
export async function saveReminderSettings(qc: QueryClient, patch: ReminderPatch, isUndo = false): Promise<void> {
  const prev = qc.getQueryData<ReminderSettings>(qk.reminders.settings)
  const was: ReminderPatch = {}
  if (prev) {
    for (const k of Object.keys(patch) as (keyof ReminderPatch)[]) {
      const p = prev as unknown as Record<string, unknown>
      ;(was as Record<string, unknown>)[k] = k === 'web' || k === 'repeat' ? { ...(p[k] as object) } : p[k] ?? null
    }
    qc.setQueryData<ReminderSettings>(qk.reminders.settings, {
      ...prev, ...patch, web: { ...prev.web, ...(patch.web ?? {}) },
      repeat: { enabled: false, every_min: 10, max: 3, ...prev.repeat, ...(patch.repeat ?? {}) },
    } as ReminderSettings)
  }
  return api
    .put<ReminderSettings>('/reminders/settings', patch as Record<string, unknown>)
    .then((s) => {
      qc.setQueryData(qk.reminders.settings, s)
      if (isUndo) savedToast(t('common.changeUndone'))
      else savedToast(t('common.saved2'), prev ? () => void saveReminderSettings(qc, was, true) : undefined)
    })
    .catch((e) => {
      if (prev) qc.setQueryData(qk.reminders.settings, prev)
      toastError(e, t('settings.save.reminderSettingsNotSaved'))
    })
}

export function useSaveReminders(): (patch: ReminderPatch) => Promise<void> {
  const qc = useQueryClient()
  return useCallback((patch) => saveReminderSettings(qc, patch), [qc])
}

/**
 * A text field's own copy while you type. It is saved after `delay` ms without typing, when the
 * field loses focus (`flush`), and when the page goes away, so an edit is never dropped. The
 * field follows the saved value again once nothing is waiting.
 */
export function useTextDraft<T>(saved: T, commit: (v: T) => void, delay = 800): { value: T; set: (v: T) => void; flush: () => void } {
  const [draft, setDraft] = useState<{ v: T } | null>(null)
  const pending = useRef<{ v: T } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const commitRef = useRef(commit)
  commitRef.current = commit
  const flush = useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    const p = pending.current
    pending.current = null
    if (p) {
      commitRef.current(p.v)
      setDraft(null)
    }
  }, [])
  const set = useCallback((v: T) => {
    pending.current = { v }
    setDraft({ v })
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(flush, delay)
  }, [delay, flush])
  // Leaving the page saves what is waiting.
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
    const p = pending.current
    pending.current = null
    if (p) commitRef.current(p.v)
  }, [])
  return { value: draft ? draft.v : saved, set, flush }
}

/** The settings and `set(key, value)`, which saves at once with Undo. */
export function useInstantSettings() {
  const saved = useSettings()
  const save = useSaveSettings()
  // The choice shows in the same render as the click (a controlled radio would otherwise flick
  // back until the cache update arrives); it gives way to the saved value once that catches up.
  const [shown, setShown] = useState<Partial<Settings>>({})
  useEffect(() => {
    setShown((p) => {
      const keys = Object.keys(p) as (keyof Settings)[]
      const left = keys.filter((k) => JSON.stringify(saved[k]) !== JSON.stringify(p[k]))
      return left.length === keys.length ? p : (Object.fromEntries(left.map((k) => [k, p[k]])) as Partial<Settings>)
    })
  }, [saved])
  const s = useMemo(() => ({ ...saved, ...shown }) as Settings, [saved, shown])
  const set = useCallback(<K extends keyof Settings>(k: K, v: Settings[K]) => {
    setShown((p) => ({ ...p, [k]: v }))
    void save({ [k]: v } as Partial<Settings>).finally(() => setShown((p) => {
      const { [k]: _gone, ...rest } = p
      return rest as Partial<Settings>
    }))
  }, [save])
  return { s, set, save }
}
