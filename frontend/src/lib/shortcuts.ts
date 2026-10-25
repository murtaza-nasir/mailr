// Keyboard shortcuts as a pure state machine (unit-tested in shortcuts.test.ts).
//
// Every binding has a scope: 'global' keys work in every app; 'mail', 'calendar', 'tasks',
// 'recordings' and 'notes' keys work only in their app and shadow a global binding with the same keys there (so c, j,
// k, e, x and # mean the app's own action). The apps attach handlers to their actions with
// setShortcutHandlers (features/shortcuts/useAppShortcuts.ts); an app key without a handler
// does nothing and lets the browser have the key. 'settings' (Settings and any other page) has no
// keys of its own: only the global ones.

import { t } from '../i18n'
import { assistantNameNow } from '../features/rail/assistant/assistantName'

export type ShortcutAction =
  | 'compose' | 'search' | 'older' | 'newer' | 'open' | 'backToList' | 'archive' | 'delete'
  | 'spam' | 'reply' | 'replyAll' | 'forward' | 'star' | 'select' | 'label' | 'move' | 'snooze'
  | 'markRead' | 'markUnread' | 'undo' | 'help' | 'escape' | 'send'
  | 'goInbox' | 'goStarred' | 'goSent' | 'goDrafts' | 'goAll' | 'goTasks' | 'goSnoozed' | 'goImportant'
  | 'selectAll' | 'selectNone' | 'selectRead' | 'selectUnread' | 'selectStarred' | 'selectUnstarred' | 'selectSection'
  | 'markImportant' | 'markNotImportant' | 'mute' | 'archivePrev' | 'archiveNext' | 'nextMessage' | 'prevMessage'
  | 'settings' | 'toggleAgent' | 'rerunAssistant' | 'askAbout' | 'goCalendar' | 'goRecordings' | 'goFavorite' | 'goNotes'
  | 'createFrom' | 'quickNote'

export type ShortcutScope = 'global' | 'mail' | 'calendar' | 'tasks' | 'recordings' | 'notes' | 'settings'
/** The app scopes (the ones a route selects). */
export type AppScope = Exclude<ShortcutScope, 'global'>

/** Calendar keys (product spec §3A.7). */
export type CalendarShortcut =
  | 'calendar.today' | 'calendar.viewToday' | 'calendar.viewDay' | 'calendar.viewNDays' | 'calendar.viewWeek' | 'calendar.viewWorkWeek'
  | 'calendar.viewMonth' | 'calendar.viewSchedule' | 'calendar.next' | 'calendar.prev' | 'calendar.create'
  | 'calendar.open' | 'calendar.delete' | 'calendar.duplicate' | 'calendar.findTime' | 'calendar.search'
  | 'calendar.goToDate' | 'calendar.toggleLeft' | 'calendar.nextEvent' | 'calendar.prevEvent'
  | 'calendar.eventUp' | 'calendar.eventDown'
  | 'calendar.rsvpYes' | 'calendar.rsvpMaybe' | 'calendar.rsvpNo' | 'calendar.undo' | 'calendar.sync' | 'calendar.escape'

/** Tasks keys (product spec §3B.9). */
export type TaskShortcut =
  | 'tasks.quickAdd' | 'tasks.next' | 'tasks.prev' | 'tasks.edit' | 'tasks.open' | 'tasks.complete'
  | 'tasks.reschedule' | 'tasks.start' | 'tasks.snooze' | 'tasks.priority' | 'tasks.move' | 'tasks.tags'
  | 'tasks.delete' | 'tasks.select' | 'tasks.selectAll' | 'tasks.selectNone' | 'tasks.indent' | 'tasks.outdent'
  | 'tasks.moveUp' | 'tasks.moveDown' | 'tasks.duplicate' | 'tasks.search' | 'tasks.goToday' | 'tasks.goUpcoming'
  | 'tasks.goAll' | 'tasks.undo' | 'tasks.escape'

/** Recordings keys (web.recordings). */
export type RecordingShortcut =
  | 'recordings.next' | 'recordings.prev' | 'recordings.open' | 'recordings.search' | 'recordings.record'
  | 'recordings.escape'

/** Notes keys (web.notes). */
export type NoteShortcut =
  | 'notes.newNote' | 'notes.newList' | 'notes.next' | 'notes.prev' | 'notes.open' | 'notes.pin' | 'notes.archive'
  | 'notes.labels' | 'notes.delete' | 'notes.select' | 'notes.selectAll' | 'notes.selectNone' | 'notes.moveLeft'
  | 'notes.moveRight' | 'notes.search' | 'notes.goLists' | 'notes.goTables' | 'notes.undo' | 'notes.toggleLeft'
  | 'notes.escape'

export type AppShortcut = CalendarShortcut | TaskShortcut | RecordingShortcut | NoteShortcut
export type AnyShortcut = ShortcutAction | AppShortcut

export type ShortcutGroup =
  | 'Compose & chat' | 'Formatting' | 'Actions' | 'Hot keys' | 'Navigation' | 'Threadlist selection' | 'Application'
  | 'Views' | 'Events' | 'Tasks' | 'Selection' | 'Notes' | 'In an open note'

/** A group's heading in the help, in the chosen language (the group itself is an ID). */
export function shortcutGroupLabel(g: ShortcutGroup): string {
  const keys: Record<ShortcutGroup, string> = {
    'Compose & chat': 'lib.shortcuts.groups.composeChat',
    Formatting: 'lib.shortcuts.groups.formatting',
    Actions: 'lib.shortcuts.groups.actions',
    'Hot keys': 'lib.shortcuts.groups.hotKeys',
    Navigation: 'lib.shortcuts.groups.navigation',
    'Threadlist selection': 'lib.shortcuts.groups.threadlistSelection',
    Application: 'lib.shortcuts.groups.application',
    Views: 'lib.shortcuts.groups.views',
    Events: 'lib.shortcuts.groups.events',
    Tasks: 'lib.shortcuts.groups.tasks',
    Selection: 'lib.shortcuts.groups.selection',
    Notes: 'lib.shortcuts.groups.notes',
    'In an open note': 'lib.shortcuts.groups.inAnOpenNote',
  }
  return t(keys[g])
}

export interface Binding {
  /** Space-separated sequence, e.g. "g i". Shift+i is written "I"; "_" matches any one character. */
  keys: string
  action: AnyShortcut
  description: string
  group: ShortcutGroup
  /** Default 'mail'. */
  scope?: ShortcutScope
  /** Keys as the help shows them, when one row stands for several bindings ("g 1–9"). */
  helpKeys?: string
  /** Left out of the help (another binding's row covers it). */
  noHelp?: boolean
}

export const BINDINGS: Binding[] = [
  { keys: 'c', action: 'compose', description: t('common.compose'), group: 'Compose & chat' },
  { keys: 'r', action: 'reply', description: t('common.reply'), group: 'Compose & chat' },
  { keys: 'a', action: 'replyAll', description: t('common.replyAll'), group: 'Compose & chat' },
  { keys: 'f', action: 'forward', description: t('common.forward'), group: 'Compose & chat' },
  { keys: 'ctrl+enter', action: 'send', description: t('common.send'), group: 'Compose & chat' },
  { keys: '/', action: 'search', description: t('lib.shortcuts.searchMail'), group: 'Navigation' },
  { keys: 'j', action: 'older', description: t('lib.shortcuts.olderConversation'), group: 'Navigation' },
  { keys: 'k', action: 'newer', description: t('lib.shortcuts.newerConversation'), group: 'Navigation' },
  { keys: 'n', action: 'nextMessage', description: t('lib.shortcuts.nextMessage'), group: 'Navigation' },
  { keys: 'p', action: 'prevMessage', description: t('lib.shortcuts.previousMessage'), group: 'Navigation' },
  { keys: 'o', action: 'open', description: t('lib.shortcuts.openConversation'), group: 'Navigation' },
  { keys: 'enter', action: 'open', description: t('lib.shortcuts.openConversation'), group: 'Navigation' },
  { keys: 'u', action: 'backToList', description: t('lib.shortcuts.backToThreadlist'), group: 'Navigation' },
  { keys: 'g i', action: 'goInbox', description: t('lib.shortcuts.goToInbox'), group: 'Hot keys', scope: 'global' },
  { keys: 'g c', action: 'goCalendar', description: t('lib.shortcuts.goToCalendar'), group: 'Hot keys', scope: 'global' },
  { keys: 'g s', action: 'goStarred', description: t('lib.shortcuts.goToStarred'), group: 'Hot keys' },
  { keys: 'g b', action: 'goSnoozed', description: t('lib.shortcuts.goToSnoozed'), group: 'Hot keys' },
  { keys: 'g t', action: 'goSent', description: t('lib.shortcuts.goToSent'), group: 'Hot keys' },
  { keys: 'g d', action: 'goDrafts', description: t('lib.shortcuts.goToDrafts'), group: 'Hot keys' },
  { keys: 'g a', action: 'goAll', description: t('lib.shortcuts.goToAllMail'), group: 'Hot keys' },
  { keys: 'g k', action: 'goTasks', description: t('lib.shortcuts.goToTasks'), group: 'Hot keys', scope: 'global' },
  { keys: 'g n', action: 'goNotes', description: t('lib.shortcuts.goToNotes'), group: 'Hot keys', scope: 'global' },
  { keys: 'g r', action: 'goRecordings', description: t('lib.shortcuts.goToRecordingsWithA'), group: 'Hot keys', scope: 'global' },
  { keys: 'g m', action: 'goImportant', description: t('lib.shortcuts.goToImportant'), group: 'Hot keys' },
  // Favorites in their order; one help row covers the nine keys.
  ...[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n): Binding => ({
    keys: `g ${n}`, action: 'goFavorite', description: t('lib.shortcuts.goToFavorite1To'), group: 'Hot keys',
    ...(n === 1 ? { helpKeys: 'g 1–9' } : { noHelp: true }),
  })),
  { keys: '* a', action: 'selectAll', description: t('common.selectAll'), group: 'Threadlist selection' },
  { keys: '* n', action: 'selectNone', description: t('lib.shortcuts.deselectAll'), group: 'Threadlist selection' },
  { keys: '* r', action: 'selectRead', description: t('lib.shortcuts.selectRead'), group: 'Threadlist selection' },
  { keys: '* u', action: 'selectUnread', description: t('lib.shortcuts.selectUnread'), group: 'Threadlist selection' },
  { keys: '* s', action: 'selectStarred', description: t('lib.shortcuts.selectStarred'), group: 'Threadlist selection' },
  { keys: '* t', action: 'selectUnstarred', description: t('lib.shortcuts.selectUnstarred'), group: 'Threadlist selection' },
  // Inbox sections (web.section): every conversation of the focused row's section, not only the loaded rows.
  { keys: '* x', action: 'selectSection', description: t('lib.shortcuts.selectAllInThisSection'), group: 'Threadlist selection' },
  { keys: 'x', action: 'select', description: t('lib.shortcuts.selectConversation'), group: 'Actions' },
  { keys: 's', action: 'star', description: t('lib.shortcuts.toggleStar'), group: 'Actions' },
  { keys: 'e', action: 'archive', description: t('lib.shortcuts.archiveOrMoveToInbox'), group: 'Actions' },
  { keys: '#', action: 'delete', description: t('common.delete2'), group: 'Actions' },
  { keys: '!', action: 'spam', description: t('lib.shortcuts.reportAsSpam'), group: 'Actions' },
  { keys: 'l', action: 'label', description: t('lib.shortcuts.labelAsOrDragOnto'), group: 'Actions' },
  { keys: 'v', action: 'move', description: t('lib.shortcuts.moveToFolderRemovesFrom'), group: 'Actions' },
  { keys: 'b', action: 'snooze', description: t('common.snooze'), group: 'Actions' },
  { keys: 'm', action: 'mute', description: t('common.mute'), group: 'Actions' },
  { keys: 'T', action: 'createFrom', description: t('lib.shortcuts.createFromThisEmailA'), group: 'Actions' },
  { keys: 'I', action: 'markRead', description: t('common.markAsRead'), group: 'Actions' },
  { keys: 'U', action: 'markUnread', description: t('common.markAsUnread'), group: 'Actions' },
  { keys: '+', action: 'markImportant', description: t('common.markAsImportant'), group: 'Actions' },
  { keys: '=', action: 'markImportant', description: t('common.markAsImportant'), group: 'Actions' },
  { keys: '-', action: 'markNotImportant', description: t('lib.shortcuts.markAsNotImportant'), group: 'Actions' },
  { keys: '[', action: 'archivePrev', description: t('lib.shortcuts.archiveAndPrevious'), group: 'Actions' },
  { keys: ']', action: 'archiveNext', description: t('lib.shortcuts.archiveAndNext'), group: 'Actions' },
  { keys: 'z', action: 'undo', description: t('lib.shortcuts.undoLastAction'), group: 'Actions' },
  { keys: '?', action: 'help', description: t('common.keyboardShortcuts'), group: 'Application', scope: 'global' },
  { keys: 'escape', action: 'escape', description: t('lib.shortcuts.closeClear'), group: 'Application', scope: 'global' },
  { keys: 'ctrl+,', action: 'settings', description: t('common.settings'), group: 'Application', scope: 'global' },
  { keys: 'ctrl+.', action: 'toggleAgent', get description() { return t('lib.shortcuts.showOrHide', { name: assistantNameNow() }) }, group: 'Application', scope: 'global' },
  { keys: 'R', action: 'rerunAssistant', get description() { return t('lib.shortcuts.rerunSLastReply', { name: assistantNameNow() }) }, group: 'Application', scope: 'global' },
  // The ask capture about what is open: a reminder, an event, a task, a recording or the conversation.
  { keys: 'A', action: 'askAbout', get description() { return t('lib.shortcuts.askAboutWhatIsOpen', { name: assistantNameNow() }) }, group: 'Application', scope: 'global' },
  // Quick note from anywhere: the palette opens on "New note: "; "#Shopping Costco: milk" adds to a list.
  { keys: 'N', action: 'quickNote', description: t('lib.shortcuts.newNoteFromAnywhereList'), group: 'Application', scope: 'global' },
]

const cal = (keys: string, action: CalendarShortcut, description: string, group: ShortcutGroup): Binding => ({ keys, action, description, group, scope: 'calendar' })
const tsk = (keys: string, action: TaskShortcut, description: string, group: ShortcutGroup): Binding => ({ keys, action, description, group, scope: 'tasks' })

const rec = (keys: string, action: RecordingShortcut, description: string, group: ShortcutGroup): Binding => ({ keys, action, description, group, scope: 'recordings' })

/** The Recordings app's keys (web.recordings scope). */
export const RECORDING_BINDINGS: Binding[] = [
  rec('j', 'recordings.next', t('lib.shortcuts.nextRecording'), 'Navigation'),
  rec('arrowdown', 'recordings.next', t('lib.shortcuts.nextRecording'), 'Navigation'),
  rec('k', 'recordings.prev', t('lib.shortcuts.previousRecording'), 'Navigation'),
  rec('arrowup', 'recordings.prev', t('lib.shortcuts.previousRecording'), 'Navigation'),
  rec('o', 'recordings.open', t('lib.shortcuts.openTheRecording'), 'Navigation'),
  rec('enter', 'recordings.open', t('lib.shortcuts.openTheRecording'), 'Navigation'),
  rec('/', 'recordings.search', t('common.searchRecordings'), 'Navigation'),
  rec('n', 'recordings.record', t('common.newRecording'), 'Application'),
  rec('escape', 'recordings.escape', t('lib.shortcuts.closeTheRecordingThenClear'), 'Application'),
]

const nt = (keys: string, action: NoteShortcut, description: string, group: ShortcutGroup): Binding => ({ keys, action, description, group, scope: 'notes' })

/** The Notes app's keys (the `notes` scope). `g l` and `g b` replace the mail
 * meanings of `g b` (Snoozed) inside Notes. The keys of an open note are the editor's own
 * (NoteEditor), listed in NOTE_EDITOR_HELP. */
export const NOTE_BINDINGS: Binding[] = [
  nt('c', 'notes.newNote', t('lib.shortcuts.newTextNote'), 'Notes'),
  nt('l', 'notes.newList', t('common.newList'), 'Notes'),
  nt('j', 'notes.next', t('lib.shortcuts.nextCard'), 'Navigation'),
  nt('k', 'notes.prev', t('lib.shortcuts.previousCard'), 'Navigation'),
  nt('enter', 'notes.open', t('lib.shortcuts.openTheFocusedCard'), 'Navigation'),
  nt('o', 'notes.open', t('lib.shortcuts.openTheFocusedCard'), 'Navigation'),
  nt('f', 'notes.pin', t('lib.shortcuts.pinOrUnpin'), 'Notes'),
  nt('e', 'notes.archive', t('lib.shortcuts.archiveOrUnarchive'), 'Notes'),
  nt('t', 'notes.labels', t('common.labels'), 'Notes'),
  nt('#', 'notes.delete', t('lib.shortcuts.deleteToTrash'), 'Notes'),
  nt('delete', 'notes.delete', t('lib.shortcuts.deleteToTrash'), 'Notes'),
  nt('x', 'notes.select', t('common.select'), 'Selection'),
  nt('* a', 'notes.selectAll', t('common.selectAll'), 'Selection'),
  nt('* n', 'notes.selectNone', t('lib.shortcuts.selectNone'), 'Selection'),
  nt('alt+arrowleft', 'notes.moveLeft', t('lib.shortcuts.moveTheFocusedCardEarlier'), 'Notes'),
  nt('alt+arrowright', 'notes.moveRight', t('lib.shortcuts.moveTheFocusedCardLater'), 'Notes'),
  nt('/', 'notes.search', t('lib.shortcuts.searchNotes'), 'Navigation'),
  nt('g l', 'notes.goLists', t('lib.shortcuts.goToLists'), 'Navigation'),
  nt('g b', 'notes.goTables', t('common.goToTables'), 'Navigation'),
  nt('[', 'notes.toggleLeft', t('lib.shortcuts.showOrHideTheLeft'), 'Navigation'),
  nt('z', 'notes.undo', t('common.undo'), 'Notes'),
  nt('escape', 'notes.escape', t('lib.shortcuts.closeTheNoteThenClear'), 'Application'),
]

/** The keys inside an open note: the editor handles them itself. */
export const NOTE_EDITOR_HELP: { keys: string; description: string }[] = [
  { keys: 'ctrl+shift+8', description: t('lib.shortcuts.makeItAListMake') },
  { keys: 'enter', description: t('lib.shortcuts.aNewItemAfterThis') },
  { keys: 'shift+enter', description: t('lib.shortcuts.theExtraLineOfThe') },
  { keys: 'backspace', description: t('lib.shortcuts.inAnEmptyItemDelete') },
  { keys: 'ctrl+enter', description: t('lib.shortcuts.checkOrUncheckTheFocused') },
  { keys: 'alt+arrowup', description: t('lib.shortcuts.moveTheItemOrThe') },
  { keys: 'alt+arrowdown', description: t('lib.shortcuts.moveTheItemOrThe2') },
  { keys: 'alt+shift+arrowup', description: t('lib.shortcuts.moveTheItemToThe') },
  { keys: 'alt+shift+arrowdown', description: t('lib.shortcuts.moveTheItemToThe2') },
  { keys: 'alt+enter', description: t('lib.shortcuts.aNewSectionAfterThis') },
  { keys: 'escape', description: t('lib.shortcuts.closeSaves') },
]

/** The Calendar app's keys (web.calendar scope). */
export const CALENDAR_BINDINGS: Binding[] = [
  cal('t', 'calendar.today', t('common.today'), 'Navigation'),
  cal('j', 'calendar.next', t('lib.shortcuts.nextPeriod'), 'Navigation'),
  cal('n', 'calendar.next', t('lib.shortcuts.nextPeriod'), 'Navigation'),
  cal('k', 'calendar.prev', t('lib.shortcuts.previousPeriod'), 'Navigation'),
  cal('p', 'calendar.prev', t('lib.shortcuts.previousPeriod'), 'Navigation'),
  cal('g _', 'calendar.goToDate', t('lib.shortcuts.goToDateTypeIt'), 'Navigation'),
  // Arrows move between events; Tab keeps the browser's focus order. In Day and Week, with no
  // event focused, the arrows move the selected time slot instead (CalendarApp's grid keys).
  cal('arrowright', 'calendar.nextEvent', t('lib.shortcuts.nextEvent'), 'Navigation'),
  cal('arrowleft', 'calendar.prevEvent', t('lib.shortcuts.previousEvent'), 'Navigation'),
  cal('arrowdown', 'calendar.eventDown', t('lib.shortcuts.eventBelowLaterThatDay'), 'Navigation'),
  cal('arrowup', 'calendar.eventUp', t('lib.shortcuts.eventAboveEarlierThatDay'), 'Navigation'),
  cal('/', 'calendar.search', t('lib.shortcuts.searchTheCalendar'), 'Navigation'),
  cal('[', 'calendar.toggleLeft', t('lib.shortcuts.showOrHideTheLeft'), 'Navigation'),
  cal('h', 'calendar.viewToday', t('lib.shortcuts.todayView'), 'Views'),
  cal('d', 'calendar.viewDay', t('common.day'), 'Views'),
  cal('x', 'calendar.viewNDays', t('lib.shortcuts.severalDays'), 'Views'),
  cal('w', 'calendar.viewWeek', t('common.week'), 'Views'),
  cal('o', 'calendar.viewWorkWeek', t('common.workWeek'), 'Views'),
  cal('m', 'calendar.viewMonth', t('common.month'), 'Views'),
  cal('a', 'calendar.viewSchedule', t('common.schedule'), 'Views'),
  cal('c', 'calendar.create', t('lib.shortcuts.createAnEvent'), 'Events'),
  cal('e', 'calendar.open', t('lib.shortcuts.editTheFocusedEvent'), 'Events'),
  cal('enter', 'calendar.open', t('lib.shortcuts.openTheFocusedEvent'), 'Events'),
  cal('#', 'calendar.delete', t('lib.shortcuts.deleteTheFocusedEvent'), 'Events'),
  cal('delete', 'calendar.delete', t('lib.shortcuts.deleteTheFocusedEvent'), 'Events'),
  cal('backspace', 'calendar.delete', t('lib.shortcuts.deleteTheFocusedEvent'), 'Events'),
  cal('ctrl+d', 'calendar.duplicate', t('common.duplicate'), 'Events'),
  cal('f', 'calendar.findTime', t('common.findATime'), 'Events'),
  cal('y', 'calendar.rsvpYes', t('lib.shortcuts.replyYesToAnInvitation'), 'Events'),
  cal('s', 'calendar.rsvpMaybe', t('lib.shortcuts.replyMaybe'), 'Events'),
  cal('v', 'calendar.rsvpNo', t('lib.shortcuts.replyNo'), 'Events'),
  cal('z', 'calendar.undo', t('common.undo'), 'Events'),
  cal('r', 'calendar.sync', t('lib.shortcuts.refreshSyncNow'), 'Application'),
  cal('escape', 'calendar.escape', t('lib.shortcuts.closeClear'), 'Application'),
]

/** The Tasks app's keys (web.tasks scope). */
export const TASK_BINDINGS: Binding[] = [
  tsk('n', 'tasks.quickAdd', t('lib.shortcuts.addATask'), 'Tasks'),
  tsk('c', 'tasks.quickAdd', t('lib.shortcuts.addATask'), 'Tasks'),
  tsk('j', 'tasks.next', t('lib.shortcuts.nextTask'), 'Navigation'),
  tsk('arrowdown', 'tasks.next', t('lib.shortcuts.nextTask'), 'Navigation'),
  tsk('k', 'tasks.prev', t('lib.shortcuts.previousTask'), 'Navigation'),
  tsk('arrowup', 'tasks.prev', t('lib.shortcuts.previousTask'), 'Navigation'),
  tsk('enter', 'tasks.open', t('lib.shortcuts.openTheTask'), 'Navigation'),
  tsk('o', 'tasks.open', t('lib.shortcuts.openTheTask'), 'Navigation'),
  tsk('g t', 'tasks.goToday', t('lib.shortcuts.goToToday'), 'Navigation'),
  tsk('g u', 'tasks.goUpcoming', t('lib.shortcuts.goToUpcoming'), 'Navigation'),
  tsk('g a', 'tasks.goAll', t('lib.shortcuts.goToAll'), 'Navigation'),
  tsk('/', 'tasks.search', t('lib.shortcuts.searchTasks'), 'Navigation'),
  tsk('space', 'tasks.complete', t('lib.shortcuts.completeReopen'), 'Tasks'),
  tsk('e', 'tasks.edit', t('lib.shortcuts.editTheTitle'), 'Tasks'),
  tsk('f2', 'tasks.edit', t('lib.shortcuts.editTheTitle'), 'Tasks'),
  tsk('r', 'tasks.reschedule', t('lib.shortcuts.rescheduleChangeTheDueDate'), 'Tasks'),
  tsk('s', 'tasks.start', t('lib.shortcuts.setTheStartDate'), 'Tasks'),
  tsk('b', 'tasks.snooze', t('lib.shortcuts.snoozeTheReminder'), 'Tasks'),
  tsk('0', 'tasks.priority', t('lib.shortcuts.priorityNone'), 'Tasks'),
  tsk('1', 'tasks.priority', t('lib.shortcuts.priorityLow'), 'Tasks'),
  tsk('2', 'tasks.priority', t('lib.shortcuts.priorityMedium'), 'Tasks'),
  tsk('3', 'tasks.priority', t('lib.shortcuts.priorityHigh'), 'Tasks'),
  tsk('v', 'tasks.move', t('common.moveToList'), 'Tasks'),
  tsk('l', 'tasks.tags', t('common.tags'), 'Tasks'),
  tsk('#', 'tasks.delete', t('common.delete2'), 'Tasks'),
  tsk('delete', 'tasks.delete', t('common.delete2'), 'Tasks'),
  tsk('ctrl+d', 'tasks.duplicate', t('common.duplicate'), 'Tasks'),
  tsk('tab', 'tasks.indent', t('lib.shortcuts.makeASubtask'), 'Tasks'),
  tsk('shift+tab', 'tasks.outdent', t('lib.shortcuts.makeItATaskAgain'), 'Tasks'),
  tsk('alt+arrowup', 'tasks.moveUp', t('common.moveUp'), 'Tasks'),
  tsk('alt+arrowdown', 'tasks.moveDown', t('common.moveDown'), 'Tasks'),
  tsk('z', 'tasks.undo', t('common.undo'), 'Tasks'),
  tsk('x', 'tasks.select', t('common.select'), 'Selection'),
  tsk('* a', 'tasks.selectAll', t('common.selectAll'), 'Selection'),
  tsk('* n', 'tasks.selectNone', t('lib.shortcuts.selectNone'), 'Selection'),
  tsk('escape', 'tasks.escape', t('lib.shortcuts.closeThePaneThenClear'), 'Application'),
]

/** Keys the help lists that other code handles: the command palette (themes/shared/
 *  CommandPalette) and the editor's formatting keys (tiptap). The shortcut handler never sees
 *  them; they appear in every scope's help. */
export const HELP_ONLY: { keys: string; description: string; group: ShortcutGroup }[] = [
  { keys: 'ctrl+k', description: t('lib.shortcuts.commandPaletteAlsoK'), group: 'Application' },
  { keys: 'ctrl+b', description: t('lib.shortcuts.boldInAMessageYou'), group: 'Formatting' },
  { keys: 'ctrl+i', description: t('lib.shortcuts.italic'), group: 'Formatting' },
  { keys: 'ctrl+u', description: t('lib.shortcuts.underline'), group: 'Formatting' },
  { keys: 'ctrl+z', description: t('lib.shortcuts.undoTyping'), group: 'Formatting' },
  { keys: 'ctrl+shift+z', description: t('lib.shortcuts.redoTyping'), group: 'Formatting' },
]

// Bindings apps add at run time (registerBindings), on top of the tables above.
let extra: Binding[] = []
let tableCache: Map<ShortcutScope, Binding[]> | null = null

/** Add bindings (an app's own extra keys); returns a function that removes them again. */
export function registerBindings(bindings: Binding[]): () => void {
  extra = [...extra, ...bindings]
  tableCache = null
  return () => {
    extra = extra.filter((b) => !bindings.includes(b))
    tableCache = null
  }
}

function allBindings(): Binding[] {
  return [...BINDINGS, ...CALENDAR_BINDINGS, ...TASK_BINDINGS, ...RECORDING_BINDINGS, ...NOTE_BINDINGS, ...extra]
}

const scopeOf = (b: Binding): ShortcutScope => b.scope ?? 'mail'

/**
 * The keys in force in an app: its own bindings first, then the global ones it does not
 * shadow (same keys). 'global' alone gives only the global keys.
 */
export function bindingsFor(scope: ShortcutScope): Binding[] {
  tableCache ??= new Map()
  let list = tableCache.get(scope)
  if (!list) {
    const all = allBindings()
    const own = scope === 'global' ? [] : all.filter((b) => scopeOf(b) === scope)
    const taken = new Set(own.map((b) => b.keys))
    list = [...own, ...all.filter((b) => scopeOf(b) === 'global' && !taken.has(b.keys))]
    tableCache.set(scope, list)
  }
  return list
}

export type ShortcutHandler = (arg?: string) => boolean | void

const handlerSources = new Map<AppScope, () => Partial<Record<AppShortcut, ShortcutHandler>>>()

/**
 * Attach an app's handlers. `source` is read on every key press, so it can return the latest
 * closures. Returns a function that detaches them. A handler returning false leaves the key
 * to the browser (e.g. Tab when nothing is focused).
 */
export function setShortcutHandlers(scope: AppScope, source: () => Partial<Record<AppShortcut, ShortcutHandler>>): () => void {
  handlerSources.set(scope, source)
  return () => {
    if (handlerSources.get(scope) === source) handlerSources.delete(scope)
  }
}

export function shortcutHandler(scope: ShortcutScope, action: AnyShortcut): ShortcutHandler | undefined {
  if (scope === 'global') return undefined
  return handlerSources.get(scope)?.()[action as AppShortcut]
}

export function isAppShortcut(action: AnyShortcut): action is AppShortcut {
  return action.includes('.')
}

/** The global binding an app binding shadows (Esc in the calendar falls back to it when the app has no handler). */
export function shadowedGlobal(keys: string | undefined): Binding | undefined {
  return keys ? bindingsFor('global').find((b) => b.keys === keys) : undefined
}

/** The scope a URL's keys belong to: mail, calendar, tasks, recordings and notes have their own. Settings and
 * any other page get 'settings', the global keys only, so no mail key can act on a list that is not
 * on screen (# in Settings must not trash the mail selected in the hidden inbox). */
export function scopeForPath(pathname: string): AppScope {
  const seg = pathname.split('/')[1] ?? ''
  if (seg === 'mail' || seg === '') return 'mail'
  if (seg === 'tables') return 'notes'
  return seg === 'calendar' || seg === 'tasks' || seg === 'recordings' || seg === 'notes' ? seg : 'settings'
}

export interface KeyLike {
  key: string
  ctrlKey?: boolean
  metaKey?: boolean
  altKey?: boolean
  shiftKey?: boolean
}

/** Normalise a keyboard event into a token like "g", "I", "#", "ctrl+enter", "escape". */
export function keyToken(e: KeyLike): string | null {
  let k = e.key
  if (!k || k === 'Shift' || k === 'Control' || k === 'Meta' || k === 'Alt') return null
  // A letter's case comes from Shift, not from Caps Lock (r stays Reply with Caps Lock on).
  if (/^[a-zA-Z]$/.test(k)) k = e.shiftKey ? k.toUpperCase() : k.toLowerCase()
  const lower = k === ' ' ? 'space' : k.length === 1 ? k : k.toLowerCase()
  const mod = e.ctrlKey || e.metaKey
  const named = k.length > 1
  // Alt with a character is AltGr on many keyboards (typing, not a shortcut); Alt with a named
  // key (Alt+↑) is a shortcut.
  if (e.altKey) return !mod && named ? `alt+${e.shiftKey ? 'shift+' : ''}${lower}` : null
  if (mod) return `ctrl+${lower.toLowerCase()}`
  if (lower === 'esc') return 'escape'
  if (e.shiftKey && SHIFTABLE.has(lower)) return `shift+${lower}`
  return lower
}

// Named keys whose Shift form is its own shortcut (Shift+Tab, Shift+arrows).
const SHIFTABLE = new Set(['tab', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright', 'pageup', 'pagedown', 'home', 'end'])

export interface MatchState {
  pending: string[]
}

export interface MatchResult<A extends AnyShortcut = AnyShortcut> {
  action: A | null
  /** What a "_" in the binding matched (the first typed character of "g _"), else the last key
   * pressed ("2" for a priority key). */
  arg?: string
  /** The matched binding's keys, e.g. "g _". */
  keys?: string
  state: MatchState
  /** true when the key was consumed (as a prefix or a full match) */
  handled: boolean
}

const seqCache = new Map<Binding[], { seq: string[]; action: AnyShortcut; keys: string }[]>()

function seqs(scope: ShortcutScope) {
  const list = bindingsFor(scope)
  let s = seqCache.get(list)
  if (!s) {
    s = list.map((b) => ({ seq: b.keys.split(' '), action: b.action, keys: b.keys }))
    seqCache.clear()
    seqCache.set(list, s)
  }
  return s
}

// "_" stands for any one printable character.
const tokMatches = (want: string, tok: string, wild: boolean) => want === tok || (wild && want === '_' && tok.length === 1)

/**
 * Feed one key; returns the matched action (if any) and the next state. `scope` is the app the
 * key is pressed in ('mail' by default): its bindings and the global ones it does not shadow.
 * An exact key beats a "_" wildcard, so "g c" still goes to Calendar inside the calendar.
 */
export function feedKey(state: MatchState, e: KeyLike, scope: ShortcutScope = 'mail'): MatchResult {
  const tok = keyToken(e)
  if (!tok) return { action: null, state, handled: false }
  const seq = [...state.pending, tok]
  const all = seqs(scope)
  const find = (wild: boolean) => all.find((s) => s.seq.length === seq.length && s.seq.every((k, i) => tokMatches(k, seq[i], wild)))
  const prefixOf = (wild: boolean) => all.some((s) => s.seq.length > seq.length && seq.every((k, i) => tokMatches(s.seq[i], k, wild)))
  const exact = find(false)
  const prefix = prefixOf(false)
  if (exact && !prefix) return { action: exact.action, arg: tok, keys: exact.keys, state: { pending: [] }, handled: true }
  if (prefix) return { action: null, state: { pending: seq }, handled: true }
  const wild = find(true)
  if (wild && !prefixOf(true)) {
    const at = wild.seq.indexOf('_')
    return { action: wild.action, arg: at >= 0 ? seq[at] : tok, keys: wild.keys, state: { pending: [] }, handled: true }
  }
  // No match with the pending prefix: retry this key alone.
  if (state.pending.length) return feedKey({ pending: [] }, e, scope)
  return { action: null, state: { pending: [] }, handled: false }
}

/** The parts of a focused element the shortcut guard reads (a DOM element, or a stand-in in tests). */
export interface TargetLike {
  tagName?: string
  isContentEditable?: boolean
  getAttribute?: (name: string) => string | null
  hasAttribute?: (name: string) => boolean
  closest?: (selector: string) => unknown
}

// Controls by tag and by ARIA role: what a keyboard user activates, toggles or moves within.
const CONTROL_TAGS = new Set(['button', 'input', 'select', 'textarea', 'summary', 'option'])
const CONTROL_ROLES = new Set([
  'button', 'link', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option', 'tab', 'checkbox', 'radio',
  'switch', 'slider', 'spinbutton', 'combobox', 'textbox', 'searchbox', 'treeitem', 'scrollbar',
])
// Keys a control uses itself: activation (Enter, Space), focus moves (Tab), arrows and paging inside
// lists, menus and sliders, and Delete/Backspace (never a shortcut's data change from a control).
const NATIVE_KEYS = new Set([
  'enter', 'space', 'tab', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright', 'home', 'end', 'pageup', 'pagedown',
  'delete', 'backspace',
])

/** Whether the focused element is a control: a link, a button, a form field, an editable region or
 * an element with a control's role. A list row (role=row) is not; nor is an element marked
 * data-shortcuts, a surface whose keys are the app's (a calendar event: the arrows to the next, Delete;
 * Tab is never a shortcut there and keeps the browser's focus order). */
export function isInteractiveTarget(target: EventTarget | TargetLike | null): boolean {
  const el = target as TargetLike | null
  if (!el || !el.tagName) return false
  if (el.hasAttribute?.('data-shortcuts')) return false
  const tag = el.tagName.toLowerCase()
  if (CONTROL_TAGS.has(tag)) return true
  if (tag === 'a' && el.hasAttribute?.('href')) return true
  if (el.isContentEditable === true) return true
  const role = el.getAttribute?.('role')
  return !!role && CONTROL_ROLES.has(role.toLowerCase())
}

/**
 * Whether a key belongs to the focused control rather than to the shortcuts: Enter on a link or a
 * button, Space on a checkbox, Tab out of any control, arrows in a menu. Keys with Ctrl or Meta
 * (Ctrl+Enter sends) and plain characters (# on a toolbar button) stay shortcuts.
 */
export function keyBelongsToTarget(target: EventTarget | TargetLike | null, e: KeyLike): boolean {
  if (e.ctrlKey || e.metaKey) return false
  // Copy the fields: a DOM KeyboardEvent's `key` lives on its prototype, so a spread drops it.
  const tok = keyToken({ key: e.key, ctrlKey: e.ctrlKey, metaKey: e.metaKey, altKey: false, shiftKey: false })
  if (!tok || !NATIVE_KEYS.has(tok)) return false
  return isInteractiveTarget(target)
}

/** Surfaces where the app's single keys never run: a compose window or inline reply, Ambrose's
 *  chat, and a dialog that is not modal (a modal one such as the event detail or the palette takes
 *  app keys on purpose). Tab from To used to land on the Cc button, where `c` opened a second
 *  compose window. */
export const QUIET_SURFACES = '.compose-win, .compose-form, .side-panel, [role="dialog"]:not([aria-modal="true"])'

/** Whether a key pressed on this target must not run a single-key shortcut: the focus is inside a
 *  quiet surface (QUIET_SURFACES) and the key has no Ctrl, Meta or Alt (Ctrl + Enter, Ctrl + . stay). */
export function keyStaysInSurface(target: EventTarget | TargetLike | null, e: KeyLike): boolean {
  if (e.ctrlKey || e.metaKey || e.altKey) return false
  const el = target as TargetLike | null
  return !!el?.closest?.(QUIET_SURFACES)
}

/** Whether a key event originated in a text field (shortcuts are then ignored). */
export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  const tag = el.tagName.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  return el.isContentEditable === true
}

const KEY_NAMES: Record<string, string> = {
  enter: 'Enter', escape: t('common.esc'), space: t('common.space'), tab: t('common.tab'), delete: t('lib.shortcuts.del'), backspace: 'Backspace', f2: 'F2',
  arrowup: '↑', arrowdown: '↓', arrowleft: '←', arrowright: '→', _: t('lib.shortcuts.aDate'),
}

export function displayKeys(keys: string): string {
  return keys
    .split(' ')
    .map((k) => {
      if (/^[A-Z]$/.test(k)) return t('lib.shortcuts.shift', { k: k.toLowerCase() })
      if (k === '_') return KEY_NAMES._
      if (k.length === 1) return k
      const parts = k.split('+')
      const key = parts.pop()!
      const mods = parts.map((m) => (m === 'ctrl' ? t('lib.shortcuts.ctrl') : m === 'alt' ? t('common.alt') : m === 'shift' ? t('lib.shortcuts.shift2') : m))
      return [...mods, KEY_NAMES[key] ?? (key.length > 1 ? key[0].toUpperCase() + key.slice(1) : key)].join(' + ')
    })
    .join(' then ')
}
