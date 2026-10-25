// A theme is a UI philosophy: design tokens (CSS, scoped by [data-ui-theme]) plus a shell that
// composes the app from shared building blocks.

import type { ComponentType, ReactNode } from 'react'
import type { ConversationSummary, Label, Settings } from '../api/types'
import type { RowHandlers } from '../features/mail/ConversationRow'

export type ThemeId = 'classic' | 'office' | 'focus' | 'desk' | 'cards'

/** Props every list row component receives (the same contract as ConversationRow). */
export interface ListRowProps extends RowHandlers {
  c: ConversationSummary
  labelsById: Map<number, Label>
  selected: boolean
  focused: boolean
  view: string
  markers: boolean
  showChips: boolean
  narrow: boolean
  hideLabelId?: number
  /** This row is its grid's tab stop (the cursor row, else the first row). */
  tabStop?: boolean
  /** 1-based position in its grid (aria-rowindex), group headings included. */
  rowIndex?: number
  /** The row can be dragged onto the nav (desktop pointer). */
  draggable?: boolean
}

/** A group heading inside a list (date buckets, sender bundles). */
export interface ListGroup {
  id: string
  title: string
  count: number
  unread: number
  /** A collapsed group shows its heading only; its rows leave keyboard order too. */
  collapsed?: boolean
  collapsible?: boolean
  /** Up to a few conversations, for bundle summaries (avatars, sender names). */
  sample?: ConversationSummary[]
}

export type ListEntry = { kind: 'group'; group: ListGroup } | { kind: 'row'; c: ConversationSummary }

/** Client-side list preferences a presentation may use (sorting, quick filters, collapsed groups). */
export interface ListPrefs {
  sort: { key: SortKey; dir: 'asc' | 'desc' } | null
  quick: { unread: boolean; starred: boolean; attachments: boolean }
  collapsed: Record<string, boolean>
}
export type SortKey = 'date' | 'from' | 'subject' | 'star' | 'count' | 'labels' | 'attach'

export interface ArrangeContext {
  view: string
  prefs: ListPrefs
  labelsById: Map<number, Label>
  /** The list is one of several inbox sections, which already group the mail. */
  sectioned?: boolean
  /** Cards bundles (settings.card_bundles): only in lists without sections, always, or never. */
  bundles?: 'flat' | 'always' | 'never'
}

/**
 * How a theme draws the message list. MailList keeps all the data logic (layouts, sections,
 * paging, selection, keyboard order); the presentation only decides the rows, grouping and
 * the chrome around them.
 */
export interface ListPresentation {
  /** Row component; ConversationRow in Classic. */
  Row: ComponentType<ListRowProps>
  /** Estimated row height for the virtualiser (rows are measured after render). */
  rowHeight: (narrow: boolean) => number
  /** Group and order the rows. The returned order is also the keyboard (j/k) order. */
  arrange?: (items: ConversationSummary[], ctx: ArrangeContext) => ListEntry[] | null
  /** Heading drawn for a group entry. */
  GroupHead?: ComponentType<{ group: ListGroup; view: string }>
  /** A header drawn above the rows (table columns). */
  ListHeader?: ComponentType<{ view: string; narrow: boolean }>
  /** 'minimal': selection, refresh and paging only (the shell has its own command bar). */
  toolbar: 'full' | 'minimal'
  /** Where the inbox layout tabs sit: inside the scrolling list (Gmail) or above the toolbar. */
  tabsPlacement: 'inside' | 'top'
  /** How many indicators an inbox tab has room for (lib/tabIndicators.ts): 'full' (two pills,
   *  the default) or 'compact' (one, a small number or a coloured dot). Phones get 'minimal'. */
  tabIndicators?: 'full' | 'compact'
  /** Force the two-line narrow row even without a reading pane. */
  alwaysNarrow?: boolean
}

/** Setting values a theme forces while it is active (the stored settings are left alone). */
export type ThemeOverrides = Partial<Pick<Settings, 'reading_pane' | 'density'>>

export interface ShellProps {
  /** The routed page (mail list, conversation, settings), wrapped in the error boundary. */
  children: ReactNode
}

/**
 * How the Calendar and Tasks apps present details in this theme (plan §3, the theme table).
 * The apps read it with useThemeDef().apps; the frame around them is the shell's.
 */
export interface AppPresentation {
  /** Event detail: a popover at the event, the reading pane, a centred card, or a bottom sheet. */
  eventDetail: 'popover' | 'pane' | 'card' | 'sheet'
  /** Task detail: a pane on the right, a pane below the list, a centred card, or a bottom sheet. */
  taskDetail: 'pane' | 'below' | 'card' | 'sheet'
  /** Note detail: a card over the grid (Classic: a popover card as in
   *  Keep; Focus: a centred card), the reading pane (Workspace), the pane below (Desk), or a bottom sheet (Cards). */
  noteDetail: 'popover' | 'pane' | 'card' | 'below' | 'sheet'
  /** The task list: rows, or a sortable table (Desk). */
  taskList: 'list' | 'table'
}

export interface ThemeDef {
  id: ThemeId
  name: string
  /** One line: the philosophy. */
  tagline: string
  /** Two or three sentences for the picker. */
  description: string
  /** Small SVG preview of the layout; draws with the page's current tokens. */
  Preview: ComponentType
  /** The app frame: navigation, top bar, panels, where compose lives. */
  Shell: ComponentType<ShellProps>
  list: ListPresentation
  /** Calendar and Tasks detail presentation. */
  apps: AppPresentation
  overrides?: ThemeOverrides
  /** The theme's reading pane, given the viewport (overrides reading_pane). */
  readingPane?: (wide: boolean) => Settings['reading_pane']
  /** Reading panes the user may pick in this theme (remembered per theme in
   *  settings.reading_pane_by_theme). Absent: the theme's layout is fixed. */
  paneChoices?: Settings['reading_pane'][]
  /** The shell draws the reader itself (Office: tabs); MailPage then draws only the list. */
  externalReader?: boolean
}
