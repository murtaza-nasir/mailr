// The approval card's head and the grouping of the waiting list (approval.approve_many):
// type labels, the heading (the object's own title, no quotes or question mark), the facts line,
// the short reason, the two-line clamp with "more", and groups of consecutive requests.
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApprovalCard, isClamped } from '../rail/assistant/ApprovalCard'
import type { Display } from '../rail/assistant/present/types'
import type { ApprovalRef } from '../rail/assistant/useAssistant'
import { requesterKey, type PendingApproval } from './approvals'
import { groupPhrase, headingOf, kindLabel, kindOf, reasonOf, segments, splitFields, type CardHead } from './cardHead'

const LONG = 'Research workshop: dry run in the seminar room with the team (check projector, outlets and the HDMI cable before Monday)'
const TAINTED = 'An agent wrote this text after reading email (or through an API token), so it asks first'

const head = (over: Partial<CardHead> = {}): CardHead => ({
  kind: 'task_new', kind_label: 'New task', kind_label_msg: { key: 'approvals.kind.task_new' }, name: LONG,
  reason_short: 'Asks first: written through an API token', reason_short_msg: { key: 'approvals.reason_short.token_text' }, ...over,
})

const taskDisplay = (title = `Add task “${LONG.slice(0, 80)}”`): Display => ({
  v: 1, icon: 'task', title, summary: null, danger: false, reason: TAINTED, text: title,
  fields: [
    { label: 'Task', value: { t: 'text', text: LONG } },
    { label: 'Due', value: { t: 'when', text: 'Mon 30 Nov, 09:00', start: '2026-11-30T09:00', end: null, all_day: false } },
    { label: 'List', value: { t: 'text', text: 'Calendar' } },
    { label: 'Notes', value: { t: 'quote', text: 'Bring the adapter.' } },
  ],
})

const pa = (id: number, over: Partial<PendingApproval> = {}): PendingApproval => ({
  id, subject: 'token:4', actor: 'claude-code', action: 'tasks.create', target: null, preview: null, status: 'pending', result: null,
  decided_by: null, created_at: null, expires_at: null, decided_at: null, executed_at: null, chat_session_id: null,
  head: head(), ...over,
})

describe('card head', () => {
  it('labels the kind of request, in this app’s language', () => {
    expect(kindLabel({ head: head() })).toBe('New task')
    expect(kindLabel({ head: head({ kind: 'send', kind_label: 'Send email', kind_label_msg: { key: 'approvals.kind.send' } }) })).toBe('Send email')
    expect(kindLabel({ head: null })).toBeNull()
    expect(kindOf({ action: 'tasks.create', head: null })).toBe('tasks.create')
  })

  it('heads the card with the object’s own title: whole, without quotes or a question mark', () => {
    const h = headingOf({ action: 'tasks.create', display: taskDisplay(), head: head() })
    expect(h).toBe(LONG)
    expect(h).not.toMatch(/[“”?]/)
    // No name (a filter, an older server): the card's title as the server wrote it.
    expect(headingOf({ action: 'rules.write', display: { ...taskDisplay('Create a filter'), fields: [] }, head: head({ name: null }) })).toBe('Create a filter')
    expect(headingOf({ action: 'mail.send', title: 'Send “Hi”', head: null })).toBe('Send “Hi”')
  })

  it('puts short fields on the facts line, keeps long ones below, and drops the one that repeats the heading', () => {
    const { facts, blocks } = splitFields(taskDisplay().fields, LONG)
    expect(facts.map((f) => f.label)).toEqual(['Due', 'List'])
    expect(blocks.map((f) => f.label)).toEqual(['Notes'])
    // An email: every recipient stays, the subject (the heading) leaves the line.
    const mail = splitFields([
      { label: 'To', value: { t: 'people', text: 'a@x.example, b@y.example', items: [{ name: null, email: 'a@x.example' }, { name: null, email: 'b@y.example' }], more: 0 } },
      { label: 'Subject', value: { t: 'text', text: 'Workshop room' } },
      { label: '', value: { t: 'quote', text: 'Hello' } },
    ], 'Workshop room')
    expect(mail.facts.map((f) => f.label)).toEqual(['To'])
    expect(mail.blocks).toHaveLength(1)
  })

  it('gives the short reason, and the whole one for ⓘ', () => {
    const r = reasonOf({ action: 'tasks.create', display: taskDisplay(), head: head() })
    expect(r).toEqual({ short: 'Asks first: written through an API token', full: TAINTED })
    // An older server: no short line, so "Asks first" with the whole reason behind it.
    expect(reasonOf({ action: 'tasks.create', display: taskDisplay(), head: null })).toEqual({ short: 'Asks first', full: TAINTED })
    expect(reasonOf({ action: 'tasks.create', display: { ...taskDisplay(), reason: null }, head: head({ reason_short: null }) })).toBeNull()
  })

  it('says what a group asks for, in the plural the count needs', () => {
    expect(groupPhrase('task_new', 'claude-code', 2)).toBe('claude-code wants to add 2 tasks')
    expect(groupPhrase('send', 'Ambrose', 3)).toBe('Ambrose wants to send 3 emails')
    expect(groupPhrase('no-such-kind', 'claude-code', 2)).toBe('claude-code has 2 requests')
  })
})

describe('the card', () => {
  const ref: ApprovalRef = {
    id: 7, action: 'tasks.create', title: 'x', status: 'pending', display: taskDisplay(), outcome: null, head: head(),
    also_covers: { session: 1, always: 1, always_derived: 1 }, grants: true,
  }
  const html = renderToStaticMarkup(createElement(ApprovalCard, { a: ref, onStatus: () => undefined, onDecided: () => undefined, requester: 'claude-code' }))

  it('draws the type label, who asked, the clamped heading, the facts and the short reason', () => {
    expect(html).toContain('class="ap-kind">New task<')
    expect(html).toContain('claude-code')
    expect(html).toMatch(/class="ap-heading clamp"[^>]*>Research workshop: dry run/)
    expect(html).toContain('Mon 30 Nov, 09:00')
    expect(html).toContain('Asks first: written through an API token')
    // The whole reason waits behind ⓘ; it is not on the card until asked for.
    expect(html).not.toContain(TAINTED)
    expect(html).toContain('aria-label="Why it asks"')
  })

  it('has one row of actions: Allow once, Deny, the grants menu with its count, and ⋮', () => {
    const row = html.slice(html.indexOf('class="ap-buttons"'))
    const order = ['Allow once', 'Deny', '+1', 'More choices'].map((s) => row.indexOf(s))
    expect(order.every((i) => i >= 0)).toBe(true)
    expect([...order].sort((a, b) => a - b)).toEqual(order)
    expect(row).toContain('aria-label="More ways to allow (also approves 1 other waiting)"')
    // Ask moved into ⋮; the grants into their menu.
    expect(row).not.toContain('Allow for this chat')
    expect(html).not.toContain('Ask Ambrose<')
  })

  it('shows "more" only when the heading is clamped', () => {
    expect(isClamped({ scrollHeight: 60, clientHeight: 40 } as HTMLElement)).toBe(true)
    expect(isClamped({ scrollHeight: 40, clientHeight: 40 } as HTMLElement)).toBe(false)
    // Before it is measured the card shows no toggle.
    expect(html).not.toContain('ap-heading-more')
  })
})

describe('groups (Allow all, Deny all)', () => {
  it('groups consecutive waiting requests of one kind from one requester', () => {
    const shown = [
      pa(9), pa(8),
      pa(7, { head: head({ kind: 'event_new' }), action: 'calendar.create' }),
      pa(6), pa(5, { subject: 'token:5', actor: 'hermes' }), pa(4, { subject: 'token:5', actor: 'hermes' }),
      pa(3, { status: 'approved' }), pa(2), pa(1),
    ]
    const s = segments(shown, requesterKey)
    expect(s.map((x) => [x.group, x.items.map((p) => p.id)])).toEqual([
      [true, [9, 8]], [false, [7]], [false, [6]], [true, [5, 4]], [false, [3]], [true, [2, 1]],
    ])
  })

  it('keeps chats apart, and never groups a review or a cleanup plan', () => {
    const s = segments([
      pa(4, { subject: 'assistant', chat_session_id: 'c1' }), pa(3, { subject: 'assistant', chat_session_id: 'c2' }),
      pa(2, { head: head({ kind: 'review' }) }), pa(1, { head: head({ kind: 'review' }) }),
    ], requesterKey)
    expect(s.every((x) => !x.group)).toBe(true)
  })
})
