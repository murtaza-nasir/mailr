// mailr service worker.
//
// - App shell: the build's files are precached under one version (the build writes VERSION and
//   PRECACHE below; see vite.config.ts). A new build installs beside the running one and waits:
//   it takes over only when the user picks Reload in the "New version" banner (SKIP_WAITING).
// - Mail for offline reading: recent list pages, opened conversations and their bodies, and the
//   few answers the shell needs to start (session, labels, counts, layouts) are kept in a cache
//   (and the Notes grid and opened lists, so a list works in a shop with no signal)
//   per signed-in user, network first (the server's answer wins when it comes in time), with
//   limits per kind. Signing out, or a session check that says signed out, deletes them.
// - Notifications: reminders (Web Push with signed action tokens), new mail, approvals, failed
//   sends and assistant messages; the app badge shows the Inbox's unread count. An approval's
//   notification closes when the approval is decided anywhere (the approval.decided push).
// (features/reminders/logic.ts holds the reminder rules.)
const VERSION = 'dev'
const PRECACHE = []
const SHELL = `mailr-shell-${VERSION}`
const DATA = 'mailr-data-'
const META = 'mailr-meta'
const SUMMARY_TAG = 'mailr-reminders'
const MISSED_TAG = 'mailr-reminders-missed'
const STACK_LIMIT = 3
const ICON = '/icon-192.png'
const BADGE = '/icon-64.png'

// ---------- install, update, activate ----------

self.addEventListener('install', (event) => {
  // index.html and the icons, then every hashed file of this build. One missing file must not
  // fail the whole install (a deploy may race it): those load on demand.
  event.waitUntil(
    caches.open(SHELL).then(async (c) => {
      await c.addAll(['/index.html', '/manifest.webmanifest', '/icon-192.png', '/icon-64.png'])
      await Promise.all(PRECACHE.map((u) => c.add(u).catch(() => undefined)))
    }),
  )
  // No skipWaiting here: a waiting build activates when the user agrees (message SKIP_WAITING).
  // The very first install has nothing to wait for and activates at once.
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('mailr-shell-') && k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('message', (event) => {
  const m = event.data || {}
  if (m.type === 'SKIP_WAITING') self.skipWaiting()
  else if (m.type === 'mailr:signout') event.waitUntil(forgetUser())
  // The page's session names the user (the first page load passed before this worker took over).
  else if (m.type === 'mailr:user' && m.id != null) event.waitUntil(setUser(m.id))
  // The page knows of a newer build but no new worker came: the next start fetches the page.
  else if (m.type === 'mailr:refresh-shell') event.waitUntil(caches.open(SHELL).then((c) => c.delete('/index.html')))
  else if (m.type === 'mailr:badge') event.waitUntil(setBadge(m.count))
  else if (m.type === 'mailr:close-conversations') event.waitUntil(closeConversations(m.ids || []))
  else if (m.type === 'mailr:version' && event.source) event.source.postMessage({ type: 'mailr:version', version: VERSION })
})

// ---------- per-user data cache ----------

let user = null // the signed-in user's id, from the session answer
async function currentUser() {
  if (user !== null) return user
  const hit = await caches.open(META).then((c) => c.match('/__mailr/user')).catch(() => null)
  user = hit ? (await hit.text()) || null : null
  return user
}
async function setUser(id) {
  const next = id == null ? null : String(id)
  if (next === (await currentUser())) return
  // Another account on this device never sees the last one's mail.
  await dropData()
  user = next
  const meta = await caches.open(META)
  if (next) await meta.put('/__mailr/user', new Response(next))
  else await meta.delete('/__mailr/user')
}
async function dropData() {
  const keys = await caches.keys()
  await Promise.all(keys.filter((k) => k.startsWith(DATA)).map((k) => caches.delete(k)))
}
async function forgetUser() {
  user = null
  await dropData()
  await caches.open(META).then((c) => c.delete('/__mailr/user'))
  await setBadge(0)
}

// What is kept, and how many of each (the oldest written goes first).
const KINDS = [
  { kind: 'session', re: /^\/api\/v1\/auth\/session$/, max: 1 },
  { kind: 'boot', re: /^\/api\/v1\/(labels|counts|layouts|layouts\/[^/]+\/counts|pictures\/local)$/, max: 12 },
  { kind: 'lists', re: /^\/api\/v1\/conversations$/, max: 40 },
  { kind: 'convs', re: /^\/api\/v1\/conversations\/[^/]+$/, max: 150 },
  { kind: 'bodies', re: /^\/api\/v1\/messages\/[^/]+\/body$/, max: 300 },
  // Lists for the shop with no signal: the Notes grid and the notes and lists opened.
  { kind: 'notes', re: /^\/api\/v1\/notes$/, max: 12 },
  { kind: 'note', re: /^\/api\/v1\/notes\/n[0-9A-Za-z_-]{4,}$/, max: 80 },
]
function kindOf(url) {
  return KINDS.find((k) => k.re.test(url.pathname)) || null
}
async function remember(k, req, res) {
  const u = await currentUser()
  if (!u || !res.ok || !(res.headers.get('content-type') || '').includes('application/json')) return
  const cache = await caches.open(`${DATA}${u}-${k.kind}`)
  await cache.put(req, res)
  const keys = await cache.keys()
  if (keys.length > k.max) await Promise.all(keys.slice(0, keys.length - k.max).map((r) => cache.delete(r)))
}
async function recall(k, req) {
  const u = await currentUser()
  if (!u) return null
  const cache = await caches.open(`${DATA}${u}-${k.kind}`)
  const hit = await cache.match(req, { ignoreVary: true })
  if (!hit) return null
  // Say it came from this device's cache.
  const headers = new Headers(hit.headers)
  headers.set('X-Mailr-Cached', '1')
  return new Response(hit.body, { status: hit.status, statusText: hit.statusText, headers })
}

/** Network first. The cache answers when the network fails, or when it is slow and a copy is
 *  here; the network's answer still refreshes the cache (stale-while-revalidate on a slow line).
 *  `X-Mailr-Cache: only` asks for the copy alone (the app's instant start, lib/offlineCache.ts). */
async function dataFetch(event, k) {
  const req = event.request
  if (req.headers.get('X-Mailr-Cache') === 'only') return (await recall(k, req)) || new Response('', { status: 504 })
  const network = fetch(req).then(async (res) => {
    if (res.status === 401) {
      await forgetUser()
      return res
    }
    if (k.kind === 'session' && res.ok) {
      const body = await res.clone().json().catch(() => null)
      if (body && body.authenticated === false) {
        await forgetUser()
        return res
      }
      if (body && body.user && body.user.id != null) await setUser(body.user.id)
    }
    await remember(k, req, res.clone()).catch(() => undefined)
    return res
  })
  event.waitUntil(network.catch(() => undefined))
  const slow = new Promise((resolve) => setTimeout(() => resolve('slow'), k.kind === 'bodies' || k.kind === 'convs' ? 6000 : 4000))
  try {
    const first = await Promise.race([network, slow])
    if (first !== 'slow') return first
    const copy = await recall(k, req)
    return copy || (await network)
  } catch (e) {
    const copy = await recall(k, req)
    if (copy) return copy
    throw e
  }
}

// ---------- fetch ----------

self.addEventListener('fetch', (event) => {
  const req = event.request
  const url = new URL(req.url)
  if (req.method !== 'GET' || url.origin !== self.location.origin) return
  // The help pages (/help/) are the server's, never the app's shell, and are not kept offline.
  if (url.pathname === '/help' || url.pathname.startsWith('/help/')) return
  if (url.pathname.startsWith('/api/')) {
    const k = kindOf(url)
    // The event stream, attachments, images and everything else go straight to the network.
    if (k && req.headers.get('X-Mailr-No-Cache') !== '1') event.respondWith(dataFetch(event, k))
    return
  }
  if (req.mode === 'navigate') {
    event.respondWith(navigate(req))
    return
  }
  if (url.pathname.startsWith('/assets/')) {
    // Hashed build files never change: cache first. (Vary is ignored: a module script's request
    // carries Origin, the precache's did not, and some servers answer with Vary: Origin.)
    event.respondWith(
      caches.match(req, { ignoreVary: true }).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) {
              const copy = res.clone()
              caches.open(SHELL).then((c) => c.put(req, copy))
            }
            return res
          }),
      ),
    )
    return
  }
  // Icons, splash screens and the manifest: the cached copy now, a fresh one for next time.
  if (/\.(png|ico|svg|webmanifest)$/.test(url.pathname)) {
    event.respondWith(
      caches.open(SHELL).then(async (c) => {
        const hit = await c.match(req, { ignoreVary: true })
        const fresh = fetch(req).then((res) => {
          if (res.ok) c.put(req, res.clone())
          return res
        })
        if (hit) {
          event.waitUntil(fresh.catch(() => undefined))
          return hit
        }
        return fresh
      }),
    )
  }
})

// Old mail links with internal ids (/mail/<view>/<digits>) are moved by the server.
const SERVER_PATHS = /^\/mail\/[^/]+\/\d+$/

async function navigate(req) {
  const url = new URL(req.url)
  // A development worker has no precache: always the network (vite serves a fresh page).
  if (VERSION === 'dev' || SERVER_PATHS.test(url.pathname)) return fetch(req).catch(() => caches.match('/index.html', { ignoreVary: true }))
  // The installed app starts from the shell this worker installed (all its files are here);
  // the server's page is fetched on a miss.
  const shell = await caches.open(SHELL).then((c) => c.match('/index.html', { ignoreVary: true }))
  if (shell) return shell
  return fetch(req).catch(() => caches.match('/index.html', { ignoreVary: true }))
}

// ---------- badge ----------

async function setBadge(count) {
  try {
    if (!('setAppBadge' in self.navigator)) return
    if (count && count > 0) await self.navigator.setAppBadge(count)
    else await self.navigator.clearAppBadge()
  } catch {
    /* badges are best effort */
  }
}

/** The Inbox's unread count, as the sidebar shows it (GET /counts), into the app badge. */
async function refreshBadge() {
  try {
    const res = await fetch('/api/v1/counts', { credentials: 'same-origin', headers: { Accept: 'application/json', 'X-Mailr-No-Cache': '1' } })
    if (!res.ok) return
    const c = await res.json()
    const n = c && c.views && c.views.inbox ? c.views.inbox.unread : undefined
    if (typeof n === 'number') await setBadge(n)
  } catch {
    /* offline */
  }
}

// ---------- mail, approval and assistant notifications ----------

function pathOf(click, fallback) {
  try {
    if (!click) return fallback
    const u = new URL(click, self.location.origin)
    // Only this app's own pages open from a notification.
    return u.origin === self.location.origin ? u.pathname + u.search + u.hash : fallback
  } catch {
    return fallback
  }
}

function canAct() {
  try {
    return 'actions' in Notification.prototype && (Notification.maxActions || 0) > 0
  } catch {
    return false
  }
}

async function showMail(d) {
  const type = d.type || 'mailr'
  const cid = d.conversation_id
  const tag = cid ? `c${cid}` : d.pending_id ? `a${d.pending_id}` : d.outbox_id ? `o${d.outbox_id}` : `mailr-${type}`
  const mail = (type === 'new_mail' || type === 'important_mail') && !!cid
  const url = pathOf(d.click, d.pending_id ? `/approvals/${d.pending_id}` : '/mail/inbox')
  const opts = {
    body: d.body || '', tag, icon: ICON, badge: BADGE, renotify: type !== 'test',
    data: { mail, conversation_id: cid || null, url, type },
  }
  // Archive and Mark read on the notification where the platform shows buttons (Chrome on
  // Android and desktop); elsewhere (iOS) a tap opens the conversation.
  if (mail && canAct()) opts.actions = [{ action: 'archive', title: 'Archive' }, { action: 'read', title: 'Mark read' }]
  await self.registration.showNotification(d.title || 'mailr', opts)
  if (mail) await refreshBadge()
}

/** ACT approval.sync @web.notification: close the notifications of approvals that left pending
 *  (decided on any device, expired or withdrawn). The tag is the one showMail gives them. */
async function closeApprovals(ids) {
  if (!ids.length) return
  const want = new Set(ids.map((x) => `a${x}`))
  for (const n of await self.registration.getNotifications()) if (want.has(n.tag)) n.close()
}

/** Close the notifications of conversations read or archived elsewhere. */
async function closeConversations(ids) {
  if (!ids.length) return
  const want = new Set(ids.map((x) => `c${x}`))
  for (const n of await self.registration.getNotifications()) if (want.has(n.tag)) n.close()
}

/** ACT mail.archive @web.notification, ACT mail.read @web.notification from a notification's button, with the session's cookie. */
async function actOnMail(n, action) {
  const d = n.data || {}
  n.close()
  try {
    const s = await fetch('/api/v1/auth/session', { credentials: 'same-origin', headers: { Accept: 'application/json', 'X-Mailr-No-Cache': '1' } })
    const me = s.ok ? await s.json() : null
    if (!me || !me.authenticated || !me.csrf) throw new Error('signed out')
    const res = await fetch('/api/v1/conversations/actions', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRF-Token': me.csrf, 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ ids: [d.conversation_id], action }),
    })
    if (!res.ok) throw new Error(String(res.status))
    for (const c of await windows()) c.postMessage({ type: 'mailr:mail-changed' })
    await refreshBadge()
  } catch {
    // Signed out or offline: open the conversation instead, where the action can be taken.
    await focusOrOpen(d.url, { type: 'mailr:navigate', url: d.url })
  }
}

// ---------- reminders (Web Push) ----------

function withParam(path, name, value) {
  const base = path && path.startsWith('/') ? path : '/mail/inbox'
  return `${base}${base.includes('?') ? '&' : '?'}${name}=${encodeURIComponent(value)}`
}

/**
 * Task: Done, Snooze 1 h. Event: Snooze 5 min, Remind before…, Dismiss while the start is more than
 * 5 minutes away (a browser with two buttons drops Dismiss); then Snooze 5 min and Dismiss, or Join
 * within 5 min of a start with a link.
 */
function buttons(kind, start, join, prep) {
  if (kind === 'task') return [{ action: 'done', title: 'Done' }, { action: 'snooze', title: 'Snooze 1 h' }]
  // A note reminder (n|<id>|abs|<instant>): nothing to complete or join.
  if (kind === 'note') return [{ action: 'snooze', title: 'Snooze 1 h' }, { action: 'dismiss', title: 'Dismiss' }]
  const t = start ? Date.parse(start) : NaN
  // An event with guests (d.prep) offers Prep second, as logic.ts notificationButtons does.
  if (!Number.isNaN(t) && t - Date.now() > 5 * 60_000)
    return prep
      ? [{ action: 'snooze', title: 'Snooze 5 min' }, { action: 'prep', title: 'Prep' }, { action: 'before', title: 'Remind before…' }]
      : [{ action: 'snooze', title: 'Snooze 5 min' }, { action: 'before', title: 'Remind before…' }, { action: 'dismiss', title: 'Dismiss' }]
  const soon = !Number.isNaN(t) && t - Date.now() <= 5 * 60_000 && Date.now() - t < 60 * 60_000
  if (join && soon) return [{ action: 'snooze', title: 'Snooze 5 min' }, { action: 'join', title: 'Join' }, ...(prep ? [{ action: 'prep', title: 'Prep' }] : [])]
  return [{ action: 'snooze', title: 'Snooze 5 min' }, ...(prep ? [{ action: 'prep', title: 'Prep' }] : []), { action: 'dismiss', title: 'Dismiss' }]
}

function kindOfKey(key) {
  return key.startsWith('t|') ? 'task' : key.startsWith('n|') ? 'note' : 'event'
}

function summaryTitle(kinds) {
  const one = kinds.every((k) => k === kinds[0])
  return `${kinds.length} ${one ? `${kinds[0]} reminder` : 'reminder'}${kinds.length === 1 ? '' : 's'}`
}

async function showPush(d) {
  const key = d.key
  const kind = (d.item && d.item.kind) || kindOfKey(String(key))
  const reg = self.registration
  if (d.missed) {
    // Missed reminders never arrive as a burst: one quiet notice that counts them.
    const prev = await reg.getNotifications({ tag: MISSED_TAG })
    const n = ((prev[0] && prev[0].data && prev[0].data.count) || 0) + 1
    return reg.showNotification(`${n} missed reminder${n === 1 ? '' : 's'}`, {
      tag: MISSED_TAG, body: 'Open mailr to review them.', icon: ICON, badge: BADGE, silent: true,
      data: { summary: true, count: n, url: withParam('/mail/inbox', 'reminders', 'review') },
    })
  }
  const showing = (await reg.getNotifications()).filter((n) => n.tag && n.tag.includes('|'))
  const tags = new Set(showing.map((n) => n.tag))
  tags.add(key)
  if (tags.size > STACK_LIMIT) {
    const kinds = [...tags].map(kindOfKey)
    for (const n of showing) n.close()
    return reg.showNotification(summaryTitle(kinds), {
      tag: SUMMARY_TAG, body: 'Review them one by one in mailr.', icon: ICON, badge: BADGE, requireInteraction: true,
      data: { summary: true, url: withParam('/mail/inbox', 'reminders', 'review') },
    })
  }
  // A repeat (Remind again, d.again) has the same tag: it replaces the notification and, with
  // renotify, alerts again. Missed reminders never repeat (the server sends none). ACT reminder.repeat @web.notification
  // Without notification buttons (iOS) a tap opens the reminder's sheet in mailr instead.
  return reg.showNotification(d.title || ({ task: 'Task reminder', note: 'Note reminder' }[kind] || 'Event reminder'), {
    tag: key, body: d.body || '', icon: ICON, badge: BADGE, requireInteraction: true, renotify: true,
    // ACT event.join @web.notification: the server's push carries the link as join_url.
    actions: buttons(kind, d.start, d.join || d.join_url, d.prep),
    data: { key, kind, tokens: d.tokens || null, join: d.join || d.join_url || null, prep: pathOf(d.prep, null), url: withParam(d.click, 'reminder', key) },
  })
}

self.addEventListener('push', (event) => {
  let d = null
  try {
    d = event.data ? event.data.json() : null
  } catch {
    d = null
  }
  if (d && d.kind === 'reminder' && d.key) {
    event.waitUntil(showPush(d))
    return
  }
  // approval.decided carries no text: it only closes notifications. The server never sends it to
  // a push service that ends silent subscriptions (Safari), so nothing is shown here.
  if (d && d.type === 'approval.decided') {
    event.waitUntil(closeApprovals(Array.isArray(d.ids) ? d.ids : []))
    return
  }
  // Every push shows a notification: Safari (iOS) ends a subscription whose pushes show nothing.
  event.waitUntil(showMail(d || { title: 'mailr', body: 'Open mailr to see what is new.' }))
})

async function windows() {
  return self.clients.matchAll({ type: 'window', includeUncontrolled: true })
}

async function focusOrOpen(url, message) {
  const list = await windows()
  const best = list.find((c) => c.focused) || list.find((c) => c.visibilityState === 'visible') || list[0]
  if (best) {
    await best.focus().catch(() => undefined)
    best.postMessage(message)
    return
  }
  await self.clients.openWindow(url)
}

async function act(n, action) {
  const d = n.data || {}
  if (d.mail && (action === 'archive' || action === 'read')) return actOnMail(n, action)
  if (action === 'join' && d.join) {
    n.close()
    return self.clients.openWindow(d.join)
  }
  if (d.test) return n.close()
  if (action === 'prep' && d.prep) {
    // ACT event.prep @web.notification: Prep opens the event with its meeting prep pack. It handles nothing,
    // so the notification stays until the reminder is snoozed or dismissed.
    const url = pathOf(d.prep, null)
    if (url) return focusOrOpen(url, { type: 'mailr:navigate', url })
  }
  if (action === 'before') {
    // ACT event.remind_before @web.notification: mailr opens on the sheet's offsets. Nothing is handled until one
    // is picked, so the notification stays.
    const url = d.url ? `${d.url}${d.url.includes('?') ? '&' : '?'}expand=before` : withParam(d.click, 'reminder', d.key)
    return focusOrOpen(url, { type: 'mailr:open-reminder', key: d.key, expand: 'before' })
  }
  const token = d.tokens && d.tokens[action]
  if (token) {
    // ACT reminder.dismiss @web.notification, ACT task.snooze @web.notification, ACT event.snooze @web.notification, ACT task.complete @web.notification: the signed token
    // stands in for the session, so the buttons work with every mailr tab closed. Each button
    // has its own token, valid for that action only.
    const res = await fetch('/api/v1/reminders/act', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'omit',
      body: JSON.stringify({ token, action }),
    }).catch(() => null)
    if (res && res.ok) {
      n.close()
      for (const c of await windows()) c.postMessage({ type: 'mailr:reminder-acted', key: d.key })
      return
    }
    // An expired or refused token: open mailr on the reminder instead.
    return focusOrOpen(d.url, { type: 'mailr:open-reminder', key: d.key })
  }
  // A notification an open tab showed (no token): that tab acts through its session.
  const list = await windows()
  if (list.length) {
    n.close()
    list[0].postMessage({ type: 'mailr:reminder-act', key: d.key, action })
    return
  }
  return self.clients.openWindow(d.url)
}

self.addEventListener('notificationclick', (event) => {
  const n = event.notification
  const d = n.data || {}
  if (event.action) {
    event.waitUntil(act(n, event.action))
    return
  }
  if (d.type && !d.key) {
    // Mail, approvals, failed sends, the assistant: open it in the running app (no reload), or
    // start the app there.
    n.close()
    event.waitUntil(focusOrOpen(d.url || '/mail/inbox', { type: 'mailr:navigate', url: d.url || '/mail/inbox' }))
    return
  }
  // A click opens the sheet (or the triage queue for a summary) and handles nothing; the
  // notification stays until the reminder is handled (ACT notification.open @web.notification, ACT notification.open_stack @web.notification).
  if (d.summary) n.close()
  if (d.test) {
    n.close()
    return
  }
  event.waitUntil(focusOrOpen(d.url || '/mail/inbox', d.summary ? { type: 'mailr:open-triage' } : { type: 'mailr:open-reminder', key: d.key }))
})
