import { assistantNameNow } from '../rail/assistant/useAssistantName'
// The reminders in the "Waiting for you" bell: fired and missed
// reminders (GET /reminders/active), missed ones quietly under "Missed"; a row opens the
// quick-action sheet in place, "Review all" opens the one-by-one triage queue, and "Clear all" dismisses them all with one Undo. A one-time
// banner offers "Get reminders on this computer" (permission is asked only from that click or from
// Settings → Notifications).
// ACT notification.open @web.notification, ACT notification.open_stack @web.notification @web.triage, ACT notification.subscribe @web.notification
// Clear all in the section's header dismisses every reminder it lists (useClearAllReminders, reminder.dismiss_all).

import { ArrowLeft, BellRing, Bot, CalendarDays, CircleCheck, StickyNote, X } from 'lucide-react'
import { useState } from 'react'
import { toast } from '../../stores/ui'
import { useClearAllReminders, useReminderActions, useReminderUI, useSaveReminderSettings } from './api'
import { whenLine, type ReminderItem } from './logic'
import { notificationsSupported, permission, pushEvents, pushSupported, subscribePush } from './notify'
import { ReminderSheet, useReminderPrefs } from './ReminderSheet'
import { openAsk, reminderAbout } from '../ask/askStore'
import '../ask/ask.css'
import './reminders.css'
import { t } from '../../i18n'

const BANNER_KEY = 'mailr.reminders.banner'

function bannerDismissed(): boolean {
  try {
    return localStorage.getItem(BANNER_KEY) === 'done'
  } catch {
    return true
  }
}

/** One reminder's sheet, opened in place of the bell's list. */
export function ReminderSheetInPlace({ item, onBack, onLeave }: { item: ReminderItem; onBack: () => void; onLeave: () => void }) {
  const actions = useReminderActions()
  return (
    <div className="rm-tray-sheet">
      <button type="button" className="btn text sm rm-back" onClick={onBack}>
        <ArrowLeft size={16} />{' '}{t('reminders.remindersSection.everythingWaiting')}
      </button>
      <ReminderSheet item={item} actions={actions} onDone={onBack} onOpen={onLeave} />
    </div>
  )
}

/** "Get reminders on this computer": once, until turned on or dismissed. */
export function ReminderBanner() {
  const [banner, setBanner] = useState(() => notificationsSupported() && permission() === 'default' && !bannerDismissed())
  const save = useSaveReminderSettings()
  if (!banner) return null
  const hideBanner = () => {
    try {
      localStorage.setItem(BANNER_KEY, 'done')
    } catch {
      /* private mode */
    }
    setBanner(false)
  }
  const enable = async () => {
    // ACT notification.subscribe @web.notification (the one-time banner in the bell; this click is the user gesture)
    const perm = await Notification.requestPermission()
    hideBanner()
    if (perm !== 'granted') {
      toast({ message: t('reminders.remindersSection.notificationsStayOffYouCan') })
      return
    }
    save.mutate({ web: { desktop: true } })
    if (pushSupported()) {
      // Keep what the subscription already carries (new mail, approvals) and add reminders.
      const had = (await pushEvents().catch(() => null)) ?? []
      const r = await subscribePush([...new Set([...had, 'reminder'])]).catch((e: Error) => ({ ok: false, reason: e.message }))
      if (r.ok) save.mutate({ web: { push: true } })
    }
    toast({ message: t('reminders.remindersSection.remindersWillShowOnThis') })
  }
  return (
    <div className="rm-banner" role="region" aria-label={t('reminders.remindersSection.getRemindersOnThisComputer')}>
      <BellRing size={18} aria-hidden />
      <span>{t('reminders.remindersSection.getRemindersOnThisComputer2')}</span>
      <button type="button" className="btn filled sm" onClick={() => void enable()}>{t('reminders.remindersSection.turnOn')}</button>
      <button type="button" className="icon-btn xs" aria-label={t('common.notNow')} onClick={hideBanner}><X size={14} /></button>
    </div>
  )
}

/** The Reminders section: "Now", then "Missed". Nothing when no reminder is waiting. */
export function RemindersSection({ items, onOpen, onLeave }: { items: ReminderItem[]; onOpen: (key: string) => void; onLeave: () => void }) {
  const prefs = useReminderPrefs()
  const clearAll = useClearAllReminders()
  if (!items.length) return null
  const now = new Date()
  const missed = items.filter((r) => r.missed)
  const fired = items.filter((r) => !r.missed)
  const title = fired.length ? t('common.reminders') : t('reminders.remindersSection.missedReminders')
  return (
    <section className="wb-section wb-reminders" role="region" aria-label={title}>
      <header className="wb-section-head">
        <h3>{title}</h3>
        <span className="spacer" />
        {items.length > 1 && (
          <button type="button" className="btn tonal sm" onClick={() => { onLeave(); useReminderUI.getState().set({ triage: items.map((r) => r.key) }) }}>
            {t('reminders.remindersSection.reviewAll')}
          </button>
        )}
        <button type="button" className="btn text sm" data-testid="reminder-clear-all"
          aria-label={t('reminders.remindersSection.clearAllReminders', { count: items.length })}
          data-tip={t('reminders.remindersSection.dismissEveryReminderInThis')} onClick={() => void clearAll(items)}>
          {t('reminders.remindersSection.clearAll')}
        </button>
      </header>
      <div className="rm-list" role="list">
        {fired.map((r) => <Row key={r.key} r={r} now={now} prefs={prefs} onOpen={() => onOpen(r.key)} onLeave={onLeave} />)}
        {missed.length > 0 && fired.length > 0 && <div className="rm-section">{t('reminders.remindersSection.missed')}</div>}
        {missed.map((r) => <Row key={r.key} r={r} now={now} prefs={prefs} onOpen={() => onOpen(r.key)} onLeave={onLeave} />)}
      </div>
    </section>
  )
}

function Row({ r, now, prefs, onOpen, onLeave }: { r: ReminderItem; now: Date; prefs: ReturnType<typeof useReminderPrefs>; onOpen: () => void; onLeave: () => void }) {
  return (
    <div role="listitem" className="rm-row-wrap">
      <button type="button" className={`rm-row${r.missed ? ' missed' : ''}`} onClick={onOpen} aria-label={`${r.title}. ${whenLine(r, now, prefs)}`} data-testid="reminder-row">
        <span className={`rm-kind ${r.kind}`} aria-hidden>{r.kind === 'task' ? <CircleCheck size={18} /> : r.kind === 'note' ? <StickyNote size={18} /> : <CalendarDays size={18} />}</span>
        <span className="rm-row-text">
          <span className="rm-row-title" dir="auto">{r.title}</span>
          <span className="rm-row-when">{whenLine(r, now, prefs)}{r.state === 'snoozed' ? ' · snoozed' : ''}</span>
        </span>
      </button>
      {/* ACT agent.ask_about @web.triage: Ask from the bell's row; the reminder stays in the bell. */}
      <button type="button" className="icon-btn sm rm-row-ask" aria-label={t('reminders.remindersSection.askAbout', { name: assistantNameNow(), title: r.title })} data-tip={t('reminders.remindersSection.ask', { name: assistantNameNow() })} data-testid="reminder-row-ask"
        onClick={(e) => {
          openAsk(reminderAbout(r), e.currentTarget)
          onLeave()
        }}>
        <Bot size={16} />
      </button>
    </div>
  )
}
