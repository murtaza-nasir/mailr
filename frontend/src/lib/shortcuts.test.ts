// ACT-SURFACES: web.ask
import { describe, expect, it } from 'vitest'
import {
  bindingsFor, displayKeys, feedKey, isInteractiveTarget, keyBelongsToTarget, keyStaysInSurface, keyToken, registerBindings, scopeForPath,
  setShortcutHandlers, shadowedGlobal, shortcutHandler, type Binding, type KeyLike, type MatchState, type ShortcutScope,
  type TargetLike,
} from './shortcuts'

function run(keys: { key: string; ctrlKey?: boolean }[]) {
  let state: MatchState = { pending: [] }
  const actions: (string | null)[] = []
  for (const k of keys) {
    const r = feedKey(state, k)
    state = r.state
    actions.push(r.action)
  }
  return { actions, state }
}

describe('shortcuts', () => {
  it('single keys', () => {
    expect(run([{ key: 'c' }]).actions).toEqual(['compose'])
    expect(run([{ key: '#' }]).actions).toEqual(['delete'])
    expect(run([{ key: 'I', shiftKey: true } as { key: string }]).actions).toEqual(['markRead'])
    expect(run([{ key: 'Escape' }]).actions).toEqual(['escape'])
  })
  it('sequences', () => {
    expect(run([{ key: 'g' }, { key: 'i' }]).actions).toEqual([null, 'goInbox'])
    expect(run([{ key: '*' }, { key: 'u' }]).actions).toEqual([null, 'selectUnread'])
  })
  it('* x selects the section; * a and x keep their meaning', () => {
    expect(run([{ key: '*' }, { key: 'x' }]).actions).toEqual([null, 'selectSection'])
    expect(run([{ key: '*' }, { key: 'a' }]).actions).toEqual([null, 'selectAll'])
    expect(run([{ key: 'x' }]).actions).toEqual(['select'])
    expect(bindingsFor('mail').filter((b) => b.keys === '* x')).toHaveLength(1)
  })
  it('broken sequence retries the key alone', () => {
    expect(run([{ key: 'g' }, { key: 'e' }]).actions).toEqual([null, 'archive'])
  })
  it('modifiers', () => {
    expect(run([{ key: 'Enter', ctrlKey: true }]).actions).toEqual(['send'])
    expect(keyToken({ key: 'a', altKey: true })).toBeNull()
    expect(keyToken({ key: 'Shift' })).toBeNull()
  })
  it('unknown keys are not handled', () => {
    const r = feedKey({ pending: [] }, { key: 'q' })
    expect(r.handled).toBe(false)
  })
})

describe('two-key sequences', () => {
  it('g k goes to Tasks (not "newer")', () => {
    let st: MatchState = { pending: [] }
    const a = feedKey(st, { key: 'g' })
    st = a.state
    expect(a.action).toBeNull()
    const b = feedKey(st, { key: 'k' })
    expect(b.action).toBe('goTasks')
  })
  it('] archives and moves on', () => {
    expect(feedKey({ pending: [] }, { key: ']' }).action).toBe('archiveNext')
  })
})

describe('scopes', () => {
  const feed = (scope: ShortcutScope, keys: KeyLike[]) => {
    let st: MatchState = { pending: [] }
    const out: { action: string | null; arg?: string }[] = []
    for (const k of keys) {
      const r = feedKey(st, k, scope)
      st = r.state
      out.push({ action: r.action, arg: r.arg })
    }
    return out
  }
  it('the same key means each app\'s own action', () => {
    expect(feedKey({ pending: [] }, { key: 'c' }, 'mail').action).toBe('compose')
    expect(feedKey({ pending: [] }, { key: 'c' }, 'calendar').action).toBe('calendar.create')
    expect(feedKey({ pending: [] }, { key: 'c' }, 'tasks').action).toBe('tasks.quickAdd')
    expect(feedKey({ pending: [] }, { key: 'j' }, 'calendar').action).toBe('calendar.next')
    // The arrows move between events; Tab is never a calendar key.
    expect(feedKey({ pending: [] }, { key: 'ArrowRight' }, 'calendar').action).toBe('calendar.nextEvent')
    expect(feedKey({ pending: [] }, { key: 'ArrowLeft' }, 'calendar').action).toBe('calendar.prevEvent')
    expect(feedKey({ pending: [] }, { key: 'ArrowDown' }, 'calendar').action).toBe('calendar.eventDown')
    expect(feedKey({ pending: [] }, { key: 'ArrowUp' }, 'calendar').action).toBe('calendar.eventUp')
    expect(bindingsFor('calendar').some((b) => b.keys === 'tab' || b.keys === 'shift+tab')).toBe(false)
    expect(feedKey({ pending: [] }, { key: 'k' }, 'tasks').action).toBe('tasks.prev')
    expect(feedKey({ pending: [] }, { key: 'x' }, 'tasks').action).toBe('tasks.select')
    expect(feedKey({ pending: [] }, { key: 'x' }, 'calendar').action).toBe('calendar.viewNDays')
    expect(feedKey({ pending: [] }, { key: '#' }, 'calendar').action).toBe('calendar.delete')
    expect(feedKey({ pending: [] }, { key: 'e' }, 'mail').action).toBe('archive')
  })
  it('global keys work everywhere: g c, g k, g i, ?', () => {
    for (const scope of ['mail', 'calendar', 'tasks', 'global'] as const) {
      expect(feed(scope, [{ key: 'g' }, { key: 'c' }]).map((r) => r.action)).toEqual([null, 'goCalendar'])
      expect(feed(scope, [{ key: 'g' }, { key: 'k' }]).map((r) => r.action)).toEqual([null, 'goTasks'])
      expect(feed(scope, [{ key: 'g' }, { key: 'i' }]).map((r) => r.action)).toEqual([null, 'goInbox'])
      expect(feedKey({ pending: [] }, { key: '?' }, scope).action).toBe('help')
    }
  })
  it('mail-only keys stay in mail', () => {
    expect(feed('tasks', [{ key: 'g' }, { key: 't' }]).map((r) => r.action)).toEqual([null, 'tasks.goToday'])
    expect(feed('mail', [{ key: 'g' }, { key: 't' }]).map((r) => r.action)).toEqual([null, 'goSent'])
    expect(feedKey({ pending: [] }, { key: 'I', shiftKey: true }, 'calendar').action).toBeNull()
  })
  it('the calendar\'s "g then a date" yields the first character', () => {
    expect(feed('calendar', [{ key: 'g' }, { key: '1' }])).toEqual([{ action: null, arg: undefined }, { action: 'calendar.goToDate', arg: '1' }])
    expect(feed('calendar', [{ key: 'g' }, { key: 'd' }])[1]).toEqual({ action: 'calendar.goToDate', arg: 'd' })
    // An exact global key beats the wildcard.
    expect(feed('calendar', [{ key: 'g' }, { key: 'c' }])[1].action).toBe('goCalendar')
  })
  it('named keys with Shift and Alt', () => {
    expect(feedKey({ pending: [] }, { key: 'Tab' }, 'tasks').action).toBe('tasks.indent')
    expect(feedKey({ pending: [] }, { key: 'Tab', shiftKey: true }, 'tasks').action).toBe('tasks.outdent')
    expect(feedKey({ pending: [] }, { key: 'ArrowUp', altKey: true }, 'tasks').action).toBe('tasks.moveUp')
    expect(feedKey({ pending: [] }, { key: ' ' }, 'tasks').action).toBe('tasks.complete')
    expect(feedKey({ pending: [] }, { key: 'd', ctrlKey: true }, 'calendar').action).toBe('calendar.duplicate')
    const pri = feedKey({ pending: [] }, { key: '2' }, 'tasks')
    expect([pri.action, pri.arg]).toEqual(['tasks.priority', '2'])
    // Alt with a character is AltGr typing, never a shortcut.
    expect(keyToken({ key: 'ł', altKey: true })).toBeNull()
  })
  it('apps attach handlers; Esc falls back to the global binding it shadows', () => {
    const calls: string[] = []
    const off = setShortcutHandlers('calendar', () => ({ 'calendar.today': () => void calls.push('today') }))
    shortcutHandler('calendar', 'calendar.today')?.()
    expect(calls).toEqual(['today'])
    expect(shortcutHandler('calendar', 'calendar.create')).toBeUndefined()
    off()
    expect(shortcutHandler('calendar', 'calendar.today')).toBeUndefined()
    const esc = feedKey({ pending: [] }, { key: 'Escape' }, 'calendar')
    expect(esc.action).toBe('calendar.escape')
    expect(shadowedGlobal(esc.keys)?.action).toBe('escape')
  })
  it('apps can add keys at run time', () => {
    const b: Binding = { keys: 'q', action: 'calendar.sync', description: 'Sync', group: 'Application', scope: 'calendar' }
    const off = registerBindings([b])
    expect(feedKey({ pending: [] }, { key: 'q' }, 'calendar').action).toBe('calendar.sync')
    expect(feedKey({ pending: [] }, { key: 'q' }, 'mail').action).toBeNull()
    off()
    expect(feedKey({ pending: [] }, { key: 'q' }, 'calendar').action).toBeNull()
  })
  it('help lists the app\'s keys first, then the global ones', () => {
    const cal = bindingsFor('calendar')
    expect(cal[0].scope).toBe('calendar')
    expect(cal.some((b) => b.action === 'compose')).toBe(false)
    expect(cal.some((b) => b.action === 'help')).toBe(true)
    expect(cal.some((b) => b.action === 'escape')).toBe(false) // shadowed by calendar.escape
    expect(scopeForPath('/calendar/week/2026-10-01')).toBe('calendar')
    expect(scopeForPath('/settings/general')).toBe('settings')
    expect(displayKeys('g _')).toBe('g then a date')
    expect(displayKeys('alt+arrowup')).toBe('Alt + ↑')
    expect(displayKeys('ctrl+enter')).toBe('Ctrl + Enter')
    expect(displayKeys('+')).toBe('+')
  })
  it('Shift+A asks about what is open in every app (agent.ask_about); no app key shadows it', () => {
    for (const scope of ['mail', 'calendar', 'tasks', 'recordings'] as const) {
      expect(feedKey({ pending: [] }, { key: 'A', shiftKey: true }, scope).action).toBe('askAbout')
    }
    // Plain a stays reply all in mail and Schedule in the calendar.
    expect(feedKey({ pending: [] }, { key: 'a' }, 'mail').action).toBe('replyAll')
    expect(feedKey({ pending: [] }, { key: 'a' }, 'calendar').action).toBe('calendar.viewSchedule')
    expect(displayKeys('A')).toBe('Shift + a')
  })

  it('a letter takes its case from Shift, not Caps Lock', () => {
    // Caps Lock on: r gives "R" without Shift, Shift+r gives "r".
    expect(keyToken({ key: 'R', shiftKey: false })).toBe('r')
    expect(keyToken({ key: 'A' })).toBe('a')
    expect(keyToken({ key: 'r', shiftKey: true })).toBe('R')
    expect(feedKey({ pending: [] }, { key: 'R', shiftKey: false }, 'mail').action).toBe('reply')
    expect(feedKey({ pending: [] }, { key: 'A', shiftKey: false }, 'mail').action).toBe('replyAll')
    expect(feedKey({ pending: [] }, { key: 'r', shiftKey: true }, 'mail').action).toBe('rerunAssistant')
    // Characters that are not letters keep their key: # and ? are typed with Shift.
    expect(keyToken({ key: '#', shiftKey: true })).toBe('#')
    expect(keyToken({ key: '?', shiftKey: true })).toBe('?')
  })
})

describe('routes and scopes', () => {
  it('mail keys work only in mail; Settings and other pages get the global keys alone', () => {
    expect(scopeForPath('/mail/inbox')).toBe('mail')
    expect(scopeForPath('/mail/inbox/cK7fQ2mXb9')).toBe('mail')
    expect(scopeForPath('/')).toBe('mail')
    expect(scopeForPath('/tasks/today')).toBe('tasks')
    expect(scopeForPath('/recordings')).toBe('recordings')
    expect(scopeForPath('/settings/general')).toBe('settings')
    expect(scopeForPath('/settings')).toBe('settings')
    expect(scopeForPath('/approvals/aK7fQ2mXb9')).toBe('settings')
    const settings = bindingsFor('settings')
    expect(settings.every((b) => (b.scope ?? 'mail') === 'global')).toBe(true)
    for (const key of ['#', 'e', '!', 'm', 'x', 's', 'I', 'U', 'z', 'l', 'v', 'b', '[', ']', 'j', 'k', 'o']) {
      const shiftKey = key === 'I' || key === 'U'
      expect(feedKey({ pending: [] }, { key, shiftKey }, 'settings').action).toBeNull()
    }
    expect(feedKey({ pending: [] }, { key: 'Enter' }, 'settings').action).toBeNull()
    expect(feedKey({ pending: [] }, { key: '*' }, 'settings').handled).toBe(false)
    // The global keys still work there.
    expect(run2('settings', ['g', 'i'])).toBe('goInbox')
    expect(feedKey({ pending: [] }, { key: '?' }, 'settings').action).toBe('help')
    expect(feedKey({ pending: [] }, { key: ',', ctrlKey: true }, 'settings').action).toBe('settings')
    expect(feedKey({ pending: [] }, { key: 'Escape' }, 'settings').action).toBe('escape')
  })
})

function run2(scope: ShortcutScope, keys: string[]) {
  let state: MatchState = { pending: [] }
  let last: string | null = null
  for (const key of keys) {
    const r = feedKey(state, { key }, scope)
    state = r.state
    last = r.action
  }
  return last
}

// A stand-in for a focused DOM element.
function el(tag: string, attrs: Record<string, string> = {}, editable = false): TargetLike {
  return {
    tagName: tag.toUpperCase(),
    isContentEditable: editable,
    getAttribute: (n) => attrs[n] ?? null,
    hasAttribute: (n) => n in attrs,
  }
}

describe('a focused control keeps its own keys', () => {
  const controls: [string, TargetLike][] = [
    ['link', el('a', { href: '/mail/starred' })],
    ['button', el('button')],
    ['role=button', el('div', { role: 'button', tabindex: '0' })],
    ['role=menuitem', el('div', { role: 'menuitem' })],
    ['role=checkbox', el('span', { role: 'checkbox' })],
    ['role=tab', el('div', { role: 'tab' })],
    ['role=option', el('li', { role: 'option' })],
    ['summary', el('summary')],
    ['select', el('select')],
    ['input', el('input')],
    ['textarea', el('textarea')],
    ['contenteditable', el('div', {}, true)],
  ]
  it.each(controls)('%s: Enter, Space, Tab, arrows and Delete are the control\'s', (_name, t) => {
    expect(isInteractiveTarget(t)).toBe(true)
    for (const e of [
      { key: 'Enter' }, { key: ' ' }, { key: 'Tab' }, { key: 'Tab', shiftKey: true }, { key: 'ArrowDown' },
      { key: 'ArrowUp', altKey: true }, { key: 'Delete' }, { key: 'Backspace' }, { key: 'Home' },
    ] as KeyLike[]) {
      expect(keyBelongsToTarget(t, e)).toBe(true)
    }
  })
  it('reads a real KeyboardEvent, whose key is a prototype getter', () => {
    class FakeKeyboardEvent {
      #key: string
      constructor(key: string) { this.#key = key }
      get key() { return this.#key }
      get ctrlKey() { return false }
      get metaKey() { return false }
      get altKey() { return false }
      get shiftKey() { return false }
    }
    expect(keyBelongsToTarget(el('button'), new FakeKeyboardEvent('ArrowRight'))).toBe(true)
    expect(keyBelongsToTarget(el('button'), new FakeKeyboardEvent('j'))).toBe(false)
  })
  it('characters, Escape and Ctrl combinations stay shortcuts on a control', () => {
    const b = el('button')
    for (const e of [{ key: '#' }, { key: 'e' }, { key: 'j' }, { key: 'Escape' }, { key: 'Enter', ctrlKey: true },
      { key: ',', ctrlKey: true }, { key: 'd', ctrlKey: true }] as KeyLike[]) {
      expect(keyBelongsToTarget(b, e)).toBe(false)
    }
  })
  it('the page, a list row and a marked surface leave every key to the shortcuts', () => {
    const others: (TargetLike | null)[] = [
      null,
      el('body'),
      el('div', { role: 'row', tabindex: '0' }), // a mail row: Enter opens it
      el('div', { role: 'grid' }),
      el('a'), // no href: not a link
      el('div', { role: 'button', 'data-shortcuts': '' }), // a calendar event: arrows to the next, Delete
      el('div', {}, false),
    ]
    for (const t of others) {
      for (const key of ['Enter', ' ', 'Tab', 'Delete', 'ArrowDown']) expect(keyBelongsToTarget(t, { key })).toBe(false)
    }
  })
  it('the tasks keys Tab and Space are left to a control in the pane', () => {
    const btn = el('button')
    expect(feedKey({ pending: [] }, { key: 'Tab' }, 'tasks').action).toBe('tasks.indent')
    expect(feedKey({ pending: [] }, { key: ' ' }, 'tasks').action).toBe('tasks.complete')
    expect(keyBelongsToTarget(btn, { key: 'Tab' })).toBe(true)
    expect(keyBelongsToTarget(btn, { key: 'Tab', shiftKey: true })).toBe(true)
    expect(keyBelongsToTarget(btn, { key: ' ' })).toBe(true)
  })
})

describe('help-only keys', () => {
  it('list the palette and the formatting keys, and no key a scope binds', async () => {
    const { HELP_ONLY, bindingsFor } = await import('./shortcuts')
    expect(HELP_ONLY.map((h) => h.keys)).toContain('ctrl+k')
    expect(HELP_ONLY.filter((h) => h.group === 'Formatting').length).toBeGreaterThanOrEqual(3)
    for (const scope of ['mail', 'calendar', 'tasks', 'recordings', 'settings'] as const) {
      const bound = new Set(bindingsFor(scope).map((b) => b.keys))
      expect(HELP_ONLY.filter((h) => bound.has(h.keys)).map((h) => `${scope} ${h.keys}`)).toEqual([])
    }
  })
})

describe('keyStaysInSurface', () => {
  const inside = (sel: string | null) => ({ tagName: 'BUTTON', closest: (q: string) => (sel && q.includes(sel) ? {} : null) })
  it('letters inside a compose window, the side panel or a non-modal dialog are not app keys', () => {
    for (const k of ['c', 'e', '#', 'C']) {
      expect(keyStaysInSurface(inside('.compose-win'), { key: k } as KeyLike)).toBe(true)
      expect(keyStaysInSurface(inside('.side-panel'), { key: k } as KeyLike)).toBe(true)
    }
  })
  it('Ctrl, Meta and Alt combinations and keys outside stay shortcuts', () => {
    expect(keyStaysInSurface(inside('.compose-win'), { key: 'Enter', ctrlKey: true } as KeyLike)).toBe(false)
    expect(keyStaysInSurface(inside('.compose-win'), { key: '.', metaKey: true } as KeyLike)).toBe(false)
    expect(keyStaysInSurface(inside(null), { key: 'c' } as KeyLike)).toBe(false)
    expect(keyStaysInSurface(null, { key: 'c' } as KeyLike)).toBe(false)
  })
})
