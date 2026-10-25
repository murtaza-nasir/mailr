// The Calendar app's transient UI state: the focused event and slot, the open quick create,
// editor and dialogs, and a keyboard drag in progress. Server data stays in TanStack Query; the
// saved view (prefs.ts) stays on the device.

import { create } from 'zustand'
import type { ISODate } from '../../lib/dates'
import type { GridSpan } from './geometry'
import type { ApiEvent, CalEvent, EventWrite } from './types'

export interface QuickState {
  /** Instants for a timed slot; ISO dates (end exclusive) for all-day. */
  start: Date | ISODate | null
  end: Date | ISODate | null
  allDay: boolean
  anchor: DOMRect | null
}

/** What the full editor opens with (an existing event, or a new one from quick create). */
export interface EditorInit {
  mode: 'new' | 'edit' | 'duplicate'
  event?: CalEvent
  /** Values carried over from quick create or the slot. */
  seed?: Partial<{
    summary: string
    start: Date | ISODate
    end: Date | ISODate
    allDay: boolean
    location: string
    rrule: string | null
    calendar: string
    guests: { email: string; name?: string }[]
    reminders: number[]
    video: boolean
    /** The description to start with (a link to the email it came from). */
    description: string
    /** The conversation a new event is linked to (an EventLink; mail.to_event, mail.schedule_meeting). */
    conversationId: number | string | null
    /** The identity that sends the invitations and becomes ORGANIZER. */
    identityId: number | null
  }>
  tab?: 'details' | 'findtime'
  /** Called after a new event is saved (the reader offers a reply on the thread). */
  onSaved?: (ev: ApiEvent, body: EventWrite) => void
}

interface CalUI {
  focus: string | null
  slot: { day: ISODate; min: number } | null
  quick: QuickState | null
  editor: EditorInit | null
  detailAnchor: DOMRect | null
  findTime: boolean
  goTo: string | null
  drawer: boolean
  kbdDrag: { key: string; span: GridSpan; resize: boolean } | null
  set: (patch: Partial<Omit<CalUI, 'set'>>) => void
}

export const useCalUI = create<CalUI>((set) => ({
  focus: null,
  slot: null,
  quick: null,
  editor: null,
  detailAnchor: null,
  findTime: false,
  goTo: null,
  drawer: false,
  kbdDrag: null,
  set: (patch) => set(patch),
}))

/** A click on empty calendar space while quick create or an event's floating detail is open only
 *  closes it; the next click creates. Without this, every click meant to dismiss the popover opened
 *  a new one where it landed. The grid's pointerdown suppresses the mousedown Floating listens for,
 *  so the popover is closed here. */
export function dismissOpenPopover(): boolean {
  const ui = useCalUI.getState()
  if (ui.quick) {
    ui.set({ quick: null })
    return true
  }
  const detailClose = document.querySelector<HTMLButtonElement>('.cal-detail-pop .cal-detail-close')
  if (detailClose) {
    detailClose.click()
    return true
  }
  return false
}
