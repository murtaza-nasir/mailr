// One per tab (mounted by the bell): turns a fired reminder (SSE `notification` of kind
// `reminder`, re-sent by api/events.ts as the window event `mailr:reminder`) into a browser
// notification; more than three at once become one summary. A click on a notification focuses
// this tab and opens the quick-action sheet for the key (the triage queue for a summary), and never handles
// the reminder. "Remind before…" on an event notification opens the sheet on its
// offsets. A reminder handled anywhere closes its notification here. Missed reminders never
// arrive as a burst: one quiet "3 missed reminders" notice on load. A repeat (Remind again, `again`
// in the payload) has the same key, so it replaces the notification or toast and alerts again.
// ACT reminder.repeat @web.notification, ACT reminder.fire @web.notification, ACT reminder.sync @web.notification, ACT notification.open @web.notification, ACT notification.open_stack @web.notification, ACT event.remind_before @web.notification

import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router'
import { api } from '../../api/client'
import { qk } from '../../api/hooks'
import { conferenceLink } from '../calendar/eventInfo'
import type { ApiEvent } from '../calendar/types'
import { toast, useUI } from '../../stores/ui'
import { openAsk, reminderAbout } from '../ask/askStore'
import { useActiveReminders, useReminderActions, useReminderSettings, useReminderUI } from './api'
import {
  EXPAND_PARAM, itemFromPayload, kindOfKey, newMissed, planBurst, SHEET_PARAM, stackOrder, staleTags, TRIAGE_PARAM, whenLine, type ReminderItem, type ReminderPayload,
} from './logic'
import { closeTag, permission, setOpener, showMissed, showReminder, showSummary, shownTags } from './notify'
import { ReminderSheet, useReminderPrefs } from './ReminderSheet'
import { t as tr } from '../../i18n'

const ANNOUNCED = 'mailr.reminders.announced'

function readAnnounced(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(ANNOUNCED) ?? '[]') as string[])
  } catch {
    return new Set()
  }
}

function writeAnnounced(s: Set<string>) {
  try {
    localStorage.setItem(ANNOUNCED, JSON.stringify([...s].slice(-200)))
  } catch {
    /* private mode */
  }
}

const isReminderTag = (t: string) => t.includes('|')

/** The meeting link of an event that starts within 10 minutes, for the Join button (event.join). */
async function joinLink(p: ReminderPayload): Promise<string | null> {
  if (p.item?.kind !== 'event' || !p.item.uid || !p.start) return null
  const t = Date.parse(p.start)
  if (Number.isNaN(t) || t - Date.now() > 10 * 60_000) return null
  // The live stream carries the link; older servers need the event read.
  if (p.join_url !== undefined) return p.join_url || null
  try {
    // ACT event.join @web.notification
    return conferenceLink(await api.get<ApiEvent>(`/calendar/events/${encodeURIComponent(p.item.uid)}`))
  } catch {
    return null
  }
}

/** Settings → Notifications → "Missed reminders notice" (this device). */
function missedNoticeOn(): boolean {
  try {
    return localStorage.getItem('mailr.reminders.missedNotice') !== '0'
  } catch {
    return true
  }
}

/**
 * Without desktop notifications (no permission, or turned off for this browser), a reminder that
 * fires while mailr is open shows as a toast: Open for the sheet, Ask for the ask capture. It handles
 * nothing; the reminder stays in the tray. More than three at once: one toast.
 * ACT agent.ask_about @web.notification
 */
/** The in-app toast showing each reminder key, so a repeat replaces it (ACT reminder.repeat). */
const toastOf = new Map<string, number>()

function inAppToasts(live: ReminderPayload[], prefs: ReturnType<typeof useReminderPrefs>) {
  if (!live.length) return
  if (live.length > 3) {
    toast({
      message: tr('reminders.reminderHost.reminders', { count: live.length }), duration: 15_000, actionLabel: tr('common.review'),
      onAction: () => useReminderUI.getState().set({ triage: live.map((p) => p.key) }),
    })
    return
  }
  for (const p of live) {
    const item = itemFromPayload(p)
    const when = whenLine(item, new Date(), prefs)
    const earlier = toastOf.get(p.key)
    if (earlier !== undefined) useUI.getState().dismissToast(earlier)
    toastOf.set(p.key, toast({
      message: `${item.title}${when ? ` · ${when}` : ''}`, duration: 15_000,
      actionLabel: tr('common.open'), onAction: () => useReminderUI.getState().set({ sheet: p.key, sheetMode: null }),
      secondaryLabel: tr('reminders.reminderHost.ask'), onSecondary: () => openAsk(reminderAbout(item)),
    }))
  }
}

export function ReminderHost() {
  const qc = useQueryClient()
  const nav = useNavigate()
  const location = useLocation()
  const active = useActiveReminders()
  const settings = useReminderSettings()
  const prefs = useReminderPrefs()
  const actions = useReminderActions()
  const sheetKey = useReminderUI((s) => s.sheet)
  const sheetMode = useReminderUI((s) => s.sheetMode)
  const recent = useRef(new Map<string, number>())
  const seen = useRef(new Set<string>())
  const burst = useRef<ReminderPayload[]>([])
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const ctx = useRef({ prefs, desktop: true, items: [] as ReminderItem[], actions })
  ctx.current = { prefs, desktop: settings.data?.web?.desktop !== false, items: active.data ?? [], actions }

  // A click on a notification opens the sheet (one key) or the triage queue (null).
  useEffect(() => {
    const open = (key: string | null, expand?: string) => {
      if (key) useReminderUI.getState().set({ sheet: key, sheetMode: expand === 'before' ? 'offset' : null })
      else {
        const keys = stackOrder(ctx.current.items).map((r) => r.key)
        if (keys.length) useReminderUI.getState().set({ triage: keys })
        else void qc.invalidateQueries({ queryKey: qk.reminders.active }).then(() => {
          const ks = stackOrder(qc.getQueryData<ReminderItem[]>(qk.reminders.active) ?? []).map((r) => r.key)
          if (ks.length) useReminderUI.getState().set({ triage: ks })
        })
      }
    }
    setOpener(open)
    const onWorker = (e: MessageEvent) => {
      const d = (e.data ?? {}) as { type?: string; key?: string; action?: string; expand?: string }
      if (d.type === 'mailr:open-reminder') open(d.key ?? null, d.expand)
      else if (d.type === 'mailr:open-triage') open(null)
      else if (d.type === 'mailr:reminder-acted') void qc.invalidateQueries({ queryKey: qk.reminders.all })
      else if (d.type === 'mailr:reminder-act' && d.key) {
        // A button on a notification this tab showed (no token): act through the session.
        const item = ctx.current.items.find((r) => r.key === d.key) ?? itemFromPayload({ key: d.key })
        const a = ctx.current.actions
        if (d.action === 'before') open(d.key, 'before')
        else if (d.action === 'done') void a.done(item, true)
        else if (d.action === 'snooze') void a.snooze(item, new Date(Date.now() + (item.kind === 'event' ? 5 : 60) * 60_000), true)
        else void a.dismiss(item, true)
      }
    }
    navigator.serviceWorker?.addEventListener?.('message', onWorker)
    return () => {
      setOpener(() => {})
      navigator.serviceWorker?.removeEventListener?.('message', onWorker)
    }
  }, [qc])

  // Fired reminders become notifications; a burst of more than three becomes one summary.
  useEffect(() => {
    const flush = async () => {
      timer.current = null
      const batch = burst.current
      burst.current = []
      if (!batch.length) return
      if (permission() !== 'granted' || !ctx.current.desktop) {
        inAppToasts(batch.filter((p) => !p.missed), ctx.current.prefs)
        return
      }
      const missed = batch.filter((p) => p.missed)
      const live = batch.filter((p) => !p.missed)
      if (missed.length && missedNoticeOn()) await showMissed(missed.length)
      if (!live.length) return
      const showing = (await shownTags()).filter(isReminderTag)
      const plan = planBurst(showing, live.map((p) => p.key))
      for (const p of live) recent.current.set(p.key, Date.now())
      if (plan.summary) {
        const keys = [...new Set([...showing, ...live.map((p) => p.key)])]
        await showSummary(keys.map(kindOfKey), keys)
      } else for (const p of live) await showReminder(p, ctx.current.prefs, { joinUrl: await joinLink(p) })
    }
    const onReminder = (e: Event) => {
      const p = (e as CustomEvent<ReminderPayload>).detail
      if (!p?.key) return
      burst.current.push(p)
      if (!timer.current) timer.current = setTimeout(() => void flush(), 1200)
    }
    window.addEventListener('mailr:reminder', onReminder)
    return () => {
      window.removeEventListener('mailr:reminder', onReminder)
      if (timer.current) clearTimeout(timer.current)
    }
  }, [])

  // Handled anywhere, gone everywhere: a notification whose reminder left the tray closes.
  const firstLoad = useRef(true)
  useEffect(() => {
    if (!active.data) return
    const items = active.data
    for (const r of items) seen.current.add(r.key)
    void shownTags().then((tags) => {
      // A reminder that was in the tray and left it was handled somewhere. One that never reached
      // the tray yet may have just fired (the list is still loading), unless it is old.
      const cutoff = Date.now() - 15_000
      for (const t of staleTags(tags.filter(isReminderTag), items)) if (seen.current.has(t) || (recent.current.get(t) ?? 0) < cutoff) void closeTag(t)
    })
    if (firstLoad.current) {
      firstLoad.current = false
      const announced = readAnnounced()
      const fresh = newMissed(items, announced)
      if (fresh.length && permission() === 'granted' && ctx.current.desktop && missedNoticeOn()) void showMissed(fresh.length)
      for (const r of fresh) announced.add(r.key)
      if (fresh.length) writeAnnounced(announced)
    }
  }, [active.data])

  // ?reminder=<key> and ?reminders=review: a notification opened this tab.
  useEffect(() => {
    const sp = new URLSearchParams(location.search)
    const key = sp.get(SHEET_PARAM)
    const review = sp.get(TRIAGE_PARAM)
    const expand = sp.get(EXPAND_PARAM)
    if (!key && !review) return
    sp.delete(SHEET_PARAM)
    sp.delete(TRIAGE_PARAM)
    sp.delete(EXPAND_PARAM)
    const rest = sp.toString()
    nav(`${location.pathname}${rest ? `?${rest}` : ''}`, { replace: true, state: location.state })
    if (key) useReminderUI.getState().set({ sheet: key, sheetMode: expand === 'before' ? 'offset' : null })
    else void qc.invalidateQueries({ queryKey: qk.reminders.active }).then(() => {
      const keys = stackOrder(qc.getQueryData<ReminderItem[]>(qk.reminders.active) ?? []).map((r) => r.key)
      if (keys.length) useReminderUI.getState().set({ triage: keys })
    })
  }, [location.search, location.pathname, location.state, nav, qc])

  if (!sheetKey) return null
  return createPortal(<SheetDialog k={sheetKey} mode={sheetMode ?? null} items={active.data} loading={active.isLoading} />, document.body)
}

function SheetDialog({ k, mode, items, loading }: { k: string; mode: 'offset' | null; items: ReminderItem[] | undefined; loading: boolean }) {
  const actions = useReminderActions()
  const close = () => useReminderUI.getState().set({ sheet: null, sheetMode: null })
  const item = items?.find((r) => r.key === k)
  useEffect(() => {
    if (!loading && items && !item) {
      close()
      toast({ message: tr('reminders.reminderHost.thatReminderWasAlreadyHandled') })
    }
  }, [loading, items, item])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        close()
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [])
  if (!item) return null
  return (
    <div className="scrim rm-triage-scrim" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="rm-triage rm-single" role="dialog" aria-modal="true" aria-label={item.title}>
        <ReminderSheet key={`${item.key}|${mode ?? ''}`} item={item} actions={actions} onDone={close} onOpen={close} initialMode={mode} />
      </div>
    </div>
  )
}
