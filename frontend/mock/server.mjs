// Mock mailr API for frontend development and UI testing. No dependencies.
// Implements the mailr API with in-memory sample data.
//   node mock/server.mjs            (listens on :8026)
//   MAILR_API=http://localhost:8026 npm run dev
// Sign in with demo@mailr.test / demo-password-123

import http from 'node:http'
import { randomUUID } from 'node:crypto'

const PORT = Number(process.env.PORT ?? 8026)
const now = Number(process.env.MOCK_NOW ?? Date.now())
const H = 3600_000
const D = 24 * H
const iso = (t) => new Date(t).toISOString()

const me = { id: 1, email: 'demo@mailr.test', name: 'Demo User', role: 'admin' }
let settings = {}
const CSRF = 'mock-csrf-token'
let sessionOk = false

const labels = [
  { id: 1, name: 'INBOX', role: 'inbox', color: null, show_in_list: 'show', show_in_message_list: true },
  { id: 2, name: 'Sent', role: 'sent', color: null, show_in_list: 'show', show_in_message_list: true },
  { id: 3, name: 'Drafts', role: 'drafts', color: null, show_in_list: 'show', show_in_message_list: true },
  { id: 4, name: 'Junk', role: 'junk', color: null, show_in_list: 'show', show_in_message_list: true },
  { id: 5, name: 'Trash', role: 'trash', color: null, show_in_list: 'show', show_in_message_list: true },
  { id: 10, name: 'Job Applications', role: null, color: '#16a766|#ffffff', show_in_list: 'show', show_in_message_list: true },
  { id: 11, name: 'Research', role: null, color: '#4a86e8|#ffffff', show_in_list: 'show', show_in_message_list: true },
  { id: 12, name: 'Research/2026', role: null, color: null, show_in_list: 'show', show_in_message_list: true },
  { id: 13, name: 'Paperless', role: null, color: '#ffad47|#ffffff', show_in_list: 'show', show_in_message_list: true },
  { id: 14, name: 'Todo', role: null, color: '#fb4c2f|#ffffff', show_in_list: 'unread', show_in_message_list: true },
]
let labelSeq = 20

const identities = [
  { id: 1, email: 'demo@mailr.test', name: 'Demo User', reply_to: '', signature_html: '<p>Demo User<br>Assistant Professor</p>', is_default: true, connector_id: 1, receives: [] },
  { id: 2, email: 'academic@mailr.test', name: 'Demo User', reply_to: '', signature_html: '', is_default: false, connector_id: 1, receives: [] },
]

const people = [
  { email: 'alice@example.com', name: 'Alice Smith', organization: 'Example Corp' },
  { email: 'bob@university.example', name: 'Bob Jones', organization: 'University' },
  { email: 'noreply@github.com', name: 'GitHub', organization: 'GitHub' },
  { email: 'carol@partner.org', name: 'Carol Lee', organization: 'Partner Org' },
  { email: 'nai@northfield-analytics.example', name: 'Northfield Analytics Institute', organization: '' },
]

const convs = []
const messages = []
let msgSeq = 100
function mk({ subject, from, to = [{ email: me.email, name: me.name }], hoursAgo, labels: ls = [1], seen = false, flagged = false, important = false, body, atts = [], category = null, meSender = false, draft = false, unsub = null, dmarc = 'pass' }) {
  const id = msgSeq++
  const m = {
    id,
    conversation_id: 0,
    from: meSender ? { email: me.email, name: me.name } : from,
    to,
    cc: [],
    bcc: [],
    reply_to: [],
    subject,
    date: iso(now - hoursAgo * H),
    snippet: body.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 140),
    seen: meSender || seen,
    flagged,
    important,
    draft,
    label_ids: ls,
    has_attachments: atts.length > 0,
    attachments: atts.map((a, i) => ({ part: i + 2, filename: a[0], type: a[1], size: a[2], inline: false })),
    list_unsubscribe: unsub,
    auth_results: { spf: 'pass', dkim: 'pass', dmarc },
    sent_by_me: meSender,
    category,
    _body: body,
  }
  messages.push(m)
  return m
}
function conv(msgs, extra = {}) {
  const id = convs.length + 1
  msgs.forEach((m) => (m.conversation_id = id))
  convs.push({ id, snoozed_until: null, muted: false, ...extra })
}

conv([
  mk({ subject: 'Q3 planning — draft agenda', from: people[0], hoursAgo: 50, body: '<p>Hi,</p><p>Attached is the draft agenda for the Q3 planning meeting. Can you look at section 2?</p><p>Thanks,<br>Alice</p>', atts: [['Q3-agenda.pdf', 'application/pdf', 182_000]], important: true, seen: true }),
  mk({ subject: 'Re: Q3 planning — draft agenda', from: people[0], hoursAgo: 49, meSender: true, labels: [2, 1], body: '<p>Looks good. I added two notes on the budget table.</p><div class="gmail_quote">On Tue, Alice wrote:<blockquote>Attached is the draft agenda…</blockquote></div>' }),
  mk({ subject: 'Re: Q3 planning — draft agenda', from: people[0], hoursAgo: 3, body: '<p>Great, thanks! Can we meet Tuesday at 2pm to finalise?</p>', important: true }),
])
conv([mk({ subject: 'PADS Invites Submissions for 2027 Conference', from: people[4], hoursAgo: 72, important: true, body: '<table bgcolor="#ffffff" width="100%"><tr><td style="color:#222"><h2>PADS 2027 Call for Papers</h2><p>As Program Chair for the 2027 Prairie Analytics and Decision Society conference…</p><img src="https://example.com/logo.png" alt="logo"></td></tr></table>', unsub: '<https://example.com/unsub>, <mailto:unsub@example.com>', category: 'updates' })])
conv([mk({ subject: '[example/speakr] Feature request: Archive recordings (Issue #394)', from: people[2], hoursAgo: 26, body: '<p>setup562 created an issue: Please add an option to archive individual recordings from the recordings overview.</p>', labels: [1], category: 'updates' })])
conv([mk({ subject: 'Security alert', from: { email: 'no-reply@accounts.example.com', name: 'Accounts' }, hoursAgo: 1, body: '<p>New sign-in to your account. If this was you, you don\'t need to do anything.</p>', dmarc: 'fail' })])
conv([
  mk({ subject: 'Paper revision — reviewer 2 comments', from: people[1], hoursAgo: 200, labels: [1, 11, 12], seen: true, body: '<p>Reviewer 2 wants a robustness check on the second model.</p>' }),
  mk({ subject: 'Re: Paper revision — reviewer 2 comments', from: people[1], hoursAgo: 190, labels: [2, 11], meSender: true, body: '<p>I will run it this week.</p>' }),
  mk({ subject: 'Re: Paper revision — reviewer 2 comments', from: people[1], hoursAgo: 150, labels: [1, 11], seen: true, body: '<p>Thanks. Also the literature section needs two more citations.</p>' }),
  mk({ subject: 'Re: Paper revision — reviewer 2 comments', from: people[1], hoursAgo: 20, labels: [1, 11], seen: true, flagged: true, body: '<p>Draft looks good — submit when ready.</p>' }),
], {})
conv([mk({ subject: 'Your invoice #4411', from: { email: 'billing@hosting.example', name: 'Hosting Co' }, hoursAgo: 30, seen: true, labels: [13], body: 'Plain text invoice body.\nAmount due: $12.00\n\nOn Mon, someone wrote:\n> old\n> quoted\n> text', category: 'purchases', atts: [['invoice-4411.pdf', 'application/pdf', 40_000]] })])
conv([mk({ subject: 'Season tickets are on the way!', from: { email: 'news@symphony.example', name: 'Springfield Symphony' }, hoursAgo: 380, seen: true, body: '<p style="color:#333">Plus, there\'s still time to save with a Super Pass…</p>', category: 'promotions' })])
conv([mk({ subject: 'Lunch next week?', from: people[3], hoursAgo: 0.5, body: '<p>Are you free for lunch Thursday? I found a new place near campus.</p>' })], {})
// Recent mail from a spread of senders (people, notifications, newsletters) for date groups and bundles.
const dana = { email: 'dana@studio.example', name: 'Dana Whitfield' }
const omar = { email: 'omar.h@university.example', name: 'Omar Haddad' }
const priya = { email: 'priya@lab.example', name: 'Priya Raman' }
conv([
  mk({ subject: 'Design review notes', from: dana, hoursAgo: 30, seen: true, body: '<p>Sharing the notes from the design review. The navigation changes tested well.</p>', atts: [['review-notes.pdf', 'application/pdf', 220_000], ['nav-sketch.png', 'image/png', 90_000]] }),
  mk({ subject: 'Re: Design review notes', from: dana, hoursAgo: 2, body: '<p>One more thing: can you check the colour contrast on the dark theme before Friday?</p>', labels: [1, 10], flagged: true }),
])
conv([mk({ subject: 'Thesis committee — availability for October', from: omar, hoursAgo: 4.5, important: true, body: '<p>Could you share your availability for the committee meeting in the second week of October?</p>' })])
conv([mk({ subject: 'Dataset access approved', from: priya, hoursAgo: 7, seen: true, labels: [1, 11], body: '<p>Your request for the survey panel dataset was approved. Credentials are in the portal.</p>' })])
conv([mk({ subject: 'Omar Haddad commented on your post', from: { email: 'notifications@social.example', name: 'LinkedIn' }, hoursAgo: 5, category: 'social', body: '<p>Omar Haddad commented: “Great write-up on decision models.”</p>' })])
conv([mk({ subject: 'Your order has shipped', from: { email: 'shipment-tracking@shop.example', name: 'Shop.example' }, hoursAgo: 9, seen: true, category: 'purchases', body: '<p>Your order #88213 is on its way. Arriving Thursday.</p>' })])
conv([mk({ subject: 'New in Figma: variables for prototyping', from: { email: 'news@figma.example', name: 'Figma' }, hoursAgo: 11, category: 'promotions', unsub: '<https://example.com/unsub>', body: '<p>Build richer prototypes with variables and conditional logic.</p>' })])
conv([mk({ subject: '5 new messages in #research-methods', from: { email: 'notification@slack.example', name: 'Slack' }, hoursAgo: 28, category: 'updates', body: '<p>Priya: the preregistration draft is up for comments.</p>' })])
conv([mk({ subject: 'Last chance: 40% off annual plans', from: { email: 'offers@coursera.example', name: 'Coursera' }, hoursAgo: 33, seen: true, category: 'promotions', unsub: '<https://example.com/unsub>', body: '<p>Learn anything. Offer ends Sunday.</p>' })])
conv([mk({ subject: 'Travel reimbursement submitted', from: { email: 'finance@university.example', name: 'University Finance' }, hoursAgo: 100, seen: true, labels: [1, 13], body: '<p>Your reimbursement request TR-2211 was submitted for approval.</p>' })])
conv([mk({ subject: 'Weekend plans?', from: { email: 'sam@friends.example', name: 'Sam Carter' }, hoursAgo: 130, seen: true, category: 'social', body: '<p>Hiking on Saturday if the weather holds?</p>' })])
for (let i = 0; i < 40; i++) conv([mk({ subject: `Weekly digest #${120 - i}`, from: { email: 'digest@news.example', name: 'News Digest' }, hoursAgo: 400 + i * 24, seen: i % 3 !== 0, labels: [1], body: `<p>Top stories for week ${120 - i}.</p>`, category: 'updates' })])
conv([mk({ subject: 'Draft: note to department', from: people[0], hoursAgo: 5, meSender: true, draft: true, labels: [3], to: [{ email: 'dept@university.example', name: 'Department' }], body: '<p>Dear colleagues,</p>' })])
conv([mk({ subject: 'You won a prize!!!', from: { email: 'winner@spam.example', name: 'Prize Team' }, hoursAgo: 12, labels: [4], body: '<p>Click here to claim.</p><img src="https://tracker.example/pixel.gif">' })])

const byConv = (cid) => messages.filter((m) => m.conversation_id === cid && !m._deleted)
function summary(c) {
  const ms = byConv(c.id).sort((a, b) => a.date.localeCompare(b.date))
  const vis = ms.filter((m) => !m.draft)
  const last = (vis.length ? vis : ms)[(vis.length ? vis : ms).length - 1]
  const parts = []
  for (const m of vis) if (!parts.some((p) => p.email === m.from.email)) parts.push({ ...m.from, me: m.sent_by_me })
  return {
    id: c.id,
    subject: ms[0]?.subject ?? '',
    snippet: last?.snippet ?? '',
    last_date: last?.date ?? null,
    message_count: vis.length,
    unread_count: ms.filter((m) => !m.seen && !m.draft).length,
    draft_count: ms.filter((m) => m.draft).length,
    has_attachments: ms.some((m) => m.has_attachments),
    starred: ms.some((m) => m.flagged),
    important: ms.some((m) => m.important),
    importance_score: null,
    category: last?.category ?? null,
    label_ids: [...new Set(ms.flatMap((m) => m.label_ids))],
    participants: parts,
    snoozed_until: c.snoozed_until,
    muted: c.muted,
    attachments_preview: ms.flatMap((m) => m.attachments.map((a) => ({ filename: a.filename, type: a.type }))).slice(0, 3),
  }
}

function matches(c, s, view, section, q) {
  const has = (id) => s.label_ids.includes(id)
  const hidden = has(4) || has(5)
  let ok = true
  switch (view) {
    case 'inbox': ok = has(1) && !hidden && !c.snoozed_until; break
    case 'starred': ok = s.starred && !hidden; break
    case 'snoozed': ok = !!c.snoozed_until; break
    case 'important': ok = s.important && !hidden; break
    case 'sent': ok = has(2); break
    case 'drafts': ok = s.draft_count > 0; break
    case 'scheduled': ok = outbox.some((o) => o.conversation_id === c.id && o.status === 'queued' && new Date(o.send_at) > new Date()); break
    case 'spam': ok = has(4); break
    case 'trash': ok = has(5); break
    case 'all': ok = !hidden; break
    default:
      if (view.startsWith('label:')) ok = has(Number(view.slice(6))) && !hidden
      else if (view.startsWith('category:')) ok = (s.category ?? 'primary') === view.slice(9) && !hidden
  }
  if (!ok) return false
  if (section === 'important' && !(s.important && s.unread_count > 0)) return false
  if (section === 'unread' && s.unread_count === 0) return false
  if (section === 'starred' && !s.starred) return false
  if (section === 'everything' && (s.important || s.unread_count > 0)) return false
  if (q) {
    for (const tok of q.toLowerCase().match(/\S+/g) ?? []) {
      if (tok === 'is:unread' && s.unread_count === 0) return false
      if (tok === 'is:starred' && !s.starred) return false
      if (tok === 'is:important' && !s.important) return false
      if (tok === 'has:attachment' && !s.has_attachments) return false
      if (tok.startsWith('category:')) { if ((s.category ?? 'primary') !== tok.slice(9)) return false; continue }
      if (tok.startsWith('from:')) { if (!s.participants.some((p) => p.email.includes(tok.slice(5)) || (p.name ?? '').toLowerCase().includes(tok.slice(5)) || (tok === 'from:me' && p.me))) return false; continue }
      if (tok.includes(':')) continue
      const hay = `${s.subject} ${s.snippet} ${s.participants.map((p) => p.name + p.email).join(' ')}`.toLowerCase()
      if (!hay.includes(tok)) return false
    }
  }
  return true
}


// ---------- inbox layouts (mirrors backend/mailr/core/layouts.py presets) ----------
const CAT = { primary: ['Primary', 'inbox'], promotions: ['Promotions', 'tag'], social: ['Social', 'users'], updates: ['Updates', 'info'], forums: ['Forums', 'message'], purchases: ['Purchases', 'bag'] }
const SEC_TITLES = { important: 'Important', important_unread: 'Important and unread', unread: 'Unread', starred: 'Starred', everything: 'Everything else' }
function lsec(id, title, { filter = null, category = null, limit = null, exclude_above = true } = {}) {
  const vq = ['in:inbox', category ? `category:${category}` : '', filter === 'unread' ? 'is:unread' : filter === 'starred' ? 'is:starred' : filter === 'important' ? 'is:important' : filter === 'important_unread' ? 'is:important is:unread' : ''].filter(Boolean).join(' ')
  return { id, title, base: 'inbox', category, filter, q: '', limit, exclude_above, view_all_q: vq }
}
function prioritySecs(names) {
  return names.map((n, i) => lsec(n, SEC_TITLES[n], { filter: n === 'everything' ? null : n, limit: i === names.length - 1 ? null : 10 }))
}
function allLayouts() {
  const cats = (settings.categories ?? ['primary', 'promotions', 'social', 'updates', 'forums']).filter((c) => CAT[c])
  if (!cats.includes('primary')) cats.unshift('primary')
  const one = (id, name, sections) => ({ id, name, preset: true, tabs: [{ id: 'inbox', name: 'Inbox', icon: 'inbox', badge: 'none', sections }] })
  return [
    { id: 'default', name: 'Default', preset: true, tabs: cats.map((c) => ({ id: c, name: CAT[c][0], icon: CAT[c][1], badge: c === 'primary' ? 'none' : 'unread', sections: [lsec(c, CAT[c][0], { category: c })] })) },
    one('important_first', 'Important first', prioritySecs(['important', 'everything'])),
    one('unread_first', 'Unread first', prioritySecs(['unread', 'everything'])),
    one('starred_first', 'Starred first', prioritySecs(['starred', 'everything'])),
    one('priority', 'Custom sections', prioritySecs(settings.priority_sections ?? ['important', 'unread', 'everything'])),
    ...(settings.inbox_layouts ?? []).map((l) => ({ ...l, preset: false })),
  ]
}
function activeLayoutId() {
  const t = settings.inbox_type ?? 'default'
  return t.startsWith('layout:') ? t.slice(7) : t
}
function inSection(c, s, sec) {
  if (!matches(c, s, 'inbox', null, null)) return false
  if (sec.category && (s.category ?? 'primary') !== sec.category) return false
  if (sec.filter === 'unread' && s.unread_count === 0) return false
  if (sec.filter === 'starred' && !s.starred) return false
  if (sec.filter === 'important' && !s.important) return false
  if (sec.filter === 'important_unread' && !(s.important && s.unread_count > 0)) return false
  return true
}
function sectionList(layoutId, tabId, secId) {
  const lay = allLayouts().find((l) => l.id === layoutId)
  const tab = lay?.tabs.find((t) => t.id === tabId)
  const i = tab ? tab.sections.findIndex((x) => x.id === secId) : -1
  if (i < 0) return []
  const sec = tab.sections[i]
  const above = sec.exclude_above ? tab.sections.slice(0, i) : []
  return convs.map((c) => [c, summary(c)]).filter(([c, s]) => inSection(c, s, sec) && !above.some((a) => inSection(c, s, a))).map(([, s]) => s)
}

const outbox = []
let obSeq = 1
const drafts = new Map()
const rules = [
  { id: 1, name: '', query: 'to:(docs@mailr.test)', actions: { add_labels: [13] }, position: 0, enabled: true, stop: false, source: 'gmail-import', hits: 12 },
  { id: 2, name: 'GitHub', query: 'from:noreply@github.com', actions: { archive: true, category: 'updates' }, position: 1, enabled: true, stop: false, source: 'user', hits: 40 },
]
const connectors = [
  { id: 1, kind: 'imap', name: 'demo@mailr.test', preset: 'fastmail', mode: 'store', data_class: 'normal', config: {}, retention_days: 14, delete_upstream: false, enabled: true, status: 'idle', status_detail: '', last_sync_at: iso(now - 60_000) },
  { id: 2, kind: 'caldav', name: 'Davis', preset: null, mode: 'mirror', data_class: 'normal', config: { url: 'https://cal.example.com/dav/' }, retention_days: 14, delete_upstream: false, enabled: true, status: 'idle', status_detail: '', last_sync_at: iso(now - 300_000) },
]
const tokens = [{ id: 1, name: 'Claude Code', prefix: 'mlr_Ab12', scopes: ['read', 'draft'], connector_ids: [], local_only: false, send_policy: { mode: 'approve_all' }, expires_at: null, revoked: false, last_used_at: iso(now - 2 * H), created_at: iso(now - 5 * D) }]
const tasks = [
  { uid: 't1', summary: 'Submit PADS paper', due: iso(now + D), status: 'needs-action', conversation_id: 2 },
  { uid: 't2', summary: 'Review Q3 agenda', due: iso(now - D), status: 'needs-action' },
  { uid: 't3', summary: 'Book travel', due: null, status: 'completed' },
]
const sseClients = new Set()
function broadcast(ev) {
  for (const res of sseClients) res.write(`data: ${JSON.stringify(ev)}\n\n`)
}
let changeCursor = 1

function labelView() {
  return labels.map((l) => {
    const cs = convs.map(summary).filter((s) => s.label_ids.includes(l.id))
    return { ...l, total: cs.length, unread: cs.filter((s) => s.unread_count > 0).length }
  })
}

function applyAction(ids, body, scope) {
  const msgs = scope === 'messages' ? messages.filter((m) => ids.includes(m.id)) : messages.filter((m) => ids.includes(m.conversation_id))
  const cids = [...new Set(msgs.map((m) => m.conversation_id))]
  let undo = null
  const setLabel = (m, add, rem) => (m.label_ids = [...new Set([...m.label_ids.filter((x) => !rem.includes(x)), ...add])])
  switch (body.action) {
    case 'archive': msgs.forEach((m) => setLabel(m, [], [1])); undo = { action: 'inbox' }; break
    case 'inbox': msgs.forEach((m) => setLabel(m, [1], [4, 5])); undo = { action: 'archive' }; break
    case 'trash': msgs.forEach((m) => { m._prev = m.label_ids; m.label_ids = [5] }); undo = { action: 'untrash' }; break
    case 'untrash': msgs.forEach((m) => { m.label_ids = m._prev ?? [1] }); break
    case 'spam': msgs.forEach((m) => { m._prev = m.label_ids; m.label_ids = [4] }); undo = { action: 'not_spam' }; break
    case 'not_spam': msgs.forEach((m) => { m.label_ids = m._prev ?? [1] }); break
    case 'delete_forever': msgs.forEach((m) => (m._deleted = true)); break
    case 'read': msgs.forEach((m) => (m.seen = true)); undo = { action: 'unread' }; break
    case 'unread': msgs.forEach((m) => (m.seen = false)); undo = { action: 'read' }; break
    case 'star': (scope === 'messages' ? msgs : cids.map((c) => byConv(c).at(-1))).forEach((m) => (m.flagged = true)); undo = { action: 'unstar' }; break
    case 'unstar': msgs.forEach((m) => (m.flagged = false)); undo = { action: 'star' }; break
    case 'important': msgs.forEach((m) => (m.important = true)); undo = { action: 'not_important' }; break
    case 'not_important': msgs.forEach((m) => (m.important = false)); undo = { action: 'important' }; break
    case 'add_labels': msgs.forEach((m) => setLabel(m, body.label_ids ?? [], [])); undo = { action: 'remove_labels', label_ids: body.label_ids }; break
    case 'remove_labels': msgs.forEach((m) => setLabel(m, [], body.label_ids ?? [])); undo = { action: 'add_labels', label_ids: body.label_ids }; break
    case 'move': msgs.forEach((m) => setLabel(m, [body.label_id], [1])); undo = { action: 'inbox' }; break
    case 'snooze': cids.forEach((c) => (convs.find((x) => x.id === c).snoozed_until = body.until)); undo = { action: 'unsnooze' }; break
    case 'unsnooze': cids.forEach((c) => (convs.find((x) => x.id === c).snoozed_until = null)); break
    case 'mute': cids.forEach((c) => (convs.find((x) => x.id === c).muted = true)); undo = { action: 'unmute' }; break
    case 'unmute': cids.forEach((c) => (convs.find((x) => x.id === c).muted = false)); break
    case 'category': msgs.forEach((m) => (m.category = body.category)); break
  }
  changeCursor++
  setTimeout(() => broadcast({ type: 'changes', entity: 'conversation' }), 50)
  return { ok: true, affected: ids.length, undo }
}

function replyPayload(m, mode) {
  const quote = `<div class="mailr_quote"><div dir="ltr">On ${new Date(m.date).toLocaleString()}, ${m.from.name} &lt;${m.from.email}&gt; wrote:<br></div><blockquote class="mailr_quote" style="margin:0 0 0 .8ex;border-left:1px #ccc solid;padding-left:1ex">${m._body}</blockquote></div>`
  const all = [m.from, ...m.to, ...m.cc].filter((a) => a.email !== me.email)
  return {
    identity_id: 1,
    to: mode === 'forward' ? [] : mode === 'reply_all' ? all.slice(0, 1) : [m.sent_by_me ? m.to[0] : m.from],
    cc: mode === 'reply_all' ? all.slice(1) : [],
    bcc: [],
    subject: (mode === 'forward' ? 'Fwd: ' : /^re:/i.test(m.subject) ? '' : 'Re: ') + m.subject,
    html: `<p></p>${quote}`,
    in_reply_to_message_id: mode === 'forward' ? null : m.id,
    forward_of_message_id: mode === 'forward' ? m.id : null,
    attachments: [],
  }
}

function msgOut(m) {
  // eslint-disable-next-line no-unused-vars
  const { _body, _prev, _deleted, ...rest } = m
  return rest
}

async function readBody(req) {
  const chunks = []
  for await (const c of req) chunks.push(c)
  const raw = Buffer.concat(chunks)
  if ((req.headers['content-type'] ?? '').includes('application/json')) {
    try {
      return JSON.parse(raw.toString() || '{}')
    } catch {
      return {}
    }
  }
  return { _raw: raw }
}

function send(res, status, data) {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(data === undefined ? '' : JSON.stringify(data))
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x')
  const p = url.pathname.replace(/^\/api\/v1/, '')
  const q = Object.fromEntries(url.searchParams)
  const method = req.method
  const body = ['POST', 'PUT', 'PATCH'].includes(method) ? await readBody(req) : {}
  const authed = sessionOk || (req.headers.cookie ?? '').includes('mailr_session=mock') || (req.headers.authorization ?? '').startsWith('Bearer ')
  if (!['GET', 'HEAD'].includes(method) && !p.startsWith('/auth/') && authed && !(req.headers.authorization) && req.headers['x-csrf-token'] !== CSRF) return send(res, 403, { detail: 'CSRF check failed' })
  await new Promise((r) => setTimeout(r, 60 + Math.random() * 120))

  if (p === '/auth/config') return send(res, 200, { local_login: true, oidc: true, oidc_name: 'Pocket ID', signup: false })
  if (p === '/auth/login' && method === 'POST') {
    if (body.email === me.email && body.password === 'demo-password-123') {
      res.setHeader('Set-Cookie', 'mailr_session=mock; Path=/; HttpOnly; SameSite=Lax')
      return send(res, 200, { user: me, csrf: CSRF })
    }
    return send(res, 401, { detail: 'invalid credentials' })
  }
  if (p === '/auth/logout') {
    res.setHeader('Set-Cookie', 'mailr_session=; Path=/; Max-Age=0')
    return send(res, 204)
  }
  if (p === '/auth/session') return send(res, 200, authed ? { authenticated: true, user: me, csrf: CSRF, settings, identities, counts: {} } : { authenticated: false })
  if (!authed) return send(res, 401, { detail: 'not signed in' })

  let m
  if (p === '/me') return send(res, 200, { user: me, csrf: CSRF, settings, identities, counts: { scheduled: outbox.filter((o) => o.status === 'queued' && new Date(o.send_at) > new Date()).length } })
  if (p === '/me/settings' && method === 'PATCH') { settings = { ...settings, ...body }; return send(res, 200, settings) }
  if (p === '/me/sessions') return send(res, 200, [{ id: 's1', device: 'web', user_agent: req.headers['user-agent'], ip: '127.0.0.1', created_at: iso(now - D), last_seen_at: iso(now), current: true }, { id: 's2', device: 'android', user_agent: 'mailr-android/0.1', ip: '192.168.1.20', created_at: iso(now - 9 * D), last_seen_at: iso(now - 3 * H) }])
  if (p === '/me/imap') return send(res, 200, { host: 'mail.example.com', port: 993, security: 'SSL/TLS', username: 'demo@mailr.internal' })
  if (p === '/me/imap/app-password') return send(res, 200, { password: 'abcd-efgh-ijkl-mnop' })
  if (p === '/layouts' && method === 'GET') return send(res, 200, { active: activeLayoutId(), layouts: allLayouts(), options: { bases: ['inbox', 'all', 'starred', 'important'], filters: ['unread', 'important', 'important_unread', 'starred'], icons: ['inbox', 'tag', 'users', 'info', 'message', 'bag', 'star'], limits: { tabs: 8, sections: 6, layouts: 12 } } })
  if (p === '/layouts' && method === 'PUT') { settings.inbox_layouts = body.layouts; if (body.active) settings.inbox_type = `layout:${body.active}`; return send(res, 200, { active: activeLayoutId(), layouts: allLayouts(), options: {} }) }
  if ((m = /^\/layouts\/([^/]+)\/counts$/.exec(p))) {
    const lay = allLayouts().find((l) => l.id === decodeURIComponent(m[1]))
    const out = {}
    for (const t of lay?.tabs ?? []) out[t.id] = t.badge === 'unread' ? new Set(t.sections.flatMap((x) => sectionList(lay.id, t.id, x.id)).filter((s) => s.unread_count > 0).map((s) => s.id)).size : 0
    return send(res, 200, out)
  }
  if (p === '/approvals') return send(res, 200, [])
  if (p === '/labels' && method === 'GET') return send(res, 200, labelView())
  if (p === '/labels' && method === 'POST') { const l = { id: labelSeq++, name: body.name, role: null, color: body.color ?? null, show_in_list: 'show', show_in_message_list: true }; labels.push(l); return send(res, 200, { ...l, total: 0, unread: 0 }) }
  if ((m = /^\/labels\/(\d+)$/.exec(p))) {
    const l = labels.find((x) => x.id === Number(m[1]))
    if (method === 'PATCH') { Object.assign(l, body); return send(res, 200, l) }
    if (method === 'DELETE') { labels.splice(labels.indexOf(l), 1); return send(res, 204) }
  }
  if (p === '/conversations') {
    const view = q.view ?? 'inbox'
    const order = (q.sections ?? '').split(',').filter(Boolean)
    const earlier = q.section && order.includes(q.section) ? order.slice(0, order.indexOf(q.section)) : []
    let list = q.layout ? sectionList(q.layout, q.tab, q.sec) : convs
      .map((c) => [c, summary(c)])
      .filter(([c, s]) => matches(c, s, view, q.section, q.q) && !earlier.some((e) => matches(c, s, view, e, q.q)))
      .map(([, s]) => s)
    list.sort((a, b) => (b.last_date ?? '').localeCompare(a.last_date ?? ''))
    const limit = Number(q.limit ?? 50)
    const start = Number(q.cursor ?? 0)
    return send(res, 200, { items: list.slice(start, start + limit), next_cursor: start + limit < list.length ? String(start + limit) : null, total: list.length })
  }
  if ((m = /^\/conversations\/(\d+)$/.exec(p))) {
    const c = convs.find((x) => x.id === Number(m[1]))
    if (!c) return send(res, 404, { detail: 'not found' })
    return send(res, 200, { conversation: summary(c), messages: byConv(c.id).sort((a, b) => a.date.localeCompare(b.date)).map(msgOut) })
  }
  if ((m = /^\/conversations\/(\d+)\/summary$/.exec(p))) {
    const ms = byConv(Number(m[1]))
    return send(res, 200, { status: 'ready', summary: `${ms[0].from.name} and you discussed "${ms[0].subject}". Latest: ${ms.at(-1).snippet}`, model: 'mock' })
  }
  if ((m = /^\/conversations\/(\d+)\/split$/.exec(p))) {
    const id = convs.length + 1
    convs.push({ id, snoozed_until: null, muted: false })
    messages.filter((x) => body.message_ids.includes(x.id)).forEach((x) => (x.conversation_id = id))
    return send(res, 200, { conversation_id: id })
  }
  if (p === '/conversations/actions') return send(res, 200, applyAction(body.ids ?? [], body, 'conversations'))
  if (p === '/messages/actions') return send(res, 200, applyAction(body.ids ?? [], body, 'messages'))
  if ((m = /^\/messages\/(\d+)\/body$/.exec(p))) {
    const msg = messages.find((x) => x.id === Number(m[1]))
    const isHtml = /<[a-z]/i.test(msg._body)
    let html = isHtml ? msg._body : null
    const remote = /<img[^>]+src="https?:/i.test(msg._body)
    if (html && q.images !== 'allowed') html = html.replace(/<img([^>]+)src="https?:[^"]*"/gi, '<img$1src=""')
    const qi = html ? html.indexOf('<div class="gmail_quote"') : -1
    return send(res, 200, { html, text: isHtml ? null : msg._body, remote_images: remote, quoted_html_start: qi > 0 ? qi : null })
  }
  if ((m = /^\/messages\/(\d+)\/decisions$/.exec(p))) return send(res, 200, [
    { id: 1, kind: 'label', target: '11', value: true, source: 'classifier', source_ref: 'bge-small+logreg', confidence: 0.91, reason: 'Similar to 14 conversations you labelled Research', status: 'applied', created_at: iso(now) },
    { id: 2, kind: 'importance', target: 'important', value: true, source: 'llm', source_ref: 'qwen3.8-27b', confidence: 0.77, reason: 'Asks you a direct question with a deadline', status: 'suggested', created_at: iso(now) },
  ])
  if ((m = /^\/decisions\/(\d+)\/(accept|reject)$/.exec(p))) return send(res, 200, { ok: true })
  if ((m = /^\/messages\/(\d+)\/raw$/.exec(p))) { res.writeHead(200, { 'Content-Type': 'text/plain' }); return res.end('From: someone\nSubject: raw\n\nraw message') }
  if ((m = /^\/messages\/(\d+)\/attachments\/(\d+)$/.exec(p))) { res.writeHead(200, { 'Content-Type': 'text/plain' }); return res.end('attachment bytes') }
  if (p === '/compose/reply') {
    const msg = messages.find((x) => x.id === Number(q.message_id))
    return send(res, 200, { payload: replyPayload(msg, q.mode) })
  }
  if (p === '/drafts' && method === 'POST') {
    const d = mk({ subject: body.payload.subject || '(no subject)', from: { email: me.email, name: me.name }, hoursAgo: 0, meSender: true, draft: true, labels: [3], to: body.payload.to, body: body.payload.html || '' })
    const parent = body.payload.in_reply_to_message_id ? messages.find((x) => x.id === body.payload.in_reply_to_message_id) : null
    if (parent) d.conversation_id = parent.conversation_id
    else { conv([d]) }
    drafts.set(d.id, body.payload)
    broadcast({ type: 'changes', entity: 'conversation' })
    return send(res, 200, { draft: msgOut(d) })
  }
  if ((m = /^\/drafts\/(\d+)$/.exec(p))) {
    const id = Number(m[1])
    const d = messages.find((x) => x.id === id)
    if (method === 'GET') return send(res, 200, { payload: drafts.get(id) ?? { identity_id: 1, to: d.to, cc: [], bcc: [], subject: d.subject, html: d._body, attachments: [] } })
    if (method === 'PUT') { drafts.set(id, body.payload); d.subject = body.payload.subject; d._body = body.payload.html ?? ''; d.snippet = (body.payload.html ?? '').replace(/<[^>]+>/g, ' ').slice(0, 100); return send(res, 200, { draft: msgOut(d) }) }
    if (method === 'DELETE') { d._deleted = true; drafts.delete(id); return send(res, 204) }
  }
  if (p === '/send') {
    const hold = (settings.undo_send_seconds ?? 10) * 1000
    const sendAt = body.send_at ? new Date(body.send_at) : new Date(Date.now() + hold)
    if (body.draft_id) { const d = messages.find((x) => x.id === body.draft_id); if (d) d._deleted = true }
    const parent = body.payload.in_reply_to_message_id ? messages.find((x) => x.id === body.payload.in_reply_to_message_id) : null
    const ob = { id: obSeq++, status: 'queued', send_at: sendAt.toISOString(), recipients: [...body.payload.to, ...body.payload.cc, ...body.payload.bcc].map((a) => a.email), subject: body.payload.subject, identity_id: body.payload.identity_id, token_name: null, approval_reason: '', error: '', conversation_id: parent?.conversation_id ?? null, _payload: body.payload }
    outbox.push(ob)
    setTimeout(() => {
      if (ob.status !== 'queued') return
      ob.status = 'sent'
      const sm = mk({ subject: body.payload.subject, from: {}, hoursAgo: 0, meSender: true, labels: [2], to: body.payload.to, body: body.payload.html ?? '' })
      if (parent) sm.conversation_id = parent.conversation_id
      else conv([sm])
      ob.conversation_id = sm.conversation_id
      broadcast({ type: 'changes', entity: 'conversation' })
    }, Math.max(0, sendAt.getTime() - Date.now()))
    const { _payload, ...pub } = ob
    return send(res, 200, { outbox: pub })
  }
  if (p === '/outbox') return send(res, 200, outbox.filter((o) => !q.status || o.status === q.status).map(({ _payload, ...o }) => o))
  if ((m = /^\/outbox\/(\d+)\/(cancel|approve|reject)$/.exec(p))) {
    const ob = outbox.find((o) => o.id === Number(m[1]))
    if (!ob) return send(res, 404, { detail: 'not found' })
    if (m[2] === 'cancel') { if (ob.status !== 'queued' && ob.status !== 'failed') return send(res, 409, { detail: 'already sent' }); ob.status = 'cancelled'; return send(res, 200, { payload: ob._payload, draft_id: null }) }
    ob.status = m[2] === 'approve' ? 'queued' : 'rejected'
    return send(res, 200, { ok: true })
  }
  if (p === '/uploads' && method === 'POST') return send(res, 200, { blob: randomUUID(), filename: 'upload.bin', type: 'application/octet-stream', size: body._raw?.length ?? 0 })
  if (p === '/people') {
    if (q.blocked) return send(res, 200, [{ email: 'tender@supplier.example', name: 'Procurement Department', blocked: true }])
    const t = (q.q ?? '').toLowerCase()
    return send(res, 200, people.filter((x) => x.email.includes(t) || x.name.toLowerCase().includes(t)))
  }
  if ((m = /^\/people\/([^/]+)\/block$/.exec(p))) return send(res, 200, { ok: true })
  if (p === '/events') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' })
    res.write(': hello\n\n')
    sseClients.add(res)
    const ping = setInterval(() => res.write(': ping\n\n'), 20000)
    req.on('close', () => { clearInterval(ping); sseClients.delete(res) })
    return
  }
  if (p === '/changes') return send(res, 200, { cursor: String(changeCursor), changes: [], more: false })
  if (p === '/agent/chat') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
    const ev = (e, d) => res.write(`event: ${e}\ndata: ${JSON.stringify(d)}\n\n`)
    ev('tool_call', { name: 'search', args: { q: 'from:alice newer_than:30d' } })
    await new Promise((r) => setTimeout(r, 300))
    ev('tool_result', { name: 'search', summary: '3 conversations' })
    const text = 'Alice asked to meet Tuesday at 2pm to finalise the Q3 agenda. You already sent notes on the budget table. Open question: whether section 2 needs a revised timeline.'
    for (const w of text.split(' ')) { ev('token', { text: w + ' ' }); await new Promise((r) => setTimeout(r, 25)) }
    ev('done', {})
    return res.end()
  }
  if (p === '/agent/draft') return send(res, 200, { payload: { html: `<p>Hi,</p><p>${body.instructions}</p><p>Best,<br>Demo</p>` } })
  if (p === '/connectors/presets') return send(res, 200, [
    { id: 'fastmail', name: 'Fastmail', imap: { host: 'imap.fastmail.com', port: 993, security: 'ssl' }, smtp: { host: 'smtp.fastmail.com', port: 465, security: 'ssl' }, notes: 'Use an app password from Fastmail settings.' },
    { id: 'gmail', name: 'Gmail', imap: { host: 'imap.gmail.com', port: 993, security: 'ssl' }, smtp: { host: 'smtp.gmail.com', port: 465, security: 'ssl' }, notes: 'Requires 2-step verification and an app password.' },
  ])
  if (p === '/connectors/discover') return send(res, 200, { preset: 'fastmail', imap: { host: 'imap.fastmail.com', port: 993, security: 'ssl' }, smtp: { host: 'smtp.fastmail.com', port: 465, security: 'ssl' }, source: 'MX lookup' })
  if (p === '/connectors/test') return body.secrets?.password === 'bad' ? send(res, 200, { ok: false, detail: 'AUTHENTICATIONFAILED', folders: [], identities: [], extensions: [] }) : send(res, 200, { ok: true, detail: '', folders: [{ name: 'INBOX', role: 'inbox', count: 120 }, { name: 'Sent', role: 'sent', count: 40 }, { name: 'Spam', role: 'junk', count: 3 }, { name: 'Trash', role: 'trash', count: 9 }], identities: ['alias@example.com'], extensions: ['IDLE', 'CONDSTORE'] })
  if (p === '/connectors' && method === 'GET') return send(res, 200, connectors)
  if (p === '/connectors' && method === 'POST') { const c = { id: connectors.length + 1, status: 'syncing', status_detail: 'Importing history…', last_sync_at: null, enabled: true, delete_upstream: false, preset: body.preset, ...body }; delete c.secrets; connectors.push(c); return send(res, 200, c) }
  if ((m = /^\/connectors\/(\d+)(?:\/([a-z-]+))?$/.exec(p))) {
    const c = connectors.find((x) => x.id === Number(m[1]))
    if (m[2] === 'arm-deletion') { if (body.confirm !== c.name) return send(res, 400, { detail: 'confirmation does not match' }); c.delete_upstream = true; return send(res, 200, c) }
    if (m[2] === 'pause') c.status = 'paused'
    if (m[2] === 'resume') c.status = 'idle'
    if (method === 'PATCH') Object.assign(c, body)
    if (method === 'DELETE') { connectors.splice(connectors.indexOf(c), 1); return send(res, 204) }
    return send(res, 200, c)
  }
  if (p === '/identities' && method === 'POST') { const i = { id: identities.length + 1, reply_to: '', signature_html: '', is_default: false, receives: [], ...body }; identities.push(i); return send(res, 200, i) }
  if ((m = /^\/identities\/(\d+)$/.exec(p))) {
    const i = identities.find((x) => x.id === Number(m[1]))
    if (method === 'DELETE') { identities.splice(identities.indexOf(i), 1); return send(res, 204) }
    if (body.is_default) identities.forEach((x) => (x.is_default = false))
    Object.assign(i, body)
    return send(res, 200, i)
  }
  if (p === '/rules' && method === 'GET') return send(res, 200, rules)
  if (p === '/rules' && method === 'POST') { const r = { id: rules.length + 10, position: rules.length, enabled: true, source: 'user', hits: 0, ...body }; rules.push(r); return send(res, 200, r) }
  if (p === '/rules/preview') { const list = convs.map(summary).filter((s) => matches({}, s, 'all', null, body.query)); return send(res, 200, { count: list.length, sample: list.slice(0, 5) }) }
  if (p === '/rules/reorder') { body.ids.forEach((id, i) => (rules.find((r) => r.id === id).position = i)); return send(res, 200, { ok: true }) }
  if ((m = /^\/rules\/(\d+)(?:\/apply)?$/.exec(p))) {
    const r = rules.find((x) => x.id === Number(m[1]))
    if (p.endsWith('/apply')) return send(res, 200, { affected: 4 })
    if (method === 'DELETE') { rules.splice(rules.indexOf(r), 1); return send(res, 204) }
    Object.assign(r, body)
    return send(res, 200, r)
  }
  if (p === '/tokens' && method === 'GET') return send(res, 200, tokens)
  if (p === '/tokens' && method === 'POST') { const t = { id: tokens.length + 1, prefix: 'mlr_Xy9z', revoked: false, last_used_at: null, created_at: iso(Date.now()), ...body }; tokens.push(t); return send(res, 200, { token: 'mlr_Xy9zSECRETmocktokenvalue123', api_token: t }) }
  if ((m = /^\/tokens\/(\d+)$/.exec(p))) { tokens.find((t) => t.id === Number(m[1])).revoked = true; return send(res, 204) }
  if (p === '/audit') return send(res, 200, { items: [{ id: 1, token_id: 1, actor: 'token:Claude Code', action: 'search', target: 'from:alice', inputs: { q: 'from:alice' }, result: { count: 3 }, ok: true, created_at: iso(now - 2 * H) }, { id: 2, token_id: 1, actor: 'token:Claude Code', action: 'draft.create', target: 'conversation 1', inputs: {}, result: {}, ok: true, created_at: iso(now - 2 * H) }], next_cursor: null })
  if (p === '/calendar/agenda') return send(res, 200, [
    { uid: 'e1', summary: 'Office hours', start: iso(new Date(q.from).getTime() + 10 * H), end: iso(new Date(q.from).getTime() + 11.5 * H), location: 'Hollis Hall 307' },
    { uid: 'e2', summary: 'Q3 planning with Alice', start: iso(new Date(q.from).getTime() + 14 * H), end: iso(new Date(q.from).getTime() + 15 * H), color: '#16a766' },
  ])
  if (p === '/calendar/calendars') return send(res, 200, [{ id: 'default', name: "Demo's Calendar", color: '#4a86e8', connector_id: 2, kind: 'both' }, { id: 'family', name: 'Family', color: '#16a766', connector_id: 2, kind: 'events' }])
  if (p === '/tasks' && method === 'GET') return send(res, 200, tasks)
  if (p === '/tasks' && method === 'POST') { const t = { uid: randomUUID(), status: 'needs-action', ...body }; tasks.push(t); return send(res, 200, t) }
  if ((m = /^\/tasks\/(.+)$/.exec(p))) { const t = tasks.find((x) => x.uid === decodeURIComponent(m[1])); Object.assign(t, body); return send(res, 200, t) }
  if (p === '/admin/status') return send(res, 200, { stalwart: { reachable: true, version: '0.16.23', accounts: 2 }, worker: { running: true, queued_jobs: 3, failed_jobs: 0 }, models: { 'local vLLM': 'ok', embeddings: 'bge-small (cpu)' }, storage: { messages: messages.length, disk_used: '1.2 GB' } })
  if (p === '/admin/users' && method === 'GET') return send(res, 200, [{ ...me, disabled: false, created_at: iso(now - 30 * D), last_login_at: iso(now) }, { id: 2, email: 'cleo@example.com', name: 'Cleo', role: 'user', disabled: false, created_at: iso(now - 3 * D), last_login_at: null }])
  if (p.startsWith('/admin/users')) return send(res, 200, { ok: true })
  if (p === '/admin/models' && method === 'GET') return send(res, 200, [{ id: 1, name: 'local vLLM', base_url: 'http://llm-host:8000/v1', locality: 'local', adapter: 'openai', models: ['qwen3.8-27b'], defaults: { chat: 'qwen3.8-27b', agent: 'qwen3.8-27b', tagging: 'qwen3.8-27b' }, enabled: true }])
  if (p === '/admin/models/probe') return send(res, 200, { models: ['qwen3.8-27b', 'llama-4-scout'] })
  if (p.startsWith('/admin/models')) return send(res, 200, { ok: true })
  if (p === '/admin/policies') return send(res, 200, method === 'PUT' ? body : { local_only_domains: ['springfield.edu'] })
  if (p === '/push/targets') return send(res, 200, [])
  return send(res, 404, { detail: `mock: no route ${method} ${p}` })
})

server.listen(PORT, () => console.log(`mailr mock API on http://localhost:${PORT}`))
