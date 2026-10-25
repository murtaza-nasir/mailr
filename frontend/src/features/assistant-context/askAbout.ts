// "Ask assistant" from an event detail, the task pane, the tasks selection bar,
// the recording sheet, a reminder or the reader: opens the assistant panel with the record attached.
// The chat sends it with each turn: an event, one task or a Speakr
// recording at the top of `context` (`focused_event`, `focused_task`, `focused_recording`), an
// approval card as `focused_approval` {id} (only the owner's session sees it), a note with its selected
// items as `focused_note` {id, items}, a conversation as `conversation_id`; the server keeps it on the
// chat session (agent/screen.py).
// Several tasks go as `ui.tasks.selected`. A new record starts a new chat. A local-only recording
// reaches the model by its id only.
//
// The ask capture (features/ask) hands its words over through `pending`: the panel
// starts a new chat about the record and sends them there, so replies and approvals work as usual.

import { create } from 'zustand'
import { useUI } from '../../stores/ui'
import { t } from '../../i18n'

export type AskAbout =
  | { kind: 'event'; uid: string; occurrence: string | null; title: string }
  | { kind: 'task'; uid: string; title: string }
  | { kind: 'tasks'; uids: string[] }
  | { kind: 'recording'; cid: number; rid: number; title: string }
  | { kind: 'conversation'; id: number; title: string }
  /** An approval card (web.approval): sent as `focused_approval` {id}; the assistant explains, the card decides. */
  | { kind: 'approval'; id: number; title: string }
  /** A note or list (web.note "Ask assistant"), with the items selected in it: `focused_note` {id, items}. */
  | { kind: 'note'; id: string; title: string; list: boolean; items?: string[] }

/** The tab's chat session key (useAssistant.ts keeps the chat id there). */
export const ASSISTANT_SESSION_KEY = 'mailr.assistant.session'

export interface AskPending {
  text: string
  /** true: send the words; false: put them in the message box (Open chat). */
  send: boolean
}

interface AskState {
  about: AskAbout | null
  /** Bumped by every ask, so the panel starts a new chat even for the same record. */
  stamp: number
  /** The last stamp the panel acted on (kept here, so a panel that mounts later still acts). */
  handled: number
  pending: AskPending | null
  /** A stored chat to show (a memory's "From a chat" link), and the user message to bring into view. */
  show: { session: string; message: number | null; stamp: number } | null
  /** The last `show` stamp the panel acted on (here, so a panel that mounts again does not repeat it). */
  shown: number
  /** The message the panel marks in the chat it opened for `show`. */
  found: number | null
  ask: (about: AskAbout | null, pending?: AskPending) => void
  /** Open the panel on a stored chat at one of its turns (the panel acts on `show`). */
  openChat: (session: string, message: number | null) => void
  clear: () => void
}

export const useAskAbout = create<AskState>((set) => ({
  about: null,
  stamp: 0,
  handled: 0,
  pending: null,
  show: null,
  shown: 0,
  found: null,
  ask: (about, pending) => {
    // A closed panel would reopen the last chat on mount; a new ask is a new chat.
    if (useUI.getState().panel !== 'agent') {
      try {
        sessionStorage.removeItem(ASSISTANT_SESSION_KEY)
      } catch {
        /* storage blocked */
      }
    }
    set((s) => ({ about, stamp: s.stamp + 1, pending: pending?.text.trim() ? pending : null, found: null }))
    useUI.getState().setPanel('agent')
  },
  openChat: (session, message) => {
    // A panel that mounts now reopens the tab's chat: make it this one.
    try {
      sessionStorage.setItem(ASSISTANT_SESSION_KEY, session)
    } catch {
      /* storage blocked: the panel still acts on `show` */
    }
    set((s) => ({ show: { session, message, stamp: Math.max(s.show?.stamp ?? 0, s.shown) + 1 }, found: null }))
    useUI.getState().setPanel('agent')
  },
  clear: () => set({ about: null }),
}))

/** The attached record's keys for a turn's `context` (top level) and `ui.tasks`. */
export function askContext(about: AskAbout | null = useAskAbout.getState().about): {
  top: Record<string, unknown>; tasks?: Record<string, unknown>
} {
  if (!about) return { top: {} }
  if (about.kind === 'event') return { top: { focused_event: { uid: about.uid, occurrence: about.occurrence } } }
  if (about.kind === 'task') return { top: { focused_task: about.uid } }
  // ACT recording.ask @web.recording
  if (about.kind === 'recording') return { top: { focused_recording: { connector_id: about.cid, recording_id: about.rid } } }
  if (about.kind === 'conversation') return { top: { conversation_id: about.id } }
  // ACT agent.ask_about @web.panel (an approval card)
  if (about.kind === 'approval') return { top: { focused_approval: { id: about.id } } }
  // ACT agent.ask_about @web.note
  if (about.kind === 'note') return { top: { focused_note: { id: about.id, ...(about.items?.length ? { items: about.items.slice(0, 50) } : {}) } } }
  return { top: {}, tasks: { selected: about.uids.slice(0, 50) } }
}

/** The chip's label in the chat. */
export function askLabel(about: AskAbout): string {
  if (about.kind === 'tasks') return t('assistantContext.askAbout.selectedTasks', { count: about.uids.length })
  if (about.kind === 'note') {
    const name = about.title || (about.list ? t('assistantContext.askAbout.untitledList') : t('assistantContext.askAbout.untitledNote'))
    const n = about.items?.length ?? 0
    return n ? t('assistantContext.askAbout.itemsOf', { count: n, itemName: name }) : name
  }
  if (about.title) return about.title
  return about.kind === 'event' ? t('common.noTitle') : about.kind === 'recording' ? t('assistantContext.askAbout.untitledRecording') : about.kind === 'conversation' ? t('common.noSubject') : about.kind === 'approval' ? t('assistantContext.askAbout.thisApproval') : t('assistantContext.askAbout.untitledTask')
}

/** Suggestions for the empty chat about the record. */
export function askSuggestions(about: AskAbout): string[] {
  if (about.kind === 'event') return [t('assistantContext.askAbout.prepareAnAgendaForThis'), t('assistantContext.askAbout.findATimeWithThese'), t('assistantContext.askAbout.whatShouldIReadBefore')]
  if (about.kind === 'task') return [t('assistantContext.askAbout.splitThisTaskIntoSteps'), t('assistantContext.askAbout.whenShouldIDoThis'), t('assistantContext.askAbout.draftTheEmailThisTask')]
  if (about.kind === 'recording') return [t('assistantContext.askAbout.whatDidWeDecideIn'), t('assistantContext.askAbout.whatDidIAgreeTo'), t('assistantContext.askAbout.draftAFollowUpEmail')]
  if (about.kind === 'note') return about.items?.length
    ? [t('assistantContext.askAbout.turnTheseIntoTasks'), t('assistantContext.askAbout.checkTheseOff'), t('assistantContext.askAbout.moveTheseToANew')]
    : about.list ? [t('assistantContext.askAbout.sortThisListByShop'), t('assistantContext.askAbout.turnTheUncheckedItemsInto'), t('assistantContext.askAbout.whatIsStillOnThis')] : [t('assistantContext.askAbout.makeThisAChecklist'), t('assistantContext.askAbout.summarizeThisNote'), t('assistantContext.askAbout.turnThisIntoATask')]
  if (about.kind === 'approval') return [t('assistantContext.askAbout.whyDoesThisNeedApproval'), t('assistantContext.askAbout.whatWouldAlwaysAllowLet'), t('assistantContext.askAbout.whatHappensIfIDeny')]
  if (about.kind === 'conversation') return [t('assistantContext.askAbout.summarizeThisConversationAndList'), t('assistantContext.askAbout.draftAReplyThatAccepts'), t('assistantContext.askAbout.whatHaveIDiscussedWith')]
  return [t('assistantContext.askAbout.sortTheseTasksByPriority'), t('assistantContext.askAbout.spreadTheseOverNextWeek'), t('assistantContext.askAbout.whichOfTheseCanWait')]
}
