// Browser notifications for reminders. A notification's tag is the
// reminder key, so a reminder shown twice (two tabs, or SSE and Web Push) replaces itself. With a
// service worker the notification carries buttons; the worker sends a click or a button back to
// this tab (onWorkerMessage), and acts through POST /reminders/act when the push carried a token.
// Without one (the dev server) a plain Notification is used, without buttons.

import { api } from '../../api/client'
import {
  anchorOf, clickUrl, GENERIC_TITLE, kindOfKey, MISSED_TAG, notificationButtons, summaryTitle, SUMMARY_TAG, whenLine, type ReminderKind, type ReminderPayload,
} from './logic'
import { t as tr } from '../../i18n'

const ICON = '/icon-192.png'
const BADGE = '/icon-64.png'

/** Notifications this tab made without a service worker, by tag. */
const plain = new Map<string, Notification>()

export function notificationsSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window
}

export function permission(): NotificationPermission {
  return notificationsSupported() ? Notification.permission : 'denied'
}

export function pushSupported(): boolean {
  return typeof navigator !== 'undefined' && 'serviceWorker' in navigator && typeof window !== 'undefined' && 'PushManager' in window && window.isSecureContext
}

/** The service worker that shows notifications, if this page has one. */
export async function registration(): Promise<ServiceWorkerRegistration | null> {
  try {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return null
    return (await navigator.serviceWorker.getRegistration()) ?? null
  } catch {
    return null
  }
}

/** Registers /sw.js when nothing is registered yet (production registers it at start). */
export async function ensureRegistration(): Promise<ServiceWorkerRegistration | null> {
  const have = await registration()
  if (have) return have
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return null
  try {
    await navigator.serviceWorker.register('/sw.js')
    return await navigator.serviceWorker.ready
  } catch {
    return null
  }
}

type Opener = (key: string | null) => void
let opener: Opener = () => {}

/** What a click on a notification does in this tab: open the sheet (a key) or the triage queue (null).
 * ACT notification.open @web.notification */
export function setOpener(fn: Opener) {
  opener = fn
}

function chime() {
  try {
    if (localStorage.getItem('mailr.reminders.sound') !== '1') return
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctx) return
    const ctx = new Ctx()
    const o = ctx.createOscillator()
    const g = ctx.createGain()
    o.frequency.value = 880
    g.gain.setValueAtTime(0.0001, ctx.currentTime)
    g.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + 0.02)
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.6)
    o.connect(g).connect(ctx.destination)
    o.start()
    o.stop(ctx.currentTime + 0.65)
  } catch {
    /* no audio */
  }
}

interface ShowArgs {
  tag: string
  title: string
  body: string
  data: Record<string, unknown>
  buttons?: { action: string; title: string }[]
  silent?: boolean
  sticky?: boolean
}

async function show(a: ShowArgs): Promise<void> {
  if (permission() !== 'granted') return
  const reg = await registration()
  const base: NotificationOptions & { renotify?: boolean; actions?: { action: string; title: string }[] } = {
    body: a.body, tag: a.tag, icon: ICON, badge: BADGE, data: a.data, silent: !!a.silent, requireInteraction: !!a.sticky,
  }
  if (!a.silent) chime()
  if (reg) {
    try {
      await reg.showNotification(a.title, { ...base, renotify: !a.silent, actions: a.buttons ?? [] } as NotificationOptions)
      return
    } catch {
      // Firefox rejects with NS_ERROR_FAILURE when the desktop's notification service refuses
      // (Do Not Disturb, or notifications blocked for the browser); a plain notification may
      // still go through, else the caller reports it.
    }
  }
  try {
    plain.get(a.tag)?.close()
    const n = new Notification(a.title, base)
    plain.set(a.tag, n)
    n.onclick = () => {
      window.focus()
      opener(typeof a.data.key === 'string' ? a.data.key : null)
    }
    n.onclose = () => {
      if (plain.get(a.tag) === n) plain.delete(a.tag)
    }
  } catch (e) {
    /* some browsers allow notifications only from a service worker */
    if (a.data.test) throw e
  }
}

/** The tags this tab knows to be showing. */
export async function shownTags(): Promise<string[]> {
  const reg = await registration()
  const tags = new Set(plain.keys())
  if (reg) {
    try {
      for (const n of await reg.getNotifications()) if (n.tag) tags.add(n.tag)
    } catch {
      /* ignore */
    }
  }
  return [...tags]
}

/** Closes the notification for a reminder key (handled here or elsewhere). ACT reminder.sync @web.notification */
export async function closeTag(tag: string): Promise<void> {
  plain.get(tag)?.close()
  plain.delete(tag)
  const reg = await registration()
  if (!reg) return
  try {
    for (const n of await reg.getNotifications({ tag })) n.close()
  } catch {
    /* ignore */
  }
}

/** One reminder as a notification. A repeat (p.again) has the same tag, so it replaces the first and
 * alerts again (renotify). ACT reminder.fire @web.notification, ACT reminder.repeat @web.notification */
export async function showReminder(p: ReminderPayload, prefs: { zone: string; hour12: boolean }, opts: { silent?: boolean; joinUrl?: string | null } = {}) {
  const kind: ReminderKind = p.item?.kind ?? kindOfKey(p.key)
  const now = new Date()
  const start = anchorOf(p.start, prefs.zone)
  const line = whenLine({ kind, start: p.start ?? null }, now, prefs)
  const title = p.missed && !(p.title ?? '').startsWith('Missed') ? tr('reminders.notify.missed', { title: p.title ?? '' }) : p.title ?? GENERIC_TITLE[kind]
  const body = [line, p.body].filter(Boolean).join('\n')
  await show({
    tag: p.key, title, body, silent: opts.silent || !!p.missed, sticky: !p.missed,
    buttons: notificationButtons(kind, start, now, opts.joinUrl, p.prep),
    data: { key: p.key, kind, click: p.click ?? null, url: clickUrl(p.click, p.key), tokens: p.tokens ?? null, join: opts.joinUrl ?? null, prep: p.prep ?? null },
  })
}

/** "4 reminders": one summary in place of the single notifications. ACT notification.open_stack @web.notification */
export async function showSummary(kinds: ReminderKind[], keys: string[]) {
  for (const k of keys) await closeTag(k)
  await show({ tag: SUMMARY_TAG, title: summaryTitle(kinds), body: tr('reminders.notify.reviewThemOneByOne'), data: { summary: true, url: clickUrl(undefined, null) }, sticky: true })
}

/** The quiet "3 missed reminders" notice on load (missed reminders never arrive as a burst). */
export async function showMissed(n: number) {
  await show({ tag: MISSED_TAG, title: tr('reminders.notify.missedReminders', { count: n }), body: tr('reminders.notify.theyAreInTheReminder'), data: { summary: true, url: clickUrl(undefined, null) }, silent: true })
}

/** A sample notification from Settings → Notifications. */
/** The test notification, checked: rejects when the browser accepted it but nothing was shown
 *  (Firefox loses its link to the desktop's notification service and drops them silently). */
export async function showTest() {
  const title = tr('reminders.notify.remindersWorkOnThisComputer')
  const body = tr('reminders.notify.startsIn10MinRoom')
  const reg = await registration()
  if (reg) {
    try {
      await reg.showNotification(title, { body, tag: 'mailr-test', icon: ICON, badge: BADGE, data: { test: true } } as NotificationOptions)
      await new Promise((r) => setTimeout(r, 400))
      if ((await reg.getNotifications({ tag: 'mailr-test' })).length > 0) return
    } catch {
      /* try a plain notification below */
    }
  }
  await new Promise<void>((resolve, reject) => {
    let n: Notification
    try {
      n = new Notification(title, { body, tag: 'mailr-test', icon: ICON })
    } catch (e) {
      reject(e)
      return
    }
    const timer = setTimeout(() => reject(new Error(tr('reminders.notify.notShown'))), 3000)
    n.onshow = () => { clearTimeout(timer); resolve() }
    n.onerror = () => { clearTimeout(timer); reject(new Error('refused')) }
  })
}

// ---------- Web Push (notification.subscribe) ----------

function keyBytes(b64: string): Uint8Array<ArrayBuffer> {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4)
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'))
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

function deviceName(): string {
  const ua = navigator.userAgent
  const browser = /Firefox\//.test(ua) ? tr('reminders.notify.firefox') : /Edg\//.test(ua) ? tr('reminders.notify.edge') : /Chrome\//.test(ua) ? tr('reminders.notify.chrome') : /Safari\//.test(ua) ? tr('reminders.notify.safari') : tr('reminders.notify.browser')
  const os = /Windows/.test(ua) ? tr('reminders.notify.windows') : /Mac OS X/.test(ua) ? 'macOS' : /Android/.test(ua) ? tr('common.android') : /Linux/.test(ua) ? tr('reminders.notify.linux') : ''
  return os ? tr('reminders.notify.on', { browser, os }) : browser
}

/** Asks for permission (call from a click) and subscribes this browser to Web Push for these
 *  events (core/push.py EVENTS; reminders by default). ACT notification.subscribe @web.settings @web.setup */
export async function subscribePush(events: string[] = ['reminder']): Promise<{ ok: boolean; reason?: string }> {
  if (!notificationsSupported()) return { ok: false, reason: tr('reminders.notify.thisBrowserCannotShowNotifications') }
  const perm = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission
  if (perm !== 'granted') return { ok: false, reason: tr('reminders.notify.notificationsAreBlockedForMailr') }
  if (!pushSupported()) return { ok: false, reason: tr('reminders.notify.thisBrowserCannotReceivePush') }
  const reg = await ensureRegistration()
  if (!reg) return { ok: false, reason: tr('reminders.notify.theServiceWorkerCouldNot') }
  const { public_key } = await api.get<{ public_key: string }>('/push/webpush/key')
  let sub = await reg.pushManager.getSubscription()
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(public_key) })
  const j = sub.toJSON() as { endpoint?: string; keys?: { p256dh?: string; auth?: string } }
  await api.post('/push/webpush', { endpoint: j.endpoint ?? sub.endpoint, keys: { p256dh: j.keys?.p256dh ?? '', auth: j.keys?.auth ?? '' }, device: deviceName(), events })
  return { ok: true }
}

/** The events this browser's subscription receives, or null when it has none. */
export async function pushEvents(): Promise<string[] | null> {
  const reg = await registration()
  const sub = reg && 'pushManager' in reg ? await reg.pushManager.getSubscription().catch(() => null) : null
  if (!sub) return null
  try {
    const r = await api.get<{ items: { endpoint: string; kind: string; events: string[] }[] }>('/push/targets')
    return r.items.find((t) => t.endpoint === sub.endpoint)?.events ?? []
  } catch {
    return []
  }
}

/** One subscription carries every kind this browser wants (reminders, new mail, approvals …):
 *  change the set, subscribing first when needed (call from a click), or end it when empty. */
export async function setPushEvents(events: string[]): Promise<{ ok: boolean; reason?: string }> {
  const want = [...new Set(events)]
  if (!want.length) {
    await unsubscribePush()
    return { ok: true }
  }
  return subscribePush(want)
}

/** Turns Web Push off for this browser. ACT notification.subscribe @web.settings */
export async function unsubscribePush(): Promise<void> {
  const reg = await registration()
  const sub = reg ? await reg.pushManager.getSubscription().catch(() => null) : null
  if (!sub) return
  await api.del(`/push/webpush?endpoint=${encodeURIComponent(sub.endpoint)}`).catch(() => undefined)
  await sub.unsubscribe().catch(() => undefined)
}
