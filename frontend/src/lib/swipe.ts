// Swipe actions on mail rows (touch screens), per device as on Android (Settings → Mail → Reading → Swipe actions):
// the choices and their defaults, and what a swipe does in the list it is made in. Pure, tested.
import type { ActionName, ConversationSummary } from '../api/types'
import { t } from '../i18n'
import { baseView } from './views'
import type { ArchiveSlot } from './rowArchive'

export type SwipeChoice = 'archive' | 'trash' | 'read' | 'snooze' | 'star' | 'none'

export const SWIPE_CHOICES: { id: SwipeChoice; label: string }[] = [
  { id: 'archive', label: t('common.archive') },
  { id: 'trash', label: t('common.delete2') },
  { id: 'read', label: t('lib.swipe.markReadUnread') },
  { id: 'snooze', label: t('common.snooze') },
  { id: 'star', label: t('common.star') },
  { id: 'none', label: t('common.none') },
]

/** Swipe right (start to end) and swipe left (end to start). In a right-to-left page start to end
 *  is a swipe to the left: `logicalSwipe` turns the way a finger moved into one of these. */
export interface SwipePrefs {
  right: SwipeChoice
  left: SwipeChoice
}

/** The setting a swipe on the screen uses: the same as its direction left to right, the other one
 *  right to left (a swipe toward the end does the same thing in both). */
export function logicalSwipe(screen: 'right' | 'left', rtl: boolean): keyof SwipePrefs {
  return rtl ? (screen === 'right' ? 'left' : 'right') : screen
}

export const DEFAULT_SWIPES: SwipePrefs = { right: 'archive', left: 'trash' }
const KEY = 'mailr.swipe'

function isChoice(v: unknown): v is SwipeChoice {
  return SWIPE_CHOICES.some((c) => c.id === v)
}

export function readSwipes(store: Pick<Storage, 'getItem'> | null = local()): SwipePrefs {
  try {
    const v = JSON.parse(store?.getItem(KEY) ?? 'null') as Partial<SwipePrefs> | null
    return { right: isChoice(v?.right) ? v.right : DEFAULT_SWIPES.right, left: isChoice(v?.left) ? v.left : DEFAULT_SWIPES.left }
  } catch {
    return DEFAULT_SWIPES
  }
}

export function writeSwipes(p: SwipePrefs, store: Pick<Storage, 'setItem'> | null = local()): void {
  try {
    store?.setItem(KEY, JSON.stringify(p))
  } catch {
    /* private mode */
  }
}

function local(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

export interface SwipeEffect {
  /** The server action, or 'snooze' (the snooze sheet opens), or null (the swipe does nothing here). */
  action: ActionName | 'snooze' | null
  /** What the swipe's background says. */
  label: string
  /** The background's tone: archive (green-ish), danger (red), neutral. */
  tone: 'archive' | 'danger' | 'neutral' | 'snooze'
  /** The row leaves this list. */
  removes: boolean
}

/** What a swipe does on this row in this list (Android's MailboxViewModel.swipe): in Trash the
 *  archive swipe restores, in Spam it is Not spam; deleting from Trash is not a swipe (it is for
 *  ever, and asks first in the menu). `slot` is the row's archiveSlot (lib/rowArchive.ts): a row
 *  that is not in the Inbox swipes to Move to Inbox, and stays in the list. */
export function swipeEffect(choice: SwipeChoice, c: Pick<ConversationSummary, 'unread_count' | 'starred'>, view: string, slot: ArchiveSlot = 'archive'): SwipeEffect {
  const none: SwipeEffect = { action: null, label: '', tone: 'neutral', removes: false }
  switch (choice) {
    case 'archive':
      if (baseView(view) === 'trash') return { action: 'untrash', label: t('common.restore'), tone: 'archive', removes: true }
      if (baseView(view) === 'spam') return { action: 'not_spam', label: t('common.notSpam'), tone: 'archive', removes: true }
      if (baseView(view) === 'archive') return { action: 'inbox', label: t('common.moveToInbox'), tone: 'archive', removes: true }
      if (slot === 'inbox') return { action: 'inbox', label: t('common.moveToInbox'), tone: 'archive', removes: false }
      return { action: 'archive', label: t('common.archive'), tone: 'archive', removes: baseView(view) !== 'all' && baseView(view) !== 'sent' && baseView(view) !== 'drafts' }
    case 'trash':
      if (baseView(view) === 'trash') return none
      return { action: 'trash', label: t('common.delete2'), tone: 'danger', removes: true }
    case 'read':
      return c.unread_count > 0
        ? { action: 'read', label: t('common.markRead'), tone: 'neutral', removes: false }
        : { action: 'unread', label: t('lib.swipe.markUnread'), tone: 'neutral', removes: false }
    case 'star':
      return c.starred
        ? { action: 'unstar', label: t('common.removeStar'), tone: 'neutral', removes: baseView(view) === 'starred' }
        : { action: 'star', label: t('common.star'), tone: 'neutral', removes: false }
    case 'snooze':
      if (baseView(view) === 'trash' || baseView(view) === 'spam') return none
      return { action: 'snooze', label: t('common.snooze'), tone: 'snooze', removes: false }
    default:
      return none
  }
}
