// ACT-SURFACES: web.ask
// agent.ask_about on the web (web.ask): the item becomes structured context, Send and Open chat hand
// the words to the assistant panel and never travel any other way, the words survive closing the
// capture (and a reload) until they are sent, and Shift+A attaches what is on top.
import { beforeEach, describe, expect, it, vi } from 'vitest'

function memoryStorage(): Storage {
  const m = new Map<string, string>()
  return {
    get length() { return m.size },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  }
}

async function load() {
  vi.resetModules()
  const store = await import('./askStore')
  const about = await import('../assistant-context/askAbout')
  const ui = await import('../../stores/ui')
  return { ...store, ...about, useUI: ui.useUI }
}

describe('agent.ask_about: the capture', () => {
  beforeEach(() => {
    vi.stubGlobal('sessionStorage', memoryStorage())
    vi.stubGlobal('localStorage', memoryStorage())
  })

  it('builds structured context for every kind of item', async () => {
    const { askContext } = await load()
    expect(askContext({ kind: 'event', uid: 'e1', occurrence: '2026-10-02T15:00:00Z', title: 'Budget' })).toEqual({ top: { focused_event: { uid: 'e1', occurrence: '2026-10-02T15:00:00Z' } } })
    expect(askContext({ kind: 'task', uid: 't1', title: 'Call Dana' })).toEqual({ top: { focused_task: 't1' } })
    expect(askContext({ kind: 'recording', cid: 2, rid: 9, title: 'Sync' })).toEqual({ top: { focused_recording: { connector_id: 2, recording_id: 9 } } })
    expect(askContext({ kind: 'conversation', id: 42, title: 'Re: Budget' })).toEqual({ top: { conversation_id: 42 } })
  })

  it('a reminder becomes its event or task', async () => {
    const { reminderAbout } = await load()
    expect(reminderAbout({ kind: 'task', uid: 't1', occurrence: '', title: 'Pay rent' })).toEqual({ kind: 'task', uid: 't1', title: 'Pay rent' })
    expect(reminderAbout({ kind: 'event', uid: 'e1', occurrence: '2026-10-02T15:00:00Z', title: 'Standup' }))
      .toEqual({ kind: 'event', uid: 'e1', occurrence: '2026-10-02T15:00:00Z', title: 'Standup' })
    expect(reminderAbout({ kind: 'event', uid: 'e1', occurrence: '', title: 'Standup' })).toMatchObject({ occurrence: null })
  })

  it('Send hands the words to the panel in a new chat about the item, then forgets them', async () => {
    const { useAskCapture, useAskAbout, useUI, openAsk } = await load()
    sessionStorage.setItem('mailr.assistant.session', 'old-chat')
    const about = { kind: 'task' as const, uid: 't1', title: 'Call Dana' }
    openAsk(about)
    expect(useAskCapture.getState()).toMatchObject({ open: true, about })
    useAskCapture.getState().setDraft('  move it to Friday ')
    expect(useAskCapture.getState().send()).toBe(true)
    const a = useAskAbout.getState()
    expect(a.about).toEqual(about)
    expect(a.pending).toEqual({ text: 'move it to Friday', send: true })
    expect(a.stamp).toBe(1)
    expect(useUI.getState().panel).toBe('agent')
    // A closed panel would have reopened the old chat: the ask starts a new one.
    expect(sessionStorage.getItem('mailr.assistant.session')).toBeNull()
    expect(useAskCapture.getState()).toMatchObject({ open: false, draft: '' })
    expect(sessionStorage.getItem('mailr.ask.draft')).toBeNull()
  })

  it('Send with nothing typed does nothing', async () => {
    const { useAskCapture, useAskAbout } = await load()
    useAskCapture.getState().show(null)
    useAskCapture.getState().setDraft('   ')
    expect(useAskCapture.getState().send()).toBe(false)
    expect(useAskAbout.getState().stamp).toBe(0)
    expect(useAskCapture.getState().open).toBe(true)
  })

  it('Open chat puts the words in the message box unsent; a removed chip sends no item', async () => {
    const { useAskCapture, useAskAbout } = await load()
    useAskCapture.getState().show({ kind: 'conversation', id: 7, title: 'Hello' })
    useAskCapture.getState().detach()
    useAskCapture.getState().setDraft('what is this about?')
    useAskCapture.getState().openChat()
    expect(useAskAbout.getState().about).toBeNull()
    expect(useAskAbout.getState().pending).toEqual({ text: 'what is this about?', send: false })
  })

  it('keeps the words when the capture closes, and across a reload of the tab', async () => {
    const first = await load()
    first.useAskCapture.getState().show(null)
    first.useAskCapture.getState().setDraft('remind Dana about the deck')
    first.useAskCapture.getState().close()
    expect(first.useAskCapture.getState().draft).toBe('remind Dana about the deck')
    const again = await load()
    expect(again.useAskCapture.getState().draft).toBe('remind Dana about the deck')
  })

  it('remembers "Send when I stop talking" on this browser', async () => {
    const first = await load()
    expect(first.useAskCapture.getState().autoSend).toBe(false)
    first.useAskCapture.getState().setAutoSend(true)
    const again = await load()
    expect(again.useAskCapture.getState().autoSend).toBe(true)
  })

  it('works without storage', async () => {
    vi.stubGlobal('sessionStorage', undefined)
    vi.stubGlobal('localStorage', undefined)
    const { useAskCapture } = await load()
    useAskCapture.getState().setDraft('still here')
    expect(useAskCapture.getState().draft).toBe('still here')
  })

  it('Shift+A attaches what is on top: the last opened, then what is under it', async () => {
    const { registerAskTarget, currentAskTarget, openAskFromScreen, useAskCapture } = await load()
    expect(currentAskTarget()).toBeNull()
    const offConv = registerAskTarget({ kind: 'conversation', id: 3, title: 'Budget' })
    const offEvent = registerAskTarget({ kind: 'event', uid: 'e1', occurrence: null, title: 'Review' })
    expect(currentAskTarget()).toMatchObject({ kind: 'event' })
    openAskFromScreen()
    expect(useAskCapture.getState().about).toMatchObject({ kind: 'event', uid: 'e1' })
    offEvent()
    expect(currentAskTarget()).toMatchObject({ kind: 'conversation', id: 3 })
    offConv()
    offConv()
    expect(currentAskTarget()).toBeNull()
  })
})
